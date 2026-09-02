"""ACT System — Main Application"""

from fastapi import FastAPI, Request, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os

from models import (
    init_db, get_app_by_token, create_ticket, get_ticket, list_tickets, count_tickets,
    transition_ticket, get_ticket_history, list_apps, create_app, get_app,
    create_proposal, get_proposals, update_proposal_status,
    get_ticket_stats, count_open_tickets,
    VALID_STATES, VALID_TYPES, VALID_TRANSITIONS,
    create_backlog_item, get_backlog_item, list_backlog_items,
    update_backlog_item, delete_backlog_item, VALID_BACKLOG_STATUSES,
    get_changelog
)
from notify import notify_new_ticket, notify_proposal, notify_status_change

from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(title="ACT System", version="1.0.0", docs_url="/api/docs")

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

app.add_middleware(NoCacheMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="/app/static"), name="static")
templates = Jinja2Templates(directory="/app/templates")

ADMIN_TOKEN = os.environ.get("ACT_ADMIN_TOKEN", "act-admin-secret")
_widget_js: str = ""


# --- Auth helpers ---

def verify_app_token(authorization: str = None) -> dict:
    """Extract app from Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[7:]
    app_entry = get_app_by_token(token)
    if not app_entry:
        raise HTTPException(403, "Invalid app token")
    return app_entry



# === REST API (for widgets + Claude) ===

class TicketCreate(BaseModel):
    type: str = "bug"
    title: str
    description: str
    priority: str = "normal"
    context: dict = {}

class ProposalCreate(BaseModel):
    summary: str
    diff_preview: str = ""
    branch_name: str = ""
    files_changed: list = []

class StatusTransition(BaseModel):
    status: str
    actor: str = "admin"
    detail: str = ""


@app.post("/api/tickets")
async def api_create_ticket(ticket: TicketCreate, request: Request):
    """Create ticket (called by widget). Auth via Bearer token."""
    auth = request.headers.get("Authorization", "")
    app_entry = verify_app_token(auth)
    
    if ticket.type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid type. Valid: {VALID_TYPES}")
    
    result = create_ticket(
        app_id=app_entry["id"],
        type=ticket.type,
        title=ticket.title,
        description=ticket.description,
        priority=ticket.priority,
        context=ticket.context
    )
    await notify_new_ticket(result, app_entry["name"])
    return result


@app.get("/api/tickets")
async def api_list_tickets(
    app_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100
):
    """List tickets (no auth — internal network)."""
    return list_tickets(app_id=app_id, status=status, limit=limit)


@app.get("/api/tickets/{ticket_id}")
async def api_get_ticket(ticket_id: str):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket


@app.get("/api/tickets/{ticket_id}/history")
async def api_get_history(ticket_id: str):
    return get_ticket_history(ticket_id)


@app.post("/api/tickets/{ticket_id}/transition")
async def api_transition(ticket_id: str, body: StatusTransition):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    old_status = ticket["status"]
    if body.status not in VALID_TRANSITIONS.get(old_status, []):
        raise HTTPException(400, f"Invalid transition: {old_status} → {body.status}. Valid: {VALID_TRANSITIONS[old_status]}")
    result = transition_ticket(ticket_id, body.status, actor=body.actor, detail=body.detail)
    # Auto-sync latest proposal status
    if body.status in ("approved", "rejected"):
        proposals = get_proposals(ticket_id)
        if proposals:
            update_proposal_status(proposals[0]["id"], body.status, body.detail or "")
    await notify_status_change(result, old_status, body.status, body.actor)
    return result


@app.post("/api/tickets/{ticket_id}/proposals")
async def api_create_proposal(ticket_id: str, body: ProposalCreate):
    """Create a solution proposal (called by Claude)."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    result = create_proposal(
        ticket_id=ticket_id,
        summary=body.summary,
        diff_preview=body.diff_preview,
        branch_name=body.branch_name,
        files_changed=body.files_changed
    )
    await notify_proposal(ticket, result)
    return result


@app.get("/api/tickets/{ticket_id}/proposals")
async def api_get_proposals(ticket_id: str):
    return get_proposals(ticket_id)


@app.post("/api/proposals/{proposal_id}/review")
async def api_review_proposal(proposal_id: str, status: str = Query(...), note: str = ""):
    """Approve or reject a proposal."""
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "Status must be 'approved' or 'rejected'")
    return update_proposal_status(proposal_id, status, note)


@app.get("/api/apps")
async def api_list_apps():
    return list_apps()


@app.get("/api/apps/{app_id}")
async def api_get_app(app_id: str):
    result = get_app(app_id)
    if not result:
        raise HTTPException(404, "App not found")
    return result


@app.delete("/api/apps/{app_id}")
async def api_delete_app(app_id: str):
    """Delete an app and all its tickets."""
    app_entry = get_app(app_id)
    if not app_entry:
        raise HTTPException(404, "App not found")
    delete_app(app_id)
    return {"deleted": app_id, "name": app_entry["name"]}


@app.get("/api/apps/{app_id}/changelog")
async def api_app_changelog(app_id: str):
    app_entry = get_app(app_id)
    if not app_entry:
        raise HTTPException(404, "App not found")
    return get_changelog(app_id)


@app.get("/wiki", response_class=HTMLResponse)
async def wiki_page(request: Request):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("docs.html", {
        "request": request,
        "open_ticket_count": count_open_tickets(),
    })


@app.get("/changelog", response_class=HTMLResponse)
async def changelog_index(request: Request, app_id: str = None):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    app_entry = None
    changelog = []
    if app_id:
        app_entry = get_app(app_id)
        if app_entry:
            changelog = get_changelog(app_id)
    return templates.TemplateResponse("changelog.html", {
        "request": request,
        "app": app_entry,
        "changelog": changelog,
        "open_ticket_count": count_open_tickets(),
    })


@app.get("/apps/{app_id}/changelog", response_class=HTMLResponse)
async def app_changelog(request: Request, app_id: str):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    app_entry = get_app(app_id)
    if not app_entry:
        raise HTTPException(404, "App not found")
    changelog = get_changelog(app_id)
    return templates.TemplateResponse("changelog.html", {
        "request": request,
        "app": app_entry,
        "changelog": changelog,
        "open_ticket_count": count_open_tickets(),
    })


@app.get("/api/stats")
async def api_stats():
    """Dashboard stats."""
    return get_ticket_stats()


@app.get("/api/claude/last-run")
async def api_claude_last_run():
    """Return the last act-claude.py run record."""
    from models import get_last_claude_run
    return get_last_claude_run() or {}




# === Web UI ===


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_submit(request: Request, token: str = Form(...)):
    if token != ADMIN_TOKEN:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid token"})
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("act_admin_token", token, httponly=True, max_age=86400*30)
    return response


TICKETS_PER_PAGE = 15


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, page: int = Query(1, ge=1), q: str = Query("")):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    offset = (page - 1) * TICKETS_PER_PAGE
    search = q.strip() or None
    tickets = list_tickets(limit=TICKETS_PER_PAGE, offset=offset, search=search)
    total_tickets = count_tickets(search=search)
    total_pages = max(1, (total_tickets + TICKETS_PER_PAGE - 1) // TICKETS_PER_PAGE)
    apps = list_apps()
    stats = await api_stats()
    open_ticket_count = stats["by_status"].get("new", 0) + stats["by_status"].get("proposed", 0)
    from models import get_last_claude_run
    last_run = get_last_claude_run()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "tickets": tickets, "apps": apps, "stats": stats,
        "valid_states": VALID_STATES, "transitions": VALID_TRANSITIONS,
        "open_ticket_count": open_ticket_count,
        "page": page, "total_pages": total_pages, "total_tickets": total_tickets,
        "search_query": q.strip(),
        "last_run": last_run,
    })


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(request: Request, ticket_id: str):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404)
    app_entry = get_app(ticket["app_id"])
    history = get_ticket_history(ticket_id)
    proposals = get_proposals(ticket_id)
    rollback_pending = (
        ticket["status"] == "new"
        and any("Rollback angefordert" in h.get("detail", "") for h in history)
        and not any("Rollback: merge" in h.get("detail", "") for h in history)
    )
    return templates.TemplateResponse("ticket_detail.html", {
        "request": request, "ticket": ticket, "app": app_entry,
        "history": history, "proposals": proposals,
        "open_ticket_count": count_open_tickets(),
        "rollback_pending": rollback_pending,
    })


@app.get("/apps/new", response_class=HTMLResponse)
async def new_app_form(request: Request):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("app_form.html", {"request": request})


@app.post("/apps/new")
async def create_app_submit(
    request: Request,
    name: str = Form(...),
    codebase_path: str = Form(""),
    build_cmd: str = Form(""),
    test_cmd: str = Form("")
):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    create_app(name=name, codebase_path=codebase_path, build_cmd=build_cmd, test_cmd=test_cmd)
    return RedirectResponse(url="/", status_code=303)


@app.get("/backlog", response_class=HTMLResponse)
async def backlog_list(request: Request):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("backlog.html", {
        "request": request,
        "items": list_backlog_items(),
        "apps": list_apps(),
        "valid_statuses": VALID_BACKLOG_STATUSES,
        "open_ticket_count": count_open_tickets(),
    })


@app.post("/backlog")
async def backlog_create(
    request: Request,
    title: str = Form(...),
    app_id: str = Form(...),
    description: str = Form(""),
    priority: str = Form("normal"),
):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    item = create_backlog_item(title=title, app_id=app_id, description=description, priority=priority)
    return RedirectResponse(url=f"/backlog/{item['id']}", status_code=303)


@app.get("/backlog/new", response_class=HTMLResponse)
async def backlog_new_view(request: Request):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("backlog_detail.html", {
        "request": request,
        "item": None,
        "app": None,
        "apps": list_apps(),
        "valid_statuses": VALID_BACKLOG_STATUSES,
        "open_ticket_count": count_open_tickets(),
    })


@app.get("/backlog/{item_id}", response_class=HTMLResponse)
async def backlog_detail_view(request: Request, item_id: str):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    item = get_backlog_item(item_id)
    if not item:
        raise HTTPException(404)
    app_entry = get_app(item["app_id"]) if item.get("app_id") else None
    return templates.TemplateResponse("backlog_detail.html", {
        "request": request,
        "item": item,
        "app": app_entry,
        "valid_statuses": VALID_BACKLOG_STATUSES,
        "open_ticket_count": count_open_tickets(),
    })


@app.post("/backlog/{item_id}")
async def backlog_update(
    request: Request,
    item_id: str,
    title: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
    status: str = Form(...),
    priority: str = Form("normal"),
):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    item = get_backlog_item(item_id)
    if not item:
        raise HTTPException(404)
    update_backlog_item(item_id, title=title, description=description,
                        notes=notes, status=status, priority=priority)
    return RedirectResponse(url="/backlog", status_code=303)


@app.post("/backlog/{item_id}/delete")
async def backlog_delete(request: Request, item_id: str):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    item = get_backlog_item(item_id)
    if not item:
        raise HTTPException(404)
    delete_backlog_item(item_id)
    return RedirectResponse(url="/backlog", status_code=303)


@app.post("/backlog/{item_id}/promote")
async def backlog_promote(request: Request, item_id: str):
    token = request.cookies.get("act_admin_token")
    if token != ADMIN_TOKEN:
        return RedirectResponse(url="/login")
    item = get_backlog_item(item_id)
    if not item:
        raise HTTPException(404)
    ticket_app_id = item.get("app_id")
    if not ticket_app_id:
        raise HTTPException(400, "Backlog item has no app assigned")
    ticket = create_ticket(
        app_id=ticket_app_id,
        type="feature",
        title=item["title"],
        description=item["description"] or item["title"],
        priority=item["priority"],
    )
    update_backlog_item(item_id, status="promoted")
    return RedirectResponse(url=f"/tickets/{ticket['id']}", status_code=303)


@app.on_event("startup")
def startup():
    global _widget_js
    init_db()
    with open("/app/static/act-widget.js") as f:
        _widget_js = f.read()
    print("[ACT] Database initialized")


# --- Widget delivery ---
@app.get("/widget/act-widget.js")
async def serve_widget():
    """Serve the embeddable widget JS — with CORS for cross-origin embedding."""
    return Response(
        content=_widget_js,
        media_type="application/javascript",
        headers={"Access-Control-Allow-Origin": "*"}
    )
