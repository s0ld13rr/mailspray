# mailspray

```
______  ___      __________________
___   |/  /_____ ___(_)__  /_  ___/____________________ _____  __
__  /|_/ /_  __ `/_  /__  /_____ ___  __ \_  ___/  __ `/_  / / /
_  /  / / / /_/ /_  / _  / ____/ /__  /_/ /  /   / /_/ /_  /_/ /
/_/  /_/  \__,_/ /_/  /_/  /____/ _  .___//_/    \__,_/ _\__, /
                                  /_/                 /____/
 mail password spraying toolkit // authorized testing only
```

CLI for password spraying against mail stacks: OWA, EWS, ADFS, IMAP, SMTP, Roundcube, Zimbra. Batching, optional delay and jitter, per-user attempt limits, and protocol-aware username formatting.

> **Authorized security testing only.**

## Install

**pipx** (recommended: global `mailspray` on your PATH):

```bash
git clone https://github.com/s0ld13rr/mailspray.git
cd mailspray
pipx install .
mailspray --help
```

From Git without cloning:

```bash
pipx install git+https://github.com/s0ld13rr/mailspray.git
```

When the package is on PyPI: `pipx install mailspray`.

**Editable / dev** (venv can live outside the repo):

```bash
python3 -m venv ~/venvs/mailspray
source ~/venvs/mailspray/bin/activate   # Windows: venv\Scripts\activate
cd /path/to/mailspray
pip install -e .
mailspray -V
```

## Saving hits (`-o`, `-j`)

Optional. If you use them, pass a **real path**: absolute (e.g. `-o /tmp/hits.txt`) or a **relative path that includes a directory** (e.g. `-o ../out/hits.txt`). A **bare filename** like `-o hits.txt` is **rejected** (you get a clear error). Writes are **blocked** into the installed package / dev source tree when that path would land inside it (use `/tmp`, your home directory, etc.).

- `-o PATH` — append one line per found credential (URI style).
- `-j PATH` — write JSON with structured records when the run finishes.

Full flags: **`mailspray --help`**.

---

## Protocols

| Protocol    | Default port | Notes |
|-------------|-------------|--------|
| `owa`       | 443         | Outlook Web Access |
| `ews`       | 443         | Exchange Web Services (Basic/NTLM) |
| `adfs`      | 443         | AD FS WS-Trust (UPN) |
| `imap`      | 993         | IMAP SSL or STARTTLS |
| `smtp`      | 587         | STARTTLS (or 465 SMTPS with `-P`) |
| `roundcube` | 443         | Roundcube webmail |
| `zimbra`    | 443         | Zimbra webmail |

### OWA vs EWS vs ADFS

**OWA** — browser form login to Exchange (`/owa`). Cookie session.

**EWS** — `/EWS/Exchange.asmx`, Basic or NTLM. Often reachable where OWA is filtered.

**ADFS** — federation, expects **UPN** (`user@domain`). WS-Trust at `/adfs/services/trust/2005/usernamemixed`.

---

## Username formats and `-d`

`-d DOMAIN` supplies the domain; default shape depends on protocol:

- `owa`, `ews` → `DOMAIN\user`
- `adfs`, `roundcube`, `zimbra` → `user@domain`
- `imap`, `smtp` → `plain` unless you override with `-F`

Override: `-F auto | domain_prefix | upn | plain`. If a line already has `user@…` or `DOMAIN\user`, it is left as-is.

---

## Examples

```bash
# Probe one login (exit 0 on success)
mailspray ews https://mail.example.com -k auditor -p 'Secret!' -d CORP -F upn -T 20

# OWA spray with throttling
mailspray owa mail.example.com -u users.txt -p passes.txt -d CORP \
  -n 3 -S -e 30 -J 0.3 -t 5

# ADFS (UPN applied from -d)
mailspray adfs adfs.example.com -u users.txt -p passes.txt -d corp.local \
  -n 3 -S -e 30 -J 0.3

# IMAP / internal quick mode
mailspray imap 192.168.1.10 -u users.txt -p 'Password1' -f

# Output to disk
mailspray owa https://owa.example.com -u u.txt -p p.txt -d CORP \
  -o /tmp/mailspray-hits.txt -j /tmp/mailspray-results.json
```

---

## Lockout-aware usage

Against AD-style lockout, prefer low concurrency, delays, and caps:

- `-n 3` with `-S` limits attempts per user and stops after a hit.
- `-e` / `-J` space batches in time instead of hammering logons.
- `-f` is for internal targets without strict lockout.

---

## Dependencies

Declared in `pyproject.toml` (`requests`, `urllib3`). No separate `requirements.txt` is required for install; `pipx` / `pip` resolve them from the package metadata.
