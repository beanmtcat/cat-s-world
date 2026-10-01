from __future__ import annotations

import re
from datetime import UTC, datetime
from math import ceil
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import request_id, require_csrf
from app.core.errors import AppError
from app.db.session import get_session
from app.models.core import Category, Comment, Page, Post, SiteProperties, Tag, User, UserSession
from app.models.supplemental import (
    AITool,
    FriendLink,
    Gallery,
    GalleryItem,
    Media,
    NavigationItem,
    PostCategory,
    PostTag,
    StatusNode,
    VisitDaily,
)
from app.schemas.content import CommentCreate, CommentPublic, PostDetail, PostSummary, SitePublic
from app.services.analytics import record_post_view
from app.services.content import PostService
from app.services.rate_limit import enforce_rate_limit
from app.services.redirects import resolve_redirect

router = APIRouter(tags=["public"])


def _meta(request: Request) -> dict[str, str]:
    return {"requestId": str(request_id(request))}


def _post_summary(post: Post) -> dict[str, object]:
    payload = PostSummary.model_validate(post).model_dump(mode="json", by_alias=True)
    if not payload["summary"]:
        text = re.sub(r"```.*?```", " ", post.content, flags=re.DOTALL)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"[`*_>#]", "", text)
        # Keep cards useful when legacy Halo entries omitted an excerpt.  This
        # is deliberately plain text: card APIs never expose editor HTML.
        text = text.replace("\n", " ")
        text = " ".join(part for part in text.split() if not part.startswith("#"))
        payload["summary"] = text[:150].strip() or None
    return payload


async def _post_card_payload(session: AsyncSession, post: Post) -> dict[str, object]:
    """Attach the taxonomy and counters displayed by public article cards."""
    payload = _post_summary(post)
    categories = list(
        await session.scalars(
            select(Category.name)
            .join(PostCategory, PostCategory.category_id == Category.id)
            .where(PostCategory.post_id == post.id, PostCategory.isvalid.is_(True), Category.isvalid.is_(True))
            .order_by(Category.sort_order, Category.name)
        )
    )
    tags = list(
        await session.scalars(
            select(Tag.name)
            .join(PostTag, PostTag.tag_id == Tag.id)
            .where(PostTag.post_id == post.id, PostTag.isvalid.is_(True), Tag.isvalid.is_(True))
            .order_by(Tag.name)
        )
    )
    comment_count = await session.scalar(
        select(func.count()).select_from(Comment).where(
            Comment.post_id == post.id, Comment.isvalid.is_(True), Comment.status == "approved"
        )
    )
    view_count = await session.scalar(
        select(func.coalesce(func.sum(VisitDaily.page_views), 0)).where(
            VisitDaily.path.in_((f"/archives/{post.slug}", f"/posts/{post.slug}")),
            VisitDaily.locale == post.locale,
            VisitDaily.isvalid.is_(True),
        )
    )
    payload.update(
        {
            "categories": categories,
            "tags": tags,
            "commentCount": int(comment_count or 0),
            "viewCount": int(view_count or 0),
        }
    )
    return payload


async def _post_card_payloads(session: AsyncSession, posts: list[Post]) -> list[dict[str, object]]:
    """Batch-load card decorations, avoiding four queries for every post."""
    if not posts:
        return []
    post_ids = [post.id for post in posts]
    categories: dict[UUID, list[str]] = {post_id: [] for post_id in post_ids}
    tags: dict[UUID, list[str]] = {post_id: [] for post_id in post_ids}
    comments: dict[UUID, int] = {post_id: 0 for post_id in post_ids}
    views: dict[tuple[str, str], int] = {}
    category_rows = await session.execute(
        select(PostCategory.post_id, Category.name)
        .join(Category, Category.id == PostCategory.category_id)
        .where(PostCategory.post_id.in_(post_ids), PostCategory.isvalid.is_(True), Category.isvalid.is_(True))
        .order_by(PostCategory.post_id, Category.sort_order, Category.name)
    )
    for post_id, name in category_rows:
        categories[post_id].append(name)
    tag_rows = await session.execute(
        select(PostTag.post_id, Tag.name)
        .join(Tag, Tag.id == PostTag.tag_id)
        .where(PostTag.post_id.in_(post_ids), PostTag.isvalid.is_(True), Tag.isvalid.is_(True))
        .order_by(PostTag.post_id, Tag.name)
    )
    for post_id, name in tag_rows:
        tags[post_id].append(name)
    comment_rows = await session.execute(
        select(Comment.post_id, func.count()).where(
            Comment.post_id.in_(post_ids), Comment.isvalid.is_(True), Comment.status == "approved"
        ).group_by(Comment.post_id)
    )
    for post_id, count in comment_rows:
        comments[post_id] = int(count)
    paths = [path for post in posts for path in (f"/archives/{post.slug}", f"/posts/{post.slug}")]
    view_rows = await session.execute(
        select(VisitDaily.path, VisitDaily.locale, func.sum(VisitDaily.page_views)).where(
            VisitDaily.path.in_(paths), VisitDaily.isvalid.is_(True)
        ).group_by(VisitDaily.path, VisitDaily.locale)
    )
    for path, locale, count in view_rows:
        views[(path, locale)] = int(count or 0)
    result = []
    for post in posts:
        payload = _post_summary(post)
        payload.update(
            {
                "categories": categories[post.id],
                "tags": tags[post.id],
                "commentCount": comments[post.id],
                "viewCount": views.get((f"/archives/{post.slug}", post.locale), 0)
                + views.get((f"/posts/{post.slug}", post.locale), 0),
            }
        )
        result.append(payload)
    return result


@router.get("/health/live")
async def health_live(request: Request) -> dict[str, object]:
    return {"data": {"status": "ok"}, "meta": _meta(request)}


@router.get("/health/ready")
async def health_ready(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    await session.execute(select(1))
    return {"data": {"status": "ready"}, "meta": _meta(request)}


@router.get("/posts")
async def list_posts(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    q: str | None = Query(None, max_length=120),
    category: str | None = Query(None, max_length=120),
    tag: str | None = Query(None, max_length=120),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(12, alias="pageSize", ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows, total = await PostService.list_public(session, locale, page, page_size, q, category, tag)
    return {
        "data": await _post_card_payloads(session, rows),
        "meta": {
            **_meta(request),
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": ceil(total / page_size) if total else 0,
        },
    }


@router.get("/home")
async def get_home(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """首页聚合数据；只公开已发布内容与安全的状态展示字段。"""
    posts, total = await PostService.list_public(session, locale, 1, 6, None)
    category_rows = await session.execute(
        select(Category, func.count(PostCategory.post_id).label("post_count"))
        .outerjoin(
            PostCategory, (PostCategory.category_id == Category.id) & PostCategory.isvalid.is_(True)
        )
        .where(Category.locale == locale, Category.isvalid.is_(True))
        .group_by(Category.id)
        .order_by(Category.sort_order, Category.name)
        .limit(12)
    )
    home_category_rows = await session.execute(
        select(Category, func.count(PostCategory.post_id).label("post_count"))
        .outerjoin(
            PostCategory, (PostCategory.category_id == Category.id) & PostCategory.isvalid.is_(True)
        )
        .where(
            Category.locale == locale,
            Category.isvalid.is_(True),
            Category.is_home_visible.is_(True),
        )
        .group_by(Category.id)
        .order_by(Category.sort_order, Category.name)
        .limit(12)
    )
    tag_rows = await session.execute(
        select(Tag, func.count(PostTag.post_id).label("post_count"))
        .outerjoin(PostTag, (PostTag.tag_id == Tag.id) & PostTag.isvalid.is_(True))
        .where(Tag.locale == locale, Tag.isvalid.is_(True))
        .group_by(Tag.id)
        .order_by(func.count(PostTag.post_id).desc(), Tag.name)
        .limit(12)
    )
    tools = await session.scalars(
        select(AITool)
        .where(AITool.locale == locale, AITool.isvalid.is_(True), AITool.is_featured.is_(True))
        .order_by(AITool.sort_order, AITool.name)
        .limit(9)
    )
    nodes = await session.scalars(
        select(StatusNode)
        .where(StatusNode.isvalid.is_(True))
        .order_by(StatusNode.sort_order, StatusNode.code)
        .limit(8)
    )
    site_view_count = await session.scalar(
        select(func.coalesce(func.sum(VisitDaily.page_views), 0)).where(
            VisitDaily.locale == locale,
            VisitDaily.isvalid.is_(True),
        )
    )
    return {
        "data": {
            "postCount": total,
            "siteViewCount": int(site_view_count or 0),
            "posts": await _post_card_payloads(session, posts),
            "categories": [
                {"id": str(item.id), "name": item.name, "slug": item.slug, "postCount": count}
                for item, count in category_rows
            ],
            "homeCategories": [
                {"id": str(item.id), "name": item.name, "slug": item.slug, "postCount": count}
                for item, count in home_category_rows
            ],
            "tags": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "slug": item.slug,
                    "postCount": count,
                }
                for item, count in tag_rows
            ],
            "tools": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "description": item.description,
                    "url": item.url,
                    "iconKey": item.icon_key,
                }
                for item in tools
            ],
            "statusNodes": [
                {
                    "id": str(item.id),
                    "name": item.display_name.get(
                        locale, item.display_name.get("zh-CN", item.code)
                    ),
                    "status": item.last_status,
                    "latencyMs": item.last_latency_ms,
                }
                for item in nodes
            ],
        },
        "meta": _meta(request),
    }


@router.get("/tools")
async def list_public_tools(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.scalars(
        select(AITool)
        .where(AITool.locale == locale, AITool.isvalid.is_(True))
        .order_by(AITool.sort_order, AITool.category, AITool.name)
    )
    return {
        "data": [
            {
                "id": str(item.id),
                "name": item.name,
                "slug": item.slug,
                "url": item.url,
                "description": item.description,
                "provider": item.provider,
                "category": item.category,
                "iconKey": item.icon_key,
                "isFeatured": item.is_featured,
            }
            for item in rows
        ],
        "meta": _meta(request),
    }


@router.get("/tags")
async def list_public_tags(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.execute(
        select(Tag, func.count(PostTag.post_id).label("post_count"))
        .outerjoin(PostTag, (PostTag.tag_id == Tag.id) & PostTag.isvalid.is_(True))
        .where(Tag.locale == locale, Tag.isvalid.is_(True))
        .group_by(Tag.id)
        .order_by(func.count(PostTag.post_id).desc(), Tag.name)
    )
    return {
        "data": [
            {"id": str(item.id), "name": item.name, "slug": item.slug, "postCount": count}
            for item, count in rows
        ],
        "meta": _meta(request),
    }


@router.get("/navigation")
async def get_navigation(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = list(await session.scalars(
        select(NavigationItem)
        .where(
            NavigationItem.isvalid.is_(True),
            NavigationItem.locale == locale,
        )
        .order_by(NavigationItem.location, NavigationItem.sort_order, NavigationItem.ctime, NavigationItem.id)
    ))

    def target_id(item: NavigationItem) -> UUID | None:
        value = (item.target_ids or [None])[0]
        try:
            return UUID(str(value)) if value is not None else None
        except (TypeError, ValueError, AttributeError):
            return None

    category_ids = {value for item in rows if item.target_type == "categories" if (value := target_id(item))}
    tag_ids = {value for item in rows if item.target_type == "tags" if (value := target_id(item))}
    category_urls = {
        item_id: f"/categories/{slug}"
        for item_id, slug in await session.execute(
            select(Category.id, Category.slug).where(
                Category.id.in_(category_ids), Category.isvalid.is_(True), Category.locale == locale
            )
        )
    } if category_ids else {}
    tag_urls = {
        item_id: f"/tags/{slug}"
        for item_id, slug in await session.execute(
            select(Tag.id, Tag.slug).where(Tag.id.in_(tag_ids), Tag.isvalid.is_(True), Tag.locale == locale)
        )
    } if tag_ids else {}

    def public_url(item: NavigationItem) -> str:
        item_target_id = target_id(item)
        if item.target_type == "categories" and item_target_id:
            return category_urls.get(item_target_id, item.url)
        if item.target_type == "tags" and item_target_id:
            return tag_urls.get(item_target_id, item.url)
        return item.url

    return {
        "data": [
            {
                "id": str(item.id),
                "menuId": str(item.menu_id) if item.menu_id else None,
                "location": item.location,
                "label": item.label,
                "url": public_url(item),
                "parentId": str(item.parent_id) if item.parent_id else None,
                "iconKey": item.icon_key,
                "sortOrder": item.sort_order,
                "openNewTab": item.open_new_tab,
            }
            for item in rows
        ],
        "meta": _meta(request),
    }


@router.get("/friend-links")
async def list_friend_links(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.scalars(
        select(FriendLink)
        .where(FriendLink.isvalid.is_(True), FriendLink.locale == locale)
        .order_by(FriendLink.sort_order, FriendLink.name)
    )
    return {
        "data": [
            {"id": str(item.id), "name": item.name, "url": item.url, "logoUrl": item.logo_url,
             "description": item.description, "rel": item.rel, "target": item.target}
            for item in rows
        ],
        "meta": _meta(request),
    }


@router.get("/galleries")
async def list_public_galleries(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.execute(
        select(Gallery, func.count(GalleryItem.id))
        .outerjoin(GalleryItem, (GalleryItem.gallery_id == Gallery.id) & GalleryItem.isvalid.is_(True))
        .where(Gallery.isvalid.is_(True), Gallery.collection_kind == "gallery", Gallery.locale == locale, Gallery.status == "published", Gallery.visibility == "public")
        .group_by(Gallery.id)
        .order_by(Gallery.sort_order, Gallery.ctime.desc())
    )
    return {"data": [
        {"id": str(item.id), "slug": item.slug, "name": item.name, "description": item.description,
         "coverUrl": item.cover_url, "itemCount": count}
        for item, count in rows
    ], "meta": _meta(request)}


@router.get("/galleries/{slug}")
async def get_public_gallery(
    slug: str,
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    gallery = await session.scalar(select(Gallery).where(
        Gallery.isvalid.is_(True), Gallery.collection_kind == "gallery", Gallery.locale == locale, Gallery.slug == slug,
        Gallery.status == "published", Gallery.visibility.in_(("public", "unlisted")),
    ))
    if gallery is None:
        raise AppError("GALLERY_NOT_FOUND", "图库不存在。", 404)
    item_filter = (
        GalleryItem.gallery_id == gallery.id,
        GalleryItem.isvalid.is_(True),
        Media.isvalid.is_(True),
    )
    total = await session.scalar(
        select(func.count())
        .select_from(GalleryItem)
        .join(Media, Media.id == GalleryItem.media_id)
        .where(*item_filter)
    )
    items = await session.execute(
        select(Media, GalleryItem).join(GalleryItem, GalleryItem.media_id == Media.id).where(
            *item_filter
        # Imported attachments retain their original timestamp on Media.  Prefer an
        # explicitly curated capture time when available, otherwise show newest
        # files first.  The id tie-breaker keeps infinite-scroll pages stable.
        ).order_by(
            func.coalesce(GalleryItem.captured_at, Media.ctime).desc(),
            GalleryItem.id.desc(),
        ).offset((page - 1) * page_size).limit(page_size)
    )
    total_count = int(total or 0)
    return {"data": {
        "id": str(gallery.id), "slug": gallery.slug, "name": gallery.name, "description": gallery.description,
        "coverUrl": gallery.cover_url,
        "items": [{"id": str(media.id), "url": media.source_url, "mimeType": media.mime_type,
                   "name": media.original_name, "alt": item.alt_text or media.original_name,
                   "title": item.title, "description": item.description}
                  for media, item in items],
    }, "meta": {**_meta(request), "page": page, "pageSize": page_size,
                "total": total_count, "totalPages": ceil(total_count / page_size) if total_count else 0}}


@router.get("/posts/{slug}")
async def get_post(
    slug: str,
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    post = await PostService.get_public(session, locale, slug)
    payload = PostDetail.model_validate(post).model_dump(mode="json", by_alias=True)
    categories = [
        {"name": name, "slug": category_slug}
        for name, category_slug in (
            await session.execute(
                select(Category.name, Category.slug)
                .join(PostCategory, PostCategory.category_id == Category.id)
                .where(
                    PostCategory.post_id == post.id,
                    PostCategory.isvalid.is_(True),
                    Category.isvalid.is_(True),
                )
                .order_by(Category.sort_order, Category.name)
            )
        ).all()
    ]
    tags = [
        {"name": name, "slug": tag_slug}
        for name, tag_slug in (
            await session.execute(
                select(Tag.name, Tag.slug)
                .join(PostTag, PostTag.tag_id == Tag.id)
                .where(
                    PostTag.post_id == post.id,
                    PostTag.isvalid.is_(True),
                    Tag.isvalid.is_(True),
                )
                .order_by(Tag.name)
            )
        ).all()
    ]
    payload["categories"] = categories
    payload["tags"] = tags
    payload["commentCount"] = int(
        await session.scalar(
            select(func.count()).select_from(Comment).where(
                Comment.post_id == post.id,
                Comment.isvalid.is_(True),
                Comment.status == "approved",
            )
        )
        or 0
    )
    view_count = await record_post_view(session, request, slug=post.slug, locale=post.locale)
    if view_count is not None:
        payload["viewCount"] = view_count
        await session.commit()
    else:
        payload["viewCount"] = await session.scalar(
            select(func.coalesce(func.sum(VisitDaily.page_views), 0)).where(
                VisitDaily.path.in_((f"/archives/{post.slug}", f"/posts/{post.slug}")),
                VisitDaily.locale == post.locale,
                VisitDaily.isvalid.is_(True),
            )
        ) or 0
    return {"data": payload, "meta": _meta(request)}


@router.get("/posts/{post_id}/translations")
async def post_translations(
    post_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    posts = await PostService.get_translation_ids(session, post_id)
    return {"data": [_post_summary(post) for post in posts], "meta": _meta(request)}


@router.get("/pages/{slug}")
async def get_page(
    slug: str,
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    page = await session.scalar(
        select(Page).where(
            Page.isvalid.is_(True),
            Page.status == "published",
            Page.visibility.in_(("public", "unlisted")),
            Page.locale == locale,
            Page.slug == slug,
        )
    )
    if page is None:
        raise AppError("PAGE_NOT_FOUND", "页面不存在。", 404)
    return {
        "data": {
            "id": str(page.id),
            "locale": page.locale,
            "slug": page.slug,
            "title": page.title,
            "content": page.content,
            "contentFormat": page.content_format,
            "canonicalUrl": page.canonical_url,
            "robotsIndex": page.robots_index,
            "robotsFollow": page.robots_follow,
        },
        "meta": _meta(request),
    }


@router.get("/site")
async def get_site(
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    properties = await session.scalar(
        select(SiteProperties).where(SiteProperties.isvalid.is_(True))
    )
    if properties is None:
        raise AppError("SITE_NOT_CONFIGURED", "站点尚未完成配置。", 503)
    site_name = properties.site_name_zh if locale == "zh-CN" else properties.site_name_en
    description = (
        properties.site_description_zh if locale == "zh-CN" else properties.site_description_en
    )
    data = SitePublic(
        site_name=site_name,
        site_description=description,
        logo_url=properties.logo_url,
        favicon_url=properties.favicon_url,
        default_og_image_url=properties.default_og_image_url,
        locale=locale,
        comments_enabled=properties.comments_enabled,
        sitemap_enabled=properties.sitemap_enabled,
        posts_per_page=properties.posts_per_page,
        category_posts_per_page=properties.category_posts_per_page,
        tag_posts_per_page=properties.tag_posts_per_page,
        site_keywords=properties.site_keywords,
        seo_noindex=properties.seo_noindex,
        registration_enabled=properties.registration_enabled,
    )
    return {"data": data.model_dump(mode="json", by_alias=True), "meta": _meta(request)}


@router.get("/redirects/resolve", status_code=200, response_model=None)
async def get_redirect(
    request: Request,
    path: str = Query(..., min_length=1, max_length=2048),
    session: AsyncSession = Depends(get_session),
) -> Response | dict[str, object]:
    redirect = await resolve_redirect(session, path)
    if redirect is None:
        return Response(status_code=204)
    redirect.hit_count += 1
    redirect.last_hit_at = datetime.now(UTC)
    await session.commit()
    return {
        "data": {"toPath": redirect.to_path, "statusCode": redirect.status_code},
        "meta": _meta(request),
    }


@router.get("/comments")
async def list_comments(
    request: Request,
    post_id: UUID | None = Query(None, alias="postId"),
    page_id: UUID | None = Query(None, alias="pageId"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if (post_id is None) == (page_id is None):
        raise AppError("COMMENT_TARGET_REQUIRED", "必须且只能指定一个评论目标。", 422)
    target_type, target_id = ("post", post_id) if post_id else ("page", page_id)
    target_column = Comment.post_id if target_type == "post" else Comment.page_id
    where = [Comment.isvalid.is_(True), Comment.status == "approved", target_column == target_id]
    root_where = [*where, Comment.parent_id.is_(None)]
    total = int(await session.scalar(select(func.count()).select_from(Comment).where(*root_where)) or 0)
    roots = list(await session.scalars(
        select(Comment)
        .where(*root_where)
        .order_by(Comment.ctime.asc(), Comment.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ))

    # The public page is a conversation, not a chronological flat feed.  Load
    # all approved descendants for the requested target and attach them below
    # the paginated root comments.  This also avoids showing an admin reply as
    # a second unrelated comment card.
    rows = list(await session.scalars(
        select(Comment)
        .where(*where, Comment.parent_id.is_not(None))
        .order_by(Comment.ctime.asc(), Comment.id)
    ))
    children_by_parent: dict[UUID, list[Comment]] = {}
    for row in rows:
        if row.parent_id is not None:
            children_by_parent.setdefault(row.parent_id, []).append(row)

    def comment_tree(row: Comment, ancestors: frozenset[UUID] = frozenset()) -> dict[str, object]:
        # A malformed imported parent relation must never cause recursive JSON
        # construction to loop forever.
        children = [
            comment_tree(child, ancestors | {row.id})
            for child in children_by_parent.get(row.id, [])
            if child.id not in ancestors
        ]
        payload = CommentPublic(
            id=row.id,
            target_type="post" if row.post_id else "page",
            target_id=row.post_id or row.page_id,
            parent_id=row.parent_id,
            author_name=row.author_name,
            content=row.content,
            status=row.status,
            ctime=row.ctime,
        ).model_dump(mode="json", by_alias=True)
        payload["replies"] = children
        return payload

    data = [comment_tree(row, frozenset({row.id})) for row in roots]
    return {
        "data": data,
        "meta": {
            **_meta(request),
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": ceil(total / page_size) if total else 0,
        },
    }


@router.post("/comments", status_code=201)
async def create_comment(
    body: CommentCreate,
    request: Request,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    client_ip = request.client.host if request.client else "unknown"
    await enforce_rate_limit("comment:ip", client_ip, limit=12, window_seconds=3600)
    user, _ = identity
    properties = await session.scalar(
        select(SiteProperties).where(SiteProperties.isvalid.is_(True))
    )
    if properties is None or not properties.comments_enabled:
        raise AppError("COMMENTS_DISABLED", "站点当前未开放评论。", 403)
    if user.status != "active" or user.email_verified_at is None:
        raise AppError("COMMENT_LOGIN_REQUIRED", "请验证邮箱后再发表评论。", 401)
    target_model = Post if body.target_type == "post" else Page
    target = await session.get(target_model, body.target_id)
    if (
        target is None
        or not target.isvalid
        or target.status != "published"
        or target.visibility != "public"
    ):
        raise AppError("COMMENT_TARGET_NOT_FOUND", "评论目标不存在。", 404)
    if body.parent_id is not None:
        parent = await session.get(Comment, body.parent_id)
        if parent is None or not parent.isvalid:
            raise AppError("COMMENT_PARENT_INVALID", "回复目标无效。", 422)
        parent_target_id = parent.post_id if body.target_type == "post" else parent.page_id
        if parent_target_id != body.target_id:
            raise AppError("COMMENT_PARENT_INVALID", "回复目标无效。", 422)
    content = body.content.strip()
    comment = Comment(
        post_id=body.target_id if body.target_type == "post" else None,
        page_id=body.target_id if body.target_type == "page" else None,
        parent_id=body.parent_id,
        user_id=user.id,
        author_name=user.display_name,
        content=content,
        status="pending" if properties.comments_moderation_enabled else "approved",
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    data = CommentPublic(
        id=comment.id,
        target_type=body.target_type,
        target_id=body.target_id,
        parent_id=comment.parent_id,
        author_name=comment.author_name,
        content=comment.content,
        status=comment.status,
        ctime=comment.ctime,
    )
    return {"data": data.model_dump(mode="json", by_alias=True), "meta": _meta(request)}
