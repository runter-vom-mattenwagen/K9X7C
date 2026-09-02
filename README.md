# claude-homelab

Infrastructure and tooling built with **Claude** and **Claude Code**, presented
and published here so you can rebuild any of it on your own hosts and
with your own data.

Nothing here is a product. It's a working homelab: an AI that administers a Docker
host over SSH, a memory service that gives that AI durable context, a self-evolving
ticket system, the MCP servers that wire it all to Claude Desktop, and the prompts
that drive the whole way of working.

## What's inside

**Runnable services** (FastAPI + Docker, behind Traefik):

- **[app-act/](app-act/)** — a self-hosted ticket system whose tickets are triaged
  and *implemented* by Claude Code on Git branches. Embeddable widget, ntfy alerts,
  a cron-driven AI loop; the human stays the supervisor.
- **[app-memory-service/](app-memory-service/)** — cross-conversation memory:
  hybrid BM25 + embedding search over Markdown notes, reachable via MCP and REST,
  with a small web UI ("Lens").

**MCP servers** (stdio, for Claude Desktop):

- **[mcp-ssh/](mcp-ssh/)** — SSH command execution and SFTP for remote hosts
  (Paramiko), with a per-host connection pool.
- **[mcp-curl/](mcp-curl/)** — a full HTTP client (methods, headers, auth) — the
  requests Claude's built-in fetch can't make.

**Prompt-driven projects** (the prompt *is* the artifact):

- **[project-claude-server/](project-claude-server/)** — the operator prompt that
  makes Claude the admin of a single Docker host: containers, Traefik, DNS, and its
  own architecture docs.
- **[project-azure-training/](project-azure-training/)** — a Claude Code "learning
  guide" for Azure + OpenTofu + Ansible with hard free-tier cost discipline; you run
  every command yourself.
- **[project-obsidian-urlaub/](project-obsidian-urlaub/)** — a Claude Desktop prompt
  that plans trips directly into an Obsidian vault (Tasks syntax, folder-per-trip,
  booking tables).

Each directory has its own README with what it does and how to stand it up.

## Build your own

Every service ships a `compose.yml`, a `Dockerfile`, and a `.env.example` — copy the
latter to `.env`, set your own secrets, then `docker compose up -d --build`. The
prompt projects are single files: drop the `Prompt.md` into your own Claude / Claude
Code setup and replace the placeholders.

## What's *not* here (on purpose)

No secrets and no operational data. `.env` files, tokens, TLS keys, Terraform state,
and the actual memory notes / trip data are excluded and git-ignored — the point is
the *how*, and you bring your own *what*. Internal hostnames and IPs are replaced by
placeholders (`<docker-host>`, RFC-5737 addresses); the internal DNS zone
`claude.int` is kept as-is, since the whole exercise is about running your own.

## A note on "Claude"

Claude and Claude Code are products of Anthropic. This repository is a personal
collection built *with* those tools; it is not affiliated with or endorsed by
Anthropic.

## License

MIT — see [LICENSE](LICENSE).
