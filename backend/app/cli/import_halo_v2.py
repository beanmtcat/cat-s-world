"""Import a Halo v2 ZIP export into mmcat."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select, text

from app.core.errors import AppError
from app.db.session import SessionLocal
from app.importers.halo_v2 import (
    HaloCandidate,
    read_halo_v2_archive_path,
    read_halo_workdir_bundle_path,
)
from app.models.core import (
    Category,
    Comment,
    ContentRevision,
    Page,
    Post,
    Redirect,
    SiteProperties,
    Tag,
    User,
)
from app.models.supplemental import (
    FriendLink,
    Gallery,
    GalleryItem,
    ImportItem,
    ImportJob,
    Media,
    NavigationItem,
    PostCategory,
    PostTag,
    VisitDaily,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入 Halo v2 ZIP 导出包")
    parser.add_argument("archive", type=Path, help="Halo v2 导出的 ZIP 文件")
    parser.add_argument("--author-email", required=True, help="归属的已存在后台用户邮箱")
    parser.add_argument("--locale", default="zh-CN", choices=("zh-CN", "en-US"))
    parser.add_argument(
        "--update-existing", action="store_true", help="同 source_id 存在时更新文章"
    )
    parser.add_argument(
        "--reset-database", action="store_true", help="导入前清空当前数据库的 public 表数据"
    )
    parser.add_argument(
        "--confirm-reset", action="store_true", help="确认不可逆的 --reset-database 操作"
    )
    return parser.parse_args()


def _target_path(candidate: HaloCandidate) -> str:
    return f"{'/posts' if candidate.kind == 'post' else '/pages'}/{candidate.slug}"


async def _redirect(session, source_url: str | None, destination: str) -> None:
    if not source_url:
        return
    parsed = urlsplit(source_url)
    source_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    if not source_path or source_path == destination:
        return
    if await session.scalar(select(Redirect).where(Redirect.from_path == source_path)) is None:
        session.add(Redirect(from_path=source_path, to_path=destination, status_code=301))


async def _taxonomy_id(session, model, locale: str, source_id: str, source: dict[str, object]):
    slug = str(source.get("slug") or f"halo-{source_id}")
    name = str(source.get("displayName") or source_id)
    item = await session.scalar(select(model).where(model.locale == locale, model.slug == slug))
    if item is None:
        values: dict[str, object] = {"locale": locale, "slug": slug, "name": name}
        if model is Category:
            values["sort_order"] = _integer(source.get("priority"))
        item = model(**values)
        session.add(item)
        await session.flush()
    else:
        item.name = name
        if hasattr(item, "sort_order"):
            item.sort_order = _integer(source.get("priority"))
    if hasattr(item, "description"):
        item.description = str(source.get("description") or "") or None
    if isinstance(item, Category):
        item.cover_url = str(source.get("cover") or "") or None
    return item.id


async def _prepare_taxonomy(
    session, locale: str, taxonomy: dict[str, dict[str, dict[str, object]]]
) -> None:
    for model, resource_type in ((Category, "category"), (Tag, "tag")):
        for source_id, source in taxonomy[resource_type].items():
            await _taxonomy_id(session, model, locale, source_id, source)
    # Halo categories refer to their parent by resource name.  All category
    # rows already exist by this point, so this remains a logical relation and
    # does not require a database foreign key.
    for source_id, source in taxonomy["category"].items():
        parent_source_id = str(source.get("parentRef") or source.get("parent") or "")
        if not parent_source_id:
            continue
        category_id = await _taxonomy_id(session, Category, locale, source_id, source)
        parent_id = await _taxonomy_id(
            session,
            Category,
            locale,
            parent_source_id,
            taxonomy["category"].get(parent_source_id, {}),
        )
        category = await session.get(Category, category_id)
        if category is not None and category_id != parent_id:
            category.parent_id = parent_id


async def _write_taxonomy_links(
    session,
    post: Post,
    candidate: HaloCandidate,
    locale: str,
    taxonomy: dict[str, dict[str, dict[str, object]]],
) -> list[str]:
    warnings: list[str] = []
    for source_id in candidate.category_source_ids:
        if source_id not in taxonomy["category"]:
            warnings.append(f"跳过已不存在的 Halo 分类引用：{source_id}")
            continue
        category_id = await _taxonomy_id(
            session, Category, locale, source_id, taxonomy["category"].get(source_id, {})
        )
        exists = await session.scalar(
            select(PostCategory).where(
                PostCategory.post_id == post.id, PostCategory.category_id == category_id
            )
        )
        if exists is None:
            session.add(PostCategory(post_id=post.id, category_id=category_id))
    for source_id in candidate.tag_source_ids:
        if source_id not in taxonomy["tag"]:
            warnings.append(f"跳过已不存在的 Halo 标签引用：{source_id}")
            continue
        tag_id = await _taxonomy_id(
            session, Tag, locale, source_id, taxonomy["tag"].get(source_id, {})
        )
        exists = await session.scalar(
            select(PostTag).where(PostTag.post_id == post.id, PostTag.tag_id == tag_id)
        )
        if exists is None:
            session.add(PostTag(post_id=post.id, tag_id=tag_id))
    return warnings


async def _write_candidate(
    session,
    job: ImportJob,
    author: User,
    candidate: HaloCandidate,
    locale: str,
    update_existing: bool,
    taxonomy: dict[str, dict[str, dict[str, object]]],
) -> bool:
    item = ImportItem(
        job_id=job.id,
        source_id=candidate.source_id,
        source_url=candidate.source_url,
        target_type=candidate.kind,
        target_slug=candidate.slug,
        state="converted",
        warnings=list(candidate.warnings),
    )
    session.add(item)
    if candidate.kind == "post":
        post = await session.scalar(
            select(Post).where(
                Post.source_platform == "halo", Post.source_id == candidate.source_id
            )
        )
        if post is not None and not update_existing:
            item.target_post_id, item.state = post.id, "skipped"
            return False
        if post is None:
            post = Post(
                locale=locale,
                slug=candidate.slug,
                title=candidate.title,
                summary=candidate.summary,
                content=candidate.markdown.content,
                content_hash=candidate.markdown.content_hash,
                cover_url=candidate.cover_url,
                author_id=author.id,
                status="published" if candidate.is_published else "draft",
                visibility="public" if candidate.is_published else "private",
                published_at=candidate.published_at if candidate.is_published else None,
                source_platform="halo",
                source_id=candidate.source_id,
                source_url=candidate.source_url,
            )
            session.add(post)
        else:
            post.slug, post.title, post.content = (
                candidate.slug,
                candidate.title,
                candidate.markdown.content,
            )
            post.summary = candidate.summary
            post.status = "published" if candidate.is_published else "draft"
            post.visibility = "public" if candidate.is_published else "private"
            post.published_at = candidate.published_at if candidate.is_published else None
            post.content_hash, post.source_url = (
                candidate.markdown.content_hash,
                candidate.source_url,
            )
            post.cover_url = candidate.cover_url
        await session.flush()
        warnings = await _write_taxonomy_links(session, post, candidate, locale, taxonomy)
        if warnings:
            item.warnings = [*item.warnings, *warnings]
            job.warning_count += len(warnings)
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
                editor_id=author.id,
                cover_url=post.cover_url,
                change_summary="Halo v2 导入",
            )
        )
        item.target_post_id, item.state = post.id, "imported"
        await _redirect(session, candidate.source_url, _target_path(candidate))
        return True
    page = await session.scalar(
        select(Page).where(Page.source_platform == "halo", Page.source_id == candidate.source_id)
    )
    if page is not None and not update_existing:
        item.target_page_id, item.state = page.id, "skipped"
        return False
    if page is None:
        page = Page(
            locale=locale,
            slug=candidate.slug,
            title=candidate.title,
            summary=candidate.summary,
            content=candidate.markdown.content,
            author_id=author.id,
            status="published" if candidate.is_published else "draft",
            visibility="public" if candidate.is_published else "private",
            published_at=candidate.published_at if candidate.is_published else None,
            source_platform="halo",
            source_id=candidate.source_id,
            source_url=candidate.source_url,
        )
        session.add(page)
    else:
        page.slug, page.title, page.summary, page.content = (
            candidate.slug,
            candidate.title,
            candidate.summary,
            candidate.markdown.content,
        )
        page.status = "published" if candidate.is_published else "draft"
        page.visibility = "public" if candidate.is_published else "private"
        page.published_at = candidate.published_at if candidate.is_published else None
        page.source_url = candidate.source_url
    await session.flush()
    last_revision = await session.scalar(
        select(ContentRevision.revision_no)
        .where(ContentRevision.target_type == "page", ContentRevision.target_id == page.id)
        .order_by(ContentRevision.revision_no.desc())
        .limit(1)
    )
    session.add(
        ContentRevision(
            target_type="page",
            target_id=page.id,
            revision_no=(last_revision or 0) + 1,
            content=page.content,
            title=page.title,
            summary=page.summary,
            locale=page.locale,
            slug=page.slug,
            editor_id=author.id,
            change_summary="Halo v2 导入",
        )
    )
    item.target_page_id, item.state = page.id, "imported"
    await _redirect(session, candidate.source_url, _target_path(candidate))
    return True


def _string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _resource_value(resource: dict[str, object], *keys: str) -> object | None:
    spec = resource.get("spec")
    status = resource.get("status")
    for container in (spec, status):
        if not isinstance(container, dict):
            continue
        for key in keys:
            if container.get(key) is not None:
                return container[key]
    return None


def _resource_url(resource: dict[str, object]) -> str | None:
    value = _resource_value(resource, "permalink", "url", "uri", "link")
    url = _string(value)
    return url if url and urlsplit(url).scheme in {"http", "https"} else None


def _slugify(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:170] or fallback[:170]


def _setting_values(resources: list[dict[str, object]]) -> dict[str, object]:
    """Collect scalar values from Halo Settings and ConfigMap resources."""
    result: dict[str, object] = {}

    def visit(value: object, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{prefix}_{key}" if prefix else str(key))
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                import json

                visit(json.loads(value), prefix)
            except json.JSONDecodeError:
                result[re.sub(r"[^a-z0-9]", "", prefix.lower())] = value
        elif isinstance(value, (str, bool, int, float)):
            result[re.sub(r"[^a-z0-9]", "", prefix.lower())] = value

    for resource in resources:
        visit(resource.get("spec"))
        visit(resource.get("status"))
    return result


def _setting(values: dict[str, object], *aliases: str) -> object | None:
    aliases = tuple(re.sub(r"[^a-z0-9]", "", alias.lower()) for alias in aliases)
    for alias in aliases:
        if alias in values:
            return values[alias]
    for key, value in values.items():
        if any(key.endswith(alias) for alias in aliases):
            return value
    return None


async def _import_site_properties(session, resources: list[dict[str, object]]) -> int:
    # Halo has many plugin/theme ConfigMaps.  Only the core `system` map is
    # authoritative for global site identity; consuming every map can let a
    # theme's preview title overwrite the actual site title.
    system_resources = [item for item in resources if item.get("source_id") == "system"]
    values = _setting_values(system_resources)
    if not values:
        return 0
    properties = await session.scalar(
        select(SiteProperties).where(SiteProperties.isvalid.is_(True))
    )
    site_name = _string(_setting(values, "siteName", "title", "blogTitle", "name"))
    if properties is None:
        properties = SiteProperties(
            site_name_zh=site_name or "猫子的世界",
            site_name_en=_string(_setting(values, "siteNameEn", "englishName"))
            or site_name
            or "Cats World",
        )
        session.add(properties)
    if site_name:
        properties.site_name_zh = site_name
    site_name_en = _string(_setting(values, "siteNameEn", "englishName"))
    if site_name_en:
        properties.site_name_en = site_name_en
    mapping = {
        "site_description_zh": ("siteDescription", "blogDescription", "seoDescription"),
        "site_description_en": ("siteDescriptionEn", "englishDescription"),
        "logo_url": ("logo", "logoUrl"),
        "favicon_url": ("favicon", "faviconUrl"),
        "default_og_image_url": ("defaultOgImage", "ogImage", "defaultThumbnail"),
        "default_locale": ("defaultLocale", "locale"),
        "timezone": ("timezone", "timeZone"),
    }
    for attribute, aliases in mapping.items():
        value = _string(_setting(values, *aliases))
        if value:
            setattr(properties, attribute, value)
    for attribute, aliases in {
        "comments_enabled": ("commentsEnabled", "commentEnabled", "allowComment"),
        "sitemap_enabled": ("sitemapEnabled",),
    }.items():
        value = _setting(values, *aliases)
        if isinstance(value, bool):
            setattr(properties, attribute, value)
    return 1


async def _import_media_and_galleries(
    session, resources: list[dict[str, object]], locale: str
) -> int:
    imported = 0
    galleries: dict[str, Gallery] = {}
    for resource in resources:
        source_id = str(resource["source_id"])
        url = _resource_url(resource)
        if url is None:
            continue
        media = await session.scalar(
            select(Media).where(Media.source_platform == "halo", Media.source_id == source_id)
        )
        mime_type = (
            _string(_resource_value(resource, "mediaType", "mimeType"))
            or "application/octet-stream"
        )
        if media is None:
            media = Media(
                bucket_name="halo-external",
                object_key=f"halo/external/{source_id}",
                source_url=url,
                original_name=_string(_resource_value(resource, "displayName", "name")),
                mime_type=mime_type,
                byte_size=_integer(_resource_value(resource, "size", "byteSize")),
                checksum=hashlib.sha256(url.encode("utf-8")).hexdigest(),
                width=_integer(_resource_value(resource, "width")) or None,
                height=_integer(_resource_value(resource, "height")) or None,
                source_platform="halo",
                source_id=source_id,
                origin_url=url,
            )
            session.add(media)
            await session.flush()
            imported += 1
        else:
            media.source_url = url
            media.origin_url = url
            media.mime_type = mime_type
            media.byte_size = _integer(_resource_value(resource, "size", "byteSize"))

        group = _string(_resource_value(resource, "groupName", "group"))
        if not group:
            continue
        gallery = galleries.get(group)
        if gallery is None:
            source_group_id = f"halo-attachment-group:{group}"
            gallery = await session.scalar(
                select(Gallery).where(
                    Gallery.source_platform == "halo", Gallery.source_id == source_group_id
                )
            )
            if gallery is None:
                gallery = Gallery(
                    locale=locale,
                    slug=_slugify(group, "halo-gallery"),
                    name=group,
                    status="published",
                    visibility="public",
                    source_platform="halo",
                    source_id=source_group_id,
                )
                session.add(gallery)
                await session.flush()
            galleries[group] = gallery
        exists = await session.scalar(
            select(GalleryItem).where(
                GalleryItem.gallery_id == gallery.id, GalleryItem.media_id == media.id
            )
        )
        if exists is None:
            session.add(GalleryItem(gallery_id=gallery.id, media_id=media.id))
    return imported


async def _import_navigation(session, resources: list[dict[str, object]], locale: str) -> int:
    imported = 0
    parents: list[tuple[NavigationItem, str]] = []
    for resource in resources:
        source_id = str(resource["source_id"])
        label = _string(_resource_value(resource, "displayName", "title", "name"))
        url = _string(_resource_value(resource, "href", "url"))
        if not label or not url:
            continue
        item = await session.scalar(
            select(NavigationItem).where(
                NavigationItem.source_platform == "halo", NavigationItem.source_id == source_id
            )
        )
        if item is None:
            item = NavigationItem(
                locale=locale, label=label, url=url, source_platform="halo", source_id=source_id
            )
            session.add(item)
            imported += 1
        else:
            item.label, item.url = label, url
        item.sort_order = _integer(_resource_value(resource, "priority", "sortOrder"))
        item.open_new_tab = _string(_resource_value(resource, "target")) == "_blank"
        parent = _string(_resource_value(resource, "parent", "parentRef"))
        if parent:
            parents.append((item, parent))
    await session.flush()
    for item, parent_source_id in parents:
        parent = await session.scalar(
            select(NavigationItem).where(
                NavigationItem.source_platform == "halo",
                NavigationItem.source_id == parent_source_id,
            )
        )
        item.parent_id = parent.id if parent else None
    return imported


async def _import_friend_links(session, resources: list[dict[str, object]], locale: str) -> int:
    imported = 0
    for resource in resources:
        source_id = str(resource["source_id"])
        name = _string(_resource_value(resource, "displayName", "name", "title"))
        url = _resource_url(resource)
        if not name or not url:
            continue
        link = await session.scalar(
            select(FriendLink).where(
                FriendLink.source_platform == "halo", FriendLink.source_id == source_id
            )
        )
        if link is None:
            link = FriendLink(
                locale=locale, name=name, url=url, source_platform="halo", source_id=source_id
            )
            session.add(link)
            imported += 1
        else:
            link.name, link.url = name, url
        link.logo_url = _string(_resource_value(resource, "logo", "logoUrl", "avatar"))
        link.description = _string(_resource_value(resource, "description"))
        link.target = _string(_resource_value(resource, "target")) or "_blank"
        link.sort_order = _integer(_resource_value(resource, "priority", "sortOrder"))
    return imported


async def _import_comments(
    session, resources: list[dict[str, object]], author: User, locale: str
) -> int:
    imported = 0
    pending_parents: list[tuple[Comment, str]] = []
    for resource in resources:
        source_id = str(resource["source_id"])
        spec = resource["spec"]
        assert isinstance(spec, dict)
        subject = spec.get("subjectRef")
        subject_name = subject.get("name") if isinstance(subject, dict) else None
        target = None
        target_type = str(subject.get("kind") or "") if isinstance(subject, dict) else ""
        if subject_name:
            if target_type.lower() in {"singlepage", "page"}:
                target = await session.scalar(
                    select(Page).where(
                        Page.source_platform == "halo", Page.source_id == str(subject_name)
                    )
                )
            else:
                target = await session.scalar(
                    select(Post).where(
                        Post.source_platform == "halo", Post.source_id == str(subject_name)
                    )
                )
        if target is None:
            continue
        content = _string(spec.get("raw")) or _string(spec.get("content"))
        if not content:
            continue
        comment = await session.scalar(
            select(Comment).where(Comment.source_platform == "halo", Comment.source_id == source_id)
        )
        if comment is None:
            comment = Comment(
                post_id=target.id if isinstance(target, Post) else None,
                page_id=target.id if isinstance(target, Page) else None,
                user_id=author.id,
                locale=locale,
                author_name=_string(spec.get("owner")) or author.display_name,
                content=content,
                status="approved" if bool(spec.get("approved")) else "pending",
                source_platform="halo",
                source_id=source_id,
            )
            session.add(comment)
            imported += 1
        else:
            comment.content = content
            comment.status = "approved" if bool(spec.get("approved")) else "pending"
        ip_address = _string(spec.get("ipAddress"))
        if ip_address:
            comment.ip_hash = hashlib.sha256(ip_address.encode("utf-8")).hexdigest()
        user_agent = _string(spec.get("userAgent"))
        if user_agent:
            comment.user_agent_hash = hashlib.sha256(user_agent.encode("utf-8")).hexdigest()
        parent = _string(spec.get("parentRef") or spec.get("parent"))
        if parent:
            pending_parents.append((comment, parent))
    await session.flush()
    for comment, parent_source_id in pending_parents:
        parent = await session.scalar(
            select(Comment).where(
                Comment.source_platform == "halo", Comment.source_id == parent_source_id
            )
        )
        comment.parent_id = parent.id if parent else None
    return imported


async def _import_statistics(session, resources: list[dict[str, object]], locale: str) -> int:
    imported = 0
    for resource in resources:
        spec = resource["spec"]
        assert isinstance(spec, dict)
        stat_date = _string(spec.get("statDate") or spec.get("date"))
        path = _string(spec.get("path") or spec.get("uri"))
        if not stat_date or not path:
            continue
        try:
            day = date.fromisoformat(stat_date[:10])
        except ValueError:
            continue
        item = await session.scalar(
            select(VisitDaily).where(
                VisitDaily.stat_date == day, VisitDaily.path == path, VisitDaily.locale == locale
            )
        )
        if item is None:
            item = VisitDaily(stat_date=day, path=path, locale=locale)
            session.add(item)
            imported += 1
        item.page_views = _integer(spec.get("pageViews") or spec.get("pv"))
        item.unique_visitors = _integer(spec.get("uniqueVisitors") or spec.get("uv"))
        item.sessions = _integer(spec.get("sessions"))
    return imported


async def run(args: argparse.Namespace) -> None:
    archive = args.archive.resolve()
    if not archive.is_file():
        raise SystemExit(f"找不到导入包：{archive}")
    try:
        candidates = read_halo_v2_archive_path(str(archive))
        taxonomy = {"category": {}, "tag": {}}
        auxiliary = None
    except AppError as exc:
        if exc.code != "HALO_WORKDIR_BACKUP_UNSUPPORTED":
            raise
        auxiliary = read_halo_workdir_bundle_path(str(archive))
        candidates = auxiliary.candidates
        taxonomy = auxiliary.taxonomy
    async with SessionLocal() as session:
        if args.reset_database:
            if not args.confirm_reset:
                raise SystemExit("--reset-database 必须同时指定 --confirm-reset。")
            # Keep the operator account so the import has an auditable author;
            # all migrated content, configuration, sessions and statistics reset.
            tables = (
                (
                    await session.execute(
                        text(
                            "select quote_ident(tablename) from pg_tables where schemaname = 'public' and tablename <> 'mmcat_users'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            if tables:
                await session.execute(text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY"))
            await session.commit()
        author = await session.scalar(
            select(User).where(User.email == args.author_email.lower(), User.isvalid.is_(True))
        )
        if author is None or author.role not in {"admin", "editor", "author"}:
            raise SystemExit("--author-email 必须对应有效的 admin、editor 或 author 用户。")
        job = ImportJob(
            source_platform="halo",
            source_version="v2",
            source_file_name=archive.name,
            state="running",
            total_count=len(candidates),
            created_by=author.id,
        )
        session.add(job)
        await session.flush()
        await _prepare_taxonomy(session, args.locale, taxonomy)
        for candidate in candidates:
            try:
                imported = await _write_candidate(
                    session, job, author, candidate, args.locale, args.update_existing, taxonomy
                )
                job.success_count += int(imported)
            except Exception as exc:
                session.add(
                    ImportItem(
                        job_id=job.id,
                        source_id=candidate.source_id,
                        source_url=candidate.source_url,
                        target_type=candidate.kind,
                        target_slug=candidate.slug,
                        state="failed",
                        error_message=str(exc)[:1000],
                    )
                )
                job.failure_count += 1
        auxiliary_counts: dict[str, int] = {}
        if auxiliary is not None:
            auxiliary_counts["site_properties"] = await _import_site_properties(
                session, auxiliary.site_settings
            )
            auxiliary_counts["attachments"] = await _import_media_and_galleries(
                session, auxiliary.attachments, args.locale
            )
            auxiliary_counts["navigation"] = await _import_navigation(
                session, auxiliary.menu_items, args.locale
            )
            auxiliary_counts["friend_links"] = await _import_friend_links(
                session, auxiliary.friend_links, args.locale
            )
            auxiliary_counts["comments"] = await _import_comments(
                session, auxiliary.comments, author, args.locale
            )
            auxiliary_counts["statistics"] = await _import_statistics(
                session, auxiliary.statistics, args.locale
            )
        job.state = "completed" if job.failure_count == 0 else "failed"
        job.completed_at = datetime.now(UTC)
        await session.commit()
    detail = "，".join(f"{key} {value}" for key, value in auxiliary_counts.items())
    suffix = f"；附属资源：{detail}" if detail else ""
    print(
        f"导入任务完成：{job.id}；文章和页面成功 {job.success_count}，失败 {job.failure_count}{suffix}。"
    )


if __name__ == "__main__":
    asyncio.run(run(_arguments()))
