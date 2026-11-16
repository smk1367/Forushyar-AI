from datetime import datetime, timedelta, timezone
import secrets

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status

from app.core.config import settings

_password_hasher = PasswordHasher()
_SESSION_ALGORITHM = "HS256"
_SESSION_TTL_MINUTES = 30


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise ValueError("گذرواژه باید بین ۱۲ تا ۲۵۶ نویسه باشد")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_session_token(employee_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": employee_id,
        "iat": now,
        "exp": now + timedelta(minutes=_SESSION_TTL_MINUTES),
        "jti": secrets.token_urlsafe(16),
        "iss": "forushyar-ai",
        "aud": "forushyar-dashboard",
    }
    return jwt.encode(payload, settings.session_secret, algorithm=_SESSION_ALGORITHM)


def decode_session_token(token: str) -> str:
    try:
        payload = jwt.decode(
            token,
            settings.session_secret,
            algorithms=[_SESSION_ALGORITHM],
            issuer="forushyar-ai",
            audience="forushyar-dashboard",
            options={"require": ["sub", "exp", "iat", "jti"]},
        )
        return str(payload["sub"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="نشست معتبر نیست") from exc


def require_csrf(request: Request) -> None:
    cookie_token = request.cookies.get("forushyar_csrf")
    header_token = request.headers.get("X-CSRF-Token")
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="درخواست CSRF معتبر نیست")
