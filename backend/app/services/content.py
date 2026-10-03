from __future__ import annotations

import hashlib
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.errors import AppError
from app.models.core import Category, Post, Tag
from app.models.supplemental import PostCategory, PostTag, VisitDaily
from app.schemas.common import escape_like

# Cards only need enough body text to derive a fallback excerpt.  Loading the
# whole (up to 2 MiB) document for every row made list pages needlessly heavy.
EXCERPT_SOURCE_CHARS = 4000


def post_view_paths(slug: str) -> tuple[str, str]:
    """Current and legacy public paths whose daily counters belong to one post."""
    return (f"/archives/{slug}", f"/posts/{slug}")


async def post_view_counts(
    session: AsyncSession, posts: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], int]:
    """Return all-time views keyed by (slug, locale) using a single query."""
    keys = list(posts)
    if not keys:
        return {}
    by_path = {path: slug for slug, _ in keys for path in post_view_paths(slug)}
    rows = await session.execute(
        select(VisitDaily.path, VisitDaily.locale, func.sum(VisitDaily.page_views))
        .where(VisitDaily.path.in_(by_path), VisitDaily.isvalid.is_(True))
        .group_by(VisitDaily.path, VisitDaily.locale)
    )
    totals: dict[tuple[str, str], int] = {}
    for path, locale, count in rows:
        key = (by_path[path], locale)
        totals[key] = totals.get(key, 0) + int(count or 0)
    return totals


class PostService:
    @staticmethod
    async def list_public(
        session: AsyncSession,
        locale: str,
        page: int,
        page_size: int,
        query: str | None,
        category_slug: str | None = None,
        tag_slug: str | None = None,
    ) -> tuple[list[tuple[Post, str]], int]:
        """Return (post, excerpt source) rows; `Post.content` itself is not loaded."""
        where = [
            Post.isvalid.is_(True),
            Post.status == "published",
            Post.visibility == "public",
            Post.locale == locale,
            Post.robots_index.is_(True),
        ]
        if query and query.strip():
            normalized = query.strip()
            if len(normalized) > 120:
                raise AppError("SEARCH_QUERY_TOO_LONG", "搜索关键词过长。", 422)
            pattern = f"%{escape_like(normalized)}%"
            # Title and excerpt only: both are short columns, unlike the body.
            where.append(
                or_(Post.title.ilike(pattern, escape="\\"), Post.summary.ilike(pattern, escape="\\"))
            )
        # EXISTS keeps one row per post, so no DISTINCT over wide text columns.
        if category_slug:
            where.append(
                select(PostCategory.post_id)
                .join(Category, Category.id == PostCategory.category_id)
                .where(
                    PostCategory.post_id == Post.id,
                    PostCategory.isvalid.is_(True),
                    Category.isvalid.is_(True),
                    Category.locale == locale,
                    Category.slug == category_slug,
                )
                .exists()
            )
        if tag_slug:
            where.append(
                select(PostTag.post_id)
                .join(Tag, Tag.id == PostTag.tag_id)
                .where(
                    PostTag.post_id == Post.id,
                    PostTag.isvalid.is_(True),
                    Tag.isvalid.is_(True),
                    Tag.locale == locale,
                    Tag.slug == tag_slug,
                )
                .exists()
            )
        total = await session.scalar(select(func.count()).select_from(Post).where(*where))
        rows = await session.execute(
            select(Post, func.left(Post.content, EXCERPT_SOURCE_CHARS))
            .options(defer(Post.content))
            .where(*where)
            .order_by(Post.published_at.desc(), Post.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(post, excerpt or "") for post, excerpt in rows], int(total or 0)

    @staticmethod
    async def get_public(session: AsyncSession, locale: str, slug: str) -> Post:
        post = await session.scalar(
            select(Post).where(
                Post.isvalid.is_(True),
                Post.status == "published",
                Post.visibility.in_(("public", "unlisted")),
                Post.locale == locale,
                Post.slug == slug,
            )
        )
        if post is None:
            raise AppError("POST_NOT_FOUND", "文章不存在。", 404)
        return post

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    async def get_translations(session: AsyncSession, post_id: UUID) -> list[tuple[Post, str]]:
        group_id = await session.scalar(
            select(Post.translation_group_id).where(
                Post.id == post_id,
                Post.isvalid.is_(True),
                Post.status == "published",
                Post.visibility.in_(("public", "unlisted")),
            )
        )
        if group_id is None:
            raise AppError("POST_NOT_FOUND", "文章不存在。", 404)
        rows = await session.execute(
            select(Post, func.left(Post.content, EXCERPT_SOURCE_CHARS))
            .options(defer(Post.content))
            .where(
                Post.translation_group_id == group_id,
                Post.status == "published",
                Post.visibility == "public",
                Post.isvalid.is_(True),
            )
        )
        return [(post, excerpt or "") for post, excerpt in rows]
