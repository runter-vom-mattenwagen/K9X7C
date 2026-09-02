#!/usr/bin/env python3
"""
ACT-Claude Bridge — AI-powered ticket analysis and resolution

Uses Claude Code CLI (claude --print) for AI operations — no API key needed.
Requires: Claude Code authenticated on this host (/root/.claude/.credentials.json)

Usage:
    ./act-claude.py status               # Show actionable tickets
    ./act-claude.py analyze              # Analyze new tickets, create proposals
    ./act-claude.py implement <ticket>   # Implement approved ticket on branch
    ./act-claude.py auto                 # Full cycle: analyze + implement approved
"""

import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import httpx
import argparse
import datetime
import re
import time
from pathlib import Path

# ============================================================================
# Configuration — override via environment variables
# ============================================================================

# ACT server
ACT_URL = os.environ.get("ACT_URL", "https://act.claude.int")
VERIFY_SSL = os.environ.get("ACT_VERIFY_SSL", "false").lower() == "true"

# Claude Code CLI
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/usr/local/bin/claude")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
# Pinned explicitly rather than inherited from /root/.claude/settings.json, so a
# change to the host default cannot silently move this job's model.
# Measured 2026-05-20..07-24: 3104 cron cycles, 3081 of them fully idle, 4 actual
# Claude calls (2 analyze, 2 implement), 6-15s wall clock per working cycle
# including git and docker rebuild. Volume is a non-issue; the implement path
# generates code and structured JSON, so Opus is worth it. Override: CLAUDE_MODEL.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "opus")

# ntfy notifications (set ACT_NTFY_ENABLED=false to disable)
NTFY_ENABLED = os.environ.get("ACT_NTFY_ENABLED", "false").lower() == "true"
NTFY_URL = os.environ.get("ACT_NTFY_URL", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("ACT_NTFY_TOPIC", "act")
# NOTE: this script runs on the HOST via act-cron.sh, not inside the container.
# The default must therefore be the host-side bind-mount source. "/data/act.db"
# is the container path from compose.yml and was wrongly used here until
# 2026-07-24, which silently killed claude_runs logging on 2026-04-04.
ACT_DB_PATH = os.environ.get("ACT_DB_PATH", "/daten/docker/act/vol_data/act.db")

# ============================================================================


def get_default_branch(codebase_path: str) -> str:
    """Detect the default branch (main, master, dev, etc.)."""
    result = subprocess.run(
        ["git", "-C", codebase_path, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return result.stdout.strip().split("/")[-1]
    result = subprocess.run(
        ["git", "-C", codebase_path, "branch", "--show-current"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "main"


def api(method: str, path: str, data: dict = None, retries: int = 3) -> dict | list:
    """Call ACT API with retry logic (handles 502 during self-rebuild)."""
    url = f"{ACT_URL}{path}"
    for attempt in range(retries):
        try:
            with httpx.Client(verify=VERIFY_SSL, timeout=30) as client:
                if method == "GET":
                    r = client.get(url)
                else:
                    r = client.post(url, json=data)
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  [API] {e} — retry in {wait}s ({attempt + 1}/{retries})")
                time.sleep(wait)
            else:
                raise


def notify(title: str, message: str, priority: str = "default", tags: str = "ticket", ticket_id: str = ""):
    """Send ntfy notification. No-op if disabled."""
    if not NTFY_ENABLED:
        return
    try:
        payload = {
            "topic": NTFY_TOPIC,
            "title": title,
            "message": message,
            "priority": 4 if priority == "high" else 3,
            "tags": tags.split(","),
        }
        if ticket_id:
            payload["click"] = f"{ACT_URL}/tickets/{ticket_id}"
        httpx.post(NTFY_URL, json=payload, verify=VERIFY_SSL, timeout=10)
    except Exception as e:
        print(f"  [ntfy] {e}")


def record_run_start() -> int | None:
    """Insert a claude_run record and return its id."""
    try:
        db = sqlite3.connect(ACT_DB_PATH)
        cur = db.execute(
            "INSERT INTO claude_runs (started_at, status, output) VALUES (?, 'running', '')",
            (datetime.datetime.now(datetime.timezone.utc).isoformat(),)
        )
        db.commit()
        run_id = cur.lastrowid
        db.close()
        return run_id
    except Exception as e:
        print(f"  [run] {e}")
        return None


def record_run_finish(run_id: int | None, status: str, output: str) -> None:
    """Update a claude_run record with finish time, status and output."""
    if run_id is None:
        return
    try:
        db = sqlite3.connect(ACT_DB_PATH)
        db.execute(
            "UPDATE claude_runs SET finished_at=?, status=?, output=? WHERE id=?",
            (datetime.datetime.now(datetime.timezone.utc).isoformat(), status, output[:8000], run_id)
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"  [run] {e}")


SUSPICIOUS_COMMIT_MESSAGES = {"dummy", "wip", "placeholder", "todo"}


def _abort_branch(codebase_path: str, branch: str, default_branch: str):
    """Undo last commit, switch back to default branch and delete feature branch."""
    subprocess.run(["git", "-C", codebase_path, "reset", "--hard", "HEAD~1"], capture_output=True)
    subprocess.run(["git", "-C", codebase_path, "checkout", default_branch], capture_output=True)
    subprocess.run(["git", "-C", codebase_path, "branch", "-D", branch], capture_output=True)


def _cleanup_branch(codebase_path: str, branch: str, default_branch: str):
    """Switch back to default branch and delete feature branch (no commit undo)."""
    subprocess.run(["git", "-C", codebase_path, "checkout", default_branch], capture_output=True)
    subprocess.run(["git", "-C", codebase_path, "branch", "-D", branch], capture_output=True)


def _json_unescape(s: str) -> str:
    """Unescape JSON string escape sequences (for regex fallback paths)."""
    return s.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')


def _extract_first_json_object(text: str) -> str | None:
    """Extract first top-level JSON object via brace counting, respecting strings."""
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    return None


def parse_claude_json(raw: str) -> dict | None:
    """Parse Claude's JSON response with multiple fallback strategies.

    Strategy order:
      0. Extract first JSON object via brace-counting (handles multi-block responses)
      1. Direct json.loads on cleaned text
      2. json5 (trailing commas, single quotes)
      3. Regex extraction with forward-bounded search (NOT rfind)
      4. Analysis fields extraction (summary, files_changed, etc.)
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    # --- Pre-processing: if response contains --- separators, try each block ---
    blocks = [text]
    if '\n---\n' in text:
        blocks = [b.strip() for b in text.split('\n---\n') if b.strip()]

    for block in blocks:
        # Attempt 0: extract first JSON object via brace-counting
        extracted = _extract_first_json_object(block)
        if extracted:
            try:
                result = json.loads(extracted)
                if isinstance(result, dict) and (result.get("operations") or result.get("summary") or result.get("reject")):
                    return result
            except json.JSONDecodeError:
                pass

            try:
                import json5
                result = json5.loads(extracted)
                if isinstance(result, dict) and (result.get("operations") or result.get("summary") or result.get("reject")):
                    return result
            except Exception:
                pass

    # Attempt 1: direct JSON on full text (legacy — kept for simple responses)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: json5
    try:
        import json5
        return json5.loads(text)
    except Exception:
        pass

    # Attempt 3: regex extraction with FORWARD-BOUNDED search
    # Key fix: find the boundary of each operation BEFORE extracting values,
    # so one operation can't swallow the next.
    try:
        ops = []
        op_pattern = re.compile(r'"action"\s*:\s*"(modify|create)"\s*,\s*"file"\s*:\s*"([^"]+)"')
        matches = list(op_pattern.finditer(text))

        for idx, m in enumerate(matches):
            action, fpath = m.group(1), m.group(2)
            op = {"action": action, "file": fpath}

            # Bound this operation's text: from match end to next operation start (or end of text)
            op_start = m.end()
            op_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            segment = text[op_start:op_end]

            if action == "modify":
                s_idx = segment.find('"search"')
                r_idx = segment.find('"replace"')
                if s_idx >= 0 and r_idx > s_idx:
                    s_val_start = segment.index('"', s_idx + 8) + 1
                    search_raw = segment[s_val_start:r_idx].rstrip().rstrip(",").rstrip().rstrip('"')
                    op["search"] = _json_unescape(search_raw)

                    r_val_start = segment.index('"', r_idx + 9) + 1
                    rest = segment[r_val_start:]
                    # Forward search: find first "} or "}] that closes this value
                    for marker in ['"}', '"}']: 
                        pos = rest.find(marker)
                        if pos > 0:
                            op["replace"] = _json_unescape(rest[:pos])
                            break

            elif action == "create":
                c_idx = segment.find('"content"')
                if c_idx >= 0:
                    c_val_start = segment.index('"', c_idx + 9) + 1
                    rest = segment[c_val_start:]
                    for marker in ['"}', '"}']:
                        pos = rest.find(marker)
                        if pos > 0:
                            op["content"] = _json_unescape(rest[:pos])
                            break

            if action == "modify" and op.get("search") and op.get("replace"):
                ops.append(op)
            elif action == "create" and op.get("content"):
                ops.append(op)

        if ops:
            cm = re.search(r'"commit_message"\s*:\s*"([^"]*)"', text)
            tn = re.search(r'"test_notes"\s*:\s*"([^"]*)"', text)
            return {
                "operations": ops,
                "commit_message": cm.group(1) if cm else "ACT implementation",
                "test_notes": tn.group(1) if tn else "",
            }
    except Exception:
        pass

    # Attempt 4: extract analysis fields (summary, files_changed, etc.)
    try:
        if '"summary"' in text and '"files_changed"' in text:
            result = {}
            field_order = ["summary", "files_changed", "diff_preview", "branch_name", "complexity", "risk"]
            for i, field in enumerate(field_order):
                marker = f'"{field}"'
                pos = text.find(marker)
                if pos < 0:
                    continue
                val_start = text.index('"', pos + len(marker) + 1)

                if field == "files_changed":
                    arr_start = text.index('[', pos)
                    arr_end = text.index(']', arr_start) + 1
                    try:
                        result[field] = json.loads(text[arr_start:arr_end])
                    except json.JSONDecodeError:
                        result[field] = []
                    continue

                next_field = None
                for nf in field_order[i+1:]:
                    nf_pos = text.find(f'"{nf}"', val_start)
                    if nf_pos > 0:
                        next_field = nf_pos
                        break

                if next_field:
                    raw_val = text[val_start+1:next_field].rstrip().rstrip(',').rstrip().rstrip('"')
                else:
                    raw_val = text[val_start+1:].rstrip().rstrip('}').rstrip().rstrip('"')

                result[field] = _json_unescape(raw_val)

            if result.get("summary"):
                return result
    except Exception:
        pass

    return None


def _docker_rebuild(codebase_path: str) -> str:
    """Run docker compose build + up -d. Returns a status string."""
    r = subprocess.run(
        ["docker", "compose", "build", "--quiet"],
        capture_output=True, text=True, cwd=codebase_path, timeout=120
    )
    if r.returncode == 0:
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            capture_output=True, text=True, cwd=codebase_path, timeout=30
        )
        time.sleep(5)  # Wait for container to become healthy
        return "Docker Rebuild + Restart OK"
    return f"Docker Rebuild fehlgeschlagen: {r.stderr[:200]}"


def claude_ask(prompt: str, system: str = "") -> str:
    """Call Claude Code CLI in non-interactive mode (no tools, pure text)."""
    cmd = [CLAUDE_BIN, "--print", "--model", CLAUDE_MODEL, "--allowedTools", ""]

    full_prompt = f"{system}\n\n---\n\n{prompt}" if system else prompt

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(full_prompt)
        prompt_file = f.name

    try:
        result = subprocess.run(
            cmd,
            stdin=open(prompt_file),
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            cwd='/tmp',
            env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
        )
        if result.returncode != 0:
            print(f"  [WARN] claude exit code {result.returncode}")
            if result.stderr:
                print(f"  [STDERR] {result.stderr[:300]}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] Claude CLI timed out after {CLAUDE_TIMEOUT}s")
        return ""
    finally:
        os.unlink(prompt_file)


def read_codebase(path: str, max_files: int = 30, max_size: int = 50000) -> str:
    """Read codebase files into a string for context."""
    if not os.path.isdir(path):
        return f"[ERROR] Codebase path not found: {path}"

    skip_dirs = {".git", "node_modules", "__pycache__", "vol_data", "vol_logs", ".venv", "venv"}
    skip_ext = {".pyc", ".db", ".sqlite", ".png", ".jpg", ".gif", ".woff", ".woff2", ".ico", ".lock"}

    files = []
    total_size = 0
    base = Path(path)

    for p in sorted(base.rglob("*")):
        if any(sd in p.parts for sd in skip_dirs):
            continue
        if not p.is_file() or p.suffix in skip_ext:
            continue
        try:
            content = p.read_text(errors="replace")
        except Exception:
            continue
        if total_size + len(content) > max_size:
            break
        rel = str(p.relative_to(base))
        files.append(f"### {rel}\n```\n{content}\n```")
        total_size += len(content)
        if len(files) >= max_files:
            break

    tree = subprocess.run(
        ["find", ".", "-maxdepth", "3", "-not", "-path", "./.git/*"],
        capture_output=True, text=True, cwd=path
    ).stdout

    return f"## File tree\n```\n{tree}\n```\n\n## File contents\n\n" + "\n\n".join(files)


def analyze_ticket(ticket: dict, app: dict) -> dict:
    """Let Claude analyze a ticket and produce a proposal."""
    codebase = read_codebase(app["codebase_path"]) if app.get("codebase_path") else "[No codebase path configured]"

    # Get ticket history for context (especially reopen reasons)
    history = api("GET", f"/api/tickets/{ticket['id']}/history")
    history_text = ""
    for h in history:
        if h.get("detail"):
            history_text += f"- {h['timestamp'][:16]} [{h['actor']}] {h['action']}: {h['detail']}\n"

    system = (
        "You are a senior developer analyzing bug reports and feature requests. "
        "The full codebase is provided below — do NOT try to read files yourself. "
        "Respond ONLY in valid JSON, no markdown fences, no preamble. "
        "FIRST decide: is this ticket worth implementing? Reject it if it is spam, "
        "gibberish, a duplicate concept with no actionable details, a joke, an insult, "
        "technically impossible, completely outside the scope of the described app, "
        "or otherwise clearly not implementable. Be strict but fair. "
        "If rejecting: {\"reject\": true, \"reject_reason\": \"concise explanation in the ticket's language\"} "
        "If implementing: {\"summary\": \"2-3 sentences\", \"files_changed\": [\"list\"], "
        "\"diff_preview\": \"description of changes per file\", "
        "\"branch_name\": \"act/TICKET-ID\", \"complexity\": \"low|medium|high\", "
        "\"risk\": \"brief assessment\"}"
    )

    prompt = f"""Analyze this ticket and decide whether to implement or reject it.

## Ticket
- ID: {ticket['id']}
- Type: {ticket['type']}
- Title: {ticket['title']}
- Priority: {ticket['priority']}
- Description: {ticket['description']}
- Context URL: {ticket.get('context_url', '')}
- Console Errors: {ticket.get('context_errors', '')}
{"## History" + chr(10) + history_text if history_text else ""}
## App: {app['name']}
- Codebase: {app.get('codebase_path', 'N/A')}

## Codebase
{codebase}
"""

    raw = claude_ask(prompt, system)

    proposal = parse_claude_json(raw)
    if proposal is None:
        proposal = {
            "summary": raw[:500],
            "files_changed": [],
            "diff_preview": raw,
            "branch_name": f"act/{ticket['id'].lower()}",
            "complexity": "unknown",
            "risk": "Could not parse structured response",
        }

    if not isinstance(proposal, dict):
        proposal = {"summary": str(proposal)[:500], "files_changed": [], "diff_preview": "", "complexity": "unknown", "risk": "Unexpected format"}

    proposal.setdefault("branch_name", f"act/{ticket['id'].lower()}")
    return proposal



def _load_targeted_files(codebase_path: str, files_to_change: list, proposal: dict,
                         max_file_size: int = 15000) -> str:
    """Load file contents for implementation context. Large files get windowed to relevant sections."""
    targeted = []
    base = Path(codebase_path)
    for fname in files_to_change:
        fpath = base / fname
        if not fpath.exists():
            continue
        try:
            file_content = fpath.read_text(errors="replace")
            if len(file_content) > max_file_size:
                hint = proposal.get("diff_preview", "") + " " + proposal.get("summary", "")
                keywords = [w for w in hint.split() if len(w) > 4 and w.isalnum()][:10]
                lines = file_content.split("\n")
                best_line, best_score = 0, 0
                for i, line in enumerate(lines):
                    score = sum(1 for kw in keywords if kw.lower() in line.lower())
                    if score > best_score:
                        best_score = score
                        best_line = i
                window = 150
                start = max(0, best_line - window)
                end = min(len(lines), best_line + window)
                section = "\n".join(lines[start:end])[:max_file_size]
                file_content = f"[File: {len(lines)} lines, showing {start}-{end}]\n{section}"
            targeted.append(f"### {fname}\n```\n{file_content}\n```")
        except Exception as e:
            targeted.append(f"### {fname}\n[ERROR: {e}]")
    if targeted:
        return "## Targeted Files\n\n" + "\n\n".join(targeted)
    return read_codebase(codebase_path, max_files=10, max_size=20000)



def _run_checks(codebase_path: str, changed_files: list, app: dict) -> tuple[list[str], bool]:
    """Run automated checks on changed files. Returns (check_lines, all_passed)."""
    checks = []
    all_passed = True

    for fname in changed_files:
        fpath = os.path.join(codebase_path, fname)
        if not os.path.exists(fpath):
            continue
        try:
            txt = open(fpath).read()
        except Exception as e:
            checks.append(f"  ✗ {fname}: {e}")
            all_passed = False
            continue

        if fname.endswith(".py"):
            r = subprocess.run(["python3", "-m", "py_compile", fpath], capture_output=True, text=True)
            checks.append(f"  ✓ {fname}: Python syntax OK" if r.returncode == 0
                          else f"  ✗ {fname}: Python syntax ERROR: {r.stderr.strip()}")
            if r.returncode != 0:
                all_passed = False
        elif fname.endswith(".js"):
            opens = txt.count("{") + txt.count("(") + txt.count("[")
            closes = txt.count("}") + txt.count(")") + txt.count("]")
            if abs(opens - closes) <= 1:
                checks.append(f"  ✓ {fname}: JS bracket balance OK")
            else:
                checks.append(f"  ⚠ {fname}: JS bracket mismatch (open={opens}, close={closes})")
                all_passed = False
        elif fname.endswith(".html"):
            ok = True
            for tag in ["div", "span", "button", "form", "table"]:
                o = txt.lower().count(f"<{tag}")
                c = txt.lower().count(f"</{tag}")
                if o != c:
                    checks.append(f"  ⚠ {fname}: <{tag}> mismatch ({o}/{c})")
                    ok = False
            if ok:
                checks.append(f"  ✓ {fname}: HTML tag balance OK")
            if not ok:
                all_passed = False
        elif fname.endswith(".css"):
            if txt.count("{") == txt.count("}"):
                checks.append(f"  ✓ {fname}: CSS OK")
            else:
                checks.append(f"  ⚠ {fname}: CSS bracket mismatch")
                all_passed = False

    for cmd_field, label in [("test_cmd", "test"), ("build_cmd", "build")]:
        cmd = app.get(cmd_field)
        if not cmd:
            continue
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=codebase_path, timeout=60)
            checks.append(f"  ✓ {label}: {cmd} passed" if r.returncode == 0
                          else f"  ✗ {label}: {cmd} failed: {r.stderr[:200]}")
            if r.returncode != 0:
                all_passed = False
        except Exception as e:
            checks.append(f"  ✗ {label}: {e}")
            all_passed = False

    return checks, all_passed



def _unescape_json_values(impl: dict) -> dict:
    """Unescape residual JSON escapes in operation values.

    Even after json.loads, Claude sometimes double-escapes content,
    leaving \" and \\n literals in source code. This cleans them up.
    """
    for op in impl.get("operations", []):
        for key in ("search", "replace", "content"):
            if key in op and isinstance(op[key], str):
                if '\\"' in op[key] or '\\n' in op[key]:
                    op[key] = _json_unescape(op[key])
    return impl


def implement_ticket(ticket: dict, app: dict):
    """Implement approved ticket using Claude Code on a branch."""
    codebase_path = app.get("codebase_path")
    if not codebase_path or not os.path.isdir(codebase_path):
        print(f"  [ERROR] Invalid codebase path: {codebase_path}")
        return False

    ticket_id = ticket["id"]
    branch = f"act/{ticket_id.lower()}"
    default_branch = get_default_branch(codebase_path)

    proposals = api("GET", f"/api/tickets/{ticket_id}/proposals")
    approved = [p for p in proposals if p["status"] == "approved"]
    if not approved:
        print(f"  [SKIP] No approved proposal for {ticket_id}")
        return False

    proposal = approved[0]

    # Create branch
    subprocess.run(["git", "-C", codebase_path, "checkout", "-b", branch], capture_output=True)
    print(f"  Branch: {branch}")

    # Transition to implementing
    api("POST", f"/api/tickets/{ticket_id}/transition",
        {"status": "implementing", "actor": "claude", "detail": f"Starting implementation on {branch}"})

    # Load targeted files
    files_to_change = proposal.get("files_changed", [])
    if isinstance(files_to_change, str):
        try:
            files_to_change = json.loads(files_to_change)
        except (json.JSONDecodeError, TypeError):
            files_to_change = []
    if not isinstance(files_to_change, list):
        files_to_change = []

    codebase = _load_targeted_files(codebase_path, files_to_change, proposal)

    system = (
        "You are implementing an approved change. "
        "The full codebase is provided below — do NOT try to read files yourself. "
        "Respond ONLY in valid JSON, no markdown fences, no preamble. "
        "JSON schema: {\"operations\": [{\"action\": \"modify\", \"file\": \"path\", "
        "\"search\": \"exact substring\", \"replace\": \"replacement\"}], "
        "\"commit_message\": \"msg\", \"test_notes\": \"verification\"} "
        "For create: {\"action\": \"create\", \"file\": \"path\", \"content\": \"full content\"} "
        "RULES: 1. RELATIVE paths. 2. search = EXACT substring, keep to 1-3 unique lines. "
        "3. ESCAPE all double quotes inside search/replace values as backslash-quote."
    )

    prompt = f"""Implement this approved proposal.

## Proposal
{proposal['summary']}

## Diff Preview
{proposal['diff_preview']}

## Files to change
{json.dumps(files_to_change)}

## Current Codebase
{codebase}
"""

    raw = claude_ask(prompt, system)

    impl = parse_claude_json(raw)
    if impl is None:
        try:
            json.loads(raw)
        except json.JSONDecodeError as je:
            e = je
        else:
            e = Exception("Unknown parse failure")
        # Log full error for debugging
        raw_preview = raw[:500] if raw else "(empty response)"
        error_detail = f"JSON Parse Error: {e}\n\nClaude Response (first 500 chars):\n{raw_preview}"
        print(f"  [ERROR] Could not parse implementation response")
        print(f"  [JSON ERROR] {e}")
        print(f"  [RAW] {raw_preview}")

        # Write full response to log file for debugging
        log_file = f"/var/log/act-claude-debug-{ticket_id}.log"
        try:
            with open(log_file, "w") as lf:
                lf.write(f"Ticket: {ticket_id}\n")
                lf.write(f"Time: {datetime.datetime.now().isoformat()}\n")
                lf.write(f"Error: {e}\n\n")
                lf.write(f"Full Claude Response:\n{raw}\n")
            error_detail += f"\n\nFull log: {log_file}"
        except Exception:
            pass

        api("POST", f"/api/tickets/{ticket_id}/transition",
            {"status": "new", "actor": "claude", "detail": error_detail})
        _cleanup_branch(codebase_path, branch, default_branch)
        return False

    # Unescape residual JSON escaping in operation values
    impl = _unescape_json_values(impl)

    # Apply operations
    applied = 0
    errors = []
    for op in impl.get("operations", []):
        fpath = op["file"]
        if fpath.startswith("/"):
            fpath = os.path.relpath(fpath, codebase_path) if fpath.startswith(codebase_path) else os.path.basename(fpath)
        filepath = os.path.join(codebase_path, fpath)
        try:
            if op["action"] == "create":
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, "w") as f:
                    f.write(op["content"])
                applied += 1
            elif op["action"] == "modify":
                with open(filepath) as f:
                    content = f.read()
                if op["search"] not in content:
                    errors.append(f"Search string not found in {op['file']}")
                    continue
                content = content.replace(op["search"], op["replace"], 1)
                with open(filepath, "w") as f:
                    f.write(content)
                applied += 1
        except Exception as e:
            errors.append(f"{op['file']}: {e}")

    if applied == 0:
        print(f"  [ERROR] No operations applied. Errors: {errors}")
        api("POST", f"/api/tickets/{ticket_id}/transition",
            {"status": "new", "actor": "claude", "detail": f"Implementation failed: {'; '.join(errors)}"})
        _cleanup_branch(codebase_path, branch, default_branch)
        return False

    # Commit
    commit_msg = impl.get("commit_message", f"ACT: {ticket_id} — {ticket['title']}")
    subprocess.run(["git", "-C", codebase_path, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", codebase_path, "commit", "-m", commit_msg], capture_output=True)

    # Get commit hash and changed files for audit trail
    commit_hash = subprocess.run(
        ["git", "-C", codebase_path, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()

    changed_files_raw = subprocess.run(
        ["git", "-C", codebase_path, "diff", "--name-only", "HEAD~1", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()
    changed_files = [f for f in changed_files_raw.split("\n") if f]

    # --- Implementation sanity check: reject empty diffs and dummy commits ---
    empty_diff = not changed_files
    dummy_message = commit_msg.strip().lower() in SUSPICIOUS_COMMIT_MESSAGES

    if empty_diff or dummy_message:
        reason = []
        if empty_diff:
            reason.append("keine Dateien geändert (leerer Diff)")
        if dummy_message:
            reason.append(f"verdächtige Commit-Message: \"{commit_msg}\"")
        reason_str = ", ".join(reason)
        print(f"  [SANITY] Implementation ungültig: {reason_str}")
        _abort_branch(codebase_path, branch, default_branch)
        api("POST", f"/api/tickets/{ticket_id}/transition",
            {"status": "new", "actor": "claude",
             "detail": f"Implementation abgebrochen: {reason_str}. Ticket zur erneuten Bearbeitung zurückgesetzt."})
        notify(
            title=f"Implementation ungültig: {ticket['title']}",
            message=f"Ticket {ticket_id} wurde zurückgesetzt.\nGrund: {reason_str}",
            priority="high", tags="warning,ticket", ticket_id=ticket_id,
        )
        return False

    # --- Automated checks ---
    checks, all_passed = _run_checks(codebase_path, changed_files, app)
    check_summary = "ALL PASSED" if all_passed else "ISSUES FOUND"

    # Build detailed audit entry
    detail_parts = [
        f"Branch: {branch}",
        f"Commit: {commit_hash}",
        f"Commit-Message: {commit_msg}",
        f"Geänderte Dateien: {', '.join(changed_files)}",
        f"Operationen: {applied} angewendet",
    ]
    if errors:
        detail_parts.append(f"Warnings: {'; '.join(errors)}")

    detail_parts.append(f"")
    detail_parts.append(f"Automatische Checks ({check_summary}):")
    detail_parts.extend(checks if checks else ["  (keine prüfbaren Dateien)"])

    if impl.get("test_notes"):
        detail_parts.append(f"")
        detail_parts.append(f"Manuelle Verifikation:")
        detail_parts.append(f"  {impl['test_notes']}")

    # --- Auto-merge into default branch ---
    subprocess.run(["git", "-C", codebase_path, "checkout", default_branch], capture_output=True)
    merge_result = subprocess.run(
        ["git", "-C", codebase_path, "merge", branch, "--no-ff", "-m",
         f"Merge {branch}: {commit_msg}"],
        capture_output=True, text=True
    )

    if merge_result.returncode != 0:
        detail_parts.append(f"")
        detail_parts.append(f"⚠ Auto-Merge fehlgeschlagen: {merge_result.stderr.strip()[:200]}")
        detail_parts.append(f"Branch {branch} bleibt bestehen — manueller Merge nötig.")
        detail = "\n".join(detail_parts)
        api("POST", f"/api/tickets/{ticket_id}/transition",
            {"status": "testing", "actor": "claude", "detail": detail})
        print(f"  ⚠️  Merge conflict — branch {branch} needs manual merge")
        notify(
            title=f"Merge-Konflikt: {ticket['title']}",
            message=f"Branch {branch} konnte nicht automatisch gemergt werden.\nManueller Merge nötig.",
            priority="high", tags="warning,ticket", ticket_id=ticket_id,
        )
        return True  # ticket is in testing, human needs to resolve

    # Merge succeeded — delete feature branch
    subprocess.run(["git", "-C", codebase_path, "branch", "-d", branch], capture_output=True)

    merge_hash = subprocess.run(
        ["git", "-C", codebase_path, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()

    detail_parts.append(f"")
    detail_parts.append(f"Auto-Merge: {branch} → {default_branch} (merge commit: {merge_hash})")

    # Docker rebuild if codebase has compose.yml
    compose_file = os.path.join(codebase_path, "compose.yml")
    if os.path.exists(compose_file):
        detail_parts.append(f"Docker Rebuild gestartet...")
        status = _docker_rebuild(codebase_path)
        detail_parts.append(f"  {'✓' if 'OK' in status else '✗'} {status}")

    detail = "\n".join(detail_parts)
    api("POST", f"/api/tickets/{ticket_id}/transition",
        {"status": "testing", "actor": "claude", "detail": detail})

    print(f"  ✅ Implemented + merged: {applied} ops → {default_branch}")
    if errors:
        print(f"  ⚠️  Warnings: {errors}")

    notify(
        title=f"Deployed: {ticket['title']}",
        message=f"Ticket {ticket_id}\n{applied} Operationen, gemergt in {default_branch}\nLive — bitte prüfen.",
        priority="high", tags="rocket,ticket", ticket_id=ticket_id,
    )

    return True


# ============================================================================
# CLI Commands
# ============================================================================

def revert_ticket(ticket: dict, app: dict) -> bool:
    """Revert a merged ticket: git revert the merge commit."""
    codebase_path = app.get("codebase_path")
    if not codebase_path or not os.path.isdir(codebase_path):
        print(f"  [ERROR] Invalid codebase path: {codebase_path}")
        return False

    ticket_id = ticket["id"]

    # Find merge commit hash from history
    history = api("GET", f"/api/tickets/{ticket_id}/history")
    merge_hash = None
    for h in reversed(history):
        detail = h.get("detail", "")
        if "merge commit:" in detail:
            for line in detail.split("\n"):
                if "merge commit:" in line:
                    merge_hash = line.split("merge commit:")[-1].strip().rstrip(")")
                    break
            if merge_hash:
                break

    if not merge_hash:
        print(f"  [ERROR] Could not find merge commit in history")
        return False

    print(f"  Reverting merge commit {merge_hash}...")
    result = subprocess.run(
        ["git", "-C", codebase_path, "revert", "-m", "1", merge_hash, "--no-edit"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  [ERROR] Git revert failed: {result.stderr[:200]}")
        return False

    revert_hash = subprocess.run(
        ["git", "-C", codebase_path, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()

    detail = f"Rollback: merge {merge_hash} reverted → {revert_hash}"

    # Docker rebuild if needed
    compose_file = os.path.join(codebase_path, "compose.yml")
    if os.path.exists(compose_file):
        print(f"  Docker rebuild...")
        detail += "\n" + _docker_rebuild(codebase_path)

    # Clear old proposals so Claude re-analyzes fresh
    try:
        db = sqlite3.connect(ACT_DB_PATH)
        deleted = db.execute("DELETE FROM proposals WHERE ticket_id=?", (ticket_id,)).rowcount
        db.commit()
        db.close()
        if deleted:
            detail += f"\nAlte Proposals gelöscht ({deleted})"
            print(f"  Deleted {deleted} old proposal(s)")
    except Exception as e:
        print(f"  [WARN] Could not clean proposals: {e}")

    # Log the revert in history via transition cycle
    api("POST", f"/api/tickets/{ticket_id}/transition",
        {"status": "proposed", "actor": "claude", "detail": detail})
    api("POST", f"/api/tickets/{ticket_id}/transition",
        {"status": "new", "actor": "claude", "detail": "Rollback erledigt — wartet auf neue Analyse"})

    print(f"  ✅ Reverted: {merge_hash} → {revert_hash}")
    notify(
        title=f"Rollback: {ticket['title']}",
        message=f"Merge {merge_hash} wurde rückgängig gemacht.",
        tags="rewind,ticket", ticket_id=ticket_id,
    )
    return True


def cmd_revert(ticket_id: str = None):
    """Revert tickets that were rejected during testing."""
    if ticket_id:
        tickets = [api("GET", f"/api/tickets/{ticket_id}")]
    else:
        # Find tickets that went from testing → new with "Rollback angefordert"
        all_tickets = api("GET", "/api/tickets")
        tickets = []
        for t in all_tickets:
            if t.get("status") != "new":
                continue
            history = api("GET", f"/api/tickets/{t['id']}/history")
            for h in reversed(history):
                if h.get("detail", "").startswith("Rollback angefordert"):
                    # Check if revert was already done
                    already_reverted = any("Rollback: merge" in hh.get("detail", "") for hh in history)
                    if not already_reverted:
                        tickets.append(t)
                    break

    if not tickets:
        print("No tickets to revert.")
        return

    apps_cache = {a["id"]: a for a in api("GET", "/api/apps")}
    for ticket in tickets:
        app = apps_cache.get(ticket["app_id"])
        if not app:
            print(f"[SKIP] {ticket['id']}: app not found")
            continue
        print(f"[REVERT] {ticket['id']}: {ticket['title']} ({app['name']})")
        revert_ticket(ticket, app)


def cmd_status():
    """Show actionable tickets."""
    tickets = api("GET", "/api/tickets")

    buckets = {"new": [], "proposed": [], "approved": [], "implementing": [], "testing": []}
    for t in tickets:
        if t["status"] in buckets:
            buckets[t["status"]].append(t)

    print("═══ ACT Status ═══\n")
    emoji = {"new": "🆕", "proposed": "🤖", "approved": "👍", "implementing": "🔧", "testing": "🧪"}
    for status, tix in buckets.items():
        if tix:
            print(f"{emoji.get(status, '📌')} {status.upper()} ({len(tix)})")
            for t in tix:
                print(f"   {t['id']}  {t.get('app_name', '?'):20s}  {t['title'][:50]}")
            print()

    if not any(buckets.values()):
        print("No actionable tickets. 🎉")


def cmd_analyze():
    """Analyze new tickets and create proposals."""
    tickets = api("GET", "/api/tickets?status=new")
    if not tickets:
        print("No new tickets to analyze.")
        return

    apps_cache = {a["id"]: a for a in api("GET", "/api/apps")}

    for ticket in tickets:
        app = apps_cache.get(ticket["app_id"])
        if not app:
            print(f"[SKIP] {ticket['id']}: app {ticket['app_id']} not found")
            continue

        # Skip if ticket already has proposals (avoid duplicates)
        existing = api("GET", f"/api/tickets/{ticket['id']}/proposals")
        if existing:
            print(f"[SKIP] {ticket['id']}: already has {len(existing)} proposal(s)")
            continue

        print(f"[ANALYZE] {ticket['id']}: {ticket['title']} ({app['name']})")
        proposal = analyze_ticket(ticket, app)

        if proposal.get("reject"):
            reason = proposal.get("reject_reason", "Vom Triage-System abgelehnt.")
            api("POST", f"/api/tickets/{ticket['id']}/transition",
                {"status": "rejected", "actor": "claude", "detail": f"Auto-Triage: {reason}"})
            print(f"  🚫 Rejected: {reason[:80]}")
            notify(
                title=f"Auto-Rejected: {ticket['title']}",
                message=reason,
                tags="no_entry,ticket", ticket_id=ticket["id"],
            )
            continue

        api("POST", f"/api/tickets/{ticket['id']}/proposals", {
            "summary": proposal["summary"],
            "diff_preview": proposal.get("diff_preview", ""),
            "branch_name": proposal.get("branch_name", ""),
            "files_changed": proposal.get("files_changed", []),
        })
        print(f"  ✅ Proposal: {proposal['summary'][:80]}...")


def cmd_implement(ticket_id: str = None):
    """Implement approved tickets."""
    if ticket_id:
        tickets = [api("GET", f"/api/tickets/{ticket_id}")]
    else:
        tickets = api("GET", "/api/tickets?status=approved")

    if not tickets:
        print("No approved tickets to implement.")
        return

    apps_cache = {a["id"]: a for a in api("GET", "/api/apps")}

    for ticket in tickets:
        if ticket["status"] != "approved":
            print(f"[SKIP] {ticket['id']}: status is {ticket['status']}, not approved")
            continue
        app = apps_cache.get(ticket["app_id"])
        if not app:
            print(f"[SKIP] {ticket['id']}: app not found")
            continue
        print(f"[IMPLEMENT] {ticket['id']}: {ticket['title']} ({app['name']})")
        implement_ticket(ticket, app)


def cmd_auto():
    """Full cycle: revert + analyze + implement."""
    print("═══ ACT Auto Cycle ═══\n")
    print("--- Phase 1: Revert rejected implementations ---")
    cmd_revert()
    print("\n--- Phase 2: Analyze new tickets ---")
    cmd_analyze()
    print("\n--- Phase 3: Implement approved tickets ---")
    cmd_implement()
    print("\n--- Current status ---")
    cmd_status()


def main():
    parser = argparse.ArgumentParser(description="ACT-Claude Bridge (uses Claude Code CLI)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show actionable tickets")
    sub.add_parser("analyze", help="Analyze new tickets, create proposals")
    p_impl = sub.add_parser("implement", help="Implement approved tickets")
    p_impl.add_argument("ticket_id", nargs="?", help="Specific ticket ID")
    p_rev = sub.add_parser("revert", help="Revert failed implementations")
    p_rev.add_argument("ticket_id", nargs="?", help="Specific ticket ID")
    sub.add_parser("auto", help="Full cycle: revert + analyze + implement")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "analyze":
        cmd_analyze()
    elif args.command == "implement":
        cmd_implement(args.ticket_id)
    elif args.command == "revert":
        cmd_revert(args.ticket_id)
    elif args.command == "auto":
        cmd_auto()
    else:
        parser.print_help()


if __name__ == "__main__":
    import io as _io, atexit as _atexit
    _run_id = record_run_start()
    _run_buf = _io.StringIO()
    _orig_stdout = sys.stdout
    _run_ok = [True]
    _orig_excepthook = sys.excepthook
    def _excepthook(t, v, tb):
        _run_ok[0] = False
        _orig_excepthook(t, v, tb)
    sys.excepthook = _excepthook
    class _Tee:
        def write(self, s): _orig_stdout.write(s); _run_buf.write(s)
        def flush(self): _orig_stdout.flush()
        def __getattr__(self, n): return getattr(_orig_stdout, n)
    sys.stdout = _Tee()
    def _finish_run():
        sys.stdout = _orig_stdout
        record_run_finish(_run_id, 'done' if _run_ok[0] else 'error', _run_buf.getvalue())
    _atexit.register(_finish_run)
    main()
