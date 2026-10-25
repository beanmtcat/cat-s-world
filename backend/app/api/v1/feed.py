"""Public feed, studio management and authenticated external publishing endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from datetime import UTC, datetime
from math import ceil
from tempfile import SpooledTemporaryFile
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import request_id, require_csrf, require_roles
from app.api.v1.studio import (
    _delete_s3_object,
    _detect_media_type,
    _public_media_url,
    _put_s3_object,
    _scan_upload,
    _upload_object_key,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.models.core import User, UserSession
from app.models.feed import FeedComment, FeedLike, FeedPost
from app.models.supplemental import Gallery, GalleryItem
from app.schemas.feed import FeedCommentCreate, FeedCommentModeration, FeedPostPush, FeedPostStudio
from app.services.auth import current_session
from app.services.images import (
    register_image_variants,
    reusable_image_query,
    store_image_variants,
    thumbnail_urls,
)
from app.services.rate_limit import enforce_rate_limit

public_router = APIRouter(tags=["feed"])
studio_router = APIRouter(prefix="/studio/feed-posts", tags=["studio-feed"])
push_router = APIRouter(tags=["feed-push"])
MAX_FEED_IMAGE_BYTES = 10 * 1024 * 1024
FEED_IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def _payload(item: FeedPost) -> dict[str, object]:
    return {
        "id": str(item.id),
        "unitId": item.unit_id,
        "title": item.title,
        "kicker": item.kicker,
        "category": item.category,
        "bodyMd": item.body_md,
        "publishedAt": item.published_at.isoformat(),
        "source": item.source,
        "ctime": item.ctime.isoformat() if item.ctime else None,
    }


def _comment_payload(item: FeedComment) -> dict[str, object]:
    return {
        "id": str(item.id),
        "postId": str(item.post_id),
        "authorName": item.author_name,
        "body": item.body,
        "status": item.status,
        "ctime": item.ctime.isoformat(),
    }


def _meta(request: Request, page: int, page_size: int, total: int) -> dict[str, object]:
    return {
        "requestId": str(request_id(request)),
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": ceil(total / page_size) if total else 0,
    }


def _check_push_token(authorization: str | None) -> None:
    configured = get_settings().feed_posts_token_hash
    expected = configured.get_secret_value().strip().lower() if configured else ""
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise AppError("FEED_PUSH_NOT_CONFIGURED", "动态推送接口尚未配置。", 503)
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or not secrets.compare_digest(
        hashlib.sha256(token.encode("utf-8")).hexdigest(), expected
    ):
        raise AppError("FEED_PUSH_UNAUTHORIZED", "Bearer token 无效。", 401)


@public_router.get("/feed-posts")
async def list_feed_posts(
    request: Request,
    category: str | None = Query(None, max_length=80),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(20, alias="pageSize", ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    conditions = [FeedPost.isvalid.is_(True), FeedPost.published_at <= datetime.now(UTC)]
    if category:
        conditions.append(FeedPost.category == category)
    category_rows = await session.scalars(
        select(FeedPost.category)
        .where(FeedPost.isvalid.is_(True), FeedPost.published_at <= datetime.now(UTC))
        .distinct().order_by(FeedPost.category)
    )
    categories = list(category_rows)
    total = await session.scalar(select(func.count()).select_from(FeedPost).where(*conditions)) or 0
    rows = await session.scalars(
        select(FeedPost)
        .where(*conditions)
        .order_by(FeedPost.published_at.desc(), FeedPost.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(rows)
    ids = [item.id for item in items]
    likes: dict[UUID, int] = {}
    comments: dict[UUID, int] = {}
    liked_ids: set[UUID] = set()
    if ids:
        likes = dict((await session.execute(
            select(FeedLike.post_id, func.count())
            .where(FeedLike.post_id.in_(ids), FeedLike.isvalid.is_(True))
            .group_by(FeedLike.post_id)
        )).all())
        comments = dict((await session.execute(
            select(FeedComment.post_id, func.count())
            .where(FeedComment.post_id.in_(ids), FeedComment.isvalid.is_(True), FeedComment.status == "approved")
            .group_by(FeedComment.post_id)
        )).all())
        cookie = request.cookies.get(get_settings().session_cookie_name)
        if cookie:
            try:
                user, _ = await current_session(session, cookie)
                liked_ids = set((await session.scalars(
                    select(FeedLike.post_id).where(FeedLike.post_id.in_(ids), FeedLike.user_id == user.id, FeedLike.isvalid.is_(True))
                )).all())
            except AppError:
                pass
    data = []
    for item in items:
        payload = _payload(item)
        payload.update({"likeCount": likes.get(item.id, 0), "commentCount": comments.get(item.id, 0), "likedByMe": item.id in liked_ids})
        data.append(payload)
    return {"data": data, "meta": {**_meta(request, page, page_size, total), "categories": categories}}


@public_router.post("/feed-posts/{item_id}/like")
async def toggle_feed_like(
    item_id: UUID,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = await session.scalar(select(FeedPost.id).where(FeedPost.id == item_id, FeedPost.isvalid.is_(True)))
    if not item:
        raise AppError("FEED_POST_NOT_FOUND", "动态不存在。", 404)
    existing = await session.scalar(select(FeedLike).where(FeedLike.post_id == item_id, FeedLike.user_id == identity[0].id))
    if existing:
        if existing.isvalid:
            existing.isvalid = False
            liked = False
        else:
            existing.isvalid = True
            liked = True
    else:
        session.add(FeedLike(post_id=item_id, user_id=identity[0].id))
        liked = True
    await session.commit()
    count = await session.scalar(select(func.count()).select_from(FeedLike).where(FeedLike.post_id == item_id, FeedLike.isvalid.is_(True))) or 0
    return {"data": {"liked": liked, "count": count}}


@public_router.get("/feed-posts/{item_id}/comments")
async def list_feed_comments(item_id: UUID, session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    item = await session.scalar(select(FeedPost.id).where(FeedPost.id == item_id, FeedPost.isvalid.is_(True)))
    if not item:
        raise AppError("FEED_POST_NOT_FOUND", "动态不存在。", 404)
    rows = await session.scalars(
        select(FeedComment)
        .where(FeedComment.post_id == item_id, FeedComment.isvalid.is_(True), FeedComment.status == "approved")
        .order_by(FeedComment.ctime.asc()).limit(100)
    )
    return {"data": [_comment_payload(row) for row in rows]}


@public_router.post("/feed-posts/{item_id}/comments", status_code=201)
async def create_feed_comment(
    item_id: UUID,
    body: FeedCommentCreate,
    identity: tuple[User, UserSession] = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = await session.scalar(select(FeedPost.id).where(FeedPost.id == item_id, FeedPost.isvalid.is_(True)))
    if not item:
        raise AppError("FEED_POST_NOT_FOUND", "动态不存在。", 404)
    await enforce_rate_limit("feed-comment", str(identity[0].id), limit=5, window_seconds=300)
    comment = FeedComment(
        post_id=item_id,
        user_id=identity[0].id,
        author_name=identity[0].display_name or identity[0].email,
        body=body.body,
        status="approved" if identity[0].role in {"admin", "editor"} else "pending",
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return {"data": _comment_payload(comment)}


@push_router.post("/feed-posts")
async def push_feed_post(
    body: FeedPostPush,
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Create or update a feed post using the producer's stable unit_id."""
    _check_push_token(authorization)
    await enforce_rate_limit("feed-push", request.client.host if request.client else "unknown", limit=120, window_seconds=60)
    new_id = uuid4()
    inserted = await session.scalar(
        pg_insert(FeedPost)
        .values(
            id=new_id,
            unit_id=body.unit_id,
            title=body.title,
            kicker=body.kicker,
            category=body.category,
            body_md=body.body_md,
            published_at=body.published_at,
            source="api",
        )
        .on_conflict_do_nothing(index_elements=[FeedPost.unit_id])
        .returning(FeedPost.id)
    )
    if inserted:
        await session.commit()
        response.status_code = 201
        return {"data": {"id": str(inserted), "unit_id": body.unit_id, "created": True, "updated": False}}
    existing = await session.scalar(select(FeedPost).where(FeedPost.unit_id == body.unit_id))
    if existing is None:
        raise AppError("FEED_POST_NOT_FOUND", "动态不存在，请重试。", 404)
    if not existing.isvalid:
        raise AppError("FEED_POST_DELETED", "动态已删除，不能通过推送恢复。", 409)
    changes = {
        "title": body.title,
        "kicker": body.kicker,
        "category": body.category,
        "body_md": body.body_md,
        "published_at": body.published_at,
    }
    updated = any(getattr(existing, field) != value for field, value in changes.items())
    if updated:
        for field, value in changes.items():
            setattr(existing, field, value)
        await session.commit()
    return {"data": {"id": str(existing.id), "unit_id": body.unit_id, "created": False, "updated": updated}}


@push_router.post("/feed-images", status_code=201)
async def upload_feed_image(
    request: Request,
    group_id: UUID | None = Query(None),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Store a Markdown-ready image in S3 and register it in the media library."""
    _check_push_token(authorization)
    settings = get_settings()
    public_base = urlsplit(str(settings.s3_public_base_url))
    if (
        public_base.scheme != "https"
        or public_base.hostname != "s3.hzcat.com"
        or public_base.path.strip("/") != settings.s3_bucket
        or public_base.query
        or public_base.fragment
    ):
        raise AppError("FEED_IMAGE_PUBLIC_URL_INVALID", "图片公开地址必须是 s3.hzcat.com 上的 S3 bucket 路径。", 503)
    if group_id is not None:
        group = await session.get(Gallery, group_id)
        if group is None or not group.isvalid or group.collection_kind != "attachment_group":
            raise AppError("MEDIA_GROUP_NOT_FOUND", "指定的附件分组不存在。", 404)
    await enforce_rate_limit("feed-image", request.client.host if request.client else "unknown", limit=30, window_seconds=3600)
    declared_size = request.headers.get("content-length")
    if declared_size:
        try:
            if int(declared_size) > MAX_FEED_IMAGE_BYTES:
                raise AppError("FEED_IMAGE_TOO_LARGE", "图片不能超过 10 MiB。", 413)
        except ValueError as error:
            raise AppError("FEED_IMAGE_SIZE_INVALID", "图片大小请求头无效。", 422) from error
    declared_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared_type not in FEED_IMAGE_EXTENSIONS:
        raise AppError("FEED_IMAGE_TYPE_NOT_ALLOWED", "仅支持 PNG、JPEG、GIF 和 WebP 图片。", 415)

    image = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")  # noqa: SIM115 - closed in finally
    digest = hashlib.sha256()
    header = bytearray()
    byte_size = 0
    try:
        async for chunk in request.stream():
            byte_size += len(chunk)
            if byte_size > MAX_FEED_IMAGE_BYTES:
                raise AppError("FEED_IMAGE_TOO_LARGE", "图片不能超过 10 MiB。", 413)
            if len(header) < 64:
                header.extend(chunk[: 64 - len(header)])
            digest.update(chunk)
            image.write(chunk)
        if not byte_size:
            raise AppError("FEED_IMAGE_EMPTY", "不能上传空图片。", 422)
        detected_type = _detect_media_type(bytes(header))
        if detected_type != declared_type:
            raise AppError("FEED_IMAGE_TYPE_MISMATCH", "图片内容与声明的类型不一致。", 422)
        try:
            await asyncio.to_thread(_scan_upload, image)
        except Exception as error:  # noqa: BLE001 - scanner transport varies by deployment.
            raise AppError("FEED_IMAGE_SCAN_FAILED", "图片未通过安全扫描。", 422) from error

        existing = await session.scalar(
            reusable_image_query(digest.hexdigest())
        )
        if existing is not None:
            changed = False
            if not existing.isvalid:
                existing.isvalid = True
                changed = True
            if group_id is not None:
                membership = await session.scalar(
                    select(GalleryItem).where(GalleryItem.gallery_id == group_id, GalleryItem.media_id == existing.id)
                )
                if membership is None:
                    session.add(GalleryItem(gallery_id=group_id, media_id=existing.id, sort_order=0))
                    changed = True
                elif not membership.isvalid:
                    membership.isvalid = True
                    changed = True
            if changed:
                await session.commit()
            url = existing.source_url
            thumbnail_url = (await thumbnail_urls(session, [url])).get(url)
            return {"data": {"url": url, "thumbnail_url": thumbnail_url, "object_key": existing.object_key, "mime_type": existing.mime_type, "byte_size": existing.byte_size, "group_id": str(group_id) if group_id else None, "markdown": f"![图片]({url})"}}

        try:
            variants, object_key, thumbnail_key, etag = await asyncio.to_thread(
                store_image_variants, image, f"feed-{uuid4().hex}.webp",
                put=_put_s3_object, delete=_delete_s3_object, make_key=_upload_object_key,
            )
        except AppError:
            raise
        except Exception as error:  # noqa: BLE001 - S3-compatible SDK errors vary by host.
            raise AppError("FEED_IMAGE_UPLOAD_FAILED", "图片上传到 S3 失败。", 502) from error
        url = _public_media_url(object_key)
        thumbnail_url = _public_media_url(thumbnail_key)
        try:
            media = await register_image_variants(
                session, variants=variants, checksum=digest.hexdigest(),
                bucket_name=settings.s3_bucket, object_key=object_key,
                source_url=url, thumbnail_url=thumbnail_url,
                original_name=f"feed-{object_key.rsplit('/', 1)[-1]}", source_platform="feed", etag=etag,
            )
            if group_id is not None:
                membership = await session.scalar(select(GalleryItem).where(
                    GalleryItem.gallery_id == group_id, GalleryItem.media_id == media.id,
                ))
                if membership is None:
                    session.add(GalleryItem(gallery_id=group_id, media_id=media.id, sort_order=0))
                else:
                    membership.isvalid = True
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            await asyncio.to_thread(_delete_s3_object, object_key)
            await asyncio.to_thread(_delete_s3_object, thumbnail_key)
            raise AppError("FEED_IMAGE_CONFLICT", "相同图片正在上传，请重试。", 409) from error
        except Exception:
            await session.rollback()
            await asyncio.to_thread(_delete_s3_object, object_key)
            await asyncio.to_thread(_delete_s3_object, thumbnail_key)
            raise
        return {"data": {"url": url, "thumbnail_url": thumbnail_url, "object_key": object_key, "mime_type": variants.full.mime_type, "byte_size": media.byte_size, "group_id": str(group_id) if group_id else None, "markdown": f"![图片]({url})"}}
    finally:
        image.close()


@studio_router.get("")
async def list_studio_feed_posts(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    condition = FeedPost.isvalid.is_(True)
    total = await session.scalar(select(func.count()).select_from(FeedPost).where(condition)) or 0
    rows = await session.scalars(
        select(FeedPost)
        .where(condition)
        .order_by(FeedPost.published_at.desc(), FeedPost.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {"data": [_payload(item) for item in rows], "meta": _meta(request, page, page_size, total)}


@studio_router.post("", status_code=201)
async def create_studio_feed_post(
    body: FeedPostStudio,
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = FeedPost(
        unit_id=body.unit_id or f"studio_{uuid4().hex}",
        title=body.title,
        kicker=body.kicker,
        category=body.category,
        body_md=body.body_md,
        published_at=body.published_at,
        source="studio",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return {"data": _payload(item), "meta": {"requestId": str(request_id(request))}}


@studio_router.put("/{item_id}")
async def update_studio_feed_post(
    item_id: UUID,
    body: FeedPostStudio,
    request: Request,
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = await session.scalar(select(FeedPost).where(FeedPost.id == item_id, FeedPost.isvalid.is_(True)))
    if not item:
        raise AppError("FEED_POST_NOT_FOUND", "动态不存在。", 404)
    item.title = body.title
    item.kicker = body.kicker
    item.category = body.category
    item.body_md = body.body_md
    item.published_at = body.published_at
    await session.commit()
    await session.refresh(item)
    return {"data": _payload(item), "meta": {"requestId": str(request_id(request))}}


@studio_router.delete("/{item_id}", status_code=204)
async def delete_studio_feed_post(
    item_id: UUID,
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    item = await session.scalar(select(FeedPost).where(FeedPost.id == item_id, FeedPost.isvalid.is_(True)))
    if not item:
        raise AppError("FEED_POST_NOT_FOUND", "动态不存在。", 404)
    item.isvalid = False
    await session.commit()
    return Response(status_code=204)


@studio_router.get("/comments")
async def list_studio_feed_comments(
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await session.scalars(
        select(FeedComment).where(FeedComment.isvalid.is_(True)).order_by(FeedComment.ctime.desc()).limit(200)
    )
    return {"data": [_comment_payload(row) for row in rows]}


@studio_router.patch("/comments/{comment_id}")
async def moderate_feed_comment(
    comment_id: UUID,
    body: FeedCommentModeration,
    _: tuple[User, UserSession] = Depends(require_roles("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    item = await session.scalar(select(FeedComment).where(FeedComment.id == comment_id, FeedComment.isvalid.is_(True)))
    if not item:
        raise AppError("FEED_COMMENT_NOT_FOUND", "评论不存在。", 404)
    item.status = body.status
    await session.commit()
    await session.refresh(item)
    return {"data": _comment_payload(item)}
