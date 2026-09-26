from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, request_id, require_csrf
from app.core.config import get_settings
from app.core.security import hash_identifier
from app.db.session import get_session
from app.models.core import User, UserSession
from app.schemas.auth import LoginRequest, RegisterRequest, UserPublic
from app.services.auth import login, register_member, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_payload(user: User) -> dict[str, object]:
    return UserPublic(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified_at is not None,
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
    await register_member(session, body.email, body.password, body.display_name)
    await session.commit()
    return {"data": {"accepted": True}, "meta": {"requestId": str(request_id(request))}}


@router.post("/login")
async def login_user(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
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
        "data": {"user": _user_payload(user), "csrfToken": csrf_token},
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
    request: Request, identity: tuple[User, UserSession] = Depends(get_current_user)
) -> dict[str, object]:
    return {"data": _user_payload(identity[0]), "meta": {"requestId": str(request_id(request))}}
