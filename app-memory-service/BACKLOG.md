# Memory-Service — Backlog

Offene Verbesserungen fuer Claude Code. Erledigtes entfernen. Details bei Bedarf
als Memory-Notiz (`type project`/`gotcha`), nicht hier ausufern lassen.

## Offen
- [ ] `ensure_repo()`: Notes-Repo auf frischem Volume automatisch anlegen. Aktuell
      hat ein neues `vol_data/notes/` kein `.git`, und `git_commit` ist bis zum
      manuellen `git init` ein stiller No-op — Loeschungen waeren dort nicht
      recoverable, obwohl die API es verspricht. Idempotent in `store.py`, aus
      `git_commit` heraus aufrufen.
- [ ] MCP-Tool-Aufrufe ohne Client-IP: `ip` aus dem FastMCP-Request-Context ziehen,
      damit `source=mcp`-Zeilen nicht `ip=NULL` bleiben.
- [ ] Off-box-Backup des Notes-Repos (`vol_data/notes`) nach Gogs — DR, damit ein
      Volume-Verlust nicht die ganze Git-Historie mitnimmt.
- [ ] Keine Testsuite. Mindestens Smoke-Tests: `/healthz`, `/api/tools`, und ein
      Roundtrip search -> get -> upsert(id) -> delete gegen einen Wegwerf-Scope.
- [ ] Wiki-Seite: `/api/reindex`-Beispiel nutzt noch einen Bearer, obwohl `/api/*`
      tokenfrei ist — entfernen (kosmetisch).
- [ ] Skalierung: erst wenn der Bestand Groessenordnung 10^4+ erreicht, ANN/
      `sqlite-vec` gegen den aktuellen numpy-Full-Scan evaluieren. Bis dahin nichts tun.
