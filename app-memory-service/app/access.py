"""Access log for the memory service.

Records every read and write so it becomes visible who asks what, and -- more
useful over time -- which notes are never retrieved at all. Notes nobody ever
hits are the honest candidates for the housekeeping pass
(see backlog-housekeeping-cron).

Lives in the same SQLite file as the index on purpose: derived, disposable
data. Losing it costs nothing.
"""

import datetime

KEEP_ROWS = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS access (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL,
  action  TEXT NOT NULL,   -- search | get | write | reindex
  source  TEXT NOT NULL,   -- lens | mcp | api
  ip      TEXT,
  note_id TEXT,
  query   TEXT,
  hits    INTEGER,
  top_id  TEXT
);
CREATE INDEX IF NOT EXISTS access_ts ON access(id DESC);
CREATE INDEX IF NOT EXISTS access_note ON access(note_id);
"""


def init(db):
    db.executescript(SCHEMA)
    try:                       # additive migration for pre-existing logs
        db.execute("ALTER TABLE access ADD COLUMN ip TEXT")
    except Exception:
        pass
    db.commit()


def source_of(request):
    """Classify the caller. The lens sends X-Lens on every fetch -- without it
    your own browsing would drown out what Claude does, which is the point."""
    try:
        if request.headers.get("x-lens"):
            return "lens"
        if request.url.path.startswith("/mcp"):
            return "mcp"
    except Exception:
        pass
    return "api"


def client_ip(request):
    """Real client behind Traefik: X-Forwarded-For leftmost is the origin;
    request.client would only ever be the proxy."""
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        xr = request.headers.get("x-real-ip")
        if xr:
            return xr.strip()
        return request.client.host if request.client else None
    except Exception:
        return None


def log(db, action, source, note_id=None, query=None, hits=None, top_id=None,
        ip=None):
    try:
        db.execute(
            "INSERT INTO access (ts, action, source, note_id, query, hits, top_id, ip)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (datetime.datetime.now().isoformat(timespec="seconds"),
             action, source, note_id, query, hits, top_id, ip))
        db.commit()
        n = db.execute("SELECT count(*) c FROM access").fetchone()["c"]
        if n > KEEP_ROWS * 1.2:
            db.execute("DELETE FROM access WHERE id NOT IN "
                       "(SELECT id FROM access ORDER BY id DESC LIMIT ?)", (KEEP_ROWS,))
            db.commit()
    except Exception as e:
        print("access log failed: %s" % e, flush=True)   # never break a request


def recent(db, limit=60):
    return [dict(r) for r in db.execute(
        "SELECT ts, action, source, note_id, query, hits, top_id, ip FROM access"
        " ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]


def read_counts(db, exclude_lens=True):
    """Full-text fetches per note. Lens reads excluded: browsing your own
    memory should not make a note look popular."""
    q = ("SELECT note_id, count(*) c FROM access"
         " WHERE action IN ('get','write') AND note_id IS NOT NULL")
    if exclude_lens:
        q += " AND source != 'lens'"
    q += " GROUP BY note_id"
    return {r["note_id"]: r["c"] for r in db.execute(q).fetchall()}


def summary(db):
    out = {}
    for r in db.execute("SELECT source, action, count(*) c FROM access"
                        " GROUP BY source, action").fetchall():
        out.setdefault(r["source"], {})[r["action"]] = r["c"]
    total = db.execute("SELECT count(*) c FROM access").fetchone()["c"]
    since = db.execute("SELECT min(ts) t FROM access").fetchone()["t"]
    misses = db.execute("SELECT count(*) c FROM access WHERE action='search'"
                        " AND (hits IS NULL OR hits=0)").fetchone()["c"]
    ips = {r["ip"]: r["c"] for r in db.execute(
        "SELECT ip, count(*) c FROM access WHERE ip IS NOT NULL"
        " GROUP BY ip ORDER BY c DESC LIMIT 10").fetchall()}
    return {"total": total, "since": since, "by_source": out,
            "by_ip": ips, "empty_searches": misses}
