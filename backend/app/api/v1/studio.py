from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import struct
from datetime import UTC, datetime
from math import ceil
from pathlib import PurePath
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import request_id, require_roles
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.core import (
    Category,
    Comment,
    ContentRevision,
    Page,
    Post,
    SiteProperties,
    Tag,
    User,
    UserSession,
)
from app.models.supplemental import (
    AITool,
    FriendLink,
    Gallery,
    GalleryItem,
    Media,
    NavigationItem,
    NavigationMenu,
    OutboxEvent,
    PostCategory,
    PostTag,
    StatusNode,
    VisitDaily,
)
from app.schemas.content import (
    PostSummary,
    StudioCommentModeration,
    StudioCommentReply,
    StudioFriendLinkInput,
    StudioGalleryInput,
    StudioMediaUpdate,
    StudioNavigationInput,
    StudioNavigationMenuInput,
    StudioPageInput,
    StudioPostCreate,
    StudioPostUpdate,
    StudioSitePropertiesUpdate,
    StudioStatusNodeInput,
    StudioTaxonomyInput,
    StudioToolInput,
    StudioUserUpdate,
)
from app.services.auth import revoke_all_user_sessions
from app.services.content import PostService
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/studio", tags=["studio"])

MAX_MEDIA_UPLOAD_BYTES = 50 * 1024 * 1024
_SAFE_MEDIA_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_INLINE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _detect_media_type(header: bytes) -> str | None:
    """Recognize a deliberately small, safe upload allow-list by file signature."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"PK\x03\x04"):
        return "application/zip"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    if header.startswith(b"OggS"):
        return "audio/ogg"
    if header.startswith(b"ID3") or header.startswith(b"\xff\xfb"):
        return "audio/mpeg"
    return None


def _scan_upload(content: BinaryIO) -> None:
    """Fail closed when the configured ClamAV daemon cannot scan an upload."""
    settings = get_settings()
    if not settings.antivirus_host:
        if settings.app_env == "production":
            raise RuntimeError("ANTIVIRUS_HOST is required in production")
        return
    content.seek(0)
    with socket.create_connection((settings.antivirus_host, settings.antivirus_port), timeout=15) as client:
        client.sendall(b"zINSTREAM\0")
        while chunk := content.read(64 * 1024):
            client.sendall(struct.pack("!I", len(chunk)))
            client.sendall(chunk)
        client.sendall(struct.pack("!I", 0))
        response = client.recv(4096).decode("utf-8", "replace")
    if "OK" not in response or "FOUND" in response:
        raise RuntimeError(response or "ClamAV did not return a clean result")


def _payload(post: Post) -> dict[str, object]:
    return PostSummary.model_validate(post).model_dump(mode="json", by_alias=True)


async def _post_library_payload(session: AsyncSession, post: Post) -> dict[str, object]:
    """Add the information the studio list needs without exposing post content."""
    category_rows = (
        await session.execute(
            select(Category.id, Category.name)
            .join(PostCategory, PostCategory.category_id == Category.id)
            .where(PostCategory.post_id == post.id, PostCategory.isvalid.is_(True), Category.isvalid.is_(True))
            .order_by(Category.sort_order, Category.name)
        )
    ).all()
    tag_rows = (
        await session.execute(
            select(Tag.id, Tag.name)
            .join(PostTag, PostTag.tag_id == Tag.id)
            .where(PostTag.post_id == post.id, PostTag.isvalid.is_(True), Tag.isvalid.is_(True))
            .order_by(Tag.name)
        )
    ).all()
    author_name = await session.scalar(select(User.display_name).where(User.id == post.author_id))
    comment_count = await session.scalar(
        select(func.count()).select_from(Comment).where(
            Comment.post_id == post.id,
            Comment.isvalid.is_(True),
            Comment.status == "approved",
        )
    )
    view_count = await session.scalar(
        select(func.coalesce(func.sum(VisitDaily.page_views), 0)).where(
            VisitDaily.isvalid.is_(True),
            VisitDaily.path.in_((f"/archives/{post.slug}", f"/posts/{post.slug}")),
        )
    )
    payload = _payload(post)
    payload.update(
        {
            "status": post.status,
            "visibility": post.visibility,
            "categoryIds": [str(item.id) for item in category_rows],
            "categoryNames": [item.name for item in category_rows],
            "tagIds": [str(item.id) for item in tag_rows],
            "tagNames": [item.name for item in tag_rows],
            "authorName": author_name or "未知作者",
            "commentCount": comment_count or 0,
            "viewCount": view_count or 0,
            "updatedAt": post.mtime,
            "createdAt": post.ctime,
        }
    )
    return payload


async def _post_taxonomy_ids(session: AsyncSession, post_id: UUID) -> tuple[list[str], list[str]]:
    """Return active logical taxonomy relations; the database intentionally has no FK constraints."""
    category_ids = list(
        await session.scalars(
            select(PostCategory.category_id).where(
                PostCategory.post_id == post_id, PostCategory.isvalid.is_(True)
            )
        )
    )
    tag_ids = list(
        await session.scalars(
            select(PostTag.tag_id).where(PostTag.post_id == post_id, PostTag.isvalid.is_(True))
        )
    )
    return [str(item) for item in category_ids], [str(item) for item in tag_ids]


async def _set_post_taxonomies(
    session: AsyncSession,
    post: Post,
    category_ids: list[UUID],
    tag_ids: list[UUID],
) -> None:
    """Validate and replace active category/tag links for one article."""
    category_ids = list(dict.fromkeys(category_ids))
    tag_ids = list(dict.fromkeys(tag_ids))
    active_categories = set(
        await session.scalars(
            select(Category.id).where(
                Category.id.in_(category_ids),
                Category.locale == post.locale,
                Category.isvalid.is_(True),
            )
        )
    ) if category_ids else set()
    active_tags = set(
        await session.scalars(
            select(Tag.id).where(
                Tag.id.in_(tag_ids),
                Tag.locale == post.locale,
                Tag.isvalid.is_(True),
            )
        )
    ) if tag_ids else set()
    if len(active_categories) != len(category_ids) or len(active_tags) != len(tag_ids):
        raise AppError("POST_TAXONOMY_INVALID", "分类或标签不存在、已失效，或与文章语言不一致。", 422)
    existing_category_ids = set(
        await session.scalars(select(PostCategory.category_id).where(PostCategory.post_id == post.id))
    )
    existing_tag_ids = set(
        await session.scalars(select(PostTag.tag_id).where(PostTag.post_id == post.id))
    )
    await session.execute(
        update(PostCategory)
        .where(PostCategory.post_id == post.id, PostCategory.isvalid.is_(True))
        .values(isvalid=False)
    )
    await session.execute(
        update(PostTag)
        .where(PostTag.post_id == post.id, PostTag.isvalid.is_(True))
        .values(isvalid=False)
    )
    if category_ids:
        await session.execute(
            update(PostCategory)
            .where(PostCategory.post_id == post.id, PostCategory.category_id.in_(category_ids))
            .values(isvalid=True)
        )
    if tag_ids:
        await session.execute(
            update(PostTag)
            .where(PostTag.post_id == post.id, PostTag.tag_id.in_(tag_ids))
            .values(isvalid=True)
        )
    session.add_all(
        [PostCategory(post_id=post.id, category_id=item) for item in category_ids if item not in existing_category_ids]
        + [PostTag(post_id=post.id, tag_id=item) for item in tag_ids if item not in existing_tag_ids]
    )


def _entity_payload(item: object, fields: tuple[str, ...]) -> dict[str, object]:
    """Serialize admin entities without leaking operational/internal fields."""
    payload: dict[str, object] = {"id": str(item.id)}
    for field in fields:
        value = getattr(item, field)
        key = "".join([field.split("_")[0], *[part.capitalize() for part in field.split("_")[1:]]])
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, list):
            value = [str(entry) if isinstance(entry, UUID) else entry for entry in value]
        payload[key] = value
    return payload


@router.get("/posts")
async def list_drafts(
    request: Request,
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    user = identity[0]
    query = select(Post).where(Post.isvalid.is_(True))
    if user.role == "author":
        query = query.where(Post.author_id == user.id)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(
        query.order_by(Post.published_at.desc().nulls_last(), Post.mtime.desc(), Post.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "data": [await _post_library_payload(session, post) for post in rows],
        "meta": {
            "requestId": str(request_id(request)),
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": ceil(total / page_size) if total else 0,
        },
    }


@router.post("/posts", status_code=201)
async def create_draft(
    body: StudioPostCreate,
    request: Request,
    identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    user = identity[0]
    post = Post(
        locale=body.locale,
        slug=body.slug,
        title=body.title.strip(),
        summary=body.summary.strip() if body.summary else None,
        content=body.content,
        content_format=body.content_format,
        content_hash=PostService.content_hash(body.content),
        cover_url=str(body.cover_url) if body.cover_url else None,
        author_id=user.id,
        status="draft",
        visibility="private",
    )
    session.add(post)
    await session.flush()
    await _set_post_taxonomies(session, post, body.category_ids, body.tag_ids)
    session.add(
        ContentRevision(
            target_type="post",
            target_id=post.id,
            revision_no=1,
            content=post.content,
            title=post.title,
            summary=post.summary,
            locale=post.locale,
            slug=post.slug,
            editor_id=user.id,
            cover_url=post.cover_url,
            change_summary="创建草稿",
        )
    )
    session.add(
        OutboxEvent(
            event_type="post.draft_created",
            aggregate_type="post",
            aggregate_id=post.id,
            payload={"postId": str(post.id), "authorId": str(user.id)},
            idempotency_key=f"post:{post.id}:draft-created",
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            "POST_SLUG_EXISTS", "该语言下的文章地址已存在。", 409, {"slug": "已被使用"}
        ) from exc
    await session.refresh(post)
    return {"data": _payload(post), "meta": {"requestId": str(request_id(request))}}


async def _editable_post(session: AsyncSession, post_id: UUID, user: User) -> Post:
    # Serialize revisions for one post so revision numbers remain unique under
    # concurrent saves.  The lock is held only for this short transaction.
    post = await session.scalar(select(Post).where(Post.id == post_id).with_for_update())
    if post is None or not post.isvalid:
        raise AppError("POST_NOT_FOUND", "文章不存在。", 404)
    if user.role == "author" and post.author_id != user.id:
        raise AppError("POST_FORBIDDEN", "你无权编辑这篇文章。", 403)
    return post


@router.get("/posts/{post_id}")
async def get_studio_post(
    post_id: UUID,
    request: Request,
    identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    post = await _editable_post(session, post_id, identity[0])
    category_ids, tag_ids = await _post_taxonomy_ids(session, post.id)
    return {
        "data": {
            **_payload(post),
            "content": post.content,
            "status": post.status,
            "visibility": post.visibility,
            "coverUrl": post.cover_url,
            "contentFormat": post.content_format,
            "categoryIds": category_ids,
            "tagIds": tag_ids,
        },
        "meta": {"requestId": str(request_id(request))},
    }


@router.patch("/posts/{post_id}")
async def update_studio_post(
    post_id: UUID,
    body: StudioPostUpdate,
    request: Request,
    identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    user = identity[0]
    post = await _editable_post(session, post_id, user)
    changes = body.model_dump(exclude_unset=True)
    for field in (
        "title",
        "slug",
        "content",
        "summary",
        "cover_url",
        "locale",
        "visibility",
        "content_format",
    ):
        if field in changes:
            setattr(post, field, changes[field])
    if "content" in changes:
        post.content_hash = PostService.content_hash(post.content)
    if "status" in changes:
        post.status = changes["status"]
        if post.status == "published":
            post.visibility = "public" if "visibility" not in changes else post.visibility
            post.published_at = post.published_at or datetime.now(UTC)
        elif post.status == "draft":
            post.visibility = "private" if "visibility" not in changes else post.visibility
            post.published_at = None
    if "category_ids" in changes or "tag_ids" in changes or "locale" in changes:
        current_category_ids, current_tag_ids = await _post_taxonomy_ids(session, post.id)
        await _set_post_taxonomies(
            session,
            post,
            changes.get("category_ids", [UUID(item) for item in current_category_ids]),
            changes.get("tag_ids", [UUID(item) for item in current_tag_ids]),
        )
    await session.flush()
    last_revision = await session.scalar(
        select(ContentRevision.revision_no)
        .where(ContentRevision.target_type == "post", ContentRevision.target_id == post.id)
        .order_by(ContentRevision.revision_no.desc())
        .limit(1)
    )
    session.add(
        ContentRevision(
            target_type="post",
            target_id=post.id,
            revision_no=(last_revision or 0) + 1,
            content=post.content,
            title=post.title,
            summary=post.summary,
            locale=post.locale,
            slug=post.slug,
            editor_id=user.id,
            cover_url=post.cover_url,
            change_summary="工作台更新",
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("POST_SLUG_EXISTS", "该语言下的文章地址已存在。", 409) from exc
    await session.refresh(post)
    return {"data": _payload(post), "meta": {"requestId": str(request_id(request))}}


@router.delete("/posts/{post_id}", status_code=204)
async def delete_studio_post(
    post_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    post = await _editable_post(session, post_id, identity[0])
    post.isvalid = False
    await session.commit()
    return Response(status_code=204)


@router.get("/dashboard")
async def dashboard(
    request: Request,
    identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    user = identity[0]
    post_scope = [Post.isvalid.is_(True)]
    if user.role == "author":
        post_scope.append(Post.author_id == user.id)
    total = await session.scalar(select(func.count(Post.id)).where(*post_scope)) or 0
    published = await session.scalar(
        select(func.count(Post.id)).where(*post_scope, Post.status == "published")
    ) or 0
    drafts = await session.scalar(
        select(func.count(Post.id)).where(*post_scope, Post.status == "draft")
    ) or 0
    pending_comments = 0
    if user.role in {"admin", "editor"}:
        pending_comments = await session.scalar(
            select(func.count(Comment.id)).where(Comment.isvalid.is_(True), Comment.status == "pending")
        ) or 0
    recent = await session.scalars(select(Post).where(*post_scope).order_by(Post.mtime.desc()).limit(6))
    return {
        "data": {
            "totalPosts": total,
            "publishedPosts": published,
            "draftPosts": drafts,
            "pendingComments": pending_comments,
            "recentPosts": [_payload(post) for post in recent],
        },
        "meta": {"requestId": str(request_id(request))},
    }


async def _taxonomy_list(session: AsyncSession, model: type[Category] | type[Tag]) -> list[dict[str, object]]:
    rows = await session.scalars(
        select(model).where(model.isvalid.is_(True)).order_by(model.locale, model.sort_order if model is Category else model.name)
    )
    fields = ("locale", "name", "slug", "description") + (("sort_order",) if model is Category else ())
    return [_entity_payload(item, fields) for item in rows]


async def _taxonomy_create(
    body: StudioTaxonomyInput, model: type[Category] | type[Tag], session: AsyncSession
) -> object:
    data = body.model_dump()
    if model is Tag:
        data.pop("sort_order")
    item = model(**data)
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("TAXONOMY_EXISTS", "该语言下的地址已存在。", 409) from exc
    await session.refresh(item)
    return item


async def _taxonomy_update(
    item_id: UUID, body: StudioTaxonomyInput, model: type[Category] | type[Tag], session: AsyncSession
) -> object:
    item = await session.get(model, item_id)
    if item is None or not item.isvalid:
        raise AppError("TAXONOMY_NOT_FOUND", "分类或标签不存在。", 404)
    for field, value in body.model_dump().items():
        if field == "sort_order" and model is Tag:
            continue
        setattr(item, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("TAXONOMY_EXISTS", "该语言下的地址已存在。", 409) from exc
    await session.refresh(item)
    return item


@router.get("/categories")
async def list_categories(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    return {"data": await _taxonomy_list(session, Category), "meta": {"requestId": str(request_id(request))}}


@router.post("/categories", status_code=201)
async def create_category(body: StudioTaxonomyInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await _taxonomy_create(body, Category, session)
    return {"data": _entity_payload(item, ("locale", "name", "slug", "description", "sort_order")), "meta": {"requestId": str(request_id(request))}}


@router.patch("/categories/{item_id}")
async def update_category(item_id: UUID, body: StudioTaxonomyInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await _taxonomy_update(item_id, body, Category, session)
    return {"data": _entity_payload(item, ("locale", "name", "slug", "description", "sort_order")), "meta": {"requestId": str(request_id(request))}}


@router.delete("/categories/{item_id}", status_code=204)
async def delete_category(item_id: UUID, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> Response:
    item = await session.get(Category, item_id)
    if item is None or not item.isvalid:
        raise AppError("TAXONOMY_NOT_FOUND", "分类不存在。", 404)
    item.isvalid = False
    await session.commit()
    return Response(status_code=204)


@router.get("/tags")
async def list_tags(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor", "author")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    return {"data": await _taxonomy_list(session, Tag), "meta": {"requestId": str(request_id(request))}}


@router.post("/tags", status_code=201)
async def create_tag(body: StudioTaxonomyInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await _taxonomy_create(body, Tag, session)
    return {"data": _entity_payload(item, ("locale", "name", "slug", "description")), "meta": {"requestId": str(request_id(request))}}


@router.patch("/tags/{item_id}")
async def update_tag(item_id: UUID, body: StudioTaxonomyInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await _taxonomy_update(item_id, body, Tag, session)
    return {"data": _entity_payload(item, ("locale", "name", "slug", "description")), "meta": {"requestId": str(request_id(request))}}


@router.delete("/tags/{item_id}", status_code=204)
async def delete_tag(item_id: UUID, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> Response:
    item = await session.get(Tag, item_id)
    if item is None or not item.isvalid:
        raise AppError("TAXONOMY_NOT_FOUND", "标签不存在。", 404)
    item.isvalid = False
    await session.commit()
    return Response(status_code=204)


def _page_payload(page: Page, include_content: bool = False) -> dict[str, object]:
    data = _entity_payload(page, ("locale", "slug", "title", "summary", "status", "visibility", "published_at", "seo_title", "seo_description", "content_format"))
    if include_content:
        data["content"] = page.content
    return data


@router.get("/pages")
async def list_pages(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    rows = await session.scalars(select(Page).where(Page.isvalid.is_(True)).order_by(Page.mtime.desc()).limit(100))
    return {"data": [_page_payload(page) for page in rows], "meta": {"requestId": str(request_id(request))}}


@router.get("/pages/{page_id}")
async def get_page_for_studio(page_id: UUID, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    page = await session.get(Page, page_id)
    if page is None or not page.isvalid:
        raise AppError("PAGE_NOT_FOUND", "独立页不存在。", 404)
    return {"data": _page_payload(page, True), "meta": {"requestId": str(request_id(request))}}


def _apply_page(page: Page, body: StudioPageInput, user: User) -> None:
    for field, value in body.model_dump().items():
        setattr(page, field, value)
    if page.status == "published":
        page.published_at = page.published_at or datetime.now(UTC)
    elif page.status == "draft":
        page.published_at = None
    page.author_id = page.author_id or user.id


@router.post("/pages", status_code=201)
async def create_page(body: StudioPageInput, request: Request, identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    page = Page(author_id=identity[0].id, **body.model_dump())
    if page.status == "published":
        page.published_at = datetime.now(UTC)
    session.add(page)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("PAGE_SLUG_EXISTS", "该语言下的独立页地址已存在。", 409) from exc
    await session.refresh(page)
    return {"data": _page_payload(page), "meta": {"requestId": str(request_id(request))}}


@router.put("/pages/{page_id}")
async def update_page(page_id: UUID, body: StudioPageInput, request: Request, identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    page = await session.get(Page, page_id)
    if page is None or not page.isvalid:
        raise AppError("PAGE_NOT_FOUND", "独立页不存在。", 404)
    _apply_page(page, body, identity[0])
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("PAGE_SLUG_EXISTS", "该语言下的独立页地址已存在。", 409) from exc
    await session.refresh(page)
    return {"data": _page_payload(page), "meta": {"requestId": str(request_id(request))}}


@router.delete("/pages/{page_id}", status_code=204)
async def delete_page(page_id: UUID, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> Response:
    page = await session.get(Page, page_id)
    if page is None or not page.isvalid:
        raise AppError("PAGE_NOT_FOUND", "独立页不存在。", 404)
    page.isvalid = False
    await session.commit()
    return Response(status_code=204)


def _tool_payload(item: AITool) -> dict[str, object]:
    return _entity_payload(item, ("locale", "name", "slug", "url", "description", "provider", "category", "icon_key", "sort_order", "is_featured"))


@router.get("/tools")
async def list_tools(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    rows = await session.scalars(select(AITool).where(AITool.isvalid.is_(True)).order_by(AITool.sort_order, AITool.name))
    return {"data": [_tool_payload(item) for item in rows], "meta": {"requestId": str(request_id(request))}}


@router.post("/tools", status_code=201)
async def create_tool(body: StudioToolInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = AITool(**body.model_dump())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("TOOL_EXISTS", "该语言下的工具地址已存在。", 409) from exc
    await session.refresh(item)
    return {"data": _tool_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.put("/tools/{tool_id}")
async def update_tool(tool_id: UUID, body: StudioToolInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.get(AITool, tool_id)
    if item is None or not item.isvalid:
        raise AppError("TOOL_NOT_FOUND", "AI 工具不存在。", 404)
    for field, value in body.model_dump().items():
        setattr(item, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("TOOL_EXISTS", "该语言下的工具地址已存在。", 409) from exc
    await session.refresh(item)
    return {"data": _tool_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.delete("/tools/{tool_id}", status_code=204)
async def delete_tool(tool_id: UUID, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> Response:
    item = await session.get(AITool, tool_id)
    if item is None or not item.isvalid:
        raise AppError("TOOL_NOT_FOUND", "AI 工具不存在。", 404)
    item.isvalid = False
    await session.commit()
    return Response(status_code=204)


def _navigation_payload(item: NavigationItem) -> dict[str, object]:
    return _entity_payload(
        item,
        (
            "menu_id", "locale", "location", "label", "url", "target_type", "target_ids",
            "icon_key", "parent_id", "sort_order", "open_new_tab",
        ),
    )


def _navigation_menu_payload(item: NavigationMenu) -> dict[str, object]:
    return _entity_payload(item, ("locale", "menu_key", "name", "location", "sort_order"))


async def _navigation_target_url(session: AsyncSession, body: StudioNavigationInput) -> str:
    """Validate editor-selected logical targets and derive their public address."""
    target_ids = list(dict.fromkeys(body.target_ids))
    if body.target_type == "custom":
        if not body.url or not body.url.strip():
            raise AppError("NAVIGATION_URL_REQUIRED", "自定义菜单必须填写链接地址。", 422)
        from urllib.parse import urlsplit

        url = body.url.strip()
        parts = urlsplit(url)
        # Never persist executable protocols in an href.  Relative links must
        # be site-rooted so they cannot be interpreted as protocol-relative.
        if (parts.scheme not in {"http", "https", "mailto"}) and not (
            not parts.scheme and not parts.netloc and parts.path.startswith("/") and not url.startswith("//")
        ):
            raise AppError("NAVIGATION_URL_INVALID", "菜单链接仅支持站内路径、HTTPS、HTTP 或 mailto。", 422)
        return url
    if body.target_type in {"post", "page"}:
        if len(target_ids) != 1:
            raise AppError("NAVIGATION_TARGET_REQUIRED", "文章或独立页菜单必须指定一个目标。", 422)
        model = Post if body.target_type == "post" else Page
        target = await session.get(model, target_ids[0])
        if target is None or not target.isvalid or target.locale != body.locale:
            raise AppError("NAVIGATION_TARGET_INVALID", "指定的菜单目标不存在、已失效或语言不一致。", 422)
        return f"/archives/{target.slug}" if body.target_type == "post" else f"/pages/{target.slug}"
    if not target_ids:
        raise AppError("NAVIGATION_TARGET_REQUIRED", "分类或标签菜单至少选择一个目标。", 422)
    model = Category if body.target_type == "categories" else Tag
    targets = list(
        await session.scalars(
            select(model).where(model.id.in_(target_ids), model.isvalid.is_(True), model.locale == body.locale)
        )
    )
    if len(targets) != len(target_ids):
        raise AppError("NAVIGATION_TARGET_INVALID", "所选分类或标签不存在、已失效或语言不一致。", 422)
    key = "categories" if body.target_type == "categories" else "tags"
    return f"/posts?{key}=" + ",".join(item.slug for item in targets)


async def _validate_navigation_parent(
    session: AsyncSession, *, item_id: UUID | None, parent_id: UUID | None, menu_id: UUID | None, locale: str
) -> None:
    """Allow arbitrary depth while rejecting cross-menu parents and cycles."""
    if parent_id is None:
        return
    if item_id == parent_id:
        raise AppError("NAVIGATION_PARENT_INVALID", "菜单项不能把自己设为父级。", 422)
    visited: set[UUID] = set()
    current_id = parent_id
    while current_id is not None:
        if current_id in visited or current_id == item_id:
            raise AppError("NAVIGATION_PARENT_CYCLE", "菜单父级不能形成循环引用。", 422)
        visited.add(current_id)
        current = await session.get(NavigationItem, current_id)
        if current is None or not current.isvalid:
            raise AppError("NAVIGATION_PARENT_INVALID", "指定的父级菜单项不存在或已失效。", 422)
        if current.menu_id != menu_id or current.locale != locale:
            raise AppError("NAVIGATION_PARENT_SCOPE", "父级菜单项必须属于同一菜单和语言。", 422)
        current_id = current.parent_id


@router.get("/navigation/menus")
async def list_navigation_menus(
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.scalars(
        select(NavigationMenu).where(NavigationMenu.isvalid.is_(True)).order_by(
            NavigationMenu.locale, NavigationMenu.sort_order, NavigationMenu.name
        )
    )
    return {"data": [_navigation_menu_payload(item) for item in rows], "meta": {"requestId": str(request_id(request))}}


@router.post("/navigation/menus", status_code=201)
async def create_navigation_menu(
    body: StudioNavigationMenuInput,
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = NavigationMenu(**body.model_dump())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("NAVIGATION_MENU_EXISTS", "该语言下菜单机器名已存在。", 409) from exc
    await session.refresh(item)
    return {"data": _navigation_menu_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.delete("/navigation/menus/{menu_id}", status_code=204)
async def delete_navigation_menu(
    menu_id: UUID,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    item = await session.get(NavigationMenu, menu_id)
    if item is None or not item.isvalid:
        raise AppError("NAVIGATION_MENU_NOT_FOUND", "菜单不存在。", 404)
    item.isvalid = False
    await session.execute(
        update(NavigationItem).where(NavigationItem.menu_id == item.id, NavigationItem.isvalid.is_(True)).values(isvalid=False)
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/navigation")
async def list_navigation(
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.scalars(
        select(NavigationItem)
        .where(NavigationItem.isvalid.is_(True))
        .order_by(NavigationItem.location, NavigationItem.locale, NavigationItem.sort_order, NavigationItem.label)
    )
    return {"data": [_navigation_payload(item) for item in rows], "meta": {"requestId": str(request_id(request))}}


@router.get("/navigation/targets")
async def list_navigation_targets(
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Return all selectable content, without limiting the article picker to one list page."""
    posts = await session.scalars(
        select(Post).where(Post.isvalid.is_(True)).order_by(Post.published_at.desc(), Post.ctime.desc())
    )
    pages = await session.scalars(select(Page).where(Page.isvalid.is_(True)).order_by(Page.title))
    categories = await session.scalars(
        select(Category).where(Category.isvalid.is_(True)).order_by(Category.sort_order, Category.name)
    )
    tags = await session.scalars(select(Tag).where(Tag.isvalid.is_(True)).order_by(Tag.name))
    def values(rows: object, name: str) -> list[dict[str, str]]:
        return [
            {"id": str(item.id), "title": getattr(item, name), "slug": item.slug, "locale": item.locale}
            for item in rows
        ]
    return {
        "data": {
            "posts": values(posts, "title"),
            "pages": values(pages, "title"),
            "categories": values(categories, "name"),
            "tags": values(tags, "name"),
        },
        "meta": {"requestId": str(request_id(request))},
    }


@router.post("/navigation", status_code=201)
async def create_navigation(
    body: StudioNavigationInput,
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if body.menu_id is not None:
        menu = await session.get(NavigationMenu, body.menu_id)
        if menu is None or not menu.isvalid or menu.locale != body.locale:
            raise AppError("NAVIGATION_MENU_INVALID", "指定菜单不存在、已失效或语言不一致。", 422)
    await _validate_navigation_parent(
        session, item_id=None, parent_id=body.parent_id, menu_id=body.menu_id, locale=body.locale
    )
    values = {
        **body.model_dump(),
        "target_ids": [str(target_id) for target_id in body.target_ids],
        "url": await _navigation_target_url(session, body),
    }
    item = NavigationItem(**values)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return {"data": _navigation_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.put("/navigation/{item_id}")
async def update_navigation(
    item_id: UUID,
    body: StudioNavigationInput,
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = await session.get(NavigationItem, item_id)
    if item is None or not item.isvalid:
        raise AppError("NAVIGATION_NOT_FOUND", "菜单项不存在。", 404)
    if body.menu_id is not None:
        menu = await session.get(NavigationMenu, body.menu_id)
        if menu is None or not menu.isvalid or menu.locale != body.locale:
            raise AppError("NAVIGATION_MENU_INVALID", "指定菜单不存在、已失效或语言不一致。", 422)
    await _validate_navigation_parent(
        session, item_id=item.id, parent_id=body.parent_id, menu_id=body.menu_id, locale=body.locale
    )
    values = {
        **body.model_dump(),
        "target_ids": [str(target_id) for target_id in body.target_ids],
        "url": await _navigation_target_url(session, body),
    }
    for field, value in values.items():
        setattr(item, field, value)
    if values.get("status") == "disabled" or "role" in values:
        await revoke_all_user_sessions(session, item.id)
    await session.commit()
    await session.refresh(item)
    return {"data": _navigation_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.delete("/navigation/{item_id}", status_code=204)
async def delete_navigation(
    item_id: UUID,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    item = await session.get(NavigationItem, item_id)
    if item is None or not item.isvalid:
        raise AppError("NAVIGATION_NOT_FOUND", "菜单项不存在。", 404)
    item.isvalid = False
    await session.commit()
    return Response(status_code=204)


def _status_node_payload(item: StatusNode) -> dict[str, object]:
    return {
        "id": str(item.id),
        "code": item.code,
        "displayNameZh": item.display_name.get("zh-CN", item.code),
        "displayNameEn": item.display_name.get("en-US"),
        "region": item.region,
        "endpoint": item.endpoint,
        "checkType": item.check_type,
        "httpMethod": item.http_method,
        "expectedStatus": item.expected_status,
        "timeoutMs": item.timeout_ms,
        "intervalSeconds": item.interval_seconds,
        "sortOrder": item.sort_order,
        "lastStatus": item.last_status,
        "lastLatencyMs": item.last_latency_ms,
        "lastCheckedAt": item.last_checked_at,
    }


def _apply_status_node(item: StatusNode, body: StudioStatusNodeInput) -> None:
    data = body.model_dump()
    item.display_name = {
        "zh-CN": data.pop("display_name_zh"),
        **({"en-US": data.pop("display_name_en")} if data["display_name_en"] else {}),
    }
    data.pop("display_name_en", None)
    for field, value in data.items():
        setattr(item, field, value)


@router.get("/status-nodes")
async def list_status_nodes(
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.scalars(
        select(StatusNode).where(StatusNode.isvalid.is_(True)).order_by(StatusNode.sort_order, StatusNode.code)
    )
    return {"data": [_status_node_payload(item) for item in rows], "meta": {"requestId": str(request_id(request))}}


@router.post("/status-nodes", status_code=201)
async def create_status_node(
    body: StudioStatusNodeInput,
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = StatusNode(code=body.code, endpoint=body.endpoint)
    _apply_status_node(item, body)
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("STATUS_NODE_EXISTS", "状态节点代码已存在。", 409) from exc
    await session.refresh(item)
    return {"data": _status_node_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.put("/status-nodes/{item_id}")
async def update_status_node(
    item_id: UUID,
    body: StudioStatusNodeInput,
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = await session.get(StatusNode, item_id)
    if item is None or not item.isvalid:
        raise AppError("STATUS_NODE_NOT_FOUND", "状态节点不存在。", 404)
    _apply_status_node(item, body)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("STATUS_NODE_EXISTS", "状态节点代码已存在。", 409) from exc
    await session.refresh(item)
    return {"data": _status_node_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.delete("/status-nodes/{item_id}", status_code=204)
async def delete_status_node(
    item_id: UUID,
    _: tuple[User, UserSession] = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    item = await session.get(StatusNode, item_id)
    if item is None or not item.isvalid:
        raise AppError("STATUS_NODE_NOT_FOUND", "状态节点不存在。", 404)
    item.isvalid = False
    await session.commit()
    return Response(status_code=204)


def _site_payload(item: SiteProperties) -> dict[str, object]:
    return _entity_payload(item, (
        "site_name_zh", "site_name_en", "site_description_zh", "site_description_en",
        "logo_url", "favicon_url", "default_og_image_url", "default_locale", "timezone",
        "comments_enabled", "sitemap_enabled",
    ))


@router.get("/settings/site")
async def get_site_settings(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.scalar(select(SiteProperties).where(SiteProperties.isvalid.is_(True)))
    return {"data": _site_payload(item) if item else None, "meta": {"requestId": str(request_id(request))}}


@router.patch("/settings/site")
async def update_site_settings(body: StudioSitePropertiesUpdate, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.scalar(select(SiteProperties).where(SiteProperties.isvalid.is_(True)))
    if item is None:
        item = SiteProperties(site_name_zh="猫子的世界", site_name_en="Cat's World")
        session.add(item)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await session.commit()
    await session.refresh(item)
    return {"data": _site_payload(item), "meta": {"requestId": str(request_id(request))}}


def _comment_payload(item: Comment) -> dict[str, object]:
    target_type = "post" if item.post_id else "page"
    return {
        "id": str(item.id), "targetType": target_type, "targetId": str(item.post_id or item.page_id),
        "authorName": item.author_name, "content": item.content, "status": item.status,
        "ctime": item.ctime, "moderationNote": item.moderation_note,
        "parentId": str(item.parent_id) if item.parent_id else None,
        "userId": str(item.user_id),
    }


def _friend_link_payload(item: FriendLink) -> dict[str, object]:
    return {
        "id": str(item.id), "locale": item.locale, "name": item.name, "url": item.url,
        "logoUrl": item.logo_url, "description": item.description, "rel": item.rel,
        "target": item.target, "sortOrder": item.sort_order, "ctime": item.ctime,
    }


def _media_payload(item: Media) -> dict[str, object]:
    return {
        "id": str(item.id), "postId": str(item.post_id) if item.post_id else None,
        "sourceUrl": item.source_url, "originUrl": item.origin_url,
        "originalName": item.original_name, "mimeType": item.mime_type,
        "byteSize": item.byte_size, "width": item.width, "height": item.height,
        "sourcePlatform": item.source_platform, "ctime": item.ctime, "mtime": item.mtime,
    }


def _upload_object_key(filename: str) -> str:
    """Generate an opaque, filesystem-safe public object key for one upload."""
    clean_name = _SAFE_MEDIA_NAME.sub("-", PurePath(filename).name).strip(".-")
    if not clean_name:
        clean_name = "attachment"
    settings = get_settings()
    prefix = settings.s3_public_prefix.strip("/") or "public"
    now = datetime.now(UTC)
    return f"{prefix}/{now:%Y/%m}/{now:%d}-{uuid4().hex[:12]}-{clean_name}"


def _public_media_url(object_key: str) -> str:
    return f"{str(get_settings().s3_public_base_url).rstrip('/')}/{object_key}"


def _put_s3_object(object_key: str, content: BinaryIO, mime_type: str) -> str | None:
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=str(settings.s3_endpoint),
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(s3={"addressing_style": "path"}),
    )
    content.seek(0)
    client.upload_fileobj(
        content,
        settings.s3_bucket,
        object_key,
        ExtraArgs={
            "ContentType": mime_type,
            "ContentDisposition": "inline" if mime_type in _INLINE_MEDIA_TYPES else "attachment",
        },
    )
    head = client.head_object(Bucket=settings.s3_bucket, Key=object_key)
    return str(head.get("ETag", "")).strip('"') or None


def _delete_s3_object(object_key: str) -> None:
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=str(settings.s3_endpoint),
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(s3={"addressing_style": "path"}),
    )
    client.delete_object(Bucket=settings.s3_bucket, Key=object_key)


def _gallery_payload(item: Gallery, item_count: int = 0) -> dict[str, object]:
    return {
        "id": str(item.id), "locale": item.locale, "slug": item.slug, "name": item.name,
        "description": item.description, "coverUrl": item.cover_url, "status": item.status,
        "visibility": item.visibility, "sortOrder": item.sort_order, "itemCount": item_count,
    }


def _user_payload(item: User) -> dict[str, object]:
    return {
        "id": str(item.id), "email": item.email, "displayName": item.display_name,
        "role": item.role, "status": item.status, "emailVerifiedAt": item.email_verified_at,
        "lastLoginAt": item.last_login_at, "lockedUntil": item.locked_until,
        "avatarMediaId": str(item.avatar_media_id) if item.avatar_media_id else None,
        "ctime": item.ctime,
    }


async def _set_gallery_media(session: AsyncSession, gallery: Gallery, media_ids: list[UUID]) -> None:
    media_ids = list(dict.fromkeys(media_ids))
    if media_ids:
        found = set(await session.scalars(select(Media.id).where(Media.id.in_(media_ids), Media.isvalid.is_(True))))
        if len(found) != len(media_ids):
            raise AppError("GALLERY_MEDIA_INVALID", "图库中包含不存在或已失效的附件。", 422)
    await session.execute(update(GalleryItem).where(GalleryItem.gallery_id == gallery.id, GalleryItem.isvalid.is_(True)).values(isvalid=False))
    rows = list(await session.scalars(select(GalleryItem).where(GalleryItem.gallery_id == gallery.id)))
    existing = {row.media_id: row for row in rows}
    for order, media_id in enumerate(media_ids):
        row = existing.get(media_id)
        if row is None:
            session.add(GalleryItem(gallery_id=gallery.id, media_id=media_id, sort_order=order))
        else:
            row.isvalid = True
            row.sort_order = order


@router.get("/friend-links")
async def list_friend_links(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    rows = await session.scalars(select(FriendLink).where(FriendLink.isvalid.is_(True)).order_by(FriendLink.locale, FriendLink.sort_order, FriendLink.ctime.desc()))
    return {"data": [_friend_link_payload(item) for item in rows], "meta": {"requestId": str(request_id(request))}}


@router.post("/friend-links", status_code=201)
async def create_friend_link(body: StudioFriendLinkInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = FriendLink(**body.model_dump())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise AppError("FRIEND_LINK_CONFLICT", "该语言下的友情链接地址已存在。", 409) from error
    await session.refresh(item)
    return {"data": _friend_link_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.put("/friend-links/{item_id}")
async def update_friend_link(item_id: UUID, body: StudioFriendLinkInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.get(FriendLink, item_id)
    if item is None or not item.isvalid:
        raise AppError("FRIEND_LINK_NOT_FOUND", "友情链接不存在。", 404)
    for field, value in body.model_dump().items():
        setattr(item, field, value)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise AppError("FRIEND_LINK_CONFLICT", "该语言下的友情链接地址已存在。", 409) from error
    await session.refresh(item)
    return {"data": _friend_link_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.delete("/friend-links/{item_id}", status_code=204)
async def delete_friend_link(item_id: UUID, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> Response:
    item = await session.get(FriendLink, item_id)
    if item is None or not item.isvalid:
        raise AppError("FRIEND_LINK_NOT_FOUND", "友情链接不存在。", 404)
    item.isvalid = False
    await session.commit()
    return Response(status_code=204)


@router.get("/media")
async def list_media(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(60, ge=12, le=120),
    keyword: str | None = Query(None, max_length=240),
    media_type: str | None = Query(None, pattern=r"^(image|video|audio|document|other)$"),
    gallery_id: UUID | None = Query(None),
    ungrouped: bool = Query(False),
    sort: str = Query("newest", pattern=r"^(newest|oldest|name)$"),
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Return a bounded page of assets rather than rendering the whole library at once."""
    filters = [Media.isvalid.is_(True)]
    if keyword and keyword.strip():
        term = f"%{keyword.strip()}%"
        filters.append((Media.original_name.ilike(term)) | (Media.source_url.ilike(term)))
    if media_type == "document":
        filters.append(~Media.mime_type.ilike("image/%"))
        filters.append(~Media.mime_type.ilike("video/%"))
        filters.append(~Media.mime_type.ilike("audio/%"))
    elif media_type and media_type != "other":
        filters.append(Media.mime_type.ilike(f"{media_type}/%"))
    elif media_type == "other":
        filters.extend(
            [
                ~Media.mime_type.ilike("image/%"),
                ~Media.mime_type.ilike("video/%"),
                ~Media.mime_type.ilike("audio/%"),
                ~Media.mime_type.ilike("application/%"),
                ~Media.mime_type.ilike("text/%"),
            ]
        )
    if gallery_id:
        filters.append(
            Media.id.in_(
                select(GalleryItem.media_id).where(
                    GalleryItem.gallery_id == gallery_id, GalleryItem.isvalid.is_(True)
                )
            )
        )
    elif ungrouped:
        filters.append(
            ~Media.id.in_(select(GalleryItem.media_id).where(GalleryItem.isvalid.is_(True)))
        )

    order_by = (
        Media.ctime.asc()
        if sort == "oldest"
        else Media.original_name.asc().nulls_last()
        if sort == "name"
        else Media.ctime.desc()
    )
    total = await session.scalar(select(func.count()).select_from(Media).where(*filters)) or 0
    rows = list(
        await session.scalars(
            select(Media)
            .where(*filters)
            .order_by(order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    media_ids = [item.id for item in rows]
    memberships: dict[UUID, list[str]] = {item_id: [] for item_id in media_ids}
    if media_ids:
        gallery_rows = await session.execute(
            select(GalleryItem.media_id, GalleryItem.gallery_id).where(
                GalleryItem.media_id.in_(media_ids), GalleryItem.isvalid.is_(True)
            )
        )
        for media_id, gallery_id in gallery_rows:
            memberships[media_id].append(str(gallery_id))
    return {
        "data": [
            {**_media_payload(item), "galleryIds": memberships.get(item.id, [])} for item in rows
        ],
        "meta": {
            "requestId": str(request_id(request)),
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": ceil(total / page_size) if total else 0,
        },
    }


@router.patch("/media/{item_id:uuid}")
async def update_media(item_id: UUID, body: StudioMediaUpdate, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.get(Media, item_id)
    if item is None or not item.isvalid:
        raise AppError("MEDIA_NOT_FOUND", "附件不存在。", 404)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await session.commit()
    await session.refresh(item)
    return {"data": _media_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.post("/media/upload", status_code=201)
async def upload_media(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=1000),
    mime_type: str = Query("application/octet-stream", min_length=1, max_length=160),
    gallery_id: UUID | None = Query(None),
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Persist a raw file body in configured S3 and record its complete public URL.

    Uploading through the API keeps S3 credentials out of the browser and works with
    private MinIO buckets.  The endpoint deliberately accepts raw bytes rather than
    multipart form data so no temporary local file is created.
    """
    client_ip = request.client.host if request.client else "unknown"
    await enforce_rate_limit("media-upload:ip", client_ip, limit=30, window_seconds=3600)
    declared_size = request.headers.get("content-length")
    if declared_size:
        try:
            if int(declared_size) > MAX_MEDIA_UPLOAD_BYTES:
                raise AppError("MEDIA_TOO_LARGE", "单个附件不能超过 50 MiB。", 413)
        except ValueError as error:
            raise AppError("MEDIA_SIZE_INVALID", "附件大小请求头无效。", 422) from error
    declared_mime_type = mime_type.strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", declared_mime_type):
        raise AppError("MEDIA_MIME_INVALID", "附件 MIME 类型无效。", 422)
    # Spool after 1 MiB: large requests are bounded by disk rather than worker
    # memory, and hashing happens in the same single pass.
    upload = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")  # noqa: SIM115 - closed in finally
    digest = hashlib.sha256()
    header = bytearray()
    byte_size = 0
    try:
        async for chunk in request.stream():
            byte_size += len(chunk)
            if byte_size > MAX_MEDIA_UPLOAD_BYTES:
                raise AppError("MEDIA_TOO_LARGE", "单个附件不能超过 50 MiB。", 413)
            digest.update(chunk)
            if len(header) < 64:
                header.extend(chunk[: 64 - len(header)])
            upload.write(chunk)
        if not byte_size:
            raise AppError("MEDIA_EMPTY", "不能上传空附件。", 422)
        safe_mime_type = _detect_media_type(bytes(header))
        if safe_mime_type is None:
            raise AppError("MEDIA_TYPE_NOT_ALLOWED", "仅支持经识别的图片、音视频、PDF 或 ZIP 文件。", 422)
        if declared_mime_type != "application/octet-stream" and declared_mime_type != safe_mime_type:
            raise AppError("MEDIA_TYPE_MISMATCH", "文件内容与声明的 MIME 类型不一致。", 422)
        try:
            await asyncio.to_thread(_scan_upload, upload)
        except Exception as error:  # noqa: BLE001 - scanner transport varies by deployment.
            raise AppError("MEDIA_SCAN_FAILED", "附件未通过安全扫描。", 422) from error

        gallery = None
        if gallery_id:
            gallery = await session.get(Gallery, gallery_id)
            if gallery is None or not gallery.isvalid:
                raise AppError("GALLERY_NOT_FOUND", "所选分组不存在。", 404)

        checksum = digest.hexdigest()
        existing = await session.scalar(
            select(Media).where(Media.checksum == checksum, Media.mime_type == safe_mime_type)
        )
        uploaded_object_key = None
        if existing is not None:
            existing.isvalid = True
            existing.original_name = PurePath(filename).name
            item = existing
        else:
            object_key = _upload_object_key(filename)
            try:
                etag = await asyncio.to_thread(_put_s3_object, object_key, upload, safe_mime_type)
            except Exception as error:  # noqa: BLE001 - SDK errors vary by S3-compatible host.
                raise AppError("MEDIA_UPLOAD_FAILED", "S3 上传失败，请检查对象存储配置。", 502) from error
            uploaded_object_key = object_key
            item = Media(
                bucket_name=get_settings().s3_bucket,
                object_key=object_key,
                source_url=_public_media_url(object_key),
                original_name=PurePath(filename).name,
                mime_type=safe_mime_type,
                byte_size=byte_size,
                checksum=checksum,
                etag=etag,
                source_platform="mmcat",
            )
            session.add(item)
            await session.flush()

        if gallery is not None:
            membership = await session.scalar(
                select(GalleryItem).where(
                    GalleryItem.gallery_id == gallery.id,
                    GalleryItem.media_id == item.id,
                )
            )
            if membership is None:
                sort_order = await session.scalar(
                    select(func.coalesce(func.max(GalleryItem.sort_order), -1)).where(
                        GalleryItem.gallery_id == gallery.id, GalleryItem.isvalid.is_(True)
                    )
                )
                session.add(
                    GalleryItem(
                        gallery_id=gallery.id,
                        media_id=item.id,
                        sort_order=(sort_order or -1) + 1,
                    )
                )
            else:
                membership.isvalid = True
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            if uploaded_object_key:
                await asyncio.to_thread(_delete_s3_object, uploaded_object_key)
            raise AppError("MEDIA_CONFLICT", "相同附件正在被上传，请刷新后重试。", 409) from error
        await session.refresh(item)
        return {"data": _media_payload(item), "meta": {"requestId": str(request_id(request))}}
    finally:
        upload.close()


@router.delete("/media/{item_id:uuid}", status_code=204)
async def delete_media(item_id: UUID, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> Response:
    item = await session.get(Media, item_id)
    if item is None or not item.isvalid:
        raise AppError("MEDIA_NOT_FOUND", "附件不存在。", 404)
    item.isvalid = False
    await session.execute(update(GalleryItem).where(GalleryItem.media_id == item.id, GalleryItem.isvalid.is_(True)).values(isvalid=False))
    await session.commit()
    return Response(status_code=204)


@router.get("/galleries")
async def list_galleries(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    query = select(Gallery, func.count(GalleryItem.id)).outerjoin(GalleryItem, (GalleryItem.gallery_id == Gallery.id) & GalleryItem.isvalid.is_(True)).where(Gallery.isvalid.is_(True)).group_by(Gallery.id).order_by(Gallery.sort_order, Gallery.ctime.desc())
    rows = (await session.execute(query)).all()
    return {"data": [_gallery_payload(item, count) for item, count in rows], "meta": {"requestId": str(request_id(request))}}


@router.get("/galleries/{gallery_id}")
async def get_gallery(gallery_id: UUID, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.get(Gallery, gallery_id)
    if item is None or not item.isvalid:
        raise AppError("GALLERY_NOT_FOUND", "图库不存在。", 404)
    media_ids = list(await session.scalars(select(GalleryItem.media_id).where(GalleryItem.gallery_id == item.id, GalleryItem.isvalid.is_(True)).order_by(GalleryItem.sort_order)))
    return {"data": {**_gallery_payload(item, len(media_ids)), "mediaIds": [str(value) for value in media_ids]}, "meta": {"requestId": str(request_id(request))}}


@router.post("/galleries", status_code=201)
async def create_gallery(body: StudioGalleryInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    values = body.model_dump(exclude={"media_ids"})
    item = Gallery(**values)
    session.add(item)
    await session.flush()
    await _set_gallery_media(session, item, body.media_ids)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise AppError("GALLERY_CONFLICT", "该语言下的图库 slug 已存在。", 409) from error
    await session.refresh(item)
    return {"data": _gallery_payload(item, len(body.media_ids)), "meta": {"requestId": str(request_id(request))}}


@router.put("/galleries/{gallery_id}")
async def update_gallery(gallery_id: UUID, body: StudioGalleryInput, request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.get(Gallery, gallery_id)
    if item is None or not item.isvalid:
        raise AppError("GALLERY_NOT_FOUND", "图库不存在。", 404)
    for field, value in body.model_dump(exclude={"media_ids"}).items():
        setattr(item, field, value)
    await _set_gallery_media(session, item, body.media_ids)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise AppError("GALLERY_CONFLICT", "该语言下的图库 slug 已存在。", 409) from error
    await session.refresh(item)
    return {"data": _gallery_payload(item, len(body.media_ids)), "meta": {"requestId": str(request_id(request))}}


@router.delete("/galleries/{gallery_id}", status_code=204)
async def delete_gallery(gallery_id: UUID, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> Response:
    item = await session.get(Gallery, gallery_id)
    if item is None or not item.isvalid:
        raise AppError("GALLERY_NOT_FOUND", "图库不存在。", 404)
    item.isvalid = False
    await session.execute(update(GalleryItem).where(GalleryItem.gallery_id == item.id, GalleryItem.isvalid.is_(True)).values(isvalid=False))
    await session.commit()
    return Response(status_code=204)


@router.get("/users")
async def list_users(request: Request, _: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    rows = await session.scalars(select(User).where(User.isvalid.is_(True)).order_by(User.ctime.desc()).limit(500))
    return {"data": [_user_payload(item) for item in rows], "meta": {"requestId": str(request_id(request))}}


@router.patch("/users/{user_id}")
async def update_user(user_id: UUID, body: StudioUserUpdate, request: Request, identity: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.get(User, user_id)
    if item is None or not item.isvalid:
        raise AppError("USER_NOT_FOUND", "用户不存在。", 404)
    values = body.model_dump(exclude_unset=True)
    avatar_media_id = values.get("avatar_media_id")
    if avatar_media_id is not None:
        avatar = await session.get(Media, avatar_media_id)
        if avatar is None or not avatar.isvalid or not avatar.mime_type.startswith("image/"):
            raise AppError("USER_AVATAR_INVALID", "头像必须选择有效的图片附件。", 422)
    if item.id == identity[0].id and ((values.get("status") and values["status"] != "active") or (values.get("role") and values["role"] != "admin")):
        raise AppError("USER_SELF_PROTECTED", "不能禁用自己或移除自己的管理员角色。", 422)
    removing_admin = item.role == "admin" and (
        values.get("role") not in (None, "admin") or values.get("status") not in (None, "active")
    )
    if removing_admin:
        active_admins = await session.scalar(
            select(func.count(User.id)).where(
                User.isvalid.is_(True), User.role == "admin", User.status == "active"
            )
        )
        if active_admins <= 1:
            raise AppError("LAST_ADMIN_PROTECTED", "不能禁用或降级最后一个活跃管理员。", 422)
    for field, value in values.items():
        setattr(item, field, value)
    await session.commit()
    await session.refresh(item)
    return {"data": _user_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.get("/comments")
async def list_comments(request: Request, status: str | None = None, _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    query = select(Comment).where(Comment.isvalid.is_(True))
    if status:
        query = query.where(Comment.status == status)
    rows = await session.scalars(query.order_by(Comment.ctime.desc()).limit(100))
    return {"data": [_comment_payload(item) for item in rows], "meta": {"requestId": str(request_id(request))}}


@router.patch("/comments/{comment_id}")
async def moderate_comment(comment_id: UUID, body: StudioCommentModeration, request: Request, identity: tuple[User, UserSession] = Depends(require_roles("admin", "editor")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.get(Comment, comment_id)
    if item is None or not item.isvalid:
        raise AppError("COMMENT_NOT_FOUND", "评论不存在。", 404)
    item.status = body.status
    item.moderation_note = body.moderation_note
    item.moderated_by = identity[0].id
    item.moderated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(item)
    return {"data": _comment_payload(item), "meta": {"requestId": str(request_id(request))}}


@router.post("/comments/{comment_id}/reply", status_code=201)
async def reply_to_comment(comment_id: UUID, body: StudioCommentReply, request: Request, identity: tuple[User, UserSession] = Depends(require_roles("admin")), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    parent = await session.get(Comment, comment_id)
    if parent is None or not parent.isvalid:
        raise AppError("COMMENT_NOT_FOUND", "评论不存在。", 404)
    reply = Comment(
        post_id=parent.post_id,
        page_id=parent.page_id,
        parent_id=parent.id,
        user_id=identity[0].id,
        locale=parent.locale,
        author_name=identity[0].display_name,
        content=body.content,
        status="approved",
        moderated_by=identity[0].id,
        moderated_at=datetime.now(UTC),
        source_platform="studio_admin_reply",
    )
    session.add(reply)
    await session.commit()
    await session.refresh(reply)
    return {"data": _comment_payload(reply), "meta": {"requestId": str(request_id(request))}}
