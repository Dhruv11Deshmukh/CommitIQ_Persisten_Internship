"""Password hashing (scrypt, standard library) and signed session tokens (JWT)."""
import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt

TOKEN_TTL_SECONDS = int(os.environ.get("AUTH_TOKEN_DAYS", "14")) * 86400
_SECRET_FILE = ".auth_secret"
_secret_cache = None


def _secret() -> str:
    """JWT_SECRET from .env, otherwise a random secret kept in .auth_secret
    so people stay signed in across backend restarts."""
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    env = os.environ.get("JWT_SECRET", "").strip()
    if env:
        _secret_cache = env
    elif os.path.exists(_SECRET_FILE):
        _secret_cache = open(_SECRET_FILE).read().strip()
    else:
        _secret_cache = secrets.token_urlsafe(48)
        with open(_SECRET_FILE, "w") as f:
            f.write(_secret_cache)
    return _secret_cache


# ---------------------------------------------------------------- passwords ---
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, dk = (stored or "").split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                              n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


DUMMY_HASH = hash_password("not-a-real-password")      # keeps login timing similar for unknown users


# ------------------------------------------------------------------- tokens ---
def make_token(user_id: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL_SECONDS}, _secret(), algorithm="HS256")


def read_token(token: str) -> int | None:
    try:
        return int(jwt.decode(token, _secret(), algorithms=["HS256"])["sub"])
    except Exception:
        return None
