"""
Sign in with Google = sign in AND connect Gmail for ingestion, in one consent screen.

* Reuses your existing credentials.json (Desktop OR Web client).
  - Desktop client: nothing to register - loopback redirects are allowed.
  - Web client: add GOOGLE_REDIRECT_URI to "Authorized redirect URIs".
* Asks for the same Gmail permissions the ingestion connector already needs
  (read mail + create DRAFTS - nothing is ever sent for you), plus Calendar when
  MEETINGS_ENABLED=true (taken from app.integrations.google_auth when present).
* After sign-in the Google token is saved on the user and, unless
  GOOGLE_SYNC_TOKEN_JSON=false, written to token.json so the existing Gmail /
  Calendar connectors immediately use the account that just signed in.
"""
import json
import os
import secrets
import time

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
from google_auth_oauthlib.flow import Flow

# The consent flow runs on localhost over http; this only relaxes oauthlib's https check.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")     # user may untick a box; we check below

IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
_FALLBACK_DATA_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
GMAIL_REQUIRED = _FALLBACK_DATA_SCOPES

CLIENT_FILE = os.environ.get("GOOGLE_CLIENT_FILE", "credentials.json")
TOKEN_FILE = os.environ.get("GOOGLE_TOKEN_FILE", "token.json")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/google/callback")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")

_STATES: dict[str, dict] = {}          # state -> {verifier, ts}; short-lived, in memory
_STATE_TTL = 600


def configured() -> bool:
    return os.path.exists(CLIENT_FILE)


def data_scopes() -> list[str]:
    try:
        from app.integrations.google_auth import required_scopes
        return list(required_scopes())
    except Exception:
        return list(_FALLBACK_DATA_SCOPES)


def _flow() -> Flow:
    return Flow.from_client_secrets_file(CLIENT_FILE, scopes=IDENTITY_SCOPES + data_scopes(),
                                         redirect_uri=REDIRECT_URI)


def _purge():
    cutoff = time.time() - _STATE_TTL
    for k in [k for k, v in _STATES.items() if v["ts"] < cutoff]:
        _STATES.pop(k, None)


def authorization_url() -> str:
    _purge()
    flow = _flow()
    state = secrets.token_urlsafe(24)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent",
                                    include_granted_scopes="true", state=state)
    _STATES[state] = {"verifier": getattr(flow, "code_verifier", None), "ts": time.time()}
    return url


def exchange(code: str, state: str) -> dict:
    """-> {sub, email, name, picture, credentials_json, gmail_ok}. Raises ValueError on a bad state."""
    _purge()
    saved = _STATES.pop(state, None)
    if not saved:
        raise ValueError("Sign-in expired or was already used. Please try again.")
    flow = _flow()
    if saved.get("verifier"):
        flow.code_verifier = saved["verifier"]
    flow.fetch_token(code=code)
    creds = flow.credentials
    info = google_id_token.verify_oauth2_token(creds.id_token, GoogleRequest(), flow.client_config["client_id"])
    if not info.get("email") or not info.get("email_verified", False):
        raise ValueError("Your Google email address is not verified.")
    granted = set(creds.scopes or flow.oauth2session.token.get("scope") or [])
    return {
        "sub": info["sub"],
        "email": info["email"].lower(),
        "name": info.get("name") or info["email"].split("@")[0],
        "picture": info.get("picture"),
        "credentials_json": creds.to_json(),
        "gmail_ok": all(s in granted for s in data_scopes()),        # EVERYTHING your setup needs (Gmail, + Calendar when meetings are on)
    }


def sync_token_file(credentials_json: str) -> None:
    if os.environ.get("GOOGLE_SYNC_TOKEN_JSON", "true").strip().lower() in ("0", "false", "no", "off"):
        return
    json.loads(credentials_json)                    # never write junk over a working token.json
    with open(TOKEN_FILE, "w") as f:
        f.write(credentials_json)
