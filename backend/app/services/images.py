"""Shared image encoding and URL mapping for uploads, cards and legacy backfills."""

from __future__ import annotations

import warnings
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import BinaryIO
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.media_thumbnail import MediaThumbnail
from app.models.supplemental import Media

MAX_IMAGE_PIXELS = 100_000_000
MAX_DECODED_PIXELS = 40_000_000
# Pillow checks the header at open(), before our explicit pixel check. Align
# its warning threshold with the application limit rather than rejecting 90MP.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
MAX_ANIMATION_FRAMES = 200
MAX_ANIMATION_PIXELS = 20_000_000


@dataclass
class EncodedImage:
    data: bytes
    width: int
    height: int
    mime_type: str = "image/webp"


@dataclass
class ImageVariants:
    full: EncodedImage
    thumbnail: EncodedImage


def _resize(frame: Image.Image, edge: int) -> Image.Image:
    frame = ImageOps.exif_transpose(frame)
    if frame.mode in {"I;16", "I;16B", "I;16L", "I"}:
        # Direct convert('L'/'RGB') clips values above 255 instead of scaling.
        frame = frame.convert("I").point(lambda value: value / 257).convert("L")
    result = frame.convert(
        "RGBA" if "A" in frame.getbands() or "transparency" in frame.info else "RGB"
    )
    result.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return result


def encode_image(content: BinaryIO, *, thumbnail_only: bool = False) -> ImageVariants:
    """Bound decoded pixels, remove metadata, preserve transparency and animation."""
    content.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(content) as image:
                if image.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                    raise ValueError("unsupported image format")
                pixels = image.width * image.height
                if pixels > MAX_IMAGE_PIXELS or (
                    image.format != "JPEG" and pixels > MAX_DECODED_PIXELS
                ):
                    raise ValueError("image pixel limit exceeded")
                original_size = image.size
                source_format = image.format
                # Read before exif_transpose removes orientation/XMP metadata.
                may_reuse_original = not image.getexif() and not any(
                    key in image.info for key in ("xmp", "XML:com.adobe.xmp")
                )
                icc_options = (
                    {"icc_profile": image.info["icc_profile"]}
                    if image.info.get("icc_profile")
                    else {}
                )
                frames = getattr(image, "n_frames", 1)
                if frames > MAX_ANIMATION_FRAMES or (
                    frames > 1 and frames * image.width * image.height > MAX_ANIMATION_PIXELS
                ):
                    raise ValueError("animation exceeds processing limit")
                if source_format == "JPEG":
                    # JPEG draft chooses a power-of-two DCT scale. Match the
                    # 3200px box to the photo's aspect ratio: a literal square
                    # target would leave an 8000x6000 JPEG at full resolution.
                    scale = min(1, 3200 / max(original_size))
                    image.draft("RGB", tuple(max(1, round(side * scale)) for side in original_size))
                image.seek(0)
                thumb = _resize(image, 640)
                thumb_buffer = BytesIO()
                thumb.save(thumb_buffer, format="WEBP", quality=76, method=4, **icc_options)
                thumbnail = EncodedImage(thumb_buffer.getvalue(), *thumb.size)
                if thumbnail_only:
                    return ImageVariants(thumbnail, thumbnail)
                full_frames = []
                durations = []
                for index in range(frames):
                    image.seek(index)
                    full_frames.append(_resize(image, 1600))
                    duration = int(image.info.get("duration", 100))
                    durations.append(100 if source_format == "GIF" and duration <= 10 else duration)
                output = BytesIO()
                options = (
                    {
                        "save_all": True,
                        "append_images": full_frames[1:],
                        "duration": durations,
                        # GIFs without a NETSCAPE loop extension play once.
                        "loop": image.info.get("loop", 1),
                    }
                    if frames > 1
                    else {}
                )
                full_frames[0].save(
                    output, format="WEBP", quality=80, method=4, **icc_options, **options
                )
                full = EncodedImage(output.getvalue(), *full_frames[0].size)
                if (
                    may_reuse_original
                    and source_format in {"JPEG", "WEBP"}
                    and max(original_size) <= 1600
                ):
                    content.seek(0)
                    original = content.read()
                    if len(original) <= len(full.data):
                        full = EncodedImage(
                            original, full.width, full.height, Image.MIME[source_format]
                        )
                return ImageVariants(full, thumbnail)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise AppError(
            "IMAGE_INVALID",
            "图片无法解码或尺寸过大（JPEG 最多 1 亿像素；PNG/WebP/GIF 最多 4000 万像素；动图最多 200 帧、总计 2000 万像素）。",
            422,
        ) from error


def store_image_variants(
    content: BinaryIO, filename: str, *, put, delete, make_key
) -> tuple[ImageVariants, str, str, str | None]:
    """Store both immutable variants; clean both keys on a partial storage failure."""
    variants = encode_image(content)
    extension = "jpg" if variants.full.mime_type == "image/jpeg" else "webp"
    key = make_key(f"{PurePath(filename).stem}.{extension}")
    thumbnail_key = f"{key.rsplit('.', 1)[0]}.thumb-640.webp"
    attempted = []
    try:
        attempted.append(key)
        etag = put(key, BytesIO(variants.full.data), variants.full.mime_type)
        attempted.append(thumbnail_key)
        put(thumbnail_key, BytesIO(variants.thumbnail.data), "image/webp")
    except Exception:
        for attempted_key in reversed(attempted):
            with suppress(Exception):
                delete(attempted_key)
        raise
    return variants, key, thumbnail_key, etag


async def thumbnail_urls(session: AsyncSession, urls) -> dict[str, str]:
    sources = {url for url in urls if url}
    if not sources:
        return {}
    return dict(
        (
            await session.execute(
                select(MediaThumbnail.source_url, MediaThumbnail.thumbnail_url).where(
                    MediaThumbnail.source_url.in_(sources)
                )
            )
        ).all()
    )


def reusable_image_query(checksum: str):
    """Legacy rows without a thumbnail have not passed the image pipeline yet."""
    return (
        select(Media)
        .where(
            Media.checksum == checksum,
            Media.mime_type.in_(("image/webp", "image/jpeg")),
            select(MediaThumbnail.source_url)
            .where(MediaThumbnail.source_url == Media.source_url)
            .exists(),
        )
        .order_by(Media.ctime.desc(), Media.id.desc())
        .limit(1)
    )


async def register_image_variants(
    session: AsyncSession,
    *,
    variants: ImageVariants,
    checksum: str,
    bucket_name: str,
    object_key: str,
    source_url: str,
    thumbnail_url: str,
    original_name: str,
    source_platform: str,
    etag: str | None,
) -> Media:
    """Reuse the row on a checksum/MIME conflict; retain its old object and URL alias.

    JPEG -> WebP may insert a separate row, but an old JPEG -> preserved JPEG
    (or WebP -> WebP) would violate the unique key unless we update that row.
    This and both thumbnail mappings are committed by the caller atomically.
    """
    item = await session.scalar(
        select(Media)
        .where(
            Media.checksum == checksum,
            Media.mime_type == variants.full.mime_type,
        )
        .with_for_update()
    )
    if item is None:
        item = Media(
            id=uuid4(),
            checksum=checksum,
            mime_type=variants.full.mime_type,
            source_platform=source_platform,
        )
        session.add(item)
    else:
        previous_url = item.source_url
        if previous_url != source_url:
            item.origin_url = item.origin_url or previous_url
            session.add(
                MediaThumbnail(
                    source_url=previous_url,
                    thumbnail_url=thumbnail_url,
                    width=variants.thumbnail.width,
                    height=variants.thumbnail.height,
                )
            )
    item.bucket_name = bucket_name
    item.object_key = object_key
    item.source_url = source_url
    item.original_name = original_name
    item.byte_size = len(variants.full.data)
    item.width, item.height = variants.full.width, variants.full.height
    item.etag = etag
    item.isvalid = True
    session.add(
        MediaThumbnail(
            source_url=source_url,
            thumbnail_url=thumbnail_url,
            width=variants.thumbnail.width,
            height=variants.thumbnail.height,
        )
    )
    return item


def first_markdown_image(markdown: str) -> str | None:
    # Existing producers use inline Markdown images. Ignore fenced code samples.
    import re

    text = re.sub(r"```.*?(?:```|\Z)", "", markdown, flags=re.DOTALL)
    match = re.search(r"!\[[^\]]*\]\(\s*<?(https?://[^\s)>]+)>?(?:\s+[^)]*)?\)", text)
    return match.group(1) if match else None
