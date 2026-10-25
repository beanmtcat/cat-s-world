from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request
from PIL import Image

from app.api.v1 import studio
from app.models.supplemental import Media


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["png", "pdf", "jpeg", "webp"])
async def test_attachment_upload_converts_images_only(monkeypatch, kind):
    buffer = BytesIO()
    if kind in {"jpeg", "webp"}:
        Image.effect_noise((128, 96), 100).convert("RGB").save(
            buffer, format=kind.upper(), quality=1
        )
    else:
        Image.new("RGB", (1800, 900), "green").save(buffer, format="PNG")
    data = b"%PDF-1.7\nordinary document\n" if kind == "pdf" else buffer.getvalue()
    mime_type = "application/pdf" if kind == "pdf" else f"image/{kind}"
    written = []

    async def no_limit(*args, **kwargs):
        pass

    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}

    def put(key, content, mime_type):
        written.append((key, content.read(), mime_type))
        return "etag"

    class Session:
        added = []

        async def scalar(self, statement):
            return None

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            for item in self.added:
                if isinstance(item, Media):
                    item.id = uuid4()

        async def commit(self):
            pass

        async def refresh(self, item):
            item.ctime = item.mtime = datetime.now(UTC)

    monkeypatch.setattr(studio, "enforce_rate_limit", no_limit)
    monkeypatch.setattr(studio, "_scan_upload", lambda content: content.seek(0))
    monkeypatch.setattr(studio, "_put_s3_object", put)
    monkeypatch.setattr(studio, "_public_media_url", lambda key: "https://images.example/" + key)
    monkeypatch.setattr(studio, "_upload_object_key", lambda filename: filename)
    monkeypatch.setattr(studio, "get_settings", lambda: SimpleNamespace(s3_bucket="images"))
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    request.state.request_id = uuid4()
    result = await studio.upload_media(
        request,
        filename="document.pdf" if kind == "pdf" else f"photo.{kind}",
        mime_type=mime_type,
        gallery_id=None,
        _=(None, None),
        session=Session(),
    )
    payload = result["data"]
    if kind == "png":
        assert payload["mimeType"] == "image/webp"
        assert payload["sourceUrl"].endswith("photo.webp")
        assert payload["thumbnailUrl"].endswith("photo.thumb-640.webp")
        assert (payload["width"], payload["height"]) == (1600, 800)
        assert len(written) == 2
    elif kind == "pdf":
        assert payload["mimeType"] == "application/pdf"
        assert payload["thumbnailUrl"] is None
        assert written == [("document.pdf", data, "application/pdf")]
    else:
        extension = "jpg" if kind == "jpeg" else "webp"
        assert payload["mimeType"] == mime_type
        assert payload["sourceUrl"].endswith("photo." + extension)
        assert payload["byteSize"] == len(data)
        assert payload["thumbnailUrl"].endswith("photo.thumb-640.webp")
        assert written[0] == (f"photo.{extension}", data, mime_type)
