from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_identifier, hash_password, verify_password
from app.models.core import User, UserSession
from app.models.supplemental import EmailVerificationToken, LoginLog

SESSION_TTL = timedelta(days=14)
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_DURATION = timedelta(minutes=15)
# A valid Argon2id value keeps the failure path constant-time for unknown
# accounts, preventing an email-existence timing oracle.
_DUMMY_PASSWORD_HASH = hash_password("mmcat-invalid-password-placeholder")


def _now() -> datetime:
    return datetime.now(UTC)


def _new_token() -> str:
    return secrets.token_urlsafe(48)


async def register_member(
    session: AsyncSession, email: str, password: str, display_name: str, requested_ip: str
) -> tuple[User, str] | None:
    normalized_email = email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == normalized_email))
    if existing is not None:
        # A repeat registration for an unverified address acts as a throttled
        # resend without disclosing whether an account already exists.
        if existing.isvalid and existing.status == "pending_verification":
            await session.execute(
                update(EmailVerificationToken)
                .where(
                    EmailVerificationToken.user_id == existing.id,
                    EmailVerificationToken.used_at.is_(None),
                    EmailVerificationToken.invalidated_at.is_(None),
                )
                .values(invalidated_at=_now())
            )
            raw_token = _new_token()
            session.add(
                EmailVerificationToken(
                    user_id=existing.id,
                    token_hash=hash_identifier(raw_token),
                    requested_ip=requested_ip,
                    expires_at=_now() + timedelta(hours=24),
                )
            )
            return existing, raw_token
        # Do not reveal whether an active account already exists to public callers.
        return None
    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=display_name.strip(),
        role="member",
        status="pending_verification",
    )
    session.add(user)
    await session.flush()
    raw_token = _new_token()
    session.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_identifier(raw_token),
            requested_ip=requested_ip,
            expires_at=_now() + timedelta(hours=24),
        )
    )
    return user, raw_token


async def verify_email(session: AsyncSession, raw_token: str) -> None:
    token = await session.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == hash_identifier(raw_token),
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.invalidated_at.is_(None),
            EmailVerificationToken.expires_at > _now(),
            EmailVerificationToken.isvalid.is_(True),
        )
    )
    if token is None:
        raise AppError("VERIFICATION_TOKEN_INVALID", "验证链接无效或已过期。", 422)
    user = await session.get(User, token.user_id)
    if user is None or not user.isvalid:
        raise AppError("VERIFICATION_TOKEN_INVALID", "验证链接无效或已过期。", 422)
    now = _now()
    user.email_verified_at = now
    user.status = "active"
    token.used_at = now


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
    password_ok = verify_password(password, user.password_hash if user else _DUMMY_PASSWORD_HASH)
    now = _now()
    if user is None or not password_ok:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= LOGIN_FAILURE_LIMIT:
                user.locked_until = now + LOGIN_LOCK_DURATION
            session.add(
                LoginLog(
                    user_id=user.id,
                    login_account=normalized_email,
                    event_type="password",
                    success=False,
                    failure_reason="invalid_credentials",
                    ip_address=ip_hash or "",
                    user_agent=user_agent_hash,
                )
            )
            await session.commit()
        raise AppError("LOGIN_FAILED", "邮箱或密码错误。", 401)
    if user.status != "active" or (user.locked_until is not None and user.locked_until > now):
        session.add(
            LoginLog(
                user_id=user.id,
                login_account=normalized_email,
                event_type="password",
                success=False,
                failure_reason="account_unavailable",
                ip_address=ip_hash or "",
                user_agent=user_agent_hash,
            )
        )
        await session.commit()
        raise AppError("LOGIN_FAILED", "邮箱或密码错误。", 401)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    raw_token, csrf_token = _new_token(), _new_token()
    active_session = UserSession(
        user_id=user.id,
        token_hash=hash_identifier(raw_token),
        csrf_secret_hash=hash_identifier(csrf_token),
        ip_address=ip_hash,
        user_agent=user_agent_hash,
        expires_at=now + SESSION_TTL,
    )
    session.add(active_session)
    await session.flush()
    session.add(
        LoginLog(
            user_id=user.id,
            session_id=active_session.id,
            login_account=normalized_email,
            event_type="password",
            success=True,
            ip_address=ip_hash or "",
            user_agent=user_agent_hash,
        )
    )
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
            User.status == "active",
            or_(User.locked_until.is_(None), User.locked_until <= _now()),
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


async def revoke_all_user_sessions(session: AsyncSession, user_id: object) -> None:
    """Invalidate all browser sessions after a security-sensitive user change."""
    await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now(), isvalid=False)
    )


def verify_csrf(raw_csrf: str | None, active_session: UserSession) -> None:
    if not raw_csrf or not secrets.compare_digest(
        hash_identifier(raw_csrf), active_session.csrf_secret_hash
    ):
        raise AppError("CSRF_INVALID", "请求校验失败，请刷新页面后重试。", 403)
