# project-claude-server

The operator prompt that turns Claude into the sole administrator of a
single-host Docker homelab.

## What it does

Given SSH access to one Linux host (via the `ssh-mcp-server` MCP running on the
operator's Mac) and Claude Code installed on that host, this prompt puts Claude
in charge of the whole box: it provisions every service as a Docker container,
publishes it through an existing **Traefik** reverse proxy, registers new
subdomains in **Unbound** DNS itself, and keeps an architecture `README.md`
under `/daten/docker/` current as it learns. It also directs the on-host Claude
Code instance and creates the control files (`compose.yml`, `CLAUDE.md`, …) new
projects need.

It is deliberately short. The heavy lifting — conventions, gotchas, past
decisions — lives outside the prompt in a memory service and per-service
`CLAUDE.md` files (see `app-memory-service` and the CLAUDE.md examples), so the
operator prompt only has to grant authority and point at the entry points.

## Build your own

1. Stand up a Docker host with Traefik and a local DNS resolver (Unbound or
   equivalent). Install Claude Code on it.
2. Connect an SSH MCP server so Claude can reach the host from your desktop.
3. Use `Prompt.md` as the session/system prompt. Replace `<docker-host>` with
   your host, and adapt the base path (`/daten/docker/`) and the reverse-proxy /
   DNS references to your setup.
4. Let Claude maintain its own architecture `README.md` and per-service context
   files from there.

`Prompt.md` is sanitized: the real hostname is replaced by `<docker-host>`.
