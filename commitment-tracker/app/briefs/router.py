import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.briefs.models import Brief
from app.briefs.service import generate_brief, announce

router = APIRouter(prefix="/briefs", tags=["briefs"])


def _out(b):
    return {"id": b.id, "kind": b.kind, "period": b.period_key, "ai_written": bool(b.ai_written),
            "created_at": b.created_at.isoformat() if b.created_at else None, **json.loads(b.content)}


@router.get("/latest")
def latest(kind: str = "daily", db: Session = Depends(get_db)):
    b = db.query(Brief).filter_by(kind=kind).order_by(Brief.id.desc()).first()
    return _out(b) if b else None


@router.get("")
def history(kind: str = "daily", limit: int = 14, db: Session = Depends(get_db)):
    rows = db.query(Brief).filter_by(kind=kind).order_by(Brief.id.desc()).limit(limit).all()
    return [{"id": b.id, "period": b.period_key, "headline": json.loads(b.content).get("headline")} for b in rows]


@router.post("/announce")
def announce_brief(kind: str = "daily", db: Session = Depends(get_db)):
    if kind not in ("daily", "weekly"):
        raise HTTPException(400, "kind must be daily or weekly")
    return announce(db, kind)


@router.get("/{bid}")
def one(bid: int, db: Session = Depends(get_db)):
    b = db.get(Brief, bid)
    if not b:
        raise HTTPException(404)
    return _out(b)


@router.post("/generate")
def generate(kind: str = "daily", db: Session = Depends(get_db)):
    if kind not in ("daily", "weekly"):
        raise HTTPException(400, "kind must be daily or weekly")
    return _out(generate_brief(db, kind, force=True))