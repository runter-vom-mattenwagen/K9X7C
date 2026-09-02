"""MCP server (streamable HTTP) + REST/UI for the homelab memory."""
import os, re, json, datetime, pathlib
from typing import List
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse, HTMLResponse, PlainTextResponse

import store, index, access

TOKEN = os.environ.get("MEMORY_TOKEN", "")
# Hard block only for near-verbatim copies. Measured over 1128 real pairs:
# p99 = 0.68, and the highest LEGITIMATE pair is 0.81 (svc-json-store vs
# svc-json-store-api, split on purpose). A paraphrase of one fact scored 0.79 --
# inside that range. Cosine alone cannot separate "same fact" from "related on
# purpose", so anything below the block is reported as advice, not refused.
DUP_THRESHOLD = 0.88
NEAR_THRESHOLD = 0.72

mcp = FastMCP("homelab-memory", stateless_http=True,
             transport_security=TransportSecuritySettings(
                 allowed_hosts=["memory.claude.int", "memory.claude.int:*"],
                 allowed_origins=["https://memory.claude.int"]))
_db = None


def db():
    global _db
    if _db is None:
        _db = index.connect()
        index.init(_db)
        access.init(_db)
    return _db


def _with_reads(hits):
    """Annotate hits with how often each note was actually fetched in full."""
    counts = access.read_counts(db())
    for h in hits:
        h["reads"] = counts.get(h["id"], 0)
    return hits


def _slim(h):
    return {k: h[k] for k in ("id", "title", "summary", "type", "scope", "updated")}


@mcp.tool()
def memory_search(query: str, scope: str = "", type: str = "", k: int = 8) -> str:
    """Search the homelab memory. Returns compact hits only (id, title, one-line
    summary). Read the summaries, then call memory_get for the few you actually
    need. scope filters e.g. 'homelab'/'strato'; type filters
    service|gotcha|rule|runbook|location|credential|fact|project."""
    res = index.search(db(), query, scope or None, type or None, k)
    access.log(db(), "search", "mcp", query=query, hits=len(res),
               top_id=res[0]["id"] if res else None)
    return json.dumps([_slim(h) for h in res], ensure_ascii=False)


@mcp.tool()
def memory_get(ids: List[str]) -> str:
    """Fetch the full text of notes by id. Pass only the ids that matter."""
    out = []
    for i in ids:
        p = store.find(i)
        if not p:
            out.append({"id": i, "error": "not found"})
            continue
        meta, body = store.load(p)
        access.log(db(), "get", "mcp", note_id=i)
        out.append({"id": i, "meta": meta, "body": body})
    return json.dumps(out, ensure_ascii=False)



SECRET_HINTS = [
    ("pem private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("known api key prefix", re.compile(
        r"(sk-ant-|ghp_|gho_|github_pat_|xox[baprs]-|AKIA[0-9A-Z]{16})")),
    # No \b before the keyword: ADMIN_TOKEN has no word boundary before "TOKEN".
    ("assigned secret value", re.compile(
        r"(?i)(token|secret|password|passwd|api[_-]?key|private[_-]?key|credential)"
        r"[\"' ]*[:=][ \"']*"
        r"(?!<|\{|\$|xxx|changeme|redacted|dein|your|placeholder|\.\.\.|env|none|null)"
        r"[A-Za-z0-9+/=_\-]{16,}")),
]


def secret_scan(*parts):
    """Return names of matched patterns. Never echoes the matched value."""
    text = "\n".join(x for x in parts if x)
    return [name for name, rx in SECRET_HINTS if rx.search(text)]


def _upsert(title, summary, body, type="fact", scope="homelab", tags=None,
            id="", supersedes="", force=False, _write_source="mcp", ip=None):
    """Single write path -- shared by the MCP tool and POST /api/note."""
    if scope not in store.SCOPES:
        return {"status": "unknown_scope", "given": scope,
                "allowed": list(store.SCOPES),
                "hint": "use a tag unless you would routinely search one "
                        "scope without the other. If a new scope is really "
                        "wanted: add it to MEMORY_SCOPES in "
                        "/daten/docker/memory/compose.yml, then "
                        "docker compose up -d. No rebuild needed."}
    hits = secret_scan(title, summary, body)
    if hits:
        return {"status": "rejected_secret", "matched": hits,
                "hint": "store where the secret lives, never its value"}
    nid = id or store.slugify(title)
    if not id and not force:
        near = [h for h in index.search(db(), title + " " + summary, k=3)
                if (h["cosine"] or 0) > DUP_THRESHOLD]
        if near:
            return {"status": "possible_duplicate", "candidates": near,
                    "hint": "pass id=<existing> to update, or force=true"}
    # An update is a full replace of the file. Frontmatter fields this API does
    # not know about (status, owner, ...) used to vanish silently -- carry them
    # over from the existing note instead.
    carried = {}
    existing = store.find(nid)
    if existing:
        try:
            old_meta, _ = store.load(existing)
            known = {"id", "title", "summary", "type", "scope", "tags",
                     "updated", "supersedes"}
            carried = {k: v for k, v in old_meta.items() if k not in known}
        except Exception:
            carried = {}
    meta = dict(carried)
    meta.update({"id": nid, "title": title, "summary": summary, "type": type,
                 "scope": scope, "tags": tags or []})
    if supersedes:
        meta["supersedes"] = supersedes
        store.delete(supersedes)
    near = [{"id": h["id"], "title": h["title"], "cosine": round(h["cosine"], 3)}
            for h in index.search(db(), title + " " + summary, k=4)
            if (h["cosine"] or 0) > NEAR_THRESHOLD and h["id"] != nid]
    path = store.save(meta, body)
    m2, b2 = store.load(path)
    index.index_note(db(), path, m2, b2, force=True)
    store.git_commit("upsert %s" % nid)
    access.log(db(), "write", _write_source, note_id=nid, ip=ip)
    res = {"status": "ok", "id": nid, "path": str(path)}
    if near:
        res["near"] = near
        res["hint"] = "close neighbours exist -- check whether one should be updated instead"
    return res


@mcp.tool()
def memory_upsert(title: str, summary: str, body: str, type: str = "fact",
                  scope: str = "homelab", tags: List[str] = None,
                  id: str = "", supersedes: str = "") -> str:
    """Store one atomic fact. Keep it to a single topic under ~800 tokens --
    broad notes average out in embedding space and stop ranking. summary must be
    a standalone one-liner under 120 chars; it is all that shows in search hits.
    Warns instead of writing if a near-duplicate exists (use its id to update)."""
    return json.dumps(_upsert(title, summary, body, type, scope, tags,
                             id, supersedes), ensure_ascii=False)


@mcp.tool()
def memory_bootstrap(scope: str = "homelab") -> str:
    """Orientation at session start: high-priority notes for a scope, titles and
    summaries only. Cheap. Follow up with memory_search for anything specific."""
    rows = db().execute(
        "SELECT id,title,summary,type FROM notes WHERE scope=? ORDER BY type,id",
        (scope,)).fetchall()
    return json.dumps({"scope": scope, "count": len(rows),
                       "notes": [dict(r) for r in rows]}, ensure_ascii=False)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request):
    n = db().execute("SELECT count(*) c FROM notes").fetchone()["c"]
    return JSONResponse({"ok": True, "notes": n, "model": index.MODEL_NAME})


@mcp.custom_route("/", methods=["GET"])
async def ui(request):
    return HTMLResponse(pathlib.Path("/app/ui.html").read_text(encoding="utf-8"))


@mcp.custom_route("/api/search", methods=["GET"])
async def api_search(request):
    q = request.query_params.get("q", "")
    if not q:
        return JSONResponse([])
    res = index.search(db(), q, request.query_params.get("scope") or None,
                       request.query_params.get("type") or None,
                       int(request.query_params.get("k", 10)))
    src = access.source_of(request)
    if src != "lens":
        access.log(db(), "search", src, query=q,
                   hits=len(res), top_id=res[0]["id"] if res else None,
                   ip=access.client_ip(request))
    return JSONResponse(_with_reads(res))


@mcp.custom_route("/api/search", methods=["POST"])
async def api_search_post(request):
    """Same as GET, but the query travels in a JSON body.

    Percent-encoding a German sentence into a query string is the caller's
    problem for no good reason -- raw spaces and umlauts make curl abort with
    exit code 3. JSON carries them untouched.
    """
    try:
        b = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)
    q = (b.get("q") or "").strip()
    if not q:
        return JSONResponse({"error": "missing field", "fields": ["q"]},
                            status_code=400)
    try:
        k = int(b.get("k", 10))
    except (TypeError, ValueError):
        k = 10
    res = index.search(db(), q, b.get("scope") or None, b.get("type") or None, k)
    src = access.source_of(request)
    if src != "lens":
        access.log(db(), "search", src, query=q,
                   hits=len(res), top_id=res[0]["id"] if res else None,
                   ip=access.client_ip(request))
    return JSONResponse(_with_reads(res))


@mcp.custom_route("/api/note", methods=["GET"])
async def api_note(request):
    nid = request.query_params.get("id", "")
    p = store.find(nid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    meta, body = store.load(p)
    access.log(db(), "get", access.source_of(request), note_id=nid,
               ip=access.client_ip(request))
    return JSONResponse({"meta": meta, "body": body, "path": str(p)})


@mcp.custom_route("/api/note", methods=["POST"])
async def api_note_write(request):
    """Write path for clients that cannot speak MCP (e.g. Claude Desktop via curl)."""
    try:
        p = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)
    missing = [f for f in ("title", "summary", "body") if not p.get(f)]
    if missing:
        return JSONResponse({"error": "missing fields", "fields": missing}, status_code=400)
    if len(p["summary"]) > 120:
        return JSONResponse({"error": "summary must be <= 120 chars",
                             "length": len(p["summary"])}, status_code=400)
    res = _upsert(p["title"], p["summary"], p["body"],
                  p.get("type", "fact"), p.get("scope", "homelab"),
                  p.get("tags"), p.get("id", ""), p.get("supersedes", ""),
                  bool(p.get("force")), access.source_of(request),
                  ip=access.client_ip(request))
    code = {"ok": 200, "possible_duplicate": 409,
            "rejected_secret": 422, "unknown_scope": 400}.get(res["status"], 400)
    return JSONResponse(res, status_code=code)


def _delete(nid, source="mcp", ip=None):
    """Remove a note. Recoverable: store.git_commit runs `git add -A`, so the
    deletion is committed to the notes repo and can be restored from history.
    Index cleanup goes through a full reindex -- 70ms on this corpus, and it
    keeps notes/FTS/vectors consistent without a second removal path."""
    p = store.find(nid)
    if not p:
        return {"status": "not_found", "id": nid}
    path = str(p)
    store.delete(nid)
    store.git_commit("delete %s" % nid)
    index.reindex(db())
    access.log(db(), "delete", source, note_id=nid, ip=ip)
    return {"status": "deleted", "id": nid, "path": path,
            "hint": "recoverable from git history in vol_data/notes"}


@mcp.tool()
def memory_delete(id: str) -> str:
    """Delete a note permanently from the memory. The file is removed but the
    deletion is committed to git, so it can be restored. Use this only when a
    note is wrong or obsolete -- to replace content, use memory_upsert with the
    same id instead."""
    return json.dumps(_delete(id, "mcp"), ensure_ascii=False)


@mcp.custom_route("/api/note", methods=["DELETE"])
async def api_note_delete(request):
    nid = request.query_params.get("id", "").strip()
    if not nid:
        return JSONResponse({"error": "missing field", "fields": ["id"]},
                            status_code=400)
    res = _delete(nid, access.source_of(request), ip=access.client_ip(request))
    return JSONResponse(res, status_code=200 if res["status"] == "deleted" else 404)


@mcp.custom_route("/api/reindex", methods=["POST"])
async def api_reindex(request):
    access.log(db(), "reindex", access.source_of(request),
               ip=access.client_ip(request))
    return JSONResponse(index.reindex(db(), force=False))


@mcp.custom_route("/api/access", methods=["GET"])
async def api_access(request):
    """Recent reads and writes. Lens requests are tagged separately so that
    browsing the memory yourself does not look like Claude using it."""
    return JSONResponse({
        "summary": access.summary(db()),
        "recent": access.recent(db(), int(request.query_params.get("limit", 60))),
    })


@mcp.custom_route("/api/stats", methods=["GET"])
async def api_stats(request):
    rows = db().execute("SELECT type, count(*) c FROM notes GROUP BY type").fetchall()
    srows = db().execute("SELECT scope, count(*) c FROM notes "
                         "GROUP BY scope ORDER BY c DESC").fetchall()
    return JSONResponse({"by_type": {r["type"]: r["c"] for r in rows},
                         "by_scope": {r["scope"]: r["c"] for r in srows},
                         "scopes_allowed": list(store.SCOPES),
                         "total": sum(r["c"] for r in rows),
                         "model": index.MODEL_NAME})


@mcp.custom_route("/api/tools", methods=["GET"])
async def api_tools(request):
    """Discovery for REST/curl clients: the same tool list the MCP endpoint
    exposes via tools/list. Without it /api/* cannot advertise what exists --
    which is how the delete/upsert paths got 'rediscovered' as missing.
    Derived from mcp.list_tools() so it never drifts from the registered set."""
    tools = await mcp.list_tools()
    return JSONResponse({"tools": [t.model_dump(by_alias=True, exclude_none=True)
                                   for t in tools]})


class TokenAuth:
    """Bearer on /mcp only.

    /api/* is deliberately unauthenticated -- read AND write -- because
    claude.int is a single-user LAN. This is a conscious trade-off, not an
    oversight: anyone who reaches this host can read the credential pointers
    and write arbitrary notes. Revisit when the network stops being
    single-user, or when this service is exposed beyond the LAN."""
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if self.token and scope.get("type") == "http" and path.startswith("/mcp"):
            hdrs = {k.lower(): v for k, v in scope.get("headers", [])}
            if hdrs.get(b"authorization", b"").decode() != "Bearer %s" % self.token:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


app = TokenAuth(mcp.streamable_http_app(), TOKEN)
