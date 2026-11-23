from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Commitment
from app.dependencies.models import CommitmentDependency as Dep
from app.dependencies import graph as G

router = APIRouter(prefix="/dependencies", tags=["dependencies"])
INACTIVE = ("done", "cancelled")


def _st(c):
    return getattr(c.status, "value", c.status)


def _load(db):
    cs = {c.id: c for c in db.query(Commitment).all()}
    deps = db.query(Dep).all()
    edges = [(d.depends_on_id, d.commitment_id) for d in deps
             if d.depends_on_id in cs and d.commitment_id in cs]
    return cs, deps, edges


def build_graph(db):
    cs, deps, all_edges = _load(db)
    active = {i for i, c in cs.items() if _st(c) not in INACTIVE}
    # keep a node if active, or if it is linked to an active node (so context isn't lost)
    linked = {x for e in all_edges for x in e if e[0] in active or e[1] in active}
    nodes = sorted(active | linked)
    edges = [e for e in all_edges if e[0] in nodes and e[1] in nodes]
    due = {i: G.effective_deadline(cs[i].due_date) for i in nodes}
    act_nodes = [n for n in nodes if n in active]
    act_edges = [e for e in edges if e[0] in active and e[1] in active]
    cycles = G.find_cycles(act_nodes, act_edges)
    in_cycle = {n for c in cycles for n in c}
    crit = G.critical_path(act_nodes, act_edges, due)
    rep = G.delay_report(act_edges, due)
    delayed = {a for r in rep.values() for a in r["affected"]}
    down = G.adjacency(act_edges)
    dep_ids = {(d.depends_on_id, d.commitment_id): d.id for d in deps}
    return {
        "nodes": [{
            "id": i, "label": cs[i].description[:80], "owner": cs[i].owner,
            "due": cs[i].due_date.isoformat() if cs[i].due_date else None,
            "status": _st(cs[i]), "priority": cs[i].priority_score,
            "in_cycle": i in in_cycle, "critical": i in crit,
            "delayed_by_upstream": i in delayed,
            "downstream_count": len(G.reachable(down, i)),
        } for i in nodes],
        "edges": [{"id": dep_ids.get(e), "from": e[0], "to": e[1],
                   "critical": e[0] in crit and e[1] in crit and crit.index(e[1]) == crit.index(e[0]) + 1}
                  for e in edges],
        "cycles": cycles,
        "critical_path": crit,
        "delays": [{"upstream": u, "reason": r["reason"], "affected": r["affected"]} for u, r in rep.items()],
    }


@router.get("/graph")
def graph(db: Session = Depends(get_db)):
    return build_graph(db)


class DepIn(BaseModel):
    commitment_id: int      # the one that waits
    depends_on_id: int      # the one it waits for


@router.post("")
def add(body: DepIn, db: Session = Depends(get_db)):
    cs, _, edges = _load(db)
    if body.commitment_id not in cs or body.depends_on_id not in cs:
        raise HTTPException(404, "commitment not found")
    if G.would_create_cycle(edges, body.depends_on_id, body.commitment_id):
        raise HTTPException(409, "This link would create a circular dependency")
    if db.query(Dep).filter_by(commitment_id=body.commitment_id, depends_on_id=body.depends_on_id).first():
        raise HTTPException(409, "Dependency already exists")
    d = Dep(commitment_id=body.commitment_id, depends_on_id=body.depends_on_id)
    db.add(d); db.commit()
    return {"id": d.id}


@router.delete("/{dep_id}")
def remove(dep_id: int, db: Session = Depends(get_db)):
    d = db.get(Dep, dep_id)
    if not d:
        raise HTTPException(404)
    db.delete(d); db.commit()
    return {"ok": True}


@router.get("/impact/{cid}")
def impact(cid: int, db: Session = Depends(get_db)):
    cs, _, edges = _load(db)
    ids = sorted(G.reachable(G.adjacency(edges), cid))
    return [{"id": i, "owner": cs[i].owner, "label": cs[i].description[:80],
             "due": cs[i].due_date.isoformat() if cs[i].due_date else None, "status": _st(cs[i])}
            for i in ids]


@router.post("/cascade/run-now")
def run_now(db: Session = Depends(get_db)):
    return {"alerts_created": cascade_check(db)}


def cascade_check(db) -> int:
    """Create cascading-delay notifications (uses the notifications table from Task 1)."""
    try:
        from app.notifications.models import Notification
    except Exception:
        return 0
    cs, _, edges = _load(db)
    active = {i for i, c in cs.items() if _st(c) not in INACTIVE}
    act_edges = [e for e in edges if e[0] in active and e[1] in active]
    due = {i: G.effective_deadline(cs[i].due_date) for i in active}
    made = 0
    for u, r in G.delay_report(act_edges, due).items():
        aff = [a for a in r["affected"] if a in active]
        if not aff:
            continue
        key = f"cascade:{u}:{cs[u].due_date}:{len(aff)}"
        if db.query(Notification).filter_by(dedupe_key=key).first():
            continue
        names = "; ".join(f"{cs[a].description[:35]} ({cs[a].owner})" for a in aff[:4])
        more = f" +{len(aff) - 4} more" if len(aff) > 4 else ""
        owners = sorted({cs[a].owner for a in aff if cs[a].owner})
        db.add(Notification(
            commitment_id=u, kind="cascade", dedupe_key=key,
            title=f"{cs[u].owner}'s deliverable is delayed",
            body=f"{cs[u].description[:60]}\n{len(aff)} downstream affected: {names}{more}. Notify: {', '.join(owners)}"))
        db.commit(); made += 1
    return made


async def cascade_loop():
    import asyncio
    from app.database import SessionLocal
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(cascade_check, db)
            finally:
                db.close()
        except Exception as e:
            print(f"Cascade check error: {e}")
        await asyncio.sleep(120)