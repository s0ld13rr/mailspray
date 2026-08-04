"""Secret / credential patterns for the cred_scan module.

scan_text(text) -> list of (category, context_line) tuples.

Patterns are intentionally conservative to keep false positives low: each entry
targets a concrete, high-signal shape (assignment with a real value, a known key
prefix, a URL with inline credentials, a PEM header, etc.). Generic "the word
password appears somewhere" matches are avoided, and key=value secrets run
through a validator so prose like "password: click the button" is rejected.
"""

import re


def _looks_secret(value):
    """A captured value is credential-like if it has a non-letter char or is long.

    Rejects plain dictionary words ("click", "button", "RENEW") that follow a
    keyword in ordinary prose, while keeping real secrets (mixed case+digits+
    symbols, or long passphrases)."""
    if not value:
        return False
    return bool(re.search(r"[^A-Za-z]", value)) or len(value) >= 10


# Each pattern: (category, compiled_regex, validator_or_None).
# The validator, when present, receives the captured value (last group) and must
# return True for the match to count. Case-sensitive shapes (AWS/PEM) skip re.I.
_PATTERNS = [
    # key=value / key: value secrets with a validated, non-trivial value.
    # Leading [\w.\-]* lets prefixed identifiers match (db_password, smtp_pwd, X-Api-Key).
    ("password", re.compile(
        r"(?i)\b[\w.\-]*(?:password|passwd|passphrase|pwd)\b\s*[:=]\s*[\"']?([^\s\"'<>]{4,})"),
        _looks_secret),
    ("secret", re.compile(
        r"(?i)\b[\w.\-]*(?:secret|client[_-]?secret|api[_-]?secret)\b\s*[:=]\s*[\"']?([^\s\"'<>]{6,})"),
        _looks_secret),
    ("api_key", re.compile(
        r"(?i)\b[\w.\-]*(?:api[_-]?key|apikey|access[_-]?key|access[_-]?token)\b\s*[:=]\s*[\"']?([A-Za-z0-9_\-\.]{12,})"),
        None),

    # Provider-specific tokens (self-validating shapes)
    ("aws_access_key", re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), None),
    ("aws_secret_key", re.compile(
        r"(?i)aws_secret_access_key\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})"), None),
    ("slack_token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})"), None),
    ("github_token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"), None),
    ("google_api_key", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b"), None),
    ("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,})"), None),

    # HTTP Authorization headers
    ("bearer_token", re.compile(r"(?i)authorization\s*:\s*bearer\s+([A-Za-z0-9_\-\.=]{12,})"), None),
    ("basic_auth_header", re.compile(r"(?i)authorization\s*:\s*basic\s+([A-Za-z0-9+/=]{12,})"), None),

    # Private keys (PEM). Case-sensitive header form.
    ("private_key", re.compile(
        r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), None),

    # Credentials inline in URLs: scheme://user:pass@host
    ("url_credentials", re.compile(
        r"\b([a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s:/@]+@[^\s/]+)"), None),

    # DB / connection strings with an embedded password
    ("connection_string", re.compile(
        r"(?i)(?:server|data source|host)\s*=\s*[^;\s]+;[^\n]*?\bpassword\s*=\s*([^;\s\"'<>]+)"), None),

    # OpenVPN config markers (a .ovpn body)
    ("openvpn", re.compile(r"(?im)^\s*auth-user-pass(?:\s+\S+)?\s*$"), None),
    ("openvpn_remote", re.compile(r"(?im)^\s*remote\s+\S+\s+\d{2,5}\b"), None),
]

# Number of characters of surrounding context to keep on each side of a match.
_CONTEXT = 60


def _context_for(text, match):
    """Return a single trimmed line/window around the match for reporting."""
    start = max(0, match.start() - _CONTEXT)
    end = min(len(text), match.end() + _CONTEXT)
    snippet = text[start:end]
    snippet = re.sub(r"\s+", " ", snippet).strip()
    return snippet


def scan_text(text):
    """Scan text; return list of (category, context) tuples. Deduped per (category, context)."""
    if not text:
        return []
    results = []
    seen = set()
    for entry in _PATTERNS:
        category, rx, validator = entry
        for m in rx.finditer(text):
            if validator is not None:
                value = m.group(m.lastindex) if m.lastindex else m.group(0)
                if not validator(value):
                    continue
            ctx = _context_for(text, m)
            key = (category, ctx)
            if key in seen:
                continue
            seen.add(key)
            results.append((category, ctx))
    return results


# Convenience for callers/tests that want just the category names.
CATEGORIES = tuple(sorted({c for c, _, _ in _PATTERNS}))
