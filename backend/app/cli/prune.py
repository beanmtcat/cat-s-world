"""Delete operational rows that only grow: raw visit events, dead sessions, spent tokens.

Run periodically, for example from cron:
    python -m app.cli.prune --visit-days 90
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_

from app.db.session import SessionLocal
from app.models.core import UserSession
from app.models.supplemental import EmailVerificationToken, VisitEvent


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清理过期的访问明细、会话与验证令牌")
    parser.add_argument("--visit-days", type=int, default=90, help="访问明细（含原始 IP）保留天数")
    parser.add_argument("--session-days", type=int, default=30, help="过期或已吊销会话的保留天数")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    if args.visit_days < 1 or args.session_days < 1:
        raise SystemExit("保留天数必须大于 0。")
    now = datetime.now(UTC)
    session_cutoff = now - timedelta(days=args.session_days)
    async with SessionLocal() as session:
        # Daily aggregates in mmcat_visit_daily are kept; only per-view rows go.
        visits = await session.execute(
            delete(VisitEvent).where(VisitEvent.occurred_at < now - timedelta(days=args.visit_days))
        )
        sessions = await session.execute(
            delete(UserSession).where(
                or_(UserSession.expires_at < session_cutoff, UserSession.revoked_at < session_cutoff)
            )
        )
        tokens = await session.execute(
            delete(EmailVerificationToken).where(EmailVerificationToken.expires_at < session_cutoff)
        )
        await session.commit()
    print(f"已清理：访问明细 {visits.rowcount}，会话 {sessions.rowcount}，验证令牌 {tokens.rowcount}。")


if __name__ == "__main__":
    asyncio.run(run(_arguments()))
