"""Derived index: SQLite FTS5 (BM25) + numpy cosine. Fully rebuildable from files.

No sqlite-vec: at a few hundred notes a 768-dim dot product over the whole
corpus is sub-millisecond, and dropping the extension removes a hard
dependency on how the base image compiled SQLite.
"""
import os, re, json, sqlite3, threading
import numpy as np
from fastembed import TextEmbedding

import store

DB_PATH = os.environ.get("DB_PATH", "/data/index.db")
MODEL_NAME = os.environ.get("EMBED_MODEL", "jinaai/jina-embeddings-v2-base-de")
DIM = 768
RRF_K = 12

_model = None
_lock = threading.Lock()
_cache = {"ids": None, "mat": None}


def model():
    global _model
    with _lock:
        if _model is None:
            _model = TextEmbedding(MODEL_NAME)
    return _model


def embed(text, kind="passage"):
    """jina-v2-de is symmetric -- no query:/passage: prefixes (that is an e5 thing)."""
    vec = np.asarray(next(iter(model().embed([text]))), dtype=np.float32)
    n = np.linalg.norm(vec)
    return vec / n if n else vec


def embed_text(meta, body):
    """Title and summary carry the condensed meaning -- weight them in."""
    return "\n".join([str(meta.get("title", "")), str(meta.get("summary", "")),
                      str(meta.get("title", "")), body])[:4000]


def connect():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS notes (
      id TEXT PRIMARY KEY, path TEXT, title TEXT, summary TEXT,
      type TEXT, scope TEXT, tags TEXT, updated TEXT, hash TEXT
    );
    CREATE TABLE IF NOT EXISTS notes_vec (id TEXT PRIMARY KEY, embedding BLOB);
    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
      id UNINDEXED, title, summary, body, tags, tokenize='unicode61'
    );
    """)
    cur = db.execute("SELECT v FROM meta WHERE k='model'").fetchone()
    if cur is None:
        db.execute("INSERT INTO meta VALUES('model',?)", (MODEL_NAME,))
    elif cur["v"] != MODEL_NAME:
        # Vectors from a different model live in a different coordinate space.
        db.executescript("DELETE FROM notes; DELETE FROM notes_fts; DELETE FROM notes_vec;")
        db.execute("UPDATE meta SET v=? WHERE k='model'", (MODEL_NAME,))
    db.commit()
    _invalidate()


def _invalidate():
    _cache["ids"], _cache["mat"] = None, None


def _matrix(db):
    if _cache["ids"] is None:
        rows = db.execute("SELECT id, embedding FROM notes_vec ORDER BY id").fetchall()
        _cache["ids"] = [r["id"] for r in rows]
        _cache["mat"] = (np.vstack([np.frombuffer(r["embedding"], dtype=np.float32)
                                    for r in rows])
                         if rows else np.zeros((0, DIM), dtype=np.float32))
    return _cache["ids"], _cache["mat"]


def index_note(db, path, meta, body, force=False):
    # Hash covers frontmatter AND body. Hashing only the body meant that
    # changing title/summary/tags/type/scope never triggered a re-index --
    # the note kept its stale row and, for the title, fell back to the id.
    nid = meta["id"]
    h = store.body_hash(json.dumps(
        {k: meta.get(k) for k in ("title", "summary", "type", "scope",
                                  "tags", "updated")},
        sort_keys=True, ensure_ascii=False, default=str) + "\n" + body)
    row = db.execute("SELECT hash FROM notes WHERE id=?", (nid,)).fetchone()
    if row and row["hash"] == h and not force:
        return False
    tags = " ".join(meta.get("tags") or [])
    for t in ("notes", "notes_fts", "notes_vec"):
        db.execute("DELETE FROM %s WHERE id=?" % t, (nid,))
    db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?)",
               (nid, str(path), meta.get("title", nid), meta.get("summary", ""),
                meta.get("type", "fact"), meta.get("scope", "misc"),
                json.dumps(meta.get("tags") or []), str(meta.get("updated", "")), h))
    db.execute("INSERT INTO notes_fts VALUES(?,?,?,?,?)",
               (nid, meta.get("title", ""), meta.get("summary", ""), body, tags))
    db.execute("INSERT INTO notes_vec VALUES(?,?)",
               (nid, embed(embed_text(meta, body)).tobytes()))
    db.commit()
    _invalidate()
    return True


def reindex(db, force=False):
    seen, changed = set(), 0
    broken, misfiled = [], []
    for path, meta, body in store.all_notes(errors=broken):
        seen.add(meta["id"])
        # store.save() writes to notes/<scope>/. A file whose directory does not
        # match its scope would get a SECOND file with the same id on next
        # upsert -- find() uses rglob, so nothing breaks until it silently does.
        if path.parent.name != meta.get("scope"):
            misfiled.append({"id": meta["id"], "scope": meta.get("scope"),
                             "dir": path.parent.name})
        if index_note(db, path, meta, body, force):
            changed += 1
    stale = [r["id"] for r in db.execute("SELECT id FROM notes") if r["id"] not in seen]
    for nid in stale:
        for t in ("notes", "notes_fts", "notes_vec"):
            db.execute("DELETE FROM %s WHERE id=?" % t, (nid,))
    db.commit()
    _invalidate()
    out = {"indexed": len(seen), "changed": changed, "removed": len(stale)}
    if broken:
        out["broken"] = broken
    if misfiled:
        out["misfiled"] = misfiled
    return out


def similar(db, vec, k=5):
    """Nearest neighbours for a raw vector -- used by the dedup check."""
    ids, mat = _matrix(db)
    if not ids:
        return []
    sims = mat @ vec
    order = np.argsort(-sims)[:k]
    return [(ids[i], float(sims[i])) for i in order]


# Stopwords are poison for an OR-joined FTS query: they match every document
# and hand BM25 a rank-1 hit that has nothing to do with the question.
STOP = set("""
aber alle als am an auf aus bei bin bis bist da damit dann das dass dem den der
des die dies diese dieser doch dort du ein eine einem einen einer eines er es
euch fuer für hab habe haben hat hatte hier ich ihr ihre im in ist ja kann
kein keine man mehr mein mit muss nach nicht noch nun nur ob oder ohne sein
seine sich sie sind so soll ueber über um und uns unter vom von vor waere wann
war was wenn wer wie wieder will wir wird wo zu zum zur
welche welcher welches meine deine seiner ihrer jeder jede jedes etwas gibt gibts hoch viele viel gut gute guten schon immer
a about all an and any are as at be been but by can do does for from get has
have how i if in into is it its me my no not of on or should so than that the
their them then there these they this to use was what when where which who why
will with you your
""".split())


def _fts_expr(q):
    toks = [t for t in re.findall(r"[\w./_-]{2,}", q, re.UNICODE)
            if t.lower() not in STOP]
    # Identifiers (paths, dots, underscores) are the whole point of the lexical
    # leg -- never drop them, even if short.
    if not toks:
        return None
    return " OR ".join('"%s"' % t.replace('"', "") for t in toks)


# Relevance floor. Without it the search always returns the nearest neighbours,
# however far away -- asking who in the family sings like Caruso then confidently
# names someone. Measured on 6 identifier queries, 3 real questions and 6
# nonsense queries:
#
#   real questions   cos 0.42..0.62
#   identifiers      cos 0.15..0.45   but bm25 -2.7..-6.6
#   nonsense         cos 0.01..0.28   bm25 none or > -3.2
#
# Cosine alone would kill identifier lookups (RRF_K scores cos 0.181), BM25
# alone lets "welches auto soll ich kaufen" through at -3.163 because the corpus
# tokenises auto-merge into "auto". Only both together separate the sets.
MIN_COSINE = 0.35        # semantic hit on its own merit
LEX_BM25 = -2.5          # strong lexical hit (more negative is better in FTS5)
LEX_MIN_COSINE = 0.12    # ... but still needs a trace of topical relation


def passes_floor(h):
    cos = h.get("cosine") or 0.0
    if cos >= MIN_COSINE:
        return True
    b = h.get("bm25")
    return b is not None and b <= LEX_BM25 and cos >= LEX_MIN_COSINE


def search(db, q, scope=None, type_=None, k=8, pool=40):
    where, args = [], []
    if scope:
        where.append("scope=?"); args.append(scope)
    if type_:
        where.append("type=?"); args.append(type_)
    allow = None
    if where:
        allow = {r["id"] for r in db.execute(
            "SELECT id FROM notes WHERE " + " AND ".join(where), args)}

    lex = {}
    expr = _fts_expr(q)
    if expr:
        rows = db.execute("SELECT id, bm25(notes_fts) s FROM notes_fts "
                          "WHERE notes_fts MATCH ? ORDER BY s LIMIT ?",
                          (expr, pool)).fetchall()
        lex = {r["id"]: (i, r["s"]) for i, r in enumerate(rows)}

    sem = {nid: (i, cos) for i, (nid, cos)
           in enumerate(similar(db, embed(q, "query"), pool))}

    fused = {}
    for nid, (rank, _) in lex.items():
        fused[nid] = fused.get(nid, 0) + 1.0 / (RRF_K + rank + 1)
    for nid, (rank, _) in sem.items():
        fused[nid] = fused.get(nid, 0) + 1.0 / (RRF_K + rank + 1)
    if allow is not None:
        fused = {n: s for n, s in fused.items() if n in allow}

    out = []
    for nid, score in sorted(fused.items(), key=lambda x: -x[1])[:k]:
        r = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        if not r:
            continue
        out.append({
            "id": nid, "title": r["title"], "summary": r["summary"],
            "type": r["type"], "scope": r["scope"], "tags": json.loads(r["tags"]),
            "updated": r["updated"], "score": round(score, 5),
            "bm25": round(lex[nid][1], 3) if nid in lex else None,
            "bm25_rank": lex[nid][0] + 1 if nid in lex else None,
            "cosine": round(sem[nid][1], 4) if nid in sem else None,
            "vec_rank": sem[nid][0] + 1 if nid in sem else None,
        })
    kept = [h for h in out if passes_floor(h)]
    if len(kept) != len(out):
        print("relevance floor dropped %d of %d for %r"
              % (len(out) - len(kept), len(out), q[:60]), flush=True)
    return kept
