"""
Layer 2: Processing & Analytics - Meeting agent.

For commitments the extractor flagged as meetings ("let's meet tomorrow at
6pm"), this agent:

  1. CHECKS your Google Calendar for a clash (free/busy only - it never reads
     what your events are called).
  2. DRAFTS an email to the other person (a Gmail draft - never sent for you):
       - you're free  -> "are you available at <time>?"
       - you're busy  -> an apology plus 2-3 free alternatives from your
                         working hours.
  3. Waits for YOU to press Schedule, which adds the meeting (with a Google
     Meet link) to your calendar. No guests are added, so Google sends no
     invitations: the email is the invitation.
  4. Keeps things in step if you later move the meeting.

The other person's own calendar can't be read (Google only shows free/busy for
calendars shared with you), which is why availability is asked by email.

Wording safety: the email body is written by the AI, then checked by code - every
time we supplied must appear exactly, and no other time, link or [placeholder]
may appear. If the check fails, a plain template is used instead, so a wrong
time can never reach a draft.

Settings (.env, all optional):
    MEETING_DEFAULT_MINUTES=30     length used for the check and the event
    MEETING_WORK_HOURS=9-18        when alternatives may be offered
    MEETING_SKIP_WEEKENDS=true
    MEETING_ALTERNATIVES=3
    MEETING_ADD_MEET_LINK=true
    OWNER_NAME                     you (also used elsewhere in the app)
"""
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple

from app.integrations.google_calendar import (
    CalendarUnavailable, availability_window, find_free_slots, is_free,
)
from app.models import AuditLog, Commitment, CommitmentStatus
from app.processing.extractor import _call_model

OPEN_STATUSES = [CommitmentStatus.open, CommitmentStatus.at_risk]


# ------------------------------------------------------------------ errors

class MeetingError(Exception):
    """Something the user can fix; the message is shown to them."""


class NeedsTime(MeetingError):
    pass


class AlreadyScheduled(MeetingError):
    pass


class NotScheduled(MeetingError):
    pass


class Busy(MeetingError):
    def __init__(self, message, alternatives):
        super().__init__(message)
        self.alternatives = alternatives


# ---------------------------------------------------------------- settings

def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def settings() -> dict:
    start, end = 9, 18
    try:
        a, b = os.environ.get("MEETING_WORK_HOURS", "9-18").split("-")
        if 0 <= int(a) < int(b) <= 24:
            start, end = int(a), int(b)
    except ValueError:
        pass
    return {
        "minutes": max(5, _int("MEETING_DEFAULT_MINUTES", 30)),
        "work_start": start,
        "work_end": end,
        "skip_weekends": _flag("MEETING_SKIP_WEEKENDS", True),
        "alternatives": max(1, _int("MEETING_ALTERNATIVES", 3)),
        "with_meet": _flag("MEETING_ADD_MEET_LINK", True),
    }


# ----------------------------------------------------------------- helpers

def has_time(dt: datetime) -> bool:
    """Midnight means 'no time mentioned' (a date-only deadline)."""
    return dt.hour != 0 or dt.minute != 0


def fmt_day(dt: datetime) -> str:
    return f"{dt:%A}, {dt.day} {dt:%B}"


def fmt_clock(dt: datetime) -> str:
    return f"{dt:%I:%M %p}".lstrip("0")


def fmt_slot(dt: datetime) -> str:
    return f"{fmt_day(dt)} at {fmt_clock(dt)}"


def other_party(c: Commitment) -> Tuple[Optional[str], Optional[str]]:
    """(name, email) of the person you're meeting. For mail you sent, that's
    the recipient; for mail you received, the sender. 'You' is OWNER_NAME."""
    me = os.environ.get("OWNER_NAME", "").strip().lower()
    if me:
        if c.owner.strip().lower() == me:
            return c.beneficiary, c.beneficiary_email
        return c.owner, c.owner_email
    if c.beneficiary_email:  # can't tell who "you" are; best effort
        return c.beneficiary, c.beneficiary_email
    return c.owner, c.owner_email


def first_name(name: Optional[str]) -> Optional[str]:
    """'Priya Sharma' -> 'Priya'. Handles the directory style 'Sharma, Priya'."""
    if not name or "@" in name:
        return None
    if "," in name:
        name = name.split(",", 1)[1]
    parts = name.split()
    return parts[0] if parts else None


def _audit(db, c: Commitment, event: str, detail: str):
    db.add(AuditLog(commitment_id=c.id, event=event, detail=detail))


# ------------------------------------------------------------- the email

_TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b", re.IGNORECASE)

EMAIL_PROMPT = """Write a short, friendly email about arranging a meeting. \
It is from {sender} to {recipient}.

Rough note on what the meeting is about (reword it naturally and do NOT copy \
any times from it): {description}

Situation: {situation}

Use these time strings EXACTLY as written, character for character, and \
mention no other date or time anywhere:
{times}

Rules: under 90 words. Plain text only - no subject line, no markdown. Do not \
invent a Zoom, Meet or any other link; if the time works, say you'll send a \
calendar invite with a Google Meet link. No placeholders like [Name]. {signoff}
Return ONLY the email body."""


def _body_is_safe(body: Optional[str], required: List[str], allowed: List[str]) -> bool:
    if not body or len(body) > 1500:
        return False
    if "[" in body or "]" in body or re.search(r"https?://|www\.", body, re.IGNORECASE):
        return False
    if any(r not in body for r in required):
        return False
    stripped = body
    for a in sorted(set(allowed + required), key=len, reverse=True):
        stripped = stripped.replace(a, "")
    return not _TIME_RE.search(stripped)  # a time we didn't supply = invented


def _template(first: Optional[str], sender: str, original: datetime,
              alternatives: List[datetime], free: bool, minutes: int) -> str:
    hello = f"Hi {first}," if first else "Hi,"
    bye = f"Thanks,\n{sender}" if sender else "Thanks"
    if free:
        middle = (f"Are you available on {fmt_slot(original)} for a meeting? "
                  f"It should take about {minutes} minutes.\n\n"
                  "If that works, just reply and I'll send you a calendar invite with a Google Meet link.")
    elif alternatives:
        options = "\n".join(f"- {fmt_slot(a)}" for a in alternatives)
        middle = (f"I'm sorry - I have a conflict on {fmt_slot(original)}. "
                  f"Could we meet at one of these times instead?\n\n{options}\n\n"
                  "Let me know which suits you, or suggest another time, and I'll send an invite "
                  "with a Google Meet link.")
    else:
        middle = (f"I'm sorry - I have a conflict on {fmt_slot(original)}. "
                  "Could you suggest a couple of times that work for you? "
                  "I'll then send an invite with a Google Meet link.")
    return f"{hello}\n\n{middle}\n\n{bye}"


def compose_email(c: Commitment, original: datetime, alternatives: List[datetime],
                  free: bool, minutes: int) -> Tuple[str, str]:
    """(subject, body). AI-written, code-verified, template as the safety net."""
    name, _ = other_party(c)
    first = first_name(name)
    sender = os.environ.get("OWNER_NAME", "").strip()

    if free:
        subject = f"Meeting on {fmt_day(original)}: does {fmt_clock(original)} work?"
        required = [fmt_slot(original)]
        situation = (f"The sender would like to meet at the time below. Ask whether the recipient is "
                     f"available then, and say it should take about {minutes} minutes.")
        allowed = required
        times = f"Time to propose: {fmt_slot(original)}"
    else:
        subject = f"Meeting on {fmt_day(original)}: can we find another time?"
        required = [fmt_slot(a) for a in alternatives]
        allowed = required + [fmt_slot(original)]
        times = f"Original time (has a clash): {fmt_slot(original)}"
        if alternatives:
            times += "\nAlternatives to offer:\n" + "\n".join(f"- {t}" for t in required)
            situation = (f"The sender has a clash at the original time, {fmt_slot(original)}. Apologise "
                         "briefly and ask which of the alternative times suits the recipient.")
        else:
            situation = (f"The sender has a clash at the original time, {fmt_slot(original)}. Apologise "
                         "briefly and ask the recipient to suggest a couple of times that work for them.")

    prompt = EMAIL_PROMPT.format(
        sender=sender or "the sender",
        recipient=first or "the recipient",
        description=c.description,
        situation=situation,
        times=times,
        signoff=(f"Sign off with just the name {sender}." if sender else "Sign off with just 'Thanks'."),
    )

    body = None
    try:
        reply = _call_model(prompt).strip()
        if _body_is_safe(reply, required, allowed):
            body = reply
        else:
            print(f"[meetings] AI email for commitment {c.id} failed verification - using the template")
    except Exception as e:  # unavailable, blocked, anything: the template always works
        print(f"[meetings] AI email unavailable ({e}) - using the template")

    if body is None:
        body = _template(first, sender, original, alternatives, free, minutes)
    return subject, body


# ----------------------------------------------------- 1. automatic check

def is_checkable(c: Commitment, now: datetime) -> bool:
    return bool(
        c.is_meeting
        and c.status in OPEN_STATUSES
        and c.due_date is not None
        and has_time(c.due_date)
        and c.due_date > now
        and c.calendar_status is None
    )


def check_meeting(db, calendar, gmail, c: Commitment, now: Optional[datetime] = None) -> dict:
    """Read-only on your calendar. Creates one Gmail DRAFT; sends nothing."""
    now = now or datetime.now()
    s = settings()
    start = c.due_date

    busy = calendar.busy_intervals(*availability_window(start))  # may raise CalendarUnavailable
    free = is_free(busy, start, s["minutes"])
    alternatives = [] if free else find_free_slots(
        busy, start, s["minutes"], count=s["alternatives"], now=now,
        work_start=s["work_start"], work_end=s["work_end"], skip_weekends=s["skip_weekends"],
    )

    _, email = other_party(c)
    subject, body = compose_email(c, start, alternatives, free, s["minutes"])

    draft_id = None
    try:
        draft_id = gmail.create_draft(email or "", subject, body)
    except Exception as e:
        print(f"[meetings] couldn't create the draft for commitment {c.id}: {e}")

    c.calendar_status = "free" if free else "busy"
    c.meeting_draft_id = draft_id

    where = f"to {email}" if email else "with no recipient (no address known)"
    if free:
        detail = f"Calendar is free at {fmt_slot(start)}"
    else:
        alts = ", ".join(fmt_slot(a) for a in alternatives) or "none found"
        detail = f"Calendar is busy at {fmt_slot(start)}; alternatives: {alts}"
    detail += f"; availability email drafted {where}" if draft_id else "; email draft could not be created"
    _audit(db, c, "meeting_checked", detail)
    db.commit()

    return {
        "id": c.id,
        "status": c.calendar_status,
        "drafted": bool(draft_id),
        "alternatives": [a.isoformat() for a in alternatives],
    }


def pending_checks(db, now: Optional[datetime] = None, limit: int = 5) -> List[Commitment]:
    """Meetings not yet looked at, soonest first. A cheap database query, so
    callers can skip touching Google entirely when there's nothing to do."""
    now = now or datetime.now()
    rows = (
        db.query(Commitment)
        .filter(Commitment.is_meeting.is_(True))
        .filter(Commitment.calendar_status.is_(None))
        .filter(Commitment.status.in_(OPEN_STATUSES))
        .filter(Commitment.due_date.isnot(None))
        .filter(Commitment.due_date > now)
        .order_by(Commitment.due_date)
        .all()
    )
    return [c for c in rows if is_checkable(c, now)][:limit]


def find_and_check_meetings(db, calendar, gmail, now: Optional[datetime] = None, limit: int = 5) -> List[dict]:
    """Checks pending meetings. CalendarUnavailable propagates (it affects every
    meeting); anything else is handled per-meeting so one bad row can't block the rest."""
    now = now or datetime.now()
    results = []
    for c in pending_checks(db, now, limit):
        try:
            results.append(check_meeting(db, calendar, gmail, c, now))
        except CalendarUnavailable:
            raise
        except Exception as e:
            print(f"[meetings] check failed for commitment {c.id}: {e}")
    return results


# -------------------------------------------- 2. you press "Schedule"

def schedule_meeting(db, calendar, c: Commitment, force: bool = False, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    s = settings()

    if c.calendar_status == "scheduled" and c.calendar_event_id:
        raise AlreadyScheduled("This meeting is already on your calendar.")
    if c.due_date is None or not has_time(c.due_date):
        raise NeedsTime("Add a time to this commitment first - a date alone can't be put on a calendar.")
    if c.due_date <= now:
        raise NeedsTime("That time has already passed.")

    busy = calendar.busy_intervals(*availability_window(c.due_date))
    if not force and not is_free(busy, c.due_date, s["minutes"]):
        alternatives = find_free_slots(
            busy, c.due_date, s["minutes"], count=s["alternatives"], now=now,
            work_start=s["work_start"], work_end=s["work_end"], skip_weekends=s["skip_weekends"],
        )
        c.calendar_status = "busy"
        db.commit()
        raise Busy("You're busy at that time.", [a.isoformat() for a in alternatives])

    name, email = other_party(c)
    who = f"With: {name or 'unknown'}" + (f" <{email}>" if email else "")
    event = calendar.create_event(
        summary=c.description[:200],
        start=c.due_date,
        minutes=s["minutes"],
        description=f"{who}\n\nAdded by Commi-Sense for commitment #{c.id}. No invitation has been sent.",
        with_meet=s["with_meet"],
    )

    c.calendar_status = "scheduled"
    c.calendar_event_id = event["id"]
    c.calendar_event_url = event["url"]
    c.meet_url = event["meet_url"]
    _audit(db, c, "meeting_scheduled", f"Added to your calendar for {fmt_slot(c.due_date)}"
                                       + (" (clash overridden)" if force else ""))
    db.commit()
    return {"id": c.id, "event_url": c.calendar_event_url, "meet_url": c.meet_url}


def unschedule_meeting(db, calendar, c: Commitment) -> None:
    if not c.calendar_event_id:
        raise NotScheduled("This meeting isn't on your calendar.")
    calendar.delete_event(c.calendar_event_id)  # already-deleted-by-hand is fine
    c.calendar_status = "removed"
    c.calendar_event_id = c.calendar_event_url = c.meet_url = None
    _audit(db, c, "meeting_unscheduled", "Removed from your calendar")
    db.commit()


# ----------------------------------- 3. keeping things in step afterwards

def delete_meeting_draft_quietly(gmail, draft_id: Optional[str]) -> None:
    if draft_id:
        gmail.delete_draft(draft_id)  # swallows "already gone"


def delete_meeting_draft(db, gmail, c: Commitment) -> None:
    if not c.meeting_draft_id:
        raise MeetingError("There's no availability email draft for this meeting.")
    delete_meeting_draft_quietly(gmail, c.meeting_draft_id)
    c.meeting_draft_id = None
    _audit(db, c, "meeting_draft_deleted", "Availability email draft deleted")
    db.commit()


def handle_time_change(db, calendar, gmail, c: Commitment) -> str:
    """Call after a meeting commitment's due date was edited (new value already
    set on `c`). Returns 'moved', 'kept' or 'reset'."""
    s = settings()

    if c.calendar_status == "scheduled" and c.calendar_event_id:
        if c.due_date is not None and has_time(c.due_date):
            calendar.update_event_time(c.calendar_event_id, c.due_date, s["minutes"])
            _audit(db, c, "meeting_rescheduled", f"Calendar event moved to {fmt_slot(c.due_date)}")
            db.commit()
            return "moved"
        return "kept"  # time was cleared; leave your calendar entry alone

    # Not on the calendar: what we said before was about a time that no longer applies.
    delete_meeting_draft_quietly(gmail, c.meeting_draft_id)
    c.meeting_draft_id = None
    c.calendar_status = None  # the next check picks it up with the new time
    db.commit()
    return "reset"