# Memory Service

A cross-conversation memory for AI coding assistants: hybrid search (BM25 +
embeddings) over Markdown notes, reachable via **MCP** and a plain **REST API**,
with a small web UI ("Lens") for browsing, searching, and inspecting access logs.

## What it does

The service is a durable, searchable store of *operative* knowledge — gotchas,
decisions, file locations, runbooks — that an assistant looks up before it acts
and writes back when it learns something lasting. Each fact is one Markdown file
with YAML frontmatter; the notes directory is the git-backed source of truth, and
the SQLite index is derived and rebuildable.

Retrieval is hybrid: lexical **BM25** (SQLite FTS5) and **cosine similarity** over
768-dim embeddings, fused with Reciprocal Rank Fusion (`RRF_K=12`) and cut off by
a relevance floor so weak matches don't leak in. Embeddings come from
`jinaai/jina-embeddings-v2-base-de` (DE/EN) via `fastembed`, normalised to unit
length so a dot product is the cosine.

## Data model

```
vol_data/notes/<scope>/<id>.md      # the notes — YAML frontmatter + Markdown body
vol_data/notes/.git                  # every write/delete is committed → recoverable
vol_data/index.db                    # derived index (notes / notes_fts / notes_vec)
```

Frontmatter carries `id, title, summary, type, tags, scope`. `type` is one of
`service gotcha rule runbook location credential fact project`; one fact per note.
`scope` partitions the corpus (e.g. per environment). The index is disposable —
rebuild it any time with `POST /api/reindex`.

> Notes are **data**, not part of this repo. `vol_data/` is git-ignored here;
> bring your own notes when you deploy.

## Interfaces

- **MCP** (`/mcp`, Bearer-authenticated) — tools `memory_search`, `memory_get`,
  `memory_upsert`, `memory_bootstrap`, `memory_delete`. Served over Streamable
  HTTP via FastMCP.
- **REST** (`/api/*`) — `search` (GET/POST), `note` (GET/POST/DELETE), `reindex`,
  `tools`, `access`, `stats`, plus `/healthz` and the Lens UI at `/`.
  `GET /api/tools` lists the MCP tools from `mcp.list_tools()`, so it never drifts
  from the actual tool set.

Only `/mcp` is token-protected. `/api/*` is left open on the assumption of a
trusted, single-user LAN — see Security below.

## Run it

```bash
cp .env.example .env          # set MEMORY_TOKEN (or leave empty to disable MCP auth)
docker compose up -d --build
curl -sf http://localhost:8000/healthz
```

The app source in `app/` is baked into the image (no bind-mount), so code changes
need a rebuild (`docker compose up -d --build`). After deploy, verify the real
body, not just the status code, e.g. `curl -s .../api/tools | jq '.tools[].name'`.

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEMORY_TOKEN` | *(empty)* | Bearer token for `/mcp`; empty disables MCP auth |
| `NOTES_DIR` | `/data/notes` | Notes root (git-backed source of truth) |
| `DB_PATH` | `/data/index.db` | Derived SQLite index |
| `EMBED_MODEL` | `jinaai/jina-embeddings-v2-base-de` | Embedding model (fingerprint) |
| `MEMORY_SCOPES` | `homelab,azure,travel` | Allowed note scopes |

Changing `EMBED_MODEL` invalidates all three tables (vectors from different models
are incompatible) and triggers a full re-embed on next start. Adding a scope needs
no rebuild — just update `MEMORY_SCOPES` and `docker compose up -d`.

## Security

`/api/*` is unauthenticated by design — appropriate only on an isolated LAN. On any
network you don't fully trust, put the whole service behind a reverse proxy with
auth (e.g. forward-auth), and treat the Bearer token on `/mcp` as the minimum, not
the boundary. Never store secret *values* in notes — only where to find them.

## Stack

Python 3.12, FastAPI via `mcp.server.fastmcp`, SQLite (FTS5 + a numpy full-scan over
embedding BLOBs — deliberately not a vector DB at this corpus size), `fastembed`,
Jinja-free vanilla-JS UI, Docker behind Traefik.

## License

MIT
