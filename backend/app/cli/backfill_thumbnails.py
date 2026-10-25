"""Generate immutable 640px WebP thumbnails while retaining every source URL.

python -m app.cli.backfill_thumbnails --limit 100
python -m app.cli.backfill_thumbnails --apply --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from io import BytesIO
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy import select

from app.api.v1.studio import (
    _delete_s3_object,
    _public_media_url,
    _put_s3_object,
    _s3_client,
    _upload_object_key,
)
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.core import Post
from app.models.feed import FeedPost
from app.models.media_thumbnail import MediaThumbnail
from app.models.supplemental import Gallery, Media
from app.services.images import encode_image, first_markdown_image

MAX_SOURCE_BYTES = 50 * 1024 * 1024


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="为旧封面、动态配图生成 640px WebP 缩略图，保留原图与正文链接。"
    )
    parser.add_argument("--apply", action="store_true", help="执行写入；默认仅列出待处理 URL")
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="本次成功生成的图片数上限；失败项不占名额（默认 100）",
    )
    parser.add_argument("--include-media", action="store_true", help="额外处理附件库中所有图片")
    parser.add_argument(
        "--source-base-url",
        action="append",
        default=[],
        help="允许下载的额外历史图片 URL 前缀，可重复指定；默认仅读取当前 S3 bucket",
    )
    return parser.parse_args()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _under_base(url: str, base_url: str) -> bool:
    source, base = urlsplit(url), urlsplit(base_url)
    return (
        source.scheme in {"http", "https"}
        and (source.scheme, source.netloc) == (base.scheme, base.netloc)
        and source.path.startswith(base.path.rstrip("/") + "/")
    )


def read_source(url: str, extra_bases: list[str]) -> bytes:
    settings = get_settings()
    public_base = str(settings.s3_public_base_url).rstrip("/")
    if _under_base(url, public_base):
        key = unquote(urlsplit(url).path[len(urlsplit(public_base).path.rstrip("/")) + 1 :])
        response = _s3_client().get_object(Bucket=settings.s3_bucket, Key=key)
        stream = response["Body"]
        try:
            if response.get("ContentLength", 0) > MAX_SOURCE_BYTES:
                raise ValueError("source exceeds 50 MiB")
            data = stream.read(MAX_SOURCE_BYTES + 1)
        finally:
            stream.close()
    elif any(_under_base(url, base) for base in extra_bases):
        # Explicit CLI prefixes only; never forward S3 credentials or follow redirects.
        request = Request(url, headers={"User-Agent": "mmcat-thumbnail-backfill/1.0"})
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            if int(response.headers.get("Content-Length", "0")) > MAX_SOURCE_BYTES:
                raise ValueError("source exceeds 50 MiB")
            data = response.read(MAX_SOURCE_BYTES + 1)
    else:
        raise ValueError(
            "outside configured bucket; use --source-base-url for this historical prefix"
        )
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds 50 MiB")
    return data


async def candidates(session, include_media: bool):
    # Standalone Page has no cover_url; only article/gallery covers are cards.
    for model in (Post, Gallery):
        rows = await session.stream_scalars(
            select(model.cover_url)
            .where(model.isvalid.is_(True), model.cover_url.is_not(None))
            .order_by(model.id)
            .execution_options(yield_per=100)
        )
        async for url in rows:
            yield url
    rows = await session.stream_scalars(
        select(FeedPost.body_md)
        .where(FeedPost.isvalid.is_(True))
        .order_by(FeedPost.published_at.desc(), FeedPost.id.desc())
        .execution_options(yield_per=100)
    )
    async for markdown in rows:
        if url := first_markdown_image(markdown):
            yield url
    if include_media:
        rows = await session.stream_scalars(
            select(Media.source_url)
            .where(Media.isvalid.is_(True), Media.mime_type.like("image/%"))
            .order_by(Media.id)
            .execution_options(yield_per=100)
        )
        async for url in rows:
            yield url


async def run(args: argparse.Namespace) -> None:
    if args.limit < 1:
        raise SystemExit("--limit 必须大于 0")
    seen = set()
    completed = 0
    attempted = 0
    failures = []
    # Keep the read cursor on its own connection; per-image writes/commits use
    # separate sessions. Continue past failures until `limit` successes or EOF.
    async with SessionLocal() as source_session:
        async for url in candidates(source_session, args.include_media):
            if not url or url in seen:
                continue
            seen.add(url)
            if await source_session.get(MediaThumbnail, url) is not None:
                continue
            attempted += 1
            if not args.apply:
                print(f"待处理 {url}")
                completed += 1
            else:
                key = None
                try:
                    data = await asyncio.to_thread(read_source, url, args.source_base_url)
                    result = await asyncio.to_thread(
                        encode_image, BytesIO(data), thumbnail_only=True
                    )
                    thumbnail = result.thumbnail
                    async with SessionLocal() as session:
                        # Recheck after IO so concurrent runs remain safe.
                        if await session.get(MediaThumbnail, url) is not None:
                            continue
                        key = _upload_object_key("thumb-640.webp")
                        await asyncio.to_thread(
                            _put_s3_object, key, BytesIO(thumbnail.data), "image/webp"
                        )
                        session.add(
                            MediaThumbnail(
                                source_url=url,
                                thumbnail_url=_public_media_url(key),
                                width=thumbnail.width,
                                height=thumbnail.height,
                            )
                        )
                        await session.commit()
                    completed += 1
                    print(
                        f"完成 {thumbnail.width}x{thumbnail.height} {len(data)} → {len(thumbnail.data)} bytes {url}"
                    )
                except Exception as error:
                    failures.append((url, str(error)))
                    if key:
                        with suppress(Exception):
                            await asyncio.to_thread(_delete_s3_object, key)
                    print(f"失败 {url}: {error}")
            if completed >= args.limit:
                break
    print(
        f"{'成功' if args.apply else '预览'} {completed} 张，尝试 {attempted} 张，失败 {len(failures)} 张。原图与正文未改动；重复运行会跳过已完成项。"
    )
    if failures:
        print("失败清单：")
        for url, error in failures:
            print(f"  {url}: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(run(_arguments()))
