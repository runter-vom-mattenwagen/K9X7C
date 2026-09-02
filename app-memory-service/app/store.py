"""Note storage: markdown files with YAML frontmatter are the source of truth."""
import os
import sys
import datetime, re, hashlib, pathlib, subprocess, datetime
import yaml

NOTES = pathlib.Path(os.environ.get("NOTES_DIR", "/data/notes"))
FM = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)

TYPES = ["service", "gotcha", "rule", "runbook", "location", "credential", "fact", "project"]


def parse(text):
    m = FM.match(text)
    if not m:
        return {}, text.strip()
    return (yaml.safe_load(m.group(1)) or {}), m.group(2).strip()


def render(meta, body):
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False,
                          default_flow_style=False).strip()
    return "---\n%s\n---\n\n%s\n" % (head, body.strip())


def body_hash(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "note").lower().strip())
    s = s.strip("-")[:60]
    # Cut back to the last word boundary instead of slicing mid-word --
    # otherwise a long title yields ids like "...18-tage-frist-als-mi".
    if len(s) == 60 and "-" in s:
        s = s.rsplit("-", 1)[0]
    return s.strip("-") or "note"


def path_for(note_id, scope):
    return NOTES / (scope or "misc") / ("%s.md" % note_id)


def find(note_id):
    hits = list(NOTES.rglob("%s.md" % note_id))
    return hits[0] if hits else None


def write_inplace(path, content):
    """O_TRUNC keeps the inode stable -- docker bind-mount lesson."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    flags = os.O_WRONLY | os.O_TRUNC if path.exists() else os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def load(path):
    meta, body = parse(pathlib.Path(path).read_text(encoding="utf-8"))
    if not meta.get("id"):
        meta["id"] = pathlib.Path(path).stem
    # PyYAML turns an unquoted 2026-07-16 into datetime.date, which JSONResponse
    # cannot serialise -- every hand-written note used to 500 on /api/note.
    # Notes written by store.save() are quoted and were unaffected, which is why
    # this only ever hit files edited by hand.
    for k, v in list(meta.items()):
        if isinstance(v, (datetime.date, datetime.datetime)):
            meta[k] = v.isoformat()
    return meta, body


# Which scopes exist is a configuration decision, not a code decision.
# Read from the environment so adding one is a compose.yml edit plus
# "docker compose up -d" -- no rebuild, no source change.
SCOPES = tuple(x.strip() for x in
               os.environ.get("MEMORY_SCOPES", "homelab,azure").split(",")
               if x.strip())


def all_notes(errors=None):
    """Yield (path, meta, body). Broken files land in `errors` instead of
    vanishing silently -- a note that disappears without a message is the
    worst failure mode this service has."""
    for p in sorted(NOTES.rglob("*.md")):
        try:
            meta, body = load(p)
        except Exception as e:
            msg = {"path": str(p), "error": "%s: %s" % (type(e).__name__, e)[:200]}
            print("BROKEN NOTE %(path)s -- %(error)s" % msg, file=sys.stderr, flush=True)
            if errors is not None:
                errors.append(msg)
            continue
        yield p, meta, body


def save(meta, body):
    meta = dict(meta)
    meta.setdefault("id", slugify(meta.get("title")))
    meta["updated"] = datetime.date.today().isoformat()
    old = find(meta["id"])
    target = path_for(meta["id"], meta.get("scope"))
    if old and old.resolve() != target.resolve():
        old.unlink()
    write_inplace(target, render(meta, body))
    return target


def delete(note_id):
    p = find(note_id)
    if not p:
        return False
    p.unlink()
    return True


def git(*args):
    try:
        return subprocess.run(["git", "-C", str(NOTES)] + list(args),
                              capture_output=True, text=True, timeout=20)
    except Exception:
        return None


def git_commit(msg):
    if not (NOTES / ".git").exists():
        return
    git("add", "-A")
    git("-c", "user.email=claude@claude.int", "-c", "user.name=Claude Memory",
        "commit", "-m", msg)
