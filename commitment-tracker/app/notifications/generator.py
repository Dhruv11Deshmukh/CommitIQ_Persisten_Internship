"""Turns commitment state into notification rows. READ-ONLY on commitments."""
import asyncio
import os
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from app.models import Commitment
from app.notifications.models import Notification, Reminder, NotificationState

INTERVAL = 60
PRIORITY_THRESHOLD = 80
ACTIVE = ("open", "at_risk", "overdue")


def _int_env(name, default):
    """Tolerates a stray inline comment in .env, e.g. 30   # minutes"""
    try:
        return int(os.getenv(name, str(default)).split("#")[0].strip())
    except ValueError:
        return default


# "Due today" reminder: one compact chat card every N minutes while today's tasks are still open.
TODAY_NUDGE_MINUTES = _int_env("TODAY_NUDGE_MINUTES", 30)   # 0 turns it off
TODAY_NUDGE_START = _int_env("TODAY_NUDGE_START", 8)        # first hour of the day it may appear
TODAY_NUDGE_END = _int_env("TODAY_NUDGE_END", 22)           # no reminders from this hour on


def _status(c):
    return getattr(c.status, "value", c.status)


def effective_deadline(due):
    """Midnight == date-only -> deadline is end of that day."""
    if due is None:
        return None
    if due.hour == 0 and due.minute == 0 and due.second == 0:
        return due + timedelta(hours=23, minutes=59)
    return due


def _fmt_due(due):
    if due is None:
        return "no deadline"
    if due.hour == 0 and due.minute == 0:
        return due.strftime("%a %d %b")
    return due.strftime("%a %d %b, %I:%M %p")


def _add(db, cid, kind, key, title, body, when=None):
    if db.query(Notification).filter_by(dedupe_key=key).first():
        return False
    db.add(Notification(commitment_id=cid, kind=kind, dedupe_key=key, title=title,
                        body=body, deliver_after=when or datetime.now()))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _get_state(db, key, default=None):
    row = db.get(NotificationState, key)
    return row.value if row else default


def _set_state(db, key, value):
    row = db.get(NotificationState, key)
    if row:
        row.value = str(value)
    else:
        db.add(NotificationState(key=key, value=str(value)))
    db.commit()


def today_digest(db, rows, now) -> int:
    """One reminder per N-minute slot while any task due today is still open. A newer reminder replaces the
    older one, so the chat never piles up. Skipped right after the daily brief (which already lists today)."""
    if TODAY_NUDGE_MINUTES <= 0 or not (TODAY_NUDGE_START <= now.hour < TODAY_NUDGE_END):
        return 0
    todays = [c for c in rows if _status(c) in ACTIVE and c.due_date
              and effective_deadline(c.due_date).date() == now.date()]
    if not todays:
        return 0
    last_brief = (db.query(Notification).filter(Notification.dedupe_key.like("brief:daily:%"))
                  .order_by(Notification.id.desc()).first())
    if last_brief and last_brief.created_at and now - last_brief.created_at < timedelta(minutes=max(TODAY_NUDGE_MINUTES - 5, 0)):
        return 0
    slot = (now.hour * 60 + now.minute) // TODAY_NUDGE_MINUTES
    key = f"today:{now.date()}:{slot}"
    n = len(todays)
    made = _add(db, None, "today", key, "Due today",
                f"{n} task{'s' if n != 1 else ''} still open. Complete them when you can.")
    if made:
        for old in (db.query(Notification).filter(Notification.kind == "today", Notification.dedupe_key != key,
                                                  Notification.status.in_(("pending", "delivered"))).all()):
            old.status, old.action_taken = "dismissed", "superseded"
        db.commit()
    return 1 if made else 0


def generate(db) -> int:
    now = datetime.now()
    made = 0
    rows = db.query(Commitment).all()

    # --- new commitments (watermark: existing rows are never backfilled) ---
    max_id = max([c.id for c in rows], default=0)
    wm = _get_state(db, "new_watermark")
    if wm is None:
        _set_state(db, "new_watermark", max_id)
        wm = max_id
    wm = int(wm)
    for c in rows:
        if c.id > wm and _status(c) in ("open", "at_risk"):
            made += _add(db, c.id, "new", f"new:{c.id}",
                         "New commitment",
                         f"{c.owner}: {c.description}\nDue {_fmt_due(c.due_date)}")
    if max_id > wm:
        _set_state(db, "new_watermark", max_id)

    seen_clash = set()
    for c in rows:
        st = _status(c)
        if st not in ACTIVE:
            continue
        dl = effective_deadline(c.due_date)
        label = f"{c.description[:90]}"
        if dl:
            left = dl - now
            if left.total_seconds() < 0:
                made += _add(db, c.id, "overdue", f"overdue:{c.id}:{now.date()}",
                             "Overdue", f"{label}\nWas due {_fmt_due(c.due_date)}")
            elif left <= timedelta(hours=2):
                made += _add(db, c.id, "upcoming", f"upcoming:{c.id}:{c.due_date}:2h",
                             "Due within 2 hours", f"{label}\nDue {_fmt_due(c.due_date)}")
            elif left <= timedelta(hours=24):
                made += _add(db, c.id, "upcoming", f"upcoming:{c.id}:{c.due_date}:24h",
                             "Due within 24 hours", f"{label}\nDue {_fmt_due(c.due_date)}")
        if (c.priority_score or 0) >= PRIORITY_THRESHOLD:
            made += _add(db, c.id, "priority", f"priority:{c.id}:critical",
                         f"High priority ({int(c.priority_score)})", label)
        gid = getattr(c, "clash_group_id", None)
        if gid and gid not in seen_clash:
            seen_clash.add(gid)
            n = sum(1 for x in rows if getattr(x, "clash_group_id", None) == gid)
            made += _add(db, c.id, "clash", f"clash:{gid}",
                         "Schedule clash", f"{n} commitments clash. Open CommiSense to resolve.")

    made += today_digest(db, rows, now)

    # --- reminders ---
    for r in db.query(Reminder).filter(Reminder.fired == False, Reminder.remind_at <= now).all():  # noqa: E712
        c = db.get(Commitment, r.commitment_id)
        r.fired = True
        db.commit()
        if c and _status(c) in ACTIVE:
            made += _add(db, c.id, "reminder", f"reminder:{r.id}",
                         "Reminder", f"{c.description[:90]}\nDue {_fmt_due(c.due_date)}")
    return made


async def notification_loop():
    from app.database import SessionLocal
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(generate, db)
            finally:
                db.close()
        except Exception as e:
            print(f"Notification generator error: {e}")
        await asyncio.sleep(INTERVAL)