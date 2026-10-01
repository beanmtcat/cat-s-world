from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, request_id, require_csrf
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_identifier
from app.db.session import get_session
from app.models.core import SiteProperties, User, UserSession
from app.models.supplemental import Media
from app.schemas.auth import LoginRequest, RegisterRequest, UserPublic, VerifyEmailRequest
from app.services.auth import login, register_member, revoke_session, verify_email
from app.services.mailer import send_verification_email
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


async def _user_payload(user: User, session: AsyncSession) -> dict[str, object]:
    avatar_url = None
    if user.avatar_media_id:
        avatar_url = await session.scalar(
            select(Media.source_url).where(Media.id == user.avatar_media_id, Media.isvalid.is_(True))
        )
    return UserPublic(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified_at is not None,
        avatar_media_id=str(user.avatar_media_id) if user.avatar_media_id else None,
        avatar_url=avatar_url,
    ).model_dump(mode="json", by_alias=True)


def _set_session_cookies(response: Response, raw_token: str, csrf_token: str) -> None:
    settings = get_settings()
    secure = settings.app_env == "production"
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=14 * 24 * 60 * 60,
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=14 * 24 * 60 * 60,
    )


@router.post("/register", status_code=202)
async def register(
    body: RegisterRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    registration_enabled = await session.scalar(
        select(SiteProperties.registration_enabled).where(SiteProperties.isvalid.is_(True))
    )
    if registration_enabled is False:
        raise AppError("REGISTRATION_DISABLED", "站点暂未开放注册。", 403)
    client_ip = request.client.host if request.client else "unknown"
    await enforce_rate_limit("register:ip", client_ip, limit=5, window_seconds=3600)
    await enforce_rate_limit("register:email", body.email.lower(), limit=3, window_seconds=3600)
    registration = await register_member(
        session, body.email, body.password, body.display_name, hash_identifier(client_ip)
    )
    await session.commit()
    if registration is not None:
        _, raw_token = registration
        try:
            await send_verification_email(body.email, raw_token)
        except Exception as error:  # noqa: BLE001 - SMTP backends vary.
            raise AppError("VERIFICATION_DELIVERY_FAILED", "验证邮件暂时无法送达，请稍后重试。", 503) from error
    return {"data": {"accepted": True}, "meta": {"requestId": str(request_id(request))}}


@router.post("/verify-email")
async def verify_email_address(
    body: VerifyEmailRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    await verify_email(session, body.token)
    await session.commit()
    return {"data": {"verified": True}, "meta": {"requestId": str(request_id(request))}}


@router.post("/login")
async def login_user(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    await enforce_rate_limit("login:ip", client_ip or "unknown", limit=20, window_seconds=900)
    await enforce_rate_limit("login:email", body.email.lower(), limit=10, window_seconds=900)
    user, raw_token, csrf_token = await login(
        session,
        body.email,
        body.password,
        hash_identifier(client_ip) if client_ip else None,
        hash_identifier(user_agent) if user_agent else None,
    )
    await session.commit()
    _set_session_cookies(response, raw_token, csrf_token)
    return {
        "data": {"user": await _user_payload(user, session), "csrfToken": csrf_token},
        "meta": {"requestId": str(request_id(request))},
    }


@router.post("/logout", status_code=204)
async def logout_user(
    request: Request,
    response: Response,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await revoke_session(session, identity[1])
    await session.commit()
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response


@router.get("/me")
async def get_me(
    request: Request,
    identity: tuple[User, UserSession] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return {"data": await _user_payload(identity[0], session), "meta": {"requestId": str(request_id(request))}}
