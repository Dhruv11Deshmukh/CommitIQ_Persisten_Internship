"""Smart dependency API: descriptive keys, link by name, auto-detected suggestions, 'who am I waiting on'."""
import os
import re
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Commitment
from app.dependencies.models import CommitmentDependency as Dep
from app.dependencies.models_smart import CommitmentKey, DependencyMeta, DependencySuggestion
from app.dependencies import graph as G
from app.dependencies import auto, reminders
from app.dependencies.keys import ensure_keys, resolve, card, slugify, tokens
from app.dependencies.router import _load

router = APIRouter(prefix="/dependencies", tags=["dependencies"])
INACTIVE = ("done", "cancelled")


def _st(c):
    return getattr(c.status, "value", c.status)


def _one(db, ref, field, prefer_owner=None):
    cid, cands = resolve(db, ref, prefer_owner)
    if cid:
        return cid
    if cands:
        raise HTTPException(409, {"reason": "ambiguous", "which": field, "ref": ref, "candidates": cands})
    raise HTTPException(404, {"reason": "not_found", "which": field, "ref": ref})


# ---------- keys ----------
@router.get("/keys")
def list_keys(all: bool = False, db: Session = Depends(get_db)):
    keys = ensure_keys(db)
    rows = [c for c in db.query(Commitment).order_by(Commitment.id.desc()).all() if all or _st(c) not in INACTIVE]
    return [card(c, keys[c.id]) for c in rows]


class KeyIn(BaseModel):
    key: str


@router.patch("/keys/{cid}")
def rename_key(cid: int, body: KeyIn, db: Session = Depends(get_db)):
    ensure_keys(db)
    row = db.query(CommitmentKey).filter_by(commitment_id=cid).first()
    new = slugify(body.key)
    if not row:
        raise HTTPException(404, "commitment not found")
    if len(new) < 2:
        raise HTTPException(422, "Key must have at least 2 letters or digits")
    if db.query(CommitmentKey).filter(CommitmentKey.key == new, CommitmentKey.commitment_id != cid).first():
        raise HTTPException(409, "That key is already used by another commitment")
    row.key = new
    db.commit()
    return {"id": cid, "key": new}


@router.get("/resolve")
def resolve_ref(ref: str, db: Session = Depends(get_db)):
    cid, cands = resolve(db, ref)
    keys = ensure_keys(db)
    c = db.get(Commitment, cid) if cid else None
    return {"match": card(c, keys[cid]) if c else None, "candidates": cands}


# ---------- link / unlink by key ----------
class LinkIn(BaseModel):
    task: str           # the commitment that waits (key, #id or words)
    depends_on: str     # the one it waits for


@router.post("/link")
def link_by_name(body: LinkIn, db: Session = Depends(get_db)):
    down = _one(db, body.task, "task")
    up = _one(db, body.depends_on, "depends_on")
    d, why = auto.link(db, down, up, "manual")
    if not d:
        raise HTTPException(409, "Those two are already linked" if why == "exists" else why)
    keys = ensure_keys(db)
    return {"id": d.id, "task": card(db.get(Commitment, down), keys[down]),
            "depends_on": card(db.get(Commitment, up), keys[up])}


@router.post("/unlink")
def unlink_by_name(body: LinkIn, db: Session = Depends(get_db)):
    down = _one(db, body.task, "task")
    up = _one(db, body.depends_on, "depends_on")
    d = db.query(Dep).filter_by(commitment_id=down, depends_on_id=up).first()
    if not d:
        raise HTTPException(404, {"reason": "no_link", "which": "task", "ref": body.task})
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.get("/meta")
def meta(db: Session = Depends(get_db)):
    return {m.dep_id: {"origin": m.origin, "evidence": m.evidence, "confidence": m.confidence}
            for m in db.query(DependencyMeta).all()}


@router.get("/links")
def links(db: Session = Depends(get_db)):
    """Every link between non-closed commitments, with keys, origin and the sentence it came from."""
    keys = ensure_keys(db)
    cs, deps, _ = _load(db)
    m = {x.dep_id: x for x in db.query(DependencyMeta).all()}
    out = []
    for d in deps:
        a, b = cs.get(d.commitment_id), cs.get(d.depends_on_id)
        if not a or not b or (_st(a) in INACTIVE and _st(b) in INACTIVE):
            continue
        x = m.get(d.id)
        out.append({"id": d.id, "task": card(a, keys[a.id]), "depends_on": card(b, keys[b.id]),
                    "origin": x.origin if x else "manual", "evidence": x.evidence if x else None,
                    "confidence": x.confidence if x else None})
    return out


# ---------- what does X depend on / who is waiting on a person ----------
@router.get("/of")
def of(ref: str, db: Session = Depends(get_db)):
    cid = _one(db, ref, "task")
    keys = ensure_keys(db)
    cs, _, edges = _load(db)
    ups = [u for u, d in edges if d == cid]
    downs = [d for u, d in edges if u == cid]
    blocked = G.reachable(G.adjacency(edges), cid)
    pend = db.query(DependencySuggestion).filter_by(commitment_id=cid, status="pending").all()
    return {"task": card(cs[cid], keys[cid]),
            "depends_on": [card(cs[i], keys[i]) for i in ups],
            "blocks": [card(cs[i], keys[i]) for i in downs],
            "blocks_total": len(blocked),
            "waiting_for": [{"id": s.id, "owner": s.upstream_owner, "text": s.upstream_text} for s in pend]}


def _person_match(name, owner, email=None):
    want = {t for t in tokens(name) if len(t) >= 2}
    have = set(tokens(owner)) | set(tokens((email or "").split("@")[0].replace(".", " ")))
    return bool(want & have)


def _is_mine(c, mine):
    """mine=True narrows to the signed-in owner (OWNER_NAME in .env); if it isn't set, nothing is filtered."""
    me = os.getenv("OWNER_NAME", "").strip()
    return not (mine and me) or _person_match(me, c.owner, c.owner_email)


@router.get("/who")
def who(person: str, mine: bool = False, db: Session = Depends(get_db)):
    """'Do my commitments depend on James?'  -> everything waiting on James's tasks, plus untracked waits."""
    keys = ensure_keys(db)
    cs, _, edges = _load(db)
    act = {i for i, c in cs.items() if _st(c) not in INACTIVE}
    theirs = {i for i in act if _person_match(person, cs[i].owner, cs[i].owner_email)}
    down = G.adjacency([e for e in edges if e[0] in act and e[1] in act])
    waiting, seen = [], set()
    for u in sorted(theirs):
        for d in sorted(G.reachable(down, u)):
            if d in act and d not in theirs and (d, u) not in seen and _is_mine(cs[d], mine):
                seen.add((d, u))
                direct = (u, d) in edges
                waiting.append({"task": card(cs[d], keys[d]), "via": card(cs[u], keys[u]), "direct": direct})
    pend = [s for s in db.query(DependencySuggestion).filter_by(status="pending").all()
            if _person_match(person, s.upstream_owner or "") and cs.get(s.commitment_id) and s.commitment_id in act
            and _is_mine(cs[s.commitment_id], mine)]
    untracked = [{"id": s.id, "task": card(cs[s.commitment_id], keys[s.commitment_id]),
                  "owner": s.upstream_owner, "text": s.upstream_text, "evidence": s.evidence} for s in pend]
    owes_them = []
    for i in sorted(theirs):                      # what the person is themselves waiting on
        for u, d in edges:
            if d == i and u in act and u not in theirs:
                owes_them.append({"task": card(cs[i], keys[i]), "waits_on": card(cs[u], keys[u])})
    n = len({w["task"]["id"] for w in waiting} | {u["task"]["id"] for u in untracked})
    name = person.strip().title()
    if n:
        summary = f"Yes. {n} commitment{'s' if n != 1 else ''} depend{'s' if n == 1 else ''} on {name}."
    elif theirs:
        summary = f"No. Nothing you're tracking waits on {name}."
    else:
        summary = f"I don't see any open commitments owned by {name}."
    return {"person": name, "summary": summary, "mine": bool(mine and os.getenv("OWNER_NAME", "").strip()), "waiting": waiting, "untracked": untracked,
            "person_waits_on": owes_them, "person_tasks": len(theirs)}


# ---------- suggestions found in messages ----------
def _sug_out(db, s, keys, cs):
    d, u = cs.get(s.commitment_id), cs.get(s.upstream_commitment_id) if s.upstream_commitment_id else None
    return {"id": s.id, "task": card(d, keys[d.id]) if d else None,
            "upstream": card(u, keys[u.id]) if u else None,
            "owner": s.upstream_owner, "text": s.upstream_text, "evidence": s.evidence,
            "confidence": s.confidence}


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db)):
    keys = ensure_keys(db)
    cs = {c.id: c for c in db.query(Commitment).all()}
    rows = [s for s in db.query(DependencySuggestion).filter_by(status="pending").order_by(DependencySuggestion.id.desc()).all()
            if s.commitment_id in cs and _st(cs[s.commitment_id]) not in INACTIVE]
    return [_sug_out(db, s, keys, cs) for s in rows]


class UpIn(BaseModel):
    upstream: Optional[str] = None      # optional: pick a different upstream (key / words)


def _sug(db, sid):
    s = db.get(DependencySuggestion, sid)
    if not s or s.status != "pending":
        raise HTTPException(404, "suggestion not found or already handled")
    return s


@router.post("/suggestions/{sid}/link")
def sug_link(sid: int, body: UpIn = UpIn(), db: Session = Depends(get_db)):
    s = _sug(db, sid)
    up = _one(db, body.upstream, "upstream", s.upstream_owner) if body.upstream else s.upstream_commitment_id
    if not up:
        raise HTTPException(422, {"reason": "no_candidate", "message": "No matching commitment is tracked yet. Use Track instead."})
    d, why = auto.link(db, s.commitment_id, up, "auto", s.evidence, s.confidence)
    if not d and why != "exists":
        raise HTTPException(409, why)
    s.status = "linked"
    db.commit()
    return {"ok": True, "dep_id": d.id if d else None}


@router.post("/suggestions/{sid}/track")
def sug_track(sid: int, db: Session = Depends(get_db)):
    """Create the missing upstream commitment (owned by the person named) and link to it."""
    s = _sug(db, sid)
    down = db.get(Commitment, s.commitment_id)
    if not down or not (s.upstream_text or "").strip():
        raise HTTPException(422, "Nothing to track")
    due = None
    if down.due_date:
        due = down.due_date - timedelta(days=1)
        if due < datetime.now():
            due = down.due_date
    c = Commitment(source_id=s.source_id, owner=(s.upstream_owner or "Unknown").strip(),
                   description=s.upstream_text.strip(), due_date=due, beneficiary=down.owner)
    db.add(c)
    db.commit()
    ensure_keys(db)
    d, why = auto.link(db, down.id, c.id, "auto", s.evidence, s.confidence)
    s.status, s.upstream_commitment_id = "tracked", c.id
    db.commit()
    keys = ensure_keys(db)
    return {"ok": True, "created": card(c, keys[c.id]), "dep_id": d.id if d else None}


@router.post("/suggestions/{sid}/dismiss")
def sug_dismiss(sid: int, db: Session = Depends(get_db)):
    s = _sug(db, sid)
    s.status = "dismissed"
    db.commit()
    return {"ok": True}


# ---------- scan messages now ----------
class ScanIn(BaseModel):
    source_id: Optional[int] = None
    limit: int = 5
    force: bool = False


@router.post("/auto/scan")
def scan_now(body: ScanIn = ScanIn(), db: Session = Depends(get_db)):
    if body.source_id:
        try:
            return auto.detect_source(db, body.source_id, force=body.force)
        except Exception as e:
            db.rollback()
            raise HTTPException(503, f"Could not read that message right now: {e}")
    return auto.scan_new(db, max(1, min(body.limit, 20)))


# ---------- 48-hour reminder drafts ----------
@router.post("/reminders/run-now")
def reminders_now(db: Session = Depends(get_db)):
    from app.ingestion import gmail_connector as gc
    made = reminders.find_and_draft(db, gc.GmailConnector(max_results=1))
    return {"drafted": len(made), "details": made}


@router.get("/reminders")
def reminders_list(db: Session = Depends(get_db)):
    from app.dependencies.models_smart import DependencyReminder
    keys = ensure_keys(db)
    cs = {c.id: c for c in db.query(Commitment).all()}
    return [{"id": r.id, "task": card(cs[r.commitment_id], keys[r.commitment_id]) if r.commitment_id in cs else None,
             "to": r.to_email, "waiting": [int(x) for x in (r.waiting_ids or "").split(",") if x],
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in db.query(DependencyReminder).order_by(DependencyReminder.id.desc()).limit(50).all()]
