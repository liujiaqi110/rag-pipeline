"""Authentication primitives (standard library only).

* Passwords: scrypt (n=2**14, r=8, p=1, 16-byte salt), stored as ``scrypt$n$r$p$salt_hex$hash_hex``; verified in
  constant time.
* Access tokens: 32 random bytes (``secrets.token_urlsafe``), opaque. Only SHA-256 of the token is stored, so a
  database leak does not leak usable tokens. Tokens expire, are revoked on logout, and stop working immediately when
  the user is deactivated (checked on every request).
* Identity is derived ONLY from the bearer token. No endpoint accepts a user id/roles to act as.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthToken, User, utcnow

_N, _R, _P = 2 ** 14, 8, 1
MIN_PASSWORD_LEN = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk.hex(), h)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(16))  # equalises timing for unknown users


def password_problems(password: str) -> Optional[str]:
    if len(password) < MIN_PASSWORD_LEN:
        return f"密码至少 {MIN_PASSWORD_LEN} 位"
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        return "密码必须同时包含字母和数字"
    return None


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:  # SQLite returns naive datetimes
        return dt.replace(tzinfo=timezone.utc)
    return dt


def authenticate(db: Session, username: str, password: str, max_failed: int, lockout_minutes: int) -> Optional[User]:
    """Returns the user or None. Unknown user, wrong password, inactive and locked are indistinguishable to the caller."""
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    now = utcnow()
    locked = _aware(user.locked_until)
    if locked and locked > now:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= max_failed:
            user.locked_until, user.failed_logins = now + timedelta(minutes=lockout_minutes), 0
        db.commit()
        return None
    if not user.active:
        return None
    user.failed_logins, user.locked_until = 0, None
    db.commit()
    return user


def issue_token(db: Session, user: User, ttl_minutes: int) -> Tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    exp = utcnow() + timedelta(minutes=ttl_minutes)
    db.add(AuthToken(token_sha256=token_digest(raw), user_id=user.id, expires_at=exp))
    db.commit()
    return raw, exp


def resolve_token(db: Session, raw: str) -> Optional[Tuple[User, AuthToken]]:
    if not raw or len(raw) > 200:
        return None
    tok = db.scalar(select(AuthToken).where(AuthToken.token_sha256 == token_digest(raw)))
    if tok is None or tok.revoked or _aware(tok.expires_at) <= utcnow():
        return None
    user = db.get(User, tok.user_id)
    if user is None or not user.active:
        return None
    return user, tok


def revoke_all(db: Session, user_id: int) -> None:
    for t in db.scalars(select(AuthToken).where(AuthToken.user_id == user_id, AuthToken.revoked.is_(False))):
        t.revoked = True
