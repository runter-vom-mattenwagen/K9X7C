"""ACT System — Database Models"""

import sqlite3
import json
import uuid
import os
from datetime import datetime, timezone

DB_PATH = os.environ.get("ACT_DB_PATH", "/data/act.db")

VALID_STATES = [
    "new", "proposed", "approved",
    "implementing", "testing", "done", "rejected"
]

VALID_TYPES = ["bug", "feature"]

VALID_TRANSITIONS = {
    "new": ["proposed", "rejected"],
    "proposed": ["approved", "rejected", "new"],
    "approved": ["implementing", "new"],
    "implementing": ["testing", "new"],
    "testing": ["done", "implementing", "new"],
    "done": ["new"],  # reopen
    "rejected": ["new"],  # reopen
}


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    db = get_db()
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS apps (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            codebase_path TEXT,
            build_cmd TEXT DEFAULT '',
            test_cmd TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            app_id TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'bug',
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            priority TEXT DEFAULT 'normal',
            context_url TEXT DEFAULT '',
            context_browser TEXT DEFAULT '',
            context_errors TEXT DEFAULT '',
            context_app_version TEXT DEFAULT '',
            context_screenshot TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (app_id) REFERENCES apps(id)
        );

        CREATE TABLE IF NOT EXISTS ticket_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            actor TEXT NOT NULL DEFAULT 'system',
            detail TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            diff_preview TEXT DEFAULT '',
            branch_name TEXT DEFAULT '',
            files_changed TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            review_note TEXT DEFAULT '',
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );

        CREATE INDEX IF NOT EXISTS idx_tickets_app ON tickets(app_id);
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_history_ticket ON ticket_history(ticket_id);
        CREATE INDEX IF NOT EXISTS idx_proposals_ticket ON proposals(ticket_id);

        CREATE TABLE IF NOT EXISTS backlog_items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idea',
            priority TEXT DEFAULT 'normal',
            app_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS claude_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            output TEXT DEFAULT ''
        );
    """)
    db.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gen_id(prefix: str = "ACT") -> str:
    short = uuid.uuid4().hex[:8].upper()
    return f"{prefix}-{short}"


def get_ticket_stats() -> dict:
    db = get_db()
    by_status = {row["status"]: row["cnt"] for row in db.execute(
        "SELECT status, COUNT(*) as cnt FROM tickets GROUP BY status"
    ).fetchall()}
    by_app = {row["name"]: row["cnt"] for row in db.execute(
        "SELECT a.name, COUNT(*) as cnt FROM tickets t JOIN apps a ON t.app_id=a.id GROUP BY a.name"
    ).fetchall()}
    total = db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    db.close()
    return {
        "total": total,
        "by_status": by_status,
        "by_app": by_app,
        "pending_review": by_status.get("proposed", 0),
    }


def count_open_tickets() -> int:
    db = get_db()
    count = db.execute(
        "SELECT COUNT(*) FROM tickets WHERE status IN ('new', 'proposed')"
    ).fetchone()[0]
    db.close()
    return count


def get_last_claude_run() -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM claude_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()
    return dict(row) if row else None


# --- App CRUD ---

def create_app(name: str, codebase_path: str = "", build_cmd: str = "", test_cmd: str = "") -> dict:
    db = get_db()
    app_id = gen_id("APP")
    token = f"act_{uuid.uuid4().hex}"
    db.execute(
        "INSERT INTO apps (id, name, token, codebase_path, build_cmd, test_cmd, created_at) VALUES (?,?,?,?,?,?,?)",
        (app_id, name, token, codebase_path, build_cmd, test_cmd, now_iso())
    )
    db.commit()
    result = dict(db.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone())
    db.close()
    return result


def get_app_by_token(token: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM apps WHERE token=?", (token,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_app(app_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def list_apps() -> list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM apps ORDER BY name").fetchall()
    db.close()
    return [dict(r) for r in rows]


def delete_app(app_id: str):
    """Delete app and all related tickets, proposals, history."""
    db = get_db()
    # Get all ticket IDs for this app
    tickets = db.execute("SELECT id FROM tickets WHERE app_id=?", (app_id,)).fetchall()
    for t in tickets:
        db.execute("DELETE FROM ticket_history WHERE ticket_id=?", (t["id"],))
        db.execute("DELETE FROM proposals WHERE ticket_id=?", (t["id"],))
    db.execute("DELETE FROM tickets WHERE app_id=?", (app_id,))
    db.execute("DELETE FROM apps WHERE id=?", (app_id,))
    db.commit()
    db.close()


def update_app(app_id: str, **kwargs) -> dict | None:
    db = get_db()
    allowed = {"name", "codebase_path", "build_cmd", "test_cmd"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        db.close()
        return get_app(app_id)
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [app_id]
    db.execute(f"UPDATE apps SET {sets} WHERE id=?", vals)
    db.commit()
    result = get_app(app_id)
    db.close()
    return result


# --- Ticket CRUD ---

def create_ticket(app_id: str, type: str, title: str, description: str,
                  priority: str = "normal", context: dict = None) -> dict:
    db = get_db()
    ticket_id = gen_id("TKT")
    ts = now_iso()
    ctx = context or {}
    db.execute(
        """INSERT INTO tickets
        (id, app_id, type, title, description, status, priority,
         context_url, context_browser, context_errors, context_app_version,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ticket_id, app_id, type, title, description, "new", priority,
         ctx.get("url", ""), ctx.get("browser", ""), ctx.get("errors", ""),
         ctx.get("app_version", ""), ts, ts)
    )
    db.execute(
        "INSERT INTO ticket_history (ticket_id, action, new_value, actor, timestamp) VALUES (?,?,?,?,?)",
        (ticket_id, "created", "new", "widget", ts)
    )
    db.commit()
    result = dict(db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
    db.close()
    return result


def get_ticket(ticket_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def list_tickets(app_id: str = None, status: str = None, limit: int = 100,
                offset: int = 0, search: str = None) -> list[dict]:
    db = get_db()
    query = "SELECT t.*, a.name as app_name FROM tickets t JOIN apps a ON t.app_id = a.id WHERE 1=1"
    params = []
    if app_id:
        query += " AND t.app_id=?"
        params.append(app_id)
    if status:
        query += " AND t.status=?"
        params.append(status)
    if search:
        query += " AND (t.title LIKE ? OR t.description LIKE ? OR t.id LIKE ?)"
        s = f"%{search}%"
        params.extend([s, s, s])
    query += " ORDER BY t.updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = db.execute(query, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def count_tickets(app_id: str = None, status: str = None, search: str = None) -> int:
    db = get_db()
    query = "SELECT COUNT(*) as cnt FROM tickets t WHERE 1=1"
    params = []
    if app_id:
        query += " AND t.app_id=?"
        params.append(app_id)
    if status:
        query += " AND t.status=?"
        params.append(status)
    if search:
        query += " AND (t.title LIKE ? OR t.description LIKE ? OR t.id LIKE ?)"
        s = f"%{search}%"
        params.extend([s, s, s])
    row = db.execute(query, params).fetchone()
    db.close()
    return row["cnt"]


def transition_ticket(ticket_id: str, new_status: str, actor: str = "admin", detail: str = "") -> dict | None:
    db = get_db()
    ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not ticket:
        db.close()
        return None
    old_status = ticket["status"]
    if new_status not in VALID_TRANSITIONS.get(old_status, []):
        db.close()
        raise ValueError(f"Invalid transition: {old_status} → {new_status}")
    ts = now_iso()
    db.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (new_status, ts, ticket_id))
    db.execute(
        "INSERT INTO ticket_history (ticket_id, action, old_value, new_value, actor, detail, timestamp) VALUES (?,?,?,?,?,?,?)",
        (ticket_id, "status_change", old_status, new_status, actor, detail, ts)
    )
    db.commit()
    result = dict(db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
    db.close()
    return result


def get_ticket_history(ticket_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM ticket_history WHERE ticket_id=? ORDER BY timestamp ASC",
        (ticket_id,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


# --- Proposal CRUD ---

def create_proposal(ticket_id: str, summary: str, diff_preview: str = "",
                    branch_name: str = "", files_changed: list = None) -> dict:
    db = get_db()
    prop_id = gen_id("PRP")
    ts = now_iso()
    db.execute(
        """INSERT INTO proposals (id, ticket_id, summary, diff_preview, branch_name, files_changed, created_at, status)
        VALUES (?,?,?,?,?,?,?,?)""",
        (prop_id, ticket_id, summary, diff_preview, branch_name,
         json.dumps(files_changed or []), ts, "pending")
    )
    db.commit()
    result = dict(db.execute("SELECT * FROM proposals WHERE id=?", (prop_id,)).fetchone())
    db.close()
    # Auto-transition ticket to proposed (separate connection)
    try:
        transition_ticket(ticket_id, "proposed", actor="claude", detail=f"Proposal {prop_id}")
    except ValueError:
        pass  # ticket might already be in proposed state
    return result


def get_proposals(ticket_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM proposals WHERE ticket_id=? ORDER BY created_at DESC",
        (ticket_id,)
    ).fetchall()
    db.close()
    result = [dict(r) for r in rows]
    for r in result:
        if isinstance(r.get("files_changed"), str):
            r["files_changed"] = json.loads(r["files_changed"])
    return result


def update_proposal_status(proposal_id: str, status: str, review_note: str = "") -> dict | None:
    db = get_db()
    db.execute(
        "UPDATE proposals SET status=?, review_note=? WHERE id=?",
        (status, review_note, proposal_id)
    )
    db.commit()
    result = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    db.close()
    return dict(result) if result else None


# --- Backlog CRUD ---

VALID_BACKLOG_STATUSES = ["idea", "developing", "ready", "discarded", "promoted"]


def create_backlog_item(title: str, app_id: str, description: str = "", notes: str = "",
                        priority: str = "normal") -> dict:
    db = get_db()
    item_id = gen_id("BLG")
    ts = now_iso()
    db.execute(
        """INSERT INTO backlog_items (id, title, description, notes, status, priority, app_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (item_id, title, description, notes, "idea", priority, app_id, ts, ts)
    )
    db.commit()
    result = dict(db.execute("SELECT * FROM backlog_items WHERE id=?", (item_id,)).fetchone())
    db.close()
    return result


def get_backlog_item(item_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM backlog_items WHERE id=?", (item_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def list_backlog_items(app_id: str = None, status: str = None) -> list[dict]:
    db = get_db()
    query = "SELECT b.*, a.name as app_name FROM backlog_items b LEFT JOIN apps a ON b.app_id = a.id WHERE 1=1"
    params = []
    if app_id:
        query += " AND b.app_id=?"
        params.append(app_id)
    if status:
        query += " AND b.status=?"
        params.append(status)
    query += " ORDER BY b.created_at DESC"
    rows = db.execute(query, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def update_backlog_item(item_id: str, **kwargs) -> dict | None:
    db = get_db()
    allowed = {"title", "description", "notes", "status", "priority"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        row = db.execute("SELECT * FROM backlog_items WHERE id=?", (item_id,)).fetchone()
        db.close()
        return dict(row) if row else None
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [item_id]
    db.execute(f"UPDATE backlog_items SET {sets} WHERE id=?", vals)
    db.commit()
    result = dict(db.execute("SELECT * FROM backlog_items WHERE id=?", (item_id,)).fetchone())
    db.close()
    return result


def get_changelog(app_id: str) -> list[dict]:
    import re
    db = get_db()
    rows = db.execute(
        """
        SELECT t.id as ticket_id, t.title, h.timestamp, h.detail,
               p.summary, p.files_changed
        FROM tickets t
        JOIN ticket_history h ON h.ticket_id = t.id
        LEFT JOIN proposals p ON p.id = (
            SELECT id FROM proposals WHERE ticket_id = t.id ORDER BY created_at DESC LIMIT 1
        )
        WHERE t.app_id = ?
          AND h.action = 'status_change'
          AND h.old_value = 'implementing'
          AND h.new_value = 'testing'
        ORDER BY h.timestamp DESC
        """,
        (app_id,)
    ).fetchall()
    db.close()
    result = []
    for row in rows:
        detail = row["detail"] or ""
        commit_match = re.search(r'([0-9a-f]{7,40})', detail)
        commit_id = commit_match.group(1) if commit_match else ""
        try:
            files = json.loads(row["files_changed"] or "[]")
        except Exception:
            files = []
        result.append({
            "date": row["timestamp"][:10],
            "commit_id": commit_id,
            "title": row["title"],
            "summary": row["summary"] or "",
            "files_changed": files,
            "ticket_id": row["ticket_id"],
        })
    return result


def delete_backlog_item(item_id: str):
    db = get_db()
    db.execute("DELETE FROM backlog_items WHERE id=?", (item_id,))
    db.commit()
    db.close()
