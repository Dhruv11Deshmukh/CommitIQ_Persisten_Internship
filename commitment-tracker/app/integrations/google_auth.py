"""
One Google sign-in shared by Gmail and Calendar.

Permissions requested
---------------------
Always (Gmail):
    gmail.readonly   read your mail to find commitments
    gmail.compose    create / delete DRAFTS (nothing is ever sent for you)
Only when MEETINGS_ENABLED=true (Calendar):
    calendar.freebusy        learn WHEN you're busy - never what your events are
    calendar.events.owned    create / move / delete the meetings Commi-Sense adds

If token.json was created before a permission was added, the saved login is
set aside and you're asked to approve once more (tick every box).

Several background loops can ask for credentials at the same moment; a lock
makes sure only ONE consent window ever opens.

Re-authorize on demand:   python -m app.integrations.google_auth
"""
import json
import os
import threading

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

TOKEN_FILE = "token.json"
CLIENT_FILE = "credentials.json"

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events.owned",
]

_lock = threading.Lock()


def meetings_enabled() -> bool:
    return os.environ.get("MEETINGS_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def required_scopes():
    return GMAIL_SCOPES + (CALENDAR_SCOPES if meetings_enabled() else [])


def _saved_scopes():
    try:
        with open(TOKEN_FILE) as f:
            return set(json.load(f).get("scopes") or [])
    except (OSError, ValueError):
        return set()


def get_credentials() -> Credentials:
    scopes = required_scopes()
    with _lock:
        creds = None
        if os.path.exists(TOKEN_FILE) and set(scopes) <= _saved_scopes():
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, scopes)
                creds = flow.run_local_server(port=0)

            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())

        return creds


if __name__ == "__main__":
    c = get_credentials()
    print("Signed in. Permissions on file:")
    for s in sorted(_saved_scopes()):
        print("  ", s)