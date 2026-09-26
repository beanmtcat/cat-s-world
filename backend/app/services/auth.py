from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_identifier, hash_password, verify_password
from app.models.core import User, UserSession

SESSION_TTL = timedelta(days=14)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_token() -> str:
    return secrets.token_urlsafe(48)


async def register_member(
    session: AsyncSession, email: str, password: str, display_name: str
) -> User:
    normalized_email = email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == normalized_email))
    if existing is not None:
        # Do not reveal whether an account already exists to public callers.
        raise AppError("REGISTRATION_ACCEPTED", "如邮箱可用，验证邮件将很快送达。", 202)
    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=display_name.strip(),
        role="member",
        status="pending_verification",
    )
    session.add(user)
    await session.flush()
    return user


async def login(
    session: AsyncSession,
    email: str,
    password: str,
    ip_hash: str | None,
    user_agent_hash: str | None,
) -> tuple[User, str, str]:
    normalized_email = email.strip().lower()
    user = await session.scalar(
        select(User).where(User.email == normalized_email, User.isvalid.is_(True))
    )
    if user is None or not verify_password(password, user.password_hash):
        raise AppError("LOGIN_FAILED", "邮箱或密码错误。", 401)
    if user.status != "active":
        raise AppError("ACCOUNT_NOT_ACTIVE", "请先验证邮箱或联系管理员。", 403)
    raw_token, csrf_token = _new_token(), _new_token()
    active_session = UserSession(
        user_id=user.id,
        token_hash=hash_identifier(raw_token),
        csrf_secret_hash=hash_identifier(csrf_token),
        ip_address=ip_hash,
        user_agent=user_agent_hash,
        expires_at=_now() + SESSION_TTL,
    )
    session.add(active_session)
    await session.flush()
    return user, raw_token, csrf_token


async def current_session(session: AsyncSession, raw_token: str | None) -> tuple[User, UserSession]:
    if not raw_token:
        raise AppError("AUTH_REQUIRED", "请先登录。", 401)
    token_hash = hash_identifier(raw_token)
    row = await session.execute(
        select(User, UserSession)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == token_hash,
            UserSession.isvalid.is_(True),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > _now(),
            User.isvalid.is_(True),
        )
    )
    item = row.first()
    if item is None:
        raise AppError("AUTH_REQUIRED", "登录已过期，请重新登录。", 401)
    user, active_session = item
    active_session.last_seen_at = _now()
    return user, active_session


async def revoke_session(session: AsyncSession, active_session: UserSession) -> None:
    active_session.revoked_at = _now()
    active_session.isvalid = False


def verify_csrf(raw_csrf: str | None, active_session: UserSession) -> None:
    if not raw_csrf or not secrets.compare_digest(
        hash_identifier(raw_csrf), active_session.csrf_secret_hash
    ):
        raise AppError("CSRF_INVALID", "请求校验失败，请刷新页面后重试。", 403)
