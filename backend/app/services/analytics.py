"""Public page-view recording."""

from __future__ import annotations

from datetime import UTC, datetime, time

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_identifier
from app.models.supplemental import VisitDaily, VisitEvent

_BOT_MARKERS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "facebookexternalhit",
    "twitterbot",
    "linkedinbot",
    "headless",
)


def _is_probable_bot(user_agent: str) -> bool:
    """Do not let crawlers inflate public reading counts."""
    normalized = user_agent.casefold()
    return not normalized or any(marker in normalized for marker in _BOT_MARKERS)


async def record_post_view(
    session: AsyncSession,
    request: Request,
    *,
    slug: str,
    locale: str,
) -> int | None:
    """Record one public article view and return the all-time count.

    The original client IP is retained for the site's own security and
    analytics needs. User-Agent remains hashed and known crawlers are ignored.
    """
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    if not client_ip or _is_probable_bot(user_agent):
        return None

    occurred_at = datetime.now(UTC)
    day_start = datetime.combine(occurred_at.date(), time.min, tzinfo=UTC)
    path = f"/archives/{slug}"
    visitor_hash = hash_identifier(client_ip)
    session_hash = hash_identifier(f"{client_ip}\n{user_agent}\n{occurred_at.date().isoformat()}")

    # Create the daily row atomically, then serialize updates for this
    # path/day/locale.  This prevents lost increments and first-view races.
    await session.execute(
        insert(VisitDaily)
        .values(
            stat_date=occurred_at.date(), path=path, locale=locale,
            page_views=0, unique_visitors=0, sessions=0,
        )
        .on_conflict_do_nothing(index_elements=["stat_date", "path", "locale"])
    )
    daily = await session.scalar(
        select(VisitDaily)
        .where(
            VisitDaily.stat_date == occurred_at.date(), VisitDaily.path == path,
            VisitDaily.locale == locale, VisitDaily.isvalid.is_(True),
        )
        .with_for_update()
    )
    if daily is None:
        return None
    seen_visitor = await session.scalar(
        select(VisitEvent.id).where(
            VisitEvent.path == path,
            VisitEvent.locale == locale,
            VisitEvent.visitor_hash == visitor_hash,
            VisitEvent.occurred_at >= day_start,
            VisitEvent.isvalid.is_(True),
        ).limit(1)
    )
    seen_session = await session.scalar(
        select(VisitEvent.id).where(
            VisitEvent.path == path,
            VisitEvent.locale == locale,
            VisitEvent.session_hash == session_hash,
            VisitEvent.occurred_at >= day_start,
            VisitEvent.isvalid.is_(True),
        ).limit(1)
    )
    daily.page_views += 1
    if seen_visitor is None:
        daily.unique_visitors += 1
    if seen_session is None:
        daily.sessions += 1
    session.add(
        VisitEvent(
            ip_address=client_ip,
            visitor_hash=visitor_hash,
            session_hash=session_hash,
            path=path,
            locale=locale,
            occurred_at=occurred_at,
        )
    )
    await session.flush()
    return await session.scalar(
        select(func.coalesce(func.sum(VisitDaily.page_views), 0)).where(
            VisitDaily.path.in_((path, f"/posts/{slug}")),
            VisitDaily.locale == locale,
            VisitDaily.isvalid.is_(True),
        )
    )
