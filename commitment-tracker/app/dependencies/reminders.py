"""Drafts a reminder email when a task other people are waiting on is still open 48 hours before its deadline.

  - Only tasks that have at least one open dependent are considered.
  - The draft is addressed to the task's owner and says who is waiting. It is saved in Gmail Drafts - NEVER sent.
  - One draft per task per deadline (moving the deadline earns a new reminder at the new 48 h mark).
  - Tasks you own yourself are skipped (nobody to email); tasks with no email on file raise a chat note instead.
  - Separate from the existing nudge pipeline: last_nudged_at and gmail_draft_id are not touched.
"""
import asyncio
import os
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.models import Commitment, AuditLog
from app.dependencies.models import CommitmentDependency as Dep
from app.dependencies.models_smart import DependencyReminder
from app.dependencies import graph as G
from app.dependencies.keys import ensure_keys

REMIND_HOURS = float(os.getenv("DEP_REMIND_HOURS", "48"))
ENABLED = os.getenv("DEP_REMIND_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
INACTIVE = ("done", "cancelled")

PROMPT = """Write a short, friendly reminder email.
From: {sender}
To: {first}
Task they owe: {task} (due {due})
People waiting on it:
{waiting}

Say that the work above depends on it, ask them to complete it so everyone stays on deadline, and invite them to
say so if they are blocked or need to move the date. {tone}
Under 90 words. Return ONLY the email body: no subject, no markdown, no placeholders.
"""


def _st(c):
    return getattr(c.status, "value", c.status)


def _gemini(prompt):
    from app.processing.extractor import client, MODEL
    r = client.models.generate_content(model=MODEL, contents=prompt)
    return (r.text or "").strip()


WRITE = _gemini      # replaced in tests


def _fmt(d):
    return d.strftime("%a %d %b") + ("" if (d.hour, d.minute) == (0, 0) else d.strftime(", %I:%M %p").replace(" 0", " "))


def _body(first, task, due, waiting, overdue):
    sender = os.getenv("OWNER_NAME", "").strip().title()
    lines = "\n".join(f"- {w.owner}: {(w.description or '')[:70]} (due {_fmt(w.due_date) if w.due_date else 'no date'})" for w in waiting[:5])
    tone = "It is already overdue, so be clear but kind." if overdue else "Keep it light, not pushy."
    try:
        text = WRITE(PROMPT.format(sender=sender or "me", first=first, task=task, due=_fmt(due), waiting=lines, tone=tone))
        if text:
            return text
    except Exception as e:
        print(f"Reminder wording by AI failed, using template: {e}")
    who = ", ".join(sorted({w.owner for w in waiting if w.owner})) or "others"
    when = f"was due {_fmt(due)}" if overdue else f"is due {_fmt(due)}"
    return (f"Hi {first},\n\nA quick reminder that \"{task}\" {when}. Waiting on it: {who}. "
            f"Finishing it on time keeps us all on deadline.\n\n"
            f"If you're blocked or need to move the date, just let me know.\n\nThanks"
            + (f",\n{sender}" if sender else ""))


def _notify(db, key, title, body):
    try:
        from app.notifications.generator import _add
        _add(db, None, "dependency", key, title, body)
    except Exception:
        pass


def find_and_draft(db, connector, now=None, hours=None):
    """Returns [{'commitment_id', 'to', 'waiting': [...]}] for the drafts created in this pass."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=hours if hours is not None else REMIND_HOURS)
    me = os.getenv("OWNER_NAME", "").strip().lower()
    cs = {c.id: c for c in db.query(Commitment).all()}
    active = {i for i, c in cs.items() if _st(c) not in INACTIVE}
    edges = [(d.depends_on_id, d.commitment_id) for d in db.query(Dep).all() if d.depends_on_id in active and d.commitment_id in active]
    keys = ensure_keys(db)
    made = []
    for up in sorted({u for u, _ in edges}):
        c = cs[up]
        if not c.due_date or G.effective_deadline(c.due_date) > horizon:
            continue
        if me and (c.owner or "").strip().lower() == me:
            continue                                            # your own task: nobody to email
        waiting = [cs[d] for u, d in edges if u == up]
        key = f"{up}:{c.due_date.isoformat()}"
        if db.query(DependencyReminder).filter_by(dedupe_key=key).first():
            continue
        if not c.owner_email:
            _notify(db, f"depremind-noemail:{key}", f"No email for {c.owner}",
                    f"{keys[up]} is due {_fmt(c.due_date)} and {len(waiting)} task(s) wait on it, "
                    f"but I have no email address for {c.owner}, so I couldn't draft a reminder.")
            continue
        first = (c.owner or "there").split()[0]
        overdue = G.effective_deadline(c.due_date) < now
        subject = f"Reminder: {c.description[:60]} {'was due' if overdue else 'due'} {_fmt(c.due_date)}" \
                  f" - {len(waiting)} task{'s' if len(waiting) != 1 else ''} waiting"
        body = _body(first, c.description, c.due_date, waiting, overdue)
        try:
            draft_id = connector.create_draft(c.owner_email, subject, body)
        except Exception as e:
            print(f"Dependency reminder draft failed for commitment {up}: {e}")
            continue
        row = DependencyReminder(dedupe_key=key, commitment_id=up, to_email=c.owner_email,
                                 draft_id=str(draft_id) if draft_id else None,
                                 waiting_ids=",".join(str(w.id) for w in waiting))
        db.add(row)
        db.add(AuditLog(commitment_id=up, event="dep_reminder",
                        detail=f"Drafted a reminder to {c.owner} ({len(waiting)} waiting on it)"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        names = ", ".join(keys[w.id] for w in waiting[:3]) + (f" +{len(waiting) - 3}" if len(waiting) > 3 else "")
        _notify(db, f"depremind:{row.id}", f"Reminder drafted for {first}",
                f"{keys[up]} {'is overdue' if overdue else 'is due ' + _fmt(c.due_date)}; {names} wait on it.\n"
                f"The email is in your Gmail Drafts. Review and send it when ready.")
        made.append({"commitment_id": up, "to": c.owner_email, "waiting": [w.id for w in waiting]})
    return made


async def reminder_loop():
    if not ENABLED:
        return
    from app.database import SessionLocal
    await asyncio.sleep(40)
    while True:
        try:
            def run():
                from app.ingestion import gmail_connector as gc
                db = SessionLocal()
                try:
                    return find_and_draft(db, gc.GmailConnector(max_results=1))
                finally:
                    db.close()
            await asyncio.to_thread(run)
        except Exception as e:
            print(f"Dependency reminder check error: {e}")
        await asyncio.sleep(600)
