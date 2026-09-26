from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import request_id, require_roles
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
from app.models.supplemental import AITool, OutboxEvent, PostCategory, PostTag
from app.schemas.content import (
    PostSummary,
    StudioCommentModeration,
    StudioPageInput,
    StudioPostCreate,
    StudioPostUpdate,
    StudioSitePropertiesUpdate,
    StudioTaxonomyInput,
    StudioToolInput,
)
from app.services.content import PostService

router = APIRouter(prefix="/studio", tags=["studio"])


def _payload(post: Post) -> dict[str, object]:
    return PostSummary.model_validate(post).model_dump(mode="json", by_alias=True)


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
        payload["".join([field.split("_")[0], *[part.capitalize() for part in field.split("_")[1:]]])] = value
    return payload


@router.get("/posts")
async def list_drafts(
    request: Request,
    page: int = Query(default=1, ge=1),
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
        "data": [_payload(post) for post in rows],
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
    post = await session.get(Post, post_id)
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
    }


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
