# ACT — AI-driven Change Tracking

Lightweight, self-hosted ticket system for web applications with AI-powered resolution workflow.

## What it does

- **Embeddable widget** — A Web Component that drops into any web app (2 lines of HTML). Collects bug reports and feature requests with automatic context capture (URL, browser, console errors, app version).
- **Ticket management** — REST API + Web UI for triaging, reviewing, and tracking tickets through a state machine lifecycle.
- **AI-powered proposals** — Claude Code analyzes tickets, proposes solutions on Git branches, and implements approved changes. Human stays in the loop as supervisor.
- **Notifications** — ntfy integration for real-time alerts on new tickets, proposals, and status changes.

## Architecture

```
┌─────────────┐    REST/Bearer    ┌─────────────┐    ntfy     ┌──────────┐
│  Your App   │ ──────────────▶   │  ACT Server │  ─────────▶ │  Admin   │
│  + Widget   │                   │  (FastAPI)  │             │  (Phone) │
└─────────────┘                   └──────┬──────┘             └──────────┘
                                         │
                                    SQLite DB
                                         │
                                  ┌──────┴───────┐
                                  │  Claude Code │
                                  │  (analysis + │
                                  │  proposals)  │
                                  └──────────────┘
```

## Ticket Lifecycle

```
new → proposed → approved → implementing → testing → done
                    ↑          │              │           │
                    └──────────┴──────────────┴───────────┘ (reopen)
                               ↓
                         rejected
```

## Quick Start

### 1. Deploy

```bash
git clone https://github.com/youruser/act.git
cd act
cp .env.example .env  # Edit admin token
docker compose up -d
```

### 2. Register an App

Open the web UI at `https://your-act-domain/` and click "+ App". You'll get an API token.

### 3. Embed the Widget

```html
<script src="https://your-act-domain/widget/act-widget.js"></script>
<act-widget
  endpoint="https://your-act-domain"
  token="act_your_token_here"
></act-widget>
```

Optional attributes:
- `position="bottom-left"` (default: `bottom-right`)
- `app-version="1.2.3"` (or set `<meta name="app-version" content="1.2.3">`)

### 4. Configure Claude Code Integration

Set the `codebase_path` when registering your app — this tells Claude where the source code lives. Claude will:

1. Pick up triaged tickets (manual trigger or cron)
2. Analyze the ticket + codebase using `claude --print` (Claude Code CLI)
3. Create a proposal with summary and diff preview
4. Wait for human approval via Web UI
5. Implement on branch `act/{ticket-id}`
6. Move ticket to `testing`, notify via ntfy

No API key needed — uses the existing Claude Code OAuth session.

## Automation (cron)

The AI loop is driven by `act-claude.py`, which runs **on the host** (not inside
the container) and calls the Claude Code CLI in print mode, sharing the host's
authenticated Claude Code session — no API key.

```bash
./act-claude.py status           # show actionable tickets
./act-claude.py analyze          # analyze new tickets → proposals
./act-claude.py implement <id>   # implement an approved ticket on a branch
./act-claude.py auto             # full cycle: analyze + implement approved
```

`act-cron.sh` wraps the `auto` cycle for scheduling. Run it every 30 minutes:

```cron
*/30 * * * * /path/to/act/act-cron.sh >> /var/log/act-claude.log 2>&1
```

Details that matter when rebuilding on your own host:

- **Model is pinned** via `CLAUDE_MODEL` (default `opus`), deliberately *not*
  inherited from `~/.claude/settings.json` — otherwise a change to the host
  default could silently move this job's model.
- **Non-interactive invocation:** `claude --print --model <model> --allowedTools ""`,
  launched from a neutral `cwd` (`/tmp`). The empty allow-list plus neutral cwd
  keep it out of agent mode, so a cron run stays deterministic and never blocks
  waiting on a tool.
- **Host paths:** because the bridge runs on the host, `ACT_DB_PATH` must point at
  the host-side bind-mount of the SQLite DB (`.../vol_data/act.db`), not the
  container path from `compose.yml`. Set `CLAUDE_BIN` if `claude` isn't on `$PATH`.
- **Auth:** the host's Claude Code OAuth session (`~/.claude/.credentials.json`).

Expect mostly idle cycles — the loop only does real work when triaged tickets
exist; most runs exit in a second or two.

## API Reference

### Widget Endpoint

```
POST /api/tickets
Authorization: Bearer <app-token>
Content-Type: application/json

{
  "type": "bug|feature",
  "title": "Brief summary",
  "description": "Details",
  "priority": "low|normal|high|critical",
  "context": {
    "url": "https://...",
    "browser": "...",
    "app_version": "...",
    "errors": "..."
  }
}
```

### Management Endpoints

```
GET    /api/tickets                    — List tickets (filter: ?app_id=&status=)
GET    /api/tickets/{id}               — Get ticket
GET    /api/tickets/{id}/history        — Audit trail
POST   /api/tickets/{id}/transition     — Change status
POST   /api/tickets/{id}/proposals      — Create proposal (Claude)
GET    /api/tickets/{id}/proposals      — List proposals
POST   /api/proposals/{id}/review       — Approve/reject proposal
GET    /api/apps                        — List registered apps
GET    /api/stats                       — Dashboard stats
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ACT_DB_PATH` | `/data/act.db` | SQLite database path |
| `ACT_ADMIN_TOKEN` | `act-admin-secret` | Web UI login token |
| `ACT_NTFY_URL` | `https://ntfy.sh` | ntfy server URL |
| `ACT_NTFY_TOPIC` | `act` | ntfy topic for notifications |
| `ACT_BASE_URL` | `https://act.claude.int` | Public URL (for notification links) |

The `act-claude.py` bridge requires Claude Code CLI (`claude --print`) authenticated on the host. No separate API key needed.

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLite (WAL mode)
- **Frontend:** Jinja2 templates, vanilla JS
- **Widget:** Vanilla JS Web Component (Shadow DOM, zero dependencies)
- **Container:** Docker + Traefik reverse proxy
- **Notifications:** ntfy

## Self-Evolution

ACT tracks its own development — the ACT system is registered as an app in itself. Report issues against ACT using the widget or API, and Claude will propose fixes to the ACT codebase.

## License

MIT
