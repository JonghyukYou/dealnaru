import hashlib
import os
import secrets
from datetime import timedelta

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserSession
from app.settings import EMAIL_VERIFICATION_REQUIRED, PRODUCTION
from app.utils.time import utc_now

SESSION_SECONDS = 12 * 60 * 60
# Keep authentication memory bounded on a small VM.
password_hasher = PasswordHasher(time_cost=3, memory_cost=32768, parallelism=2)
EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS = int(os.getenv("EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS", "24"))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
            # Preserve the previous bcrypt behavior for legacy accounts only.
            return bcrypt.checkpw(plain_password.encode("utf-8")[:72], password_hash.encode("ascii"))
        return password_hasher.verify(password_hash, plain_password)
    except (ValueError, VerificationError, InvalidHashError):
        return False


def password_needs_upgrade(password: str, old_hash: str) -> bool:
    # An ambiguous legacy input above bcrypt's limit needs a password reset.
    return old_hash.startswith("$2") and len(password.encode("utf-8")) <= 72


def hash_email_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_email_verification_token():
    token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(hours=EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS)
    return token, hash_email_verification_token(token), expires_at


def cookie_name():
    return "__Host-dealnaru_session" if PRODUCTION else "dealnaru_session"


def request_session_token(request: Request):
    # Command-line access in development/test only. Browsers use cookies.
    if not PRODUCTION:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:]
    return request.cookies.get(cookie_name())


def start_session(db: Session, request: Request, response: Response, user: User) -> str:
    now = utc_now()
    db.query(UserSession).filter(UserSession.expires_at <= now).delete(synchronize_session=False)
    previous = request.cookies.get(cookie_name())
    if previous and len(previous) == 43:
        db.query(UserSession).filter(
            UserSession.token_hash == hash_email_verification_token(previous)
        ).delete(synchronize_session=False)
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=hash_email_verification_token(token), user_id=user.id,
                       expires_at=now + timedelta(seconds=SESSION_SECONDS)))
    db.commit()
    response.set_cookie(cookie_name(), token, max_age=SESSION_SECONDS, path="/",
                        secure=PRODUCTION, httponly=True, samesite="lax")
    return token


def end_session(db: Session, request: Request, response: Response):
    token = request_session_token(request)
    if token and len(token) == 43:
        db.query(UserSession).filter(
            UserSession.token_hash == hash_email_verification_token(token)
        ).delete(synchronize_session=False)
        db.commit()
    response.delete_cookie(cookie_name(), path="/", secure=PRODUCTION, httponly=True, samesite="lax")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request_session_token(request)
    user = None
    if token and len(token) == 43:
        user = (
            db.query(User).join(UserSession)
            .filter(UserSession.token_hash == hash_email_verification_token(token),
                    UserSession.expires_at > utc_now()).first()
        )
    if user is None or (EMAIL_VERIFICATION_REQUIRED and not user.email_verified):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return current_user
