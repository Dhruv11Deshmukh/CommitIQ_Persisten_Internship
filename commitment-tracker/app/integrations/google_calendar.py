"""
Google Calendar client: free/busy checks, and creating / moving / removing
the meetings Commi-Sense adds. Only ever touches YOUR primary calendar.

Times: the rest of the app stores naive LOCAL datetimes. Everything here takes
and returns naive local datetimes and converts to/from RFC 3339 (with the
correct UTC offset for that date, DST included) at the API boundary.

Meetings are created WITHOUT guests. Adding a guest makes Google email them an
invitation instantly, which would bypass the "nothing leaves without your
review" rule - so the invitation to the other person is a Gmail draft instead.
"""
import json
import time
import uuid
from datetime import datetime, time as dtime, timedelta
from typing import List, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.integrations.google_auth import get_credentials

Interval = Tuple[datetime, datetime]  # naive local (start, end)


class CalendarUnavailable(Exception):
    """Calendar can't be used right now; the message says why and how to fix it."""


# ---------------------------------------------------------------- time helpers

def to_rfc3339(naive_local: datetime) -> str:
    """Naive local time -> RFC 3339 with that date's real UTC offset."""
    return naive_local.astimezone().isoformat()


def from_rfc3339(value: str) -> datetime:
    """RFC 3339 (any offset, or 'Z') -> naive local time."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone().replace(tzinfo=None)


# ------------------------------------------------- pure availability logic

def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def conflicts(busy: List[Interval], start: datetime, minutes: int) -> List[Interval]:
    end = start + timedelta(minutes=minutes)
    return [(bs, be) for bs, be in busy if _overlaps(start, end, bs, be)]


def is_free(busy: List[Interval], start: datetime, minutes: int) -> bool:
    return not conflicts(busy, start, minutes)


def find_free_slots(
    busy: List[Interval],
    around: datetime,
    minutes: int,
    count: int = 3,
    now: Optional[datetime] = None,
    days: int = 7,
    work_start: int = 9,
    work_end: int = 18,
    step: int = 30,
    skip_weekends: bool = True,
    min_gap: int = 120,
) -> List[datetime]:
    """Up to `count` free slots inside working hours, soonest-to-`around`
    first, returned in chronological order.

    Slots must be at least `min_gap` minutes apart (default 2 hours) so the
    options are spread out - three back-to-back slots on the same morning are
    a poor offer, since if the other person is busy then, all three fail."""
    now = now or datetime.now()
    earliest = now + timedelta(hours=1)
    duration = timedelta(minutes=minutes)

    candidates = []
    for offset in range(days + 1):
        day = around.date() + timedelta(days=offset)
        if skip_weekends and day.weekday() >= 5:
            continue
        slot = datetime.combine(day, dtime(work_start, 0))
        last_start = datetime.combine(day, dtime(work_end, 0)) - duration
        while slot <= last_start:
            if slot >= earliest and slot != around and is_free(busy, slot, minutes):
                candidates.append(slot)
            slot += timedelta(minutes=step)

    candidates.sort(key=lambda s: (abs(s - around), s))
    chosen = []
    for slot in candidates:
        if all(abs(slot - c) >= timedelta(minutes=min_gap) for c in chosen):
            chosen.append(slot)
        if len(chosen) == count:
            break
    return sorted(chosen)


def availability_window(around: datetime, days: int = 7) -> Tuple[datetime, datetime]:
    """The span to fetch busy time for: the meeting's day through `days` later."""
    start = datetime.combine(around.date(), dtime(0, 0))
    return start, start + timedelta(days=days + 1)


# ---------------------------------------------------------------- the client

def _explain(error: HttpError) -> str:
    status = getattr(error.resp, "status", None)
    try:
        payload = json.loads(error.content.decode("utf-8", "ignore"))["error"]
        message = payload.get("message", "")
        reasons = " ".join(e.get("reason", "") for e in payload.get("errors", []))
    except Exception:
        message, reasons = str(error), ""
    low = f"{message} {reasons}".lower()

    if status == 403 and ("accessnotconfigured" in low or "has not been used" in low or "is disabled" in low):
        return ("The Google Calendar API isn't enabled for your Cloud project. Enable it at "
                "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com and try again.")
    if status in (401, 403) and ("scope" in low or "insufficient" in low or "permission" in low):
        return ("Calendar permission wasn't granted. Delete token.json, restart, and approve every box "
                "on the Google consent screen.")
    if status == 401:
        return "Google sign-in expired. Delete token.json and restart to sign in again."
    return f"Google Calendar error {status}: {message or 'unknown'}"


def _meet_url(event: dict) -> Optional[str]:
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    for entry in (event.get("conferenceData") or {}).get("entryPoints", []):
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return entry["uri"]
    return None


def _meet_pending(event: dict) -> bool:
    status = ((event.get("conferenceData") or {}).get("createRequest") or {}).get("status", {})
    return status.get("statusCode") == "pending"


class GoogleCalendar:
    def __init__(self, service=None):
        self.service = service or build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)

    @staticmethod
    def _run(request):
        try:
            return request.execute()
        except HttpError as e:
            raise CalendarUnavailable(_explain(e)) from e

    def busy_intervals(self, start: datetime, end: datetime) -> List[Interval]:
        body = {"timeMin": to_rfc3339(start), "timeMax": to_rfc3339(end), "items": [{"id": "primary"}]}
        response = self._run(self.service.freebusy().query(body=body))
        calendar = (response.get("calendars") or {}).get("primary", {})
        if calendar.get("errors"):
            raise CalendarUnavailable(f"Couldn't read your calendar's availability: {calendar['errors']}")
        return sorted((from_rfc3339(b["start"]), from_rfc3339(b["end"])) for b in calendar.get("busy", []))

    def create_event(self, summary: str, start: datetime, minutes: int,
                     description: str = "", with_meet: bool = True) -> dict:
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": to_rfc3339(start)},
            "end": {"dateTime": to_rfc3339(start + timedelta(minutes=minutes))},
        }
        kwargs = {"calendarId": "primary", "body": body}
        if with_meet:
            body["conferenceData"] = {"createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }}
            kwargs["conferenceDataVersion"] = 1

        event = self._run(self.service.events().insert(**kwargs))
        meet = _meet_url(event)

        if with_meet and not meet and _meet_pending(event):
            time.sleep(1.5)  # Google sometimes finishes the Meet link a moment after the event
            try:
                event = self._run(self.service.events().get(calendarId="primary", eventId=event["id"]))
                meet = _meet_url(event)
            except CalendarUnavailable:
                pass  # the meeting exists; the link just isn't available yet

        return {"id": event["id"], "url": event.get("htmlLink"), "meet_url": meet}

    def update_event_time(self, event_id: str, start: datetime, minutes: int) -> None:
        body = {
            "start": {"dateTime": to_rfc3339(start)},
            "end": {"dateTime": to_rfc3339(start + timedelta(minutes=minutes))},
        }
        self._run(self.service.events().patch(calendarId="primary", eventId=event_id, body=body))

    def delete_event(self, event_id: str) -> bool:
        """True if deleted; False if it was already gone (deleted by hand)."""
        try:
            self.service.events().delete(calendarId="primary", eventId=event_id).execute()
            return True
        except HttpError as e:
            if getattr(e.resp, "status", None) in (404, 410):
                return False
            raise CalendarUnavailable(_explain(e)) from e