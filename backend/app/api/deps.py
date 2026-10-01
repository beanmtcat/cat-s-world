from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_identifier
from app.db.session import get_session
from app.models.core import AuditLog, User, UserSession
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
        request: Request,
        identity: tuple[User, UserSession] = Depends(require_csrf),
        session: AsyncSession = Depends(get_session),
    ) -> tuple[User, UserSession]:
        if identity[0].role not in roles:
            raise AppError("ROLE_NOT_ALLOWED", "没有执行此操作的权限。", 403)
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            segments = [part for part in request.url.path.split("/") if part]
            studio_segments = segments[segments.index("studio") + 1 :] if "studio" in segments else []
            payload: dict[str, str] = {"requestId": str(request.state.request_id)}
            if request.url.query:
                payload["query"] = str(request.url.query)
            session.add(
                AuditLog(
                    actor_id=identity[0].id,
                    action=f"{request.method} {request.url.path}",
                    target_type=studio_segments[0] if studio_segments else "studio",
                    target_id=studio_segments[-1] if len(studio_segments) > 1 else None,
                    # X-Request-ID is client-controlled and may be reused; the
                    # audit table needs its own collision-free request key.
                    request_id=uuid4(),
                    actor_ip_hash=hash_identifier(request.client.host) if request.client else None,
                    payload=payload,
                )
            )
        return identity

    return dependency


def request_id(request: Request) -> UUID:
    return request.state.request_id
