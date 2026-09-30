from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.core import Category, Post, Tag
from app.models.supplemental import PostCategory, PostTag


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
    ) -> tuple[list[Post], int]:
        where = [
            Post.isvalid.is_(True),
            Post.status == "published",
            Post.visibility == "public",
            Post.locale == locale,
            Post.robots_index.is_(True),
        ]
        if query:
            normalized = query.strip()
            if len(normalized) > 120:
                raise AppError("SEARCH_QUERY_TOO_LONG", "搜索关键词过长。", 422)
            where.append(Post.title.ilike(f"%{normalized}%"))
        statement = select(Post).where(*where)
        if category_slug:
            statement = statement.join(
                PostCategory,
                (PostCategory.post_id == Post.id) & PostCategory.isvalid.is_(True),
            ).join(
                Category,
                (Category.id == PostCategory.category_id)
                & Category.isvalid.is_(True)
                & (Category.locale == locale),
            ).where(Category.slug == category_slug)
            statement = statement.distinct()
        if tag_slug:
            statement = statement.join(
                PostTag,
                (PostTag.post_id == Post.id) & PostTag.isvalid.is_(True),
            ).join(
                Tag,
                (Tag.id == PostTag.tag_id) & Tag.isvalid.is_(True) & (Tag.locale == locale),
            ).where(Tag.slug == tag_slug)
            statement = statement.distinct()
        total = await session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await session.scalars(
            statement
            .order_by(Post.published_at.desc(), Post.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

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
    async def get_translation_ids(session: AsyncSession, post_id: UUID) -> list[Post]:
        post = await session.get(Post, post_id)
        if post is None or not post.isvalid:
            raise AppError("POST_NOT_FOUND", "文章不存在。", 404)
        rows = await session.scalars(
            select(Post).where(
                Post.translation_group_id == post.translation_group_id,
                Post.status == "published",
                Post.visibility == "public",
                Post.isvalid.is_(True),
            )
        )
        return list(rows)
