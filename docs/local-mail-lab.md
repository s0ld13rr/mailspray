# Local mail lab (IMAP, SMTP, Roundcube)

Use only on **authorized** isolated hosts. This file mirrors the procedure from the maintainer `.cursor/lab`; adjust image tags and **change all passwords** before use.

## Stack

1. **GreenMail** (`greenmail/standalone`) for SMTP and IMAP.
2. **Roundcube** (optional) in a second phase with Docker Compose **profiles**.

Example layout:

- Phase 1: `docker compose up -d` (IMAP/SMTP only).
- Phase 2: `docker compose --profile web up -d` (adds Roundcube on e.g. port 8080).

Map host ports to the GreenMail container ports documented for your image version (SMTP, IMAPS, optional plain IMAP).

## Suggested checks with mailspray

```text
mailspray imap 127.0.0.1 -P 993 -k user@example.test -p 'CORRECT' -T 15
mailspray smtp 127.0.0.1 -P 587 -k user@example.test -p 'CORRECT' -T 15
mailspray roundcube http://127.0.0.1:8080 -k user@example.test -p 'CORRECT' -d example.test -F upn -T 30
```

Use low `-t`, delays, and small user/password lists when spraying.

## Cleanup

```bash
docker compose --profile web down -v
docker compose down -v
```

Removes containers **and** named volumes so the lab does not leave mail data on disk.

## Production matrix (OWA, EWS, ADFS)

Capture observed responses on your infrastructure separately from this Docker lab. Future tooling may distinguish «authenticated» vs «mail-enabled» accounts once signals are documented.
