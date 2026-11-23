import re
import time
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import google as g
from app.auth.models import User
from app.auth.security import DUMMY_HASH, hash_password, make_token, read_token, verify_password
from app.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,19}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FAILS: dict[str, list[float]] = {}                 # identifier -> recent failure times
_MAX_FAILS, _WINDOW = 6, 600


class SignUp(BaseModel):
    display_name: str
    username: str
    email: str
    password: str


class Login(BaseModel):
    identifier: str            # email or username
    password: str


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "username": u.username, "display_name": u.display_name,
            "avatar_url": u.avatar_url, "gmail_connected": bool(u.gmail_connected),
            "has_password": bool(u.password_hash)}


def _session(u: User) -> dict:
    return {"token": make_token(u.id), "user": _user_out(u)}


def get_current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    """Dependency for routes that need a signed-in user."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    uid = read_token(token) if token else None
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


# ------------------------------------------------------------ email + password
@router.post("/signup")
def signup(body: SignUp, db: Session = Depends(get_db)):
    name, username, email = body.display_name.strip(), body.username.strip().lower(), body.email.strip().lower()
    if not 1 <= len(name) <= 60:
        raise HTTPException(400, "Enter a display name (up to 60 characters).")
    if not _USERNAME.match(username):
        raise HTTPException(400, "Username: 3-20 characters, start with a letter, lowercase letters, numbers, underscore.")
    if not _EMAIL.match(email) or len(email) > 255:
        raise HTTPException(400, "Enter a valid email address.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with this email already exists. Try signing in.")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(409, "That username is taken.")
    user = User(email=email, username=username, display_name=name,
                password_hash=hash_password(body.password), last_login_at=datetime.now())
    db.add(user)
    db.commit()
    db.refresh(user)
    return _session(user)


@router.post("/login")
def login(body: Login, db: Session = Depends(get_db)):
    ident = body.identifier.strip().lower()
    now = time.time()
    recent = [t for t in _FAILS.get(ident, []) if now - t < _WINDOW]
    if len(recent) >= _MAX_FAILS:
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    user = db.query(User).filter((User.email == ident) | (User.username == ident)).first()
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not (user and ok):
        _FAILS[ident] = recent + [now]
        if user and not user.password_hash:
            raise HTTPException(401, "This account uses Google. Choose \"Continue with Google\".")
        raise HTTPException(401, "Incorrect email/username or password.")
    _FAILS.pop(ident, None)
    user.last_login_at = datetime.now()
    db.commit()
    return _session(user)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_out(user)


# ---------------------------------------------------------------------- Google
@router.get("/google/config")
def google_config():
    return {"configured": g.configured()}


@router.get("/google/start")
def google_start():
    if not g.configured():
        return RedirectResponse(f"{g.FRONTEND_URL}/#/signin?error={quote('Google sign-in is not set up yet (credentials.json not found).')}")
    return RedirectResponse(g.authorization_url())


@router.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db)):
    def back(msg):
        return RedirectResponse(f"{g.FRONTEND_URL}/#/signin?error={quote(msg)}")

    if error or not code or not state:
        return back("Google sign-in was cancelled.")
    try:
        info = g.exchange(code, state)
    except Exception as exc:                                       # bad state, denied, network, ...
        return back(str(exc) if isinstance(exc, ValueError) else "Google sign-in failed. Please try again.")

    user = db.query(User).filter(User.google_sub == info["sub"]).first() \
        or db.query(User).filter(User.email == info["email"]).first()
    if not user:
        user = User(email=info["email"], display_name=info["name"])
        db.add(user)
    user.google_sub = info["sub"]
    user.avatar_url = info["picture"] or user.avatar_url
    user.display_name = user.display_name or info["name"]
    user.last_login_at = datetime.now()
    user.gmail_connected = info["gmail_ok"]
    if info["gmail_ok"]:                                          # incomplete permissions: keep the working token.json untouched
        user.google_token_json = info["credentials_json"]
        g.sync_token_file(info["credentials_json"])                # existing ingestion now uses this account
    db.commit()
    db.refresh(user)
    return RedirectResponse(f"{g.FRONTEND_URL}/#/auth?token={make_token(user.id)}")
