from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.core import User, UserSession
from app.services.auth import current_session, verify_csrf


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> tuple[User, UserSession]:
    settings = get_settings()
    return await current_session(session, request.cookies.get(settings.session_cookie_name))


async def require_csrf(
    request: Request, identity: tuple[User, UserSession] = Depends(get_current_user)
) -> tuple[User, UserSession]:
    verify_csrf(request.headers.get("X-CSRF-Token"), identity[1])
    return identity


def require_roles(*roles: str) -> Callable:
    async def dependency(
        identity: tuple[User, UserSession] = Depends(require_csrf),
    ) -> tuple[User, UserSession]:
        if identity[0].role not in roles:
            raise AppError("ROLE_NOT_ALLOWED", "没有执行此操作的权限。", 403)
        return identity

    return dependency


def request_id(request: Request) -> UUID:
    return request.state.request_id
