"""Chat-style notification assistant. Serves the popup UI and understands typed replies.
Additive: reads commitments, and (via the browser) acts through the existing endpoints."""
import os, re, time, json
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Commitment
from app.notifications.models import Notification

router = APIRouter(prefix="/assistant", tags=["assistant"])
HERE = os.path.dirname(os.path.abspath(__file__))
_beat = [0.0]
_main = {"hidden": False, "seq": 0}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
HELP = ("Tap an alert, then type:\n"
        "• done  • dismiss\n"
        "• reschedule to friday  • push 3 days\n• remind me 1 day before\n"
        "• schedule a meeting\n"
        "Dependencies (use names, not numbers):\n"
        "• final-presentation depends on system-architecture\n"
        "• depends on james architecture  (for the tapped alert)\n"
        "• what depends on system-architecture?\n"
        "• do my commitments depend on James?\n"
        "• show my drafts  (opens Gmail drafts)\n"
        "Or ask: what's due today? · what's overdue?")


def _st(c):
    return getattr(c.status, "value", c.status)


@router.get("/ui")
def ui():
    return FileResponse(os.path.join(HERE, "assistant.html"), media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@router.post("/heartbeat")
def heartbeat():
    _beat[0] = time.time()
    return {"ok": True}


@router.get("/open")
def is_open():
    return {"open": time.time() - _beat[0] < 12}


class VisIn(BaseModel):
    hidden: bool


@router.post("/main-visibility")
def main_visibility(body: VisIn):
    """Called by the dashboard when its window is minimised / hidden or shown again."""
    _main["hidden"] = body.hidden
    if body.hidden:
        _main["seq"] += 1
    return {"ok": True}


@router.get("/state")
def state():
    return {"open": time.time() - _beat[0] < 12, "hidden": _main["hidden"], "hidden_seq": _main["seq"]}


@router.get("/snapshot")
def snapshot(db: Session = Depends(get_db)):
    parts = [x for x in (_simple_answer(db, "overdue"), _simple_answer(db, "today")) if x and "nothing" not in x]
    return {"text": "\n\n".join(parts) if parts else "You're all caught up. Nothing is overdue or due today. 🎉"}


@router.get("/today")
def today(db: Session = Depends(get_db)):
    """Compact lists for the chat cards: overdue (earlier days), due today, and the next 7 days."""
    now = datetime.now()
    d0 = now.date()

    def deadline(c):
        d = c.due_date
        return d + timedelta(hours=23, minutes=59) if (d.hour, d.minute, d.second) == (0, 0, 0) else d

    def item(c):
        return {"id": c.id, "owner": c.owner, "text": (c.description or "")[:80],
                "due_iso": c.due_date.isoformat(),
                "score": round(c.priority_score or 0), "why": (c.priority_reason or "")[:90]}

    rows = [c for c in db.query(Commitment).all() if _st(c) in ("open", "at_risk", "overdue") and c.due_date]
    rows.sort(key=lambda c: c.due_date)
    return {
        "overdue": [item(c) for c in rows if deadline(c).date() < d0],
        "today": [item(c) for c in rows if deadline(c).date() == d0],
        "upcoming": [item(c) for c in rows if d0 < deadline(c).date() <= d0 + timedelta(days=7)],
        "priority": [item(c) for c in sorted(rows, key=lambda c: -(c.priority_score or 0))
                     if (c.priority_score or 0) >= 60][:5],
    }


@router.get("/status")
def statuses(ids: str = "", db: Session = Depends(get_db)):
    """Current status of the given commitment ids (brief rows are snapshots, this keeps them live)."""
    want = [int(i) for i in ids.split(",") if i.strip().isdigit()][:60]
    rows = db.query(Commitment).filter(Commitment.id.in_(want)).all() if want else []
    return {str(c.id): _st(c) for c in rows}


GMAIL_DRAFTS = "https://mail.google.com/mail/u/0/#drafts"


@router.get("/drafts")
def drafts(db: Session = Depends(get_db)):
    """Every Gmail draft the app has prepared, tied to its commitment. Drafts are never sent by the app."""
    reminder_ids = set()
    try:
        from app.dependencies.models_smart import DependencyReminder
        reminder_ids = {r.draft_id for r in db.query(DependencyReminder).all() if r.draft_id}
    except Exception:
        pass
    out = []
    for c in db.query(Commitment).order_by(Commitment.id.desc()).all():
        if _st(c) == "cancelled":
            continue
        for kind, did in (("meeting", getattr(c, "meeting_draft_id", None)), ("mail", c.gmail_draft_id)):
            if not did:
                continue
            label = "Meeting invite" if kind == "meeting" else (
                "Dependency reminder" if did in reminder_ids else "Follow-up")
            out.append({"commitment_id": c.id, "kind": kind, "label": label, "owner": c.owner,
                        "to": c.owner_email if kind == "mail" else None, "text": (c.description or "")[:90],
                        "due_iso": c.due_date.isoformat() if c.due_date else None, "status": _st(c)})
    return {"url": GMAIL_DRAFTS, "items": out}


@router.get("/list")
def list_all(status: str = "open", q: str = "", limit: int = 8, db: Session = Depends(get_db)):
    """Every commitment, filterable by status and a text search, for the chat's Manage card."""
    rows = db.query(Commitment).all()
    counts = {k: 0 for k in ("open", "at_risk", "overdue", "done", "cancelled")}
    for c in rows:
        counts[_st(c)] = counts.get(_st(c), 0) + 1
    needle = q.strip().lower()
    sel = [c for c in rows if _st(c) == status
           and (not needle or needle in (c.description or "").lower() or needle in (c.owner or "").lower())]
    if status in ("done", "cancelled"):
        sel.sort(key=lambda c: -c.id)
    else:
        sel.sort(key=lambda c: (c.due_date is None, c.due_date or datetime.max))
    limit = max(1, min(limit, 50))
    return {"counts": counts, "total": len(sel), "items": [
        {"id": c.id, "owner": c.owner, "text": (c.description or "")[:120],
         "due_iso": c.due_date.isoformat() if c.due_date else None, "status": _st(c)} for c in sel[:limit]]}


@router.get("/feed")
def feed(limit: int = 40, db: Session = Depends(get_db)):
    now = datetime.now()
    rows = db.query(Notification).order_by(Notification.id.desc()).limit(limit).all()
    out = []
    for n in rows:
        if n.status == "pending" and n.deliver_after and n.deliver_after > now:
            continue  # snoozed - not due yet
        c = db.get(Commitment, n.commitment_id) if n.commitment_id else None
        out.append({
            "id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "status": n.status, "dedupe_key": n.dedupe_key,
            "action_taken": n.action_taken, "commitment_id": n.commitment_id,
            "owner": c.owner if c else None,
            "due_iso": c.due_date.isoformat() if c and c.due_date else None,
            "c_status": _st(c) if c else None,
            "is_meeting": bool(getattr(c, "is_meeting", False)) if c else False,
            "calendar_status": getattr(c, "calendar_status", None) if c else None,
            "ts": (n.deliver_after or n.created_at or now).isoformat(),
        })
    out.sort(key=lambda x: x["ts"])
    return out


class ChatIn(BaseModel):
    text: str
    notification_id: Optional[int] = None


def _target_date(t, today):
    if "tomorrow" in t:
        return today + timedelta(days=1)
    if "next week" in t:
        return today + timedelta(days=7)
    m = re.search(r"(\d+)\s*(day|days|d|week|weeks|w)\b", t)
    if m:
        n = int(m.group(1)) * (7 if m.group(2).startswith("w") else 1)
        return today + timedelta(days=n)
    for i, name in enumerate(WEEKDAYS):
        if name in t or name[:3] + " " in t + " ":
            ahead = (i - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    return None


def _minutes(t, default=15):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b", t)
    if not m:
        return default
    v = float(m.group(1))
    return int(v * 60) if m.group(2).startswith("h") else int(v)


def parse_command(text, due_iso, cid=None):
    t = text.lower().strip()
    if re.match(r"^(done|complete|completed|finish|finished|mark (it )?(as )?(done|complete))\b", t):
        return {"op": "complete"}, "Marking it done."
    if re.match(r"^(dismiss|ignore|skip|clear|ok|okay|got it|accept)\b", t):
        return {"op": "dismiss" if not t.startswith(("accept", "ok", "got")) else "accept"}, "Okay."
    if re.search(r"\b(schedule|set up|setup|book|create|add)\b.*\b(meeting|call|calendar)\b", t):
        return {"op": "schedule"}, "Adding it to your calendar…"
    m = re.search(r"\b(depends on|blocked by|waiting (?:on|for)|after)\s*#?(\d+)\b", t)
    if m and cid:
        return {"op": "dep", "commitment_id": cid, "depends_on_id": int(m.group(2))}, f"Linking: this waits for #{m.group(2)}."
    m = re.search(r"\b(blocks|unblocks|before)\s*#?(\d+)\b", t)
    if m and cid:
        return {"op": "dep", "commitment_id": int(m.group(2)), "depends_on_id": cid}, f"Linking: #{m.group(2)} waits for this."
    if t.startswith("snooze") or t.startswith("later"):
        return None, "Snooze is off. Try \"push 1 day\" or \"remind me 1 day before\"."
    if t.startswith("remind"):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(hour|hours|hr|hrs|h|day|days|d)\s+before", t)
        if m:
            h = float(m.group(1)) * (24 if m.group(2).startswith("d") else 1)
            return {"op": "remind", "hours_before": h}, f"I'll remind you {m.group(1)} {m.group(2)} before it's due."
        return None, "Try \"remind me 1 day before\" or \"remind me 2 hours before\"."
    if re.match(r"^(reschedule|move|push|postpone|delay|shift)\b", t):
        d = _target_date(t, datetime.now().date())
        if not d:
            return None, "To when? e.g. \"reschedule to friday\" or \"push 3 days\"."
        base = datetime.fromisoformat(due_iso) if due_iso else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        new = datetime.combine(d, base.time())
        return {"op": "resched", "due_iso": new.isoformat()}, f"Rescheduled to {new.strftime('%a %d %b')}."
    return "no", ""


def _ask_ai(db, text):
    from app.processing.extractor import client, MODEL
    now = datetime.now()
    rows = [c for c in db.query(Commitment).all() if _st(c) in ("open", "at_risk", "overdue")]
    rows.sort(key=lambda c: c.due_date or datetime.max)
    facts = [{"id": c.id, "owner": c.owner, "task": c.description[:100],
              "due": c.due_date.isoformat() if c.due_date else None, "status": _st(c),
              "priority": int(c.priority_score or 0)} for c in rows[:40]]
    prompt = (f"You are CommiSense, a concise assistant in a small chat popup. Now: {now:%A %d %b %Y %H:%M}. "
              "Answer in at most 4 short lines using ONLY these active commitments; say so if unknown. "
              "No markdown.\nDATA:" + json.dumps(facts) + "\nQUESTION: " + text)
    r = client.models.generate_content(model=MODEL, contents=prompt)
    return (r.text or "").strip()[:700] or "I couldn't find that."


def _simple_answer(db, t):
    now = datetime.now()
    rows = [c for c in db.query(Commitment).all() if _st(c) in ("open", "at_risk", "overdue") and c.due_date]

    def dl(c):
        d = c.due_date
        return d + timedelta(hours=23, minutes=59) if (d.hour, d.minute, d.second) == (0, 0, 0) else d

    if "overdue" in t:
        sel = [c for c in rows if dl(c) < now]; head = "Overdue"
    elif "today" in t:
        sel = [c for c in rows if dl(c).date() == now.date() and dl(c) >= now]; head = "Due today"
    else:
        return None
    if not sel:
        return f"{head}: nothing. 🎉"
    sel.sort(key=lambda c: c.due_date)
    return f"{head} ({len(sel)}):\n" + "\n".join(f"• {c.owner}: {c.description[:60]} ({c.due_date:%d %b %H:%M})".replace(" 00:00", "") for c in sel[:8])


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db)):
    text = body.text.strip()
    t = text.lower()
    if t in ("help", "?", "options", "what can you do"):
        return {"reply": HELP, "do": None}
    if re.search(r"\b(brief|summary)\b", t):
        from app.briefs.models import Brief
        kind = "weekly" if "week" in t else "daily"
        b = db.query(Brief).filter_by(kind=kind).order_by(Brief.id.desc()).first()
        if b:
            c = json.loads(b.content)
            acts = "\n".join("→ " + a["action"] for a in c.get("actions", [])[:3])
            return {"reply": f"{c['headline']}\n{c['summary']}\n{acts}".strip(), "do": None}
    n = db.get(Notification, body.notification_id) if body.notification_id else None
    c = db.get(Commitment, n.commitment_id) if n and n.commitment_id else None
    cmd, reply = parse_command(text, c.due_date.isoformat() if c and c.due_date else None, c.id if c else None)
    if cmd != "no":
        if cmd is None:
            return {"reply": reply, "do": None}
        if not n:
            return {"reply": "Tap one of the alerts first, then tell me what to do with it.", "do": None}
        if cmd["op"] in ("complete", "resched", "schedule", "dep") and not c:
            return {"reply": "That alert isn't linked to a commitment.", "do": None}
        return {"reply": reply, "do": cmd}
    simple = _simple_answer(db, t)
    if simple:
        return {"reply": simple, "do": None}
    try:
        return {"reply": _ask_ai(db, text), "do": None}
    except Exception:
        return {"reply": "I couldn't reach the AI just now.\n" + HELP, "do": None}