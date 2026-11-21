"""Authentication primitives (standard library only).

* Passwords: scrypt (n=2**14, r=8, p=1, 16-byte salt), stored as ``scrypt$n$r$p$salt_hex$hash_hex``; verified in
  constant time.
* Access tokens: 32 random bytes (``secrets.token_urlsafe``), opaque. Only SHA-256 of the token is stored, so a
  database leak does not leak usable tokens. Tokens expire, are revoked on logout, and stop working immediately when
  the user is deactivated (checked on every request).
* Identity is derived ONLY from the bearer token. No endpoint accepts a user id/roles to act as.
* P4 JWT (default for new logins): HS256, claims sub, uid, jti, iat, nbf, exp, iss, aud, typ. The JWT alone is NOT
  sufficient: its jti must match a non-revoked, non-expired auth_tokens row (stored as SHA-256) of the same user, and
  the user must be active. So logout / deactivation / permission changes revoke JWTs immediately, exactly like
  opaque tokens. Roles / departments are never put in the token (always read from the DB). Only HS256 is accepted
  (alg=none and algorithm confusion are rejected). The secret comes from KB_JWT_SECRET or a random key file created
  under KB_STORAGE_DIR/secrets (git-ignored).
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


class JwtConfig:
    def __init__(self, secret: bytes, issuer: str, audience: str):
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 bytes")
        self.secret, self.issuer, self.audience = secret, issuer, audience


def load_jwt_config(settings) -> JwtConfig:
    """KB_JWT_SECRET, else a random key persisted in KB_STORAGE_DIR/secrets/jwt_hs256.key (created once)."""
    from pathlib import Path
    if settings.jwt_secret:
        key = settings.jwt_secret.encode("utf-8")
    else:
        p = Path(settings.storage_dir) / "secrets" / "jwt_hs256.key"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(secrets.token_urlsafe(64), encoding="utf-8")
            try:
                p.chmod(0o600)
            except OSError:
                pass
        key = p.read_text(encoding="utf-8").strip().encode("utf-8")
    return JwtConfig(key, settings.jwt_issuer, settings.jwt_audience)


def issue_token(db: Session, user: User, ttl_minutes: int, fmt: str = "opaque",
                jwt_cfg: Optional[JwtConfig] = None) -> Tuple[str, datetime]:
    now = utcnow()
    exp = now + timedelta(minutes=ttl_minutes)
    if fmt == "jwt":
        import jwt as pyjwt
        if jwt_cfg is None:
            raise ValueError("JWT configuration missing")
        jti = secrets.token_urlsafe(24)
        claims = {"sub": user.username, "uid": user.id, "jti": jti, "typ": "access", "iss": jwt_cfg.issuer,
                  "aud": jwt_cfg.audience, "iat": int(now.timestamp()), "nbf": int(now.timestamp()),
                  "exp": int(exp.timestamp())}
        raw = pyjwt.encode(claims, jwt_cfg.secret, algorithm="HS256")
        db.add(AuthToken(token_sha256=token_digest(jti), user_id=user.id, expires_at=exp, kind="jwt"))
    else:
        raw = secrets.token_urlsafe(32)
        db.add(AuthToken(token_sha256=token_digest(raw), user_id=user.id, expires_at=exp, kind="opaque"))
    db.commit()
    return raw, exp


def decode_jwt(raw: str, cfg: JwtConfig) -> Optional[dict]:
    import jwt as pyjwt
    try:
        claims = pyjwt.decode(raw, cfg.secret, algorithms=["HS256"], audience=cfg.audience, issuer=cfg.issuer,
                              options={"require": ["exp", "iat", "nbf", "jti", "sub", "uid", "aud", "iss"]})
    except pyjwt.PyJWTError:
        return None
    if claims.get("typ") != "access" or not isinstance(claims.get("uid"), int):
        return None
    return claims


def resolve_token(db: Session, raw: str, jwt_cfg: Optional[JwtConfig] = None) -> Optional[Tuple[User, AuthToken]]:
    if not raw or len(raw) > 2048:
        return None
    claims = None
    if raw.count(".") == 2:                                  # JWT: signature + claims, THEN the server-side record
        if jwt_cfg is None:
            return None
        claims = decode_jwt(raw, jwt_cfg)
        if claims is None:
            return None
        tok = db.scalar(select(AuthToken).where(AuthToken.token_sha256 == token_digest(claims["jti"]),
                                                AuthToken.kind == "jwt"))
        if tok is None or tok.user_id != claims["uid"]:
            return None
    else:
        tok = db.scalar(select(AuthToken).where(AuthToken.token_sha256 == token_digest(raw), AuthToken.kind == "opaque"))
        if tok is None:
            return None
    if tok.revoked or _aware(tok.expires_at) <= utcnow():
        return None
    user = db.get(User, tok.user_id)
    if user is None or not user.active:
        return None
    if claims is not None and user.username != claims["sub"]:
        return None
    return user, tok


def revoke_all(db: Session, user_id: int) -> None:
    for t in db.scalars(select(AuthToken).where(AuthToken.user_id == user_id, AuthToken.revoked.is_(False))):
        t.revoked = True
