"""Finds dependencies in ingested messages (email, Teams, transcripts...) after the normal extraction has run.
Never touches the extraction pipeline: it only reads sources + commitments and writes dependency rows.

  'Submit me the final presentation after getting the system architecture from James'
      -> final-presentation  depends on  James's system-architecture
         linked automatically if James's task is already tracked, otherwise offered as a suggestion
         ("Track James's task") - nothing is created without a confident match or your tap.
"""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.models import Commitment, Source
from app.dependencies.models import CommitmentDependency as Dep
from app.dependencies.models_smart import DependencyMeta, DependencySuggestion, DependencyScan
from app.dependencies import graph as G
from app.dependencies.keys import ensure_keys, resolve, tokens

AUTO_CONFIDENCE = float(os.getenv("DEP_AUTO_CONFIDENCE", "0.75"))   # at/above: link on its own
BACKFILL_DAYS = int(os.getenv("DEP_BACKFILL_DAYS", "7"))            # how far back the background scan looks
INACTIVE = ("done", "cancelled")

# Cheap pre-filter so ordinary messages never cost an AI call.
CUES = re.compile(r"\b(after|once|until|before|depend\w*|waiting|wait for|blocked|blocker|need\w* .{0,40}\bfrom\b|"
                  r"as soon as|upon|following|pending|prerequisite|can'?t (start|finish|submit|send)|"
                  r"when (he|she|they|\w+) (send|sends|share|shares|deliver|delivers|finish|finishes|give|gives)|"
                  r"input from|handoff|hand-off|hand off)\b", re.I)

PROMPT = """You find DEPENDENCIES between commitments in a message.
A depends on B means: A cannot be finished until B has been delivered by someone.

MESSAGE (sent {sent}):
\"\"\"{text}\"\"\"

COMMITMENTS ALREADY TRACKED (id | owner | task):
{cands}

Rules:
- Only report a dependency the message states or clearly implies. Do not invent any.
- "downstream_id" must be an id from the list: the commitment that has to WAIT.
- "upstream_id" is the id of the tracked commitment it waits for. If that task is not in the list, use null and
  fill "upstream_owner" (who must deliver it) and "upstream_text" (what must be delivered, short, imperative).
- Direction matters. "Send me the report after James gives the architecture" -> the report waits on James's architecture.
  "James cannot start until I send the report" -> James's task waits on my report.
- "confidence": 0 to 1. Use >= 0.8 only for explicit wording (after, once, until, depends on, waiting for).
- "evidence": the exact short phrase from the message.
Return ONLY a JSON array (maybe empty):
[{{"downstream_id": 0, "upstream_id": null, "upstream_owner": "", "upstream_text": "", "confidence": 0.0, "evidence": ""}}]"""


def _gemini(prompt):
    from app.processing.extractor import client, MODEL
    last = None
    for _ in range(2):
        try:
            r = client.models.generate_content(model=MODEL, contents=prompt)
            return (r.text or "").strip()
        except Exception as e:           # network / rate limit: caller decides whether to retry later
            last = e
    raise last


LLM = _gemini      # replaced in tests


def _parse(txt):
    txt = re.sub(r"^```(?:json)?|```$", "", (txt or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
    except ValueError:
        m = re.search(r"\[.*\]", txt, re.S)
        data = json.loads(m.group(0)) if m else []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _active(c):
    return getattr(c.status, "value", c.status) not in INACTIVE


def _edges(db):
    ids = {c.id for c in db.query(Commitment.id).all()}
    return [(d.depends_on_id, d.commitment_id) for d in db.query(Dep).all()
            if d.depends_on_id in ids and d.commitment_id in ids]


def _notify(db, cid, key, title, body):
    try:
        from app.notifications.generator import _add
        _add(db, cid, "dependency", key, title, body)
    except Exception:
        pass


def link(db, down, up, origin="manual", evidence=None, confidence=None, notify=False):
    """Create a link if it is valid. Returns (dep|None, reason)."""
    if down == up:
        return None, "A commitment cannot depend on itself"
    if db.query(Dep).filter_by(commitment_id=down, depends_on_id=up).first():
        return None, "exists"
    if G.would_create_cycle(_edges(db), up, down):
        return None, "This link would create a circular dependency"
    d = Dep(commitment_id=down, depends_on_id=up)
    db.add(d)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None, "exists"
    db.add(DependencyMeta(dep_id=d.id, origin=origin, evidence=evidence, confidence=confidence))
    db.commit()
    if notify:
        keys = ensure_keys(db)
        a, b = db.get(Commitment, down), db.get(Commitment, up)
        _notify(db, down, f"deplink:{d.id}", "Dependency found",
                f"{keys[down]} waits on {keys[up]} ({b.owner}).\n“{(evidence or '')[:90]}”")
    return d, "ok"


def _owner_match(a, b):
    return bool(set(tokens(a)) & set(tokens(b)))


def _suggest(db, down, up_id, owner, text, evidence, conf, source_id):
    dup = db.query(DependencySuggestion).filter(DependencySuggestion.commitment_id == down).all()
    norm = set(tokens(text))
    for s in dup:
        if (up_id and s.upstream_commitment_id == up_id) or (norm and set(tokens(s.upstream_text)) == norm
                                                             and _owner_match(s.upstream_owner, owner)):
            return None
    s = DependencySuggestion(commitment_id=down, upstream_commitment_id=up_id, upstream_owner=owner,
                             upstream_text=text, evidence=evidence, confidence=conf, source_id=source_id)
    db.add(s)
    db.commit()
    keys = ensure_keys(db)
    who = owner or "someone"
    what = keys.get(up_id) if up_id else (text or "a deliverable")
    _notify(db, down, f"depsug:{s.id}", "Possible dependency",
            f"{keys.get(down)} may wait on {who}: {what}.\n“{(evidence or '')[:90]}”")
    return s


def detect_source(db, source_id, force=False):
    """Check one ingested message. Returns {'linked': n, 'suggested': n, 'skipped': reason|None}."""
    out = {"linked": 0, "suggested": 0, "skipped": None}
    if not force and db.get(DependencyScan, source_id):
        out["skipped"] = "already scanned"
        return out
    src = db.get(Source, source_id)
    mine = db.query(Commitment).filter_by(source_id=source_id).all() if src else []
    if not mine:
        out["skipped"] = "no commitments"
    elif not CUES.search(src.raw_text or ""):
        out["skipped"] = "no dependency wording"
    if out["skipped"]:
        _mark(db, source_id, 0)
        return out
    keys = ensure_keys(db)
    pool = {c.id: c for c in db.query(Commitment).all() if _active(c)}
    pool.update({c.id: c for c in mine})
    ids = sorted(pool, key=lambda i: (pool[i].source_id != source_id, -i))[:80]
    cands = "\n".join(f"{i} | {pool[i].owner} | {(pool[i].description or '')[:100]}" for i in ids)
    prompt = PROMPT.format(sent=src.sent_at or "unknown", text=(src.raw_text or "")[:6000], cands=cands)
    found = _parse(LLM(prompt))                  # may raise: then the source stays unscanned and is retried
    mine_ids = {c.id for c in mine}
    for f in found:
        try:
            down, conf = int(f.get("downstream_id")), float(f.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        if down not in pool:
            continue
        ev, owner, text = (f.get("evidence") or "")[:300], (f.get("upstream_owner") or "").strip(), (f.get("upstream_text") or "").strip()
        up = f.get("upstream_id")
        up = int(up) if isinstance(up, (int, float, str)) and str(up).isdigit() and int(up) in pool else None
        if up == down:                           # a task cannot wait on itself
            up = None
        if up is None and text:                  # the model gave words, not an id: try our own matching
            rid, _ = resolve(db, f"{owner} {text}", prefer_owner=owner)
            if rid and rid in pool and rid != down and (not owner or _owner_match(owner, pool[rid].owner)):
                up, conf = rid, min(conf, 0.8)
        if up is not None and up != down and conf >= AUTO_CONFIDENCE and (down in mine_ids or up in mine_ids):
            d, why = link(db, down, up, "auto", ev, conf, notify=True)
            if d:
                out["linked"] += 1
            elif why != "exists":
                if _suggest(db, down, up, pool[up].owner, pool[up].description, ev, conf, source_id):
                    out["suggested"] += 1
            continue
        if up is not None or owner or text:
            if _suggest(db, down, up, owner or (pool[up].owner if up else None), text or (pool[up].description if up else None),
                        ev, conf, source_id):
                out["suggested"] += 1
    _mark(db, source_id, out["linked"] + out["suggested"])
    return out


def _mark(db, source_id, found):
    row = db.get(DependencyScan, source_id)
    if row:
        row.scanned_at, row.found = datetime.now(), found
    else:
        db.add(DependencyScan(source_id=source_id, found=found))
    db.commit()


def pending_sources(db, limit=5):
    since = datetime.utcnow() - timedelta(days=BACKFILL_DAYS)
    done = {r[0] for r in db.query(DependencyScan.source_id).all()}
    have = {r[0] for r in db.query(Commitment.source_id).filter(Commitment.source_id.isnot(None)).distinct().all()}
    rows = (db.query(Source.id).filter(Source.id.in_(have or {0}), Source.ingested_at >= since)
            .order_by(Source.id.desc()).all())
    return [r[0] for r in rows if r[0] not in done][:limit]


_fails = {}          # source_id -> failed attempts; after 3 we stop retrying until the app restarts


def scan_new(db, limit=5):
    total = {"linked": 0, "suggested": 0, "scanned": 0, "errors": 0}
    for sid in pending_sources(db, limit):
        if _fails.get(sid, 0) >= 3:
            continue
        try:
            r = detect_source(db, sid)
            total["scanned"] += 1
            total["linked"] += r["linked"]
            total["suggested"] += r["suggested"]
        except Exception as e:
            db.rollback()
            _fails[sid] = _fails.get(sid, 0) + 1
            total["errors"] += 1
            print(f"Dependency scan skipped source {sid}: {e}")
    return total


async def auto_loop():
    from app.database import SessionLocal
    await asyncio.sleep(20)
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(scan_new, db)
            finally:
                db.close()
        except Exception as e:
            print(f"Dependency auto-scan error: {e}")
        await asyncio.sleep(60)
