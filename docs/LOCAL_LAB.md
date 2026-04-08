# Local lab quick reference

Sensitive compose files and passwords stay under **`.claude/lab/`**, which is **gitignored** (see [README](../README.md)).

## Docker (IMAP, SMTP, Roundcube)

1. Copy the example compose file:

   ```bash
   mkdir -p .claude/lab
   cp docs/lab/docker-compose.example.yml .claude/lab/docker-compose.yml
   ```

2. Edit `.claude/lab/docker-compose.yml` and replace `CHANGEME` with your lab password.

3. Follow [.claude/lab/README.md](../.claude/lab/README.md) for `docker compose up`, ports, and `mailspray` command lines.

   That README also covers **cleanup** (`docker compose down -v`, optional image prune, VM snapshot notes).

## Windows VM (OWA, EWS, ADFS)

See [.claude/lab/WINDOWS_LAB.md](../.claude/lab/WINDOWS_LAB.md).

## Zimbra

See [.claude/ZIMBRA_SCOPE.md](../.claude/ZIMBRA_SCOPE.md).

Note: paths under `.claude/` exist only on your machine after you create them; they are not part of the public clone.
