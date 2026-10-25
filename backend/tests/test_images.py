from io import BytesIO

import pytest
from PIL import Image, ImageCms, JpegImagePlugin

from app.core.errors import AppError
from app.services.images import _resize, encode_image, first_markdown_image, store_image_variants


def image_bytes(size=(2400, 1200), mode="RGB", color="orange", **options):
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG", **options)
    buffer.seek(0)
    return buffer


def test_full_and_thumbnail_are_bounded_webp_without_upscaling():
    variants = encode_image(image_bytes())
    assert Image.open(BytesIO(variants.full.data)).format == "WEBP"
    assert (variants.full.width, variants.full.height) == (1600, 800)
    assert (variants.thumbnail.width, variants.thumbnail.height) == (640, 320)
    assert encode_image(image_bytes((1200, 2400))).full.height == 1600
    small = encode_image(image_bytes((128, 96)))
    assert (small.thumbnail.width, small.thumbnail.height) == (128, 96)


def test_transparency_orientation_and_metadata():
    original = image_bytes((800, 400), "RGBA", (255, 0, 0, 0))
    image = Image.open(BytesIO(encode_image(original).full.data))
    assert image.getpixel((0, 0))[3] == 0
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private author"
    result = Image.open(BytesIO(encode_image(image_bytes((800, 400), exif=exif)).full.data))
    assert result.size == (400, 800)
    assert not result.getexif()


def test_animated_upload_remains_animated_but_thumbnail_is_static():
    buffer = BytesIO()
    Image.new("RGB", (100, 50), "red").save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (100, 50), "blue")],
        duration=[100, 200],
        loop=0,
    )
    result = encode_image(buffer)
    assert Image.open(BytesIO(result.full.data)).n_frames == 2
    assert Image.open(BytesIO(result.thumbnail.data)).n_frames == 1


def test_invalid_and_oversize_images_are_rejected(monkeypatch):
    with pytest.raises(AppError):
        encode_image(BytesIO(b"\x89PNG\r\n\x1a\nnot-a-real-image"))
    monkeypatch.setattr("app.services.images.MAX_IMAGE_PIXELS", 100)
    with pytest.raises(AppError):
        encode_image(image_bytes((20, 20)))


def test_second_upload_failure_cleans_both_objects():
    writes, deletes = [], []

    def put(key, content, mime_type):
        writes.append(key)
        if len(writes) == 2:
            raise RuntimeError("storage unavailable")

    with pytest.raises(RuntimeError):
        store_image_variants(
            image_bytes((20, 20)),
            "photo.png",
            put=put,
            delete=deletes.append,
            make_key=lambda name: name,
        )
    assert set(deletes) == set(writes) == {"photo.webp", "photo.thumb-640.webp"}


def test_feed_image_extraction_ignores_code_and_supports_titles():
    assert (
        first_markdown_image(
            '```md\n![example](https://bad.example/a.png)\n```\n![photo](<https://example.com/a.png> "row")'
        )
        == "https://example.com/a.png"
    )
    assert first_markdown_image("![unsafe](javascript:alert(1))") is None


def test_48mp_jpeg_uses_draft_before_decode_and_produces_1600px_webp(monkeypatch):
    source = BytesIO()
    Image.new("RGB", (8000, 6000), "orange").save(source, format="JPEG")
    draft = JpegImagePlugin.JpegImageFile.draft
    decoded_sizes = []

    def track_draft(image, mode, size):
        assert image.tile  # draft must run before load() consumes the tile.
        result = draft(image, mode, size)
        decoded_sizes.append(image.size)
        return result

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", track_draft)
    result = encode_image(source)
    assert decoded_sizes == [(4000, 3000)]
    assert Image.open(BytesIO(result.full.data)).size == (1600, 1200)
    assert Image.open(BytesIO(result.full.data)).format == "WEBP"


def test_16_bit_png_keeps_grayscale_brightness():
    result = encode_image(image_bytes((32, 32), "I;16", 20000))
    for variant in (result.full, result.thumbnail):
        image = Image.open(BytesIO(variant.data)).convert("L")
        assert abs(image.getpixel((0, 0)) - 78) <= 2


@pytest.mark.parametrize("mode", ["I;16", "I;16B", "I;16L", "I"])
def test_all_16_bit_modes_are_scaled_before_rgb_conversion(mode):
    image = _resize(Image.new(mode, (8, 8), 20000), 640).convert("L")
    assert abs(image.getpixel((0, 0)) - 78) <= 2


@pytest.mark.parametrize("source_format", ["JPEG", "PNG", "WEBP"])
def test_icc_is_retained_in_full_image_and_thumbnail(source_format):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    source = BytesIO()
    Image.new("RGB", (1800, 900), "orange").save(source, format=source_format, icc_profile=profile)
    result = encode_image(source)
    for variant in (result.full, result.thumbnail):
        assert Image.open(BytesIO(variant.data)).info["icc_profile"] == profile


def webp_chunks(data):
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    offset = 12
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        yield data[offset : offset + 4], data[offset + 8 : offset + 8 + size]
        offset += 8 + size + (size % 2)


@pytest.mark.parametrize("duration,expected", [(None, 100), (0, 100), (10, 100), (20, 20)])
@pytest.mark.parametrize("loop", [None, 0, 3])
def test_gif_timing_and_loops_are_encoded_in_webp_chunks(duration, expected, loop):
    source = BytesIO()
    options = {}
    if duration is not None:
        options["duration"] = duration
    if loop is not None:
        options["loop"] = loop
    Image.new("RGB", (32, 32), "red").save(
        source,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")],
        **options,
    )
    chunks = list(webp_chunks(encode_image(source).full.data))
    durations = [
        int.from_bytes(payload[12:15], "little") for kind, payload in chunks if kind == b"ANMF"
    ]
    assert durations == [expected, expected]
    assert [
        int.from_bytes(payload[4:6], "little") for kind, payload in chunks if kind == b"ANIM"
    ] == [1 if loop is None else loop]


def test_animation_pixel_budget_is_20mp_and_rejects_before_resizing(monkeypatch):
    from app.services import images

    assert images.MAX_ANIMATION_PIXELS == 20_000_000
    source = BytesIO()
    Image.new("RGB", (100, 100), "red").save(
        source,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (100, 100), "blue")],
        duration=100,
    )
    monkeypatch.setattr(images, "MAX_ANIMATION_PIXELS", 19_999)
    monkeypatch.setattr(
        images, "_resize", lambda *args: pytest.fail("must reject before decoding frames")
    )
    with pytest.raises(AppError):
        encode_image(source)


@pytest.mark.parametrize("source_format", ["JPEG", "WEBP"])
def test_small_already_compressed_images_keep_original_bytes_and_storage_type(source_format):
    # High-entropy, low-quality input is much smaller than the quality-80 encode.
    source = BytesIO()
    Image.effect_noise((128, 96), 100).convert("RGB").save(source, format=source_format, quality=1)
    original = source.getvalue()
    result = encode_image(source)
    assert result.full.data == original
    assert result.full.mime_type == Image.MIME[source_format]
    assert Image.open(BytesIO(result.thumbnail.data)).format == "WEBP"
    writes = []

    def put(key, content, mime_type):
        writes.append((key, content.read(), mime_type))

    store_image_variants(
        source, "original.png", put=put, delete=lambda key: None, make_key=lambda name: name
    )
    assert writes[0][1:] == (original, Image.MIME[source_format])
    assert writes[0][0].endswith(".jpg" if source_format == "JPEG" else ".webp")
    assert writes[1][0] == "original.thumb-640.webp"


def test_small_jpeg_with_gps_is_reencoded_and_stored_without_exif():
    exif = Image.Exif()
    exif[271] = "Private camera"
    exif[34853] = {1: "N", 2: (37, 30, 0), 3: "E", 4: (122, 15, 0)}
    source = BytesIO()
    Image.effect_noise((1200, 900), 100).convert("RGB").save(
        source, format="JPEG", quality=20, exif=exif
    )
    original = source.getvalue()
    metadata = Image.open(BytesIO(original)).getexif()
    assert metadata[271] == "Private camera" and metadata.get_ifd(34853)[1] == "N"
    result = encode_image(source)
    assert result.full.mime_type == "image/webp"
    # This is the size-regression case: privacy must win over smaller original bytes.
    assert len(result.full.data) > len(original)
    stored = []

    def put(key, content, mime_type):
        stored.append((key, content.read(), mime_type))

    store_image_variants(
        source, "gps.jpg", put=put, delete=lambda key: None, make_key=lambda name: name
    )
    for key, data, mime_type in stored:
        image = Image.open(BytesIO(data))
        assert image.format == "WEBP" and mime_type == "image/webp"
        assert not image.getexif()
        assert "xmp" not in image.info
        assert key.endswith(".webp")


@pytest.mark.parametrize("source_format", ["JPEG", "WEBP"])
def test_small_images_with_xmp_cannot_reuse_original(source_format):
    source = BytesIO()
    Image.effect_noise((128, 96), 100).convert("RGB").save(
        source,
        format=source_format,
        quality=1,
        xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><private>GPS</private></x:xmpmeta>',
    )
    assert "xmp" in Image.open(BytesIO(source.getvalue())).info
    result = encode_image(source)
    assert result.full.mime_type == "image/webp"
    assert result.full.data != source.getvalue()
    for variant in (result.full, result.thumbnail):
        image = Image.open(BytesIO(variant.data))
        assert not image.getexif()
        assert not {"xmp", "XML:com.adobe.xmp"} & image.info.keys()


def test_legacy_xmp_info_key_also_disables_original_reuse(monkeypatch):
    source = BytesIO()
    Image.effect_noise((128, 96), 100).convert("RGB").save(source, format="JPEG", quality=1)
    real_open = Image.open

    def open_with_legacy_xmp(*args, **kwargs):
        image = real_open(*args, **kwargs)
        image.info["XML:com.adobe.xmp"] = "private GPS"
        return image

    with monkeypatch.context() as patch:
        patch.setattr(Image, "open", open_with_legacy_xmp)
        result = encode_image(source)
    assert result.full.mime_type == "image/webp"
    assert not real_open(BytesIO(result.full.data)).getexif()
    assert "XML:com.adobe.xmp" not in real_open(BytesIO(result.full.data)).info


def test_45mp_png_is_rejected_before_decoding(monkeypatch):
    from app.services import images

    assert images.MAX_DECODED_PIXELS == 40_000_000
    source = image_bytes((4500, 10000), "L", 78)
    monkeypatch.setattr(images, "_resize", lambda *args: pytest.fail("must reject before resizing"))
    with pytest.raises(AppError) as result:
        encode_image(source)
    assert result.value.code == "IMAGE_INVALID"
    assert "JPEG 最多 1 亿" in result.value.message
    assert "PNG/WebP/GIF 最多 4000 万" in result.value.message
