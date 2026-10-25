"""Integration regressions against an explicitly isolated PostgreSQL test server.

Set MMCAT_MIGRATION_TEST_URL to a disposable server, never a production database.
Each test creates and drops only its own mmcat_migration_test_<random> database.
"""

import asyncio
import hashlib
import importlib.util
import os
from argparse import Namespace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Request
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import get_settings
from app.models.core import Page, Post
from app.models.feed import FeedPost
from app.models.media_thumbnail import MediaThumbnail
from app.models.supplemental import Gallery, GalleryItem, Media

ROOT = Path(__file__).resolve().parents[1]


def test_backfill_previews_and_processes_real_database_candidates(
    migration_database, monkeypatch, capsys
):
    from app.cli import backfill_thumbnails as backfill

    database, config = migration_database
    command.upgrade(config, "head")
    source = BytesIO()
    Image.new("RGB", (1200, 800), "blue").save(source, format="PNG")
    reads, writes = [], []
    urls = {
        kind: f"https://images.example/{kind}.png" for kind in ("post", "gallery", "feed", "media")
    }

    def read_source(url, bases):
        reads.append(url)
        return source.getvalue()

    def put(key, content, mime_type):
        writes.append((key, content.read(), mime_type))

    monkeypatch.setattr(backfill, "read_source", read_source)
    monkeypatch.setattr(backfill, "_put_s3_object", put)
    monkeypatch.setattr(backfill, "_upload_object_key", lambda name: uuid4().hex + "-" + name)
    monkeypatch.setattr(backfill, "_public_media_url", lambda key: "https://images.example/" + key)

    async def run():
        engine = create_async_engine(database)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(backfill, "SessionLocal", sessions)
        try:
            async with sessions() as session:
                session.add_all(
                    [
                        Post(
                            title="Article",
                            slug="article",
                            content="正文",
                            content_hash="a" * 64,
                            author_id=uuid4(),
                            cover_url=urls["post"],
                        ),
                        Post(
                            title="Deleted",
                            slug="deleted",
                            content="正文",
                            content_hash="b" * 64,
                            author_id=uuid4(),
                            cover_url="https://images.example/deleted.png",
                            isvalid=False,
                        ),
                        Post(
                            title="No cover",
                            slug="no-cover",
                            content="正文",
                            content_hash="c" * 64,
                            author_id=uuid4(),
                        ),
                        Page(
                            title="Standalone page",
                            slug="page",
                            content="正文",
                            author_id=uuid4(),
                            og_image_url="https://images.example/page-og.png",
                        ),
                        Gallery(name="Gallery", slug="gallery", cover_url=urls["gallery"]),
                        FeedPost(
                            unit_id="feed",
                            title="Dynamic",
                            category="AI",
                            body_md=f"![图片]({urls['feed']})",
                            published_at=datetime.now(UTC),
                        ),
                        Media(
                            bucket_name="images",
                            object_key="media.png",
                            source_url=urls["media"],
                            mime_type="image/png",
                            byte_size=len(source.getvalue()),
                            checksum="d" * 64,
                        ),
                        MediaThumbnail(
                            source_url=urls["post"],
                            thumbnail_url="https://images.example/post-thumb.webp",
                            width=640,
                            height=427,
                        ),
                    ]
                )
                await session.commit()
            args = Namespace(apply=False, limit=20, include_media=True, source_base_url=[])
            await backfill.run(args)
            assert not reads and not writes
            assert "预览 3 张" in capsys.readouterr().out
            args.apply = True
            await backfill.run(args)
            assert set(reads) == {urls["gallery"], urls["feed"], urls["media"]}
            assert len(writes) == 3
            async with sessions() as session:
                assert set(await session.scalars(sa.select(MediaThumbnail.source_url))) == set(
                    urls.values()
                )
            await backfill.run(args)
            assert len(writes) == 3 and len(reads) == 3
        finally:
            await engine.dispose()

    asyncio.run(run())


async def _execute(url, statements, scalar=None):
    connection = await asyncpg.connect(
        sa.make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        for statement in statements:
            await connection.execute(statement)
        if scalar:
            return await connection.fetchval(scalar)
    finally:
        await connection.close()


@pytest.fixture
def migration_database(monkeypatch):
    server = os.environ.get("MMCAT_MIGRATION_TEST_URL")
    if not server:
        pytest.skip(
            "MMCAT_MIGRATION_TEST_URL is required for isolated PostgreSQL integration tests"
        )
    # Require the explicit test DB marker before creating anything on this server.
    assert sa.make_url(server).database.startswith("mmcat_migration_test")
    name = "mmcat_migration_test_" + uuid4().hex
    asyncio.run(_execute(server, [f'CREATE DATABASE "{name}"']))
    database = (
        sa.make_url(server)
        .set(drivername="postgresql+asyncpg", database=name)
        .render_as_string(hide_password=False)
    )
    monkeypatch.setenv("DATABASE_URL", database)
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    try:
        yield database, config
    finally:
        # All engines below use NullPool or dispose before leaving the test.
        get_settings.cache_clear()
        asyncio.run(_execute(server, [f'DROP DATABASE "{name}"']))


def _replay_upgrades(url, versions):
    async def run():
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:

                def replay(sync_connection):
                    with Operations.context(MigrationContext.configure(sync_connection)):
                        for version in versions:
                            path = next((ROOT / "alembic" / "versions").glob(f"{version}_*.py"))
                            spec = importlib.util.spec_from_file_location(path.stem, path)
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                            module.upgrade()

                await connection.run_sync(replay)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_empty_database_upgrades_to_head_and_can_replay_additive_migrations(migration_database):
    database, config = migration_database
    command.upgrade(config, "head")
    assert (
        asyncio.run(_execute(database, [], "SELECT version_num FROM alembic_version"))
        == "0015_media_thumbnails"
    )
    asyncio.run(
        _execute(
            database,
            [
                "INSERT INTO mmcat_feed_posts (id, unit_id, title, category, body_md, published_at) "
                "VALUES ('00000000-0000-0000-0000-000000000001', 'retained', '保留内容', 'AI', '正文', now())"
            ],
        )
    )
    command.upgrade(config, "head")
    # Explicitly replay already-applied DDL too, not just Alembic's no-op at head.
    _replay_upgrades(database, ["0009", "0010", "0011", "0012", "0013", "0014", "0015"])
    assert (
        asyncio.run(
            _execute(database, [], "SELECT title FROM mmcat_feed_posts WHERE unit_id='retained'")
        )
        == "保留内容"
    )
    asyncio.run(_execute(database, ["DROP INDEX ix_mmcat_feed_posts_category"]))
    _replay_upgrades(database, ["0014"])
    assert asyncio.run(
        _execute(database, [], "SELECT to_regclass('ix_mmcat_feed_posts_category') IS NOT NULL")
    )


def test_legacy_database_adds_missing_columns_tables_and_indexes(migration_database):
    database, config = migration_database
    command.upgrade(config, "head")
    # Simulate a populated database at 0008 without relying on 0001's current metadata.
    asyncio.run(
        _execute(
            database,
            [
                "INSERT INTO mmcat_categories (id, translation_group_id, slug, name) "
                "VALUES ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'retained', '保留分类')",
                "ALTER TABLE mmcat_comments DROP COLUMN legacy_reply_count",
                "ALTER TABLE mmcat_categories DROP COLUMN is_home_visible",
                "ALTER TABLE mmcat_pages DROP COLUMN deleted_at",
                *[
                    f"ALTER TABLE mmcat_site_properties DROP COLUMN {column}"
                    for column in (
                        "posts_per_page",
                        "category_posts_per_page",
                        "tag_posts_per_page",
                        "site_keywords",
                        "seo_noindex",
                        "registration_enabled",
                        "comments_moderation_enabled",
                    )
                ],
                "DROP INDEX ix_mmcat_visit_daily_path_locale",
                "DROP INDEX ix_mmcat_visit_events_path_occurred_at",
                "DROP INDEX ix_mmcat_audit_logs_ctime",
                "DROP TABLE mmcat_feed_posts, mmcat_feed_likes, mmcat_feed_comments, mmcat_media_thumbnails",
                "UPDATE alembic_version SET version_num='0008_legacy_comment_owners'",
            ],
        )
    )
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert (
        asyncio.run(
            _execute(database, [], "SELECT name FROM mmcat_categories WHERE slug='retained'")
        )
        == "保留分类"
    )
    assert (
        asyncio.run(
            _execute(
                database, [], "SELECT is_home_visible FROM mmcat_categories WHERE slug='retained'"
            )
        )
        is True
    )
    assert asyncio.run(_execute(database, [], "SELECT count(*) FROM mmcat_media_thumbnails")) == 0


@pytest.mark.parametrize("endpoint", ["studio", "feed"])
@pytest.mark.parametrize("kind", ["large_jpeg", "small_jpeg", "large_webp", "gps_jpeg"])
def test_upload_reprocesses_legacy_media_and_handles_checksum_mime_conflicts(
    migration_database,
    monkeypatch,
    endpoint,
    kind,
):
    from app.api.v1 import feed, studio

    database, config = migration_database
    command.upgrade(config, "head")
    source = BytesIO()
    options = {}
    source_format = "WEBP" if kind == "large_webp" else "JPEG"
    if kind in {"small_jpeg", "gps_jpeg"}:
        image = Image.effect_noise((1200, 900), 100).convert("RGB")
        options["quality"] = 20
    else:
        image = Image.new("RGB", (2400, 1200), "orange")
    if kind == "gps_jpeg":
        metadata = Image.Exif()
        metadata[271] = "Private camera"
        metadata[34853] = {1: "N", 2: (37, 30, 0)}
        options["exif"] = metadata
    image.save(source, format=source_format, **options)
    data = source.getvalue()
    mime_type = Image.MIME[source_format]
    checksum = hashlib.sha256(data).hexdigest()
    legacy_url = "https://s3.hzcat.com/halo/legacy." + (
        "webp" if source_format == "WEBP" else "jpg"
    )
    writes, deleted = {}, []

    async def no_limit(*args, **kwargs):
        pass

    def put(key, content, content_type):
        content.seek(0)
        writes["https://s3.hzcat.com/halo/" + key] = (content.read(), content_type)
        return "etag"

    module = studio if endpoint == "studio" else feed
    monkeypatch.setattr(module, "enforce_rate_limit", no_limit)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(s3_bucket="halo", s3_public_base_url="https://s3.hzcat.com/halo"),
    )
    monkeypatch.setattr(module, "_scan_upload", lambda content: None)
    monkeypatch.setattr(module, "_put_s3_object", put)
    monkeypatch.setattr(module, "_delete_s3_object", deleted.append)
    monkeypatch.setattr(module, "_public_media_url", lambda key: "https://s3.hzcat.com/halo/" + key)
    monkeypatch.setattr(module, "_upload_object_key", lambda name: uuid4().hex + "-" + name)
    if endpoint == "feed":
        monkeypatch.setattr(module, "_check_push_token", lambda value: None)

    async def run():
        engine = create_async_engine(database)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                old = Media(
                    id=uuid4(),
                    bucket_name="halo",
                    object_key="legacy",
                    source_url=legacy_url,
                    original_name="legacy",
                    mime_type=mime_type,
                    checksum=checksum,
                    byte_size=len(data),
                    width=image.width,
                    height=image.height,
                    isvalid=True,
                    source_platform="old",
                )
                old_id = old.id
                group = Gallery(
                    id=uuid4(),
                    slug="legacy-group",
                    name="旧附件组",
                    locale="zh-CN",
                    collection_kind="attachment_group",
                    status="published",
                    visibility="private",
                )
                session.add_all(
                    [old, group, GalleryItem(gallery_id=group.id, media_id=old.id, sort_order=0)]
                )
                await session.commit()
                assert await session.get(MediaThumbnail, legacy_url) is None

                async def upload():
                    async def receive():
                        return {"type": "http.request", "body": data, "more_body": False}

                    request = Request(
                        {
                            "type": "http",
                            "method": "POST",
                            "path": "/",
                            "headers": [(b"content-type", mime_type.encode())],
                        },
                        receive,
                    )
                    request.state.request_id = uuid4()
                    if endpoint == "studio":
                        result = await studio.upload_media(
                            request,
                            filename="legacy.jpg",
                            mime_type=mime_type,
                            gallery_id=group.id,
                            _=(None, None),
                            session=session,
                        )
                        payload = result["data"]
                        return payload["sourceUrl"], payload["thumbnailUrl"], payload["mimeType"]
                    result = await feed.upload_feed_image(
                        request, group_id=group.id, authorization=None, session=session
                    )
                    payload = result["data"]
                    return payload["url"], payload["thumbnail_url"], payload["mime_type"]

                url, thumbnail, content_type = await upload()
                assert url != legacy_url and thumbnail
                assert len(writes) == 2
                stored = Image.open(BytesIO(writes[url][0]))
                assert max(stored.size) <= 1600
                assert not stored.getexif()
                assert writes[url][1] == content_type
                assert await session.get(MediaThumbnail, url) is not None
                records = list(
                    await session.scalars(sa.select(Media).where(Media.checksum == checksum))
                )
                if kind in {"small_jpeg", "large_webp"}:
                    assert len(records) == 1 and records[0].id == old_id
                    assert records[0].source_url == url
                    assert await session.get(MediaThumbnail, legacy_url) is not None
                else:
                    assert len(records) == 2 and stored.format == "WEBP"
                    assert (await session.get(Media, old_id)).source_url == legacy_url
                assert await upload() == (url, thumbnail, content_type)
                assert len(writes) == 2  # Now processed, the third upload is a true dedup hit.
                assert not deleted  # Neither old originals nor new committed objects were removed.
                memberships = list(
                    await session.scalars(
                        sa.select(GalleryItem).where(GalleryItem.gallery_id == group.id)
                    )
                )
                assert len(memberships) == len(records)
        finally:
            await engine.dispose()

    asyncio.run(run())
