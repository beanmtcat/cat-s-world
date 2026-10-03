"""Public page-view recording."""

from __future__ import annotations

from datetime import UTC, datetime, time

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_identifier
from app.models.supplemental import VisitDaily, VisitEvent
from app.services.content import post_view_counts

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

    # One query answers both "seen today?" questions.  A concurrent first view
    # may be counted twice as unique; that is an acceptable trade for not
    # holding a row lock across several round trips.
    seen_visitor, seen_session = (
        await session.execute(
            select(
                func.coalesce(func.bool_or(VisitEvent.visitor_hash == visitor_hash), False),
                func.coalesce(func.bool_or(VisitEvent.session_hash == session_hash), False),
            ).where(
                VisitEvent.path == path,
                VisitEvent.locale == locale,
                VisitEvent.occurred_at >= day_start,
                VisitEvent.isvalid.is_(True),
                or_(
                    VisitEvent.visitor_hash == visitor_hash,
                    VisitEvent.session_hash == session_hash,
                ),
            )
        )
    ).one()
    new_visitor, new_session = int(not seen_visitor), int(not seen_session)
    # A single atomic upsert replaces insert + SELECT ... FOR UPDATE + UPDATE.
    upsert = insert(VisitDaily).values(
        stat_date=occurred_at.date(),
        path=path,
        locale=locale,
        page_views=1,
        unique_visitors=new_visitor,
        sessions=new_session,
    )
    await session.execute(
        upsert.on_conflict_do_update(
            index_elements=["stat_date", "path", "locale"],
            set_={
                "page_views": VisitDaily.page_views + 1,
                "unique_visitors": VisitDaily.unique_visitors + new_visitor,
                "sessions": VisitDaily.sessions + new_session,
                "mtime": func.now(),
            },
        )
    )
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
    totals = await post_view_counts(session, [(slug, locale)])
    return totals.get((slug, locale), 0)
