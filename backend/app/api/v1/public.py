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
from app.models.supplemental import AITool, PostCategory, PostTag, StatusNode
from app.schemas.content import CommentCreate, CommentPublic, PostDetail, PostSummary, SitePublic
from app.services.content import PostService
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
    page: int = Query(1, ge=1),
    page_size: int = Query(12, alias="pageSize", ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows, total = await PostService.list_public(session, locale, page, page_size, q, category)
    return {
        "data": [_post_summary(row) for row in rows],
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
    return {
        "data": {
            "postCount": total,
            "posts": [_post_summary(post) for post in posts],
            "categories": [
                {"id": str(item.id), "name": item.name, "slug": item.slug, "postCount": count}
                for item, count in category_rows
            ],
            "tags": [
                {"id": str(item.id), "name": item.name, "postCount": count}
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


@router.get("/posts/{slug}")
async def get_post(
    slug: str,
    request: Request,
    locale: str = Query("zh-CN", pattern="^(zh-CN|en-US)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    post = await PostService.get_public(session, locale, slug)
    payload = PostDetail.model_validate(post).model_dump(mode="json", by_alias=True)
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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if (post_id is None) == (page_id is None):
        raise AppError("COMMENT_TARGET_REQUIRED", "必须且只能指定一个评论目标。", 422)
    target_type, target_id = ("post", post_id) if post_id else ("page", page_id)
    target_column = Comment.post_id if target_type == "post" else Comment.page_id
    where = [Comment.isvalid.is_(True), Comment.status == "approved", target_column == target_id]
    total = int(await session.scalar(select(func.count()).select_from(Comment).where(*where)) or 0)
    rows = await session.scalars(
        select(Comment)
        .where(*where)
        .order_by(Comment.ctime.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    data = [
        CommentPublic(
            id=row.id,
            target_type="post" if row.post_id else "page",
            target_id=row.post_id or row.page_id,
            parent_id=row.parent_id,
            author_name=row.author_name,
            content=row.content,
            status=row.status,
            ctime=row.ctime,
        ).model_dump(mode="json", by_alias=True)
        for row in rows
    ]
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
    user, _ = identity
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
        if parent is None:
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
        status="pending",
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
