# mailspray

```
______  ___      __________________
___   |/  /_____ ___(_)__  /_  ___/____________________ _____  __
__  /|_/ /_  __ `/_  /__  /_____ ___  __ \_  ___/  __ `/_  / / /
_  /  / / / /_/ /_  / _  / ____/ /__  /_/ /  /   / /_/ /_  /_/ /
/_/  /_/  \__,_/ /_/  /_/  /____/ _  .___//_/    \__,_/ _\__, /
                                  /_/  v0.4.0           /____/
 mail password spraying toolkit // authorized testing only
```

Password spraying toolkit for mail systems. Supports multiple protocols with built-in lockout protection, jitter, connection reuse, and automatic username formatting per protocol.

> **For authorized security testing only.**

---

## Protocols

| Protocol    | Default Port | Method                             | Auto username format |
|-------------|-------------|------------------------------------|----------------------|
| `owa`       | 443         | Outlook Web Access (form login)    | `CORP\user`          |
| `ews`       | 443         | Exchange Web Services (Basic/NTLM) | `CORP\user`          |
| `adfs`      | 443         | AD Federation Services (WS-Trust)  | `user@domain`        |
| `imap`      | 993         | IMAP with SSL/STARTTLS             | plain (no change)    |
| `smtp`      | 587         | SMTP with STARTTLS                 | plain (no change)    |
| `roundcube` | 443         | Roundcube Webmail                  | `user@domain`        |
| `zimbra`    | 443         | Zimbra Webmail                     | `user@domain`        |

### OWA vs EWS vs ADFS — в чём разница

**OWA** — браузерный веб-интерфейс Exchange (`/owa/auth.owa`). Форма логина, cookie-based сессия. Обычно торчит наружу, хорошо известен WAF'ам.

**EWS** — API Exchange для почтовых клиентов (Outlook desktop, мобильные). Endpoint `/EWS/Exchange.asmx`, HTTP Basic или NTLM auth. Бывает открыт даже когда OWA закрыта файрволом или WAF'ом — потому что Outlook'у нужен. Один сервер Exchange, разные векторы входа.

**ADFS** — федеративный SSO-шлюз перед Exchange/O365/SharePoint. Принимает только UPN (`user@domain.com`), `DOMAIN\user` не поддерживает. Endpoint: `/adfs/services/trust/2005/usernamemixed` (WS-Trust SOAP). Отвечает 200 + SAML-токен при успехе, 500 + SOAP fault при неверных кредах.

---

## Username formats & `-d` flag

Флаг `-d` задаёт домен, а формат выбирается **автоматически по протоколу**:

- `owa`, `ews` → `CORP\user` (Exchange NTLM-стиль)
- `adfs` → `user@corp.local` (UPN, обязательный для ADFS)
- `imap`, `smtp` → без изменений (зависит от конфигурации сервера)
- `roundcube`, `zimbra` → `user@domain`

Если список пользователей уже содержит `user@domain` или `DOMAIN\user` — они не перезаписываются.

Для переопределения используй `--user-format`:

```
--user-format auto          # дефолт — по протоколу
--user-format domain_prefix # всегда CORP\user
--user-format upn           # всегда user@domain
--user-format plain         # без изменений
```

---

## Options

```
TARGET:
  PROTOCOL               Protocol: owa, ews, adfs, imap, smtp, roundcube, zimbra
  TARGET                 Host, IP, or URL (e.g. http://mail.corp.com:8080)
  -P, --port PORT        Override default port

CREDENTIALS:
  -u, --user USER        Username or file with usernames
  -p, --password PASS    Password or file with passwords
  -d, --domain DOMAIN    Domain to apply (format chosen automatically per protocol)
  --user-format FMT      Override: auto (default), domain_prefix, upn, plain

ENGINE:
  -t, --threads N        Thread count (default: 5; use --fast for local/internal targets)
  --fast                 Fast mode for internal targets: 30 threads, no delay
  --delay SEC            Delay between requests in seconds
  --jitter 0-1           Jitter factor for delay (0.0-1.0)
  --timeout SEC          Connection timeout (default: 10)
  --max-attempts N       Max login attempts per user before skipping (0 = unlimited)
  --stop-on-success      Skip remaining passwords for a user after first success

OUTPUT:
  -o, --output FILE      Output file for valid creds (default: found.txt)
  --json FILE            Save found credentials to JSON file
  -v, --verbose          Show failed attempts, skips, and errors
  --no-color             Disable colored output
```

---

## Examples

```
# Внешний OWA — безопасные дефолты: 5 потоков, задержка 30с, макс 3 попытки
mailspray owa mail.corp.com -u users.txt -p 'Winter2026!' -d CORP \
  --max-attempts 3 --stop-on-success --delay 30 --jitter 0.3

# Внешний ADFS — UPN подставляется автоматически
mailspray adfs adfs.corp.com -u users.txt -p passes.txt -d corp.local \
  --max-attempts 3 --stop-on-success --delay 30 --jitter 0.3

# Внешний EWS — может быть открыт даже когда OWA закрыта WAF'ом
mailspray ews https://mail.corp.com -u users.txt -p passes.txt -d CORP \
  --max-attempts 3 --stop-on-success --delay 20 --jitter 0.5

# Внутренний пентест — --fast: 30 потоков, без задержки
mailspray imap 192.168.1.10 -u users.txt -p 'Password1' --fast
mailspray owa 10.10.10.5 -u users.txt -p passes.txt -d CORP --fast --max-attempts 3

# SMTP внешний — без домена по умолчанию; переопределить если нужен UPN
mailspray smtp smtp.target.com -u emails.txt -p passwords.txt --delay 5 --jitter 0.4
mailspray smtp smtp.corp.com -u users.txt -p passes.txt -d corp.local --user-format upn

# Roundcube на кастомном HTTP порту
mailspray roundcube http://mail.corp.com:8080 -u users.txt -p pass.txt -d corp.com

# Zimbra + JSON output
mailspray zimbra http://webmail.target.com:8443 -u users.txt -p 'Spring2026!' \
  -d target.com --json results.json
```

---

## Lockout protection

При работе против AD (дефолтный GPO порог блокировки — 5 попыток):

- `--max-attempts 3` — пропустить юзера после 3 неудач, не достигая порога
- `--stop-on-success` — нашли пароль для юзера — больше не пробуем другие пароли
- `--delay 30 --jitter 0.3` — случайная задержка 21–39 сек между запросами

Для внутреннего пентеста без AD lockout-политики используй `--fast`.

---

## Installation

```
git clone https://github.com/s0ld13rr/mailspray
cd mailspray
pip3 install requests
python3 main.py --help
```
