import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Commitment
from app.notifications.models import Notification, Reminder
from app.notifications.generator import effective_deadline

router = APIRouter(prefix="/notifications", tags=["notifications"])
TOKEN = os.getenv("COMMIQ_API_TOKEN", "")


def _auth(authorization: Optional[str] = Header(None)):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")


def _out(n, c):
    return {
        "id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
        "commitment_id": n.commitment_id, "status": n.status,
        "due_iso": c.due_date.isoformat() if c and c.due_date else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/pending", dependencies=[Depends(_auth)])
def pending(db: Session = Depends(get_db)):
    rows = (db.query(Notification)
            .filter(Notification.status == "pending", Notification.deliver_after <= datetime.now())
            .order_by(Notification.id).limit(20).all())
    return [_out(n, db.get(Commitment, n.commitment_id) if n.commitment_id else None) for n in rows]


@router.get("", dependencies=[Depends(_auth)])
def recent(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(Notification).order_by(Notification.id.desc()).limit(limit).all()
    return [_out(n, db.get(Commitment, n.commitment_id) if n.commitment_id else None) for n in rows]


@router.post("/{nid}/delivered", dependencies=[Depends(_auth)])
def delivered(nid: int, db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if not n:
        raise HTTPException(404)
    if n.status == "pending":
        n.status, n.delivered_at = "delivered", datetime.now()
        db.commit()
    return {"ok": True}


class ActionIn(BaseModel):
    action: str
    minutes: Optional[int] = None
    hours_before: Optional[float] = None
    tomorrow_9am: bool = False


@router.post("/{nid}/action", dependencies=[Depends(_auth)])
def action(nid: int, body: ActionIn, db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if not n:
        raise HTTPException(404)
    now = datetime.now()
    if body.action == "snooze":
        if body.tomorrow_9am:
            when = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            when = now + timedelta(minutes=body.minutes or 15)
        n.status, n.deliver_after, n.delivered_at = "pending", when, None
        n.action_taken = "snooze"
    elif body.action == "remind":
        c = db.get(Commitment, n.commitment_id) if n.commitment_id else None
        dl = effective_deadline(c.due_date) if c else None
        when = (dl - timedelta(hours=body.hours_before or 1)) if dl else now + timedelta(hours=1)
        if when <= now:
            when = now + timedelta(minutes=5)
        db.add(Reminder(commitment_id=n.commitment_id, remind_at=when))
        n.status, n.action_taken = "actioned", "remind"
    elif body.action in ("accept", "dismiss", "complete", "reschedule"):
        n.status = "dismissed" if body.action == "dismiss" else "actioned"
        n.action_taken = body.action
    else:
        raise HTTPException(400, "unknown action")
    db.commit()
    return {"ok": True}


@router.post("/test", dependencies=[Depends(_auth)])
def test(db: Session = Depends(get_db)):
    db.add(Notification(kind="test", title="CommiSense test",
                        body="Desktop notifications are working.",
                        dedupe_key=f"test:{datetime.now().timestamp()}"))
    db.commit()
    return {"ok": True}