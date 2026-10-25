"""Short-form updates published by the studio or an authenticated feed producer."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdTimestampValidityMixin


class FeedPost(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_feed_posts"
    __table_args__ = (
        UniqueConstraint("unit_id"),
        Index("ix_mmcat_feed_posts_public_order", "isvalid", "published_at"),
    )

    unit_id: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(300))
    kicker: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80), index=True)
    body_md: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(20), default="api", server_default="api")


class FeedLike(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_feed_likes"
    __table_args__ = (UniqueConstraint("post_id", "user_id"),)

    post_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)


class FeedComment(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_feed_comments"
    __table_args__ = (Index("ix_mmcat_feed_comments_post_status", "post_id", "status"),)

    post_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    author_name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
