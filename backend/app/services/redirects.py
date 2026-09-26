from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.core import Redirect


def normalize_legacy_path(raw_path: str) -> str:
    if not raw_path or len(raw_path.encode("utf-8")) > 2048:
        raise AppError("INVALID_REDIRECT_PATH", "旧地址无效。", 422)
    parts = urlsplit(raw_path)
    if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith("/"):
        raise AppError("INVALID_REDIRECT_PATH", "旧地址必须是站内路径。", 422)
    if "\\" in parts.path or "/../" in f"/{parts.path}/":
        raise AppError("INVALID_REDIRECT_PATH", "旧地址无效。", 422)
    path = "/" + "/".join(part for part in parts.path.split("/") if part)
    if parts.path == "/":
        path = "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)), doseq=True)
    return f"{path}?{query}" if query else path


async def resolve_redirect(session: AsyncSession, raw_path: str) -> Redirect | None:
    source = normalize_legacy_path(raw_path)
    redirect = await session.scalar(
        select(Redirect).where(Redirect.isvalid.is_(True), Redirect.from_path == source)
    )
    if redirect is not None and not redirect.to_path.startswith("/"):
        raise AppError("INVALID_REDIRECT_TARGET", "重定向目标配置无效。", 500)
    return redirect
