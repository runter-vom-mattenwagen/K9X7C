# Memory-Service — Claude Code Context

Du arbeitest am **Code** des Memory-Service selbst, nicht an den Notizen. Der
Dienst ist ein chat-uebergreifendes Gedaechtnis: hybride Suche (BM25 + Embeddings)
ueber Markdown-Notizen, per MCP und REST erreichbar, mit Lens-UI. Laeuft als
Container `memory` hinter Traefik auf <docker-host>, `https://memory.claude.int`.

> Abgrenzung: Notiz-*Inhalte* sind Nutzdaten im Volume und werden NICHT hier
> geaendert. Diese Datei gilt fuer Aenderungen am Servicecode.

Globaler Kontext (Memory-Nutzung, Architektur-, DNS-Regeln): `/daten/docker/CLAUDE.md`.

## Stack
- Python + FastAPI via `mcp.server.fastmcp` (mcp 1.28.1), Starlette custom_routes
- SQLite `vol_data/index.db`: `notes` (Metadaten + Filter), `notes_fts` (FTS5/BM25),
  `notes_vec` (768-dim Embedding als BLOB, id = Primaerschluessel)
- Embeddings: fastembed, Modell `jinaai/jina-embeddings-v2-base-de`
  (768-dim, DE/EN, auf Laenge 1 normiert -> Skalarprodukt = Cosinus)
- Fusion: RRF (`RRF_K = 12`) + Relevanzschwelle; ASGI-App = `server:app`
- Frontend: `ui.html` (Lens, Vanilla JS)

## Verzeichnisstruktur
- `app/server.py` — FastMCP-Instanz `mcp`; MCP-Tools (`@mcp.tool`), REST-Routen
  (`@mcp.custom_route`), `TokenAuth`-Wrapper, `app`
- `app/index.py` — Indexierung (`index_note`/`reindex`), Suche (BM25 + Cosine + RRF
  + Floor), `embed`/`embed_text`, Vektormatrix-Cache (`_matrix`/`_invalidate`)
- `app/store.py` — Markdown+YAML-I/O, `slugify`, `path_for` (scope -> Verzeichnis),
  Notes-Git (`git_commit`)
- `app/access.py` — Zugriffslog (`source` lens|mcp|api, `ip` via X-Forwarded-For)
- `ui.html` — Lens (Suche, "zugriffe", Info-Panel)
- `vol_data/notes/<scope>/<id>.md` — die Notizen (Nutzdaten, NICHT Code)
- `vol_data/notes/.git` — Versionierung der Notizen (Loeschen ist recoverable)
- `vol_data/index.db` — abgeleiteter Index, verwerfbar, via `/api/reindex` neu baubar
- `compose.yml`, `Dockerfile`, `.env` (`MEMORY_TOKEN`, `EMBED_MODEL`)

## Endpunkte
- MCP (`/mcp`, Bearer): `memory_search` `memory_get` `memory_upsert`
  `memory_bootstrap` `memory_delete`
- REST (`/api/*`, LAN ohne Token): `search` (GET/POST), `note` (GET/POST/DELETE),
  `reindex`, `tools`, `access`, `stats`; dazu `/healthz`, `/`
- `GET /api/tools` listet die MCP-Tools (aus `mcp.list_tools()`, driftfrei).
- `TokenAuth` schuetzt nur `/mcp`; `/api/*` ist im LAN bewusst offen.

## Build / Deploy / Verify — immer so
Der Code in `app/` wird ins Image kopiert (kein Bind-Mount), Aenderungen wirken
erst nach Rebuild.

```
cd /daten/docker/memory
docker compose up -d --build
for i in $(seq 1 15); do \
  [ "$(curl -sk -o /dev/null -w '%{http_code}' https://memory.claude.int/healthz)" = 200 ] && break; \
  sleep 1; done
```

Danach IMMER inhaltlich verifizieren — Statuscode allein reicht nicht, grep den
echten Body, z. B. `curl -sk https://memory.claude.int/api/tools | jq '.tools[].name'`.
Erst wenn verifiziert: committen (Conventional Commit, Root-Cause im Text).

## Code-Konventionen
- Vor dem Schreiben lesen; nichts ueberschreiben, was du nicht gelesen hast.
- Edits per Patch-Skript: `assert c.count(old) == 1` vor jedem Replace, `ast.parse()`
  danach, Timestamped Backup (`shutil.copy2`). Keine blinden `sed`-Ersetzungen.
- Deutsche UI-Labels und Doku, englischer Code; Kommentare knapp, nur wo noetig.
- Pruefen statt behaupten: was per Befehl belegbar ist, ausfuehren und zeigen.

## Service-Gotchas
- Notizen NIE direkt in `vol_data/notes/` editieren — desynchronisiert den Index.
  Immer ueber `/api/note`, oder danach `POST /api/reindex`.
- `notes_vec` ist KEINE Vektor-DB: SQLite haelt nur die BLOBs, den Cosinus rechnet
  numpy (`mat @ q`, Full-Scan, Cache in `index.py`, per `_invalidate()` verworfen).
  `sqlite-vec` bewusst weggelassen — bei dieser Korpusgroesse unnoetig.
- `embed_text` gewichtet den Titel doppelt (`title + summary + title + body`,
  <= 4000 Zeichen). Aenderst du das, muessen alle Embeddings neu: `reindex(force)`.
- Modellname ist Fingerprint: aendert sich `EMBED_MODEL`, verwirft `init()` alle drei
  Tabellen (Vektoren verschiedener Modelle sind inkompatibel) -> Full-Re-Embed.
- Notes-Repo liegt in `vol_data/notes/.git`; `git_commit` laeuft nach jedem
  Write/Delete -> Loeschen ist aus der Historie wiederherstellbar. ACHTUNG: ein
  FRISCHES Volume hat kein `.git`, dann ist `git_commit` ein stiller No-op, bis
  einmal `git init` lief (siehe BACKLOG: `ensure_repo`).
- `reindex` hasht Frontmatter+Body und ueberspringt Unveraendertes -> billig;
  meldet kaputtes YAML als `broken`, Dateien im falschen Scope-Ordner als `misfiled`.
- MCP-Tool-Aufrufe loggen `ip = NULL` (FastMCP reicht das Request-Objekt nicht in
  die Tools); REST und Lens loggen die Client-IP via `X-Forwarded-For`.

## Doku-Pflicht bei Aenderungen
Neues dauerhaftes Wissen (Gotcha, Entscheidung, Ablageort) gehoert in eine
Memory-Notiz (`scope homelab`). Betrifft es die Nutzung, zusaetzlich die kanonische
Wiki-Seite `https://wiki.claude.int/doku/services/memory` (Commit im Otterwiki-Repo
als `www-data`!) und `/daten/docker/README.md`. Keine Duplikate — kanonisch ist die
Wiki-Seite.
