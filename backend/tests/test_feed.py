from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.api.v1 import feed
from app.db.session import get_session
from app.main import app
from app.schemas.feed import FeedPostPush

PUSH_BODY = {
    "unit_id": "feed_unit_smoke",
    "title": "测试动态",
    "kicker": "副标题",
    "category": "安全",
    "body_md": "正文 **markdown**",
    "published_at": "2026-10-03T12:00:00+08:00",
}


class FakeSession:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.commits = 0
        self.added: list[object] = []
        self.objects: dict[object, object] = {}

    async def scalar(self, _statement: object) -> object:
        return self.results.pop(0)

    async def get(self, _model: object, item_id: object) -> object:
        return self.objects.get(item_id)

    async def execute(self, _statement: object) -> object:
        return SimpleNamespace(all=lambda: [(item.source_url, item.thumbnail_url) for item in self.added if hasattr(item, "thumbnail_url")])

    async def commit(self) -> None:
        self.commits += 1

    def add(self, item: object) -> None:
        self.added.append(item)


def png_image() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2000, 1000), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


def test_feed_push_schema_requires_aware_time_and_safe_id() -> None:
    assert FeedPostPush(**PUSH_BODY).published_at.utcoffset().total_seconds() == 8 * 3600
    with pytest.raises(ValidationError):
        FeedPostPush(**{**PUSH_BODY, "published_at": "2026-10-03T12:00:00"})
    with pytest.raises(ValidationError):
        FeedPostPush(**{**PUSH_BODY, "unit_id": "bad id with spaces"})


def test_push_token_never_accepts_missing_or_wrong_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = hashlib.sha256(b"correct-token").hexdigest()
    monkeypatch.setattr(feed, "get_settings", lambda: SimpleNamespace(feed_posts_token_hash=SecretStr(expected)))
    feed._check_push_token("Bearer correct-token")
    with pytest.raises(Exception) as wrong:
        feed._check_push_token("Bearer wrong-token")
    assert wrong.value.status_code == 401
    with pytest.raises(Exception) as absent:
        feed._check_push_token(None)
    assert absent.value.status_code == 401


def test_push_creates_updates_and_repeating_same_data_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = hashlib.sha256(b"correct-token").hexdigest()
    monkeypatch.setattr(feed, "get_settings", lambda: SimpleNamespace(feed_posts_token_hash=SecretStr(expected)))

    async def no_limit(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(feed, "enforce_rate_limit", no_limit)
    created_id = uuid4()
    existing = SimpleNamespace(
        id=created_id,
        isvalid=True,
        title=PUSH_BODY["title"],
        kicker=PUSH_BODY["kicker"],
        category=PUSH_BODY["category"],
        body_md=PUSH_BODY["body_md"],
        published_at=FeedPostPush(**PUSH_BODY).published_at,
    )
    session = FakeSession([created_id, None, existing, None, existing, None, existing])

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    try:
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        first = client.post("/api/feed-posts", json=PUSH_BODY, headers={"Authorization": "Bearer correct-token"})
        second = client.post("/api/feed-posts", json=PUSH_BODY, headers={"Authorization": "Bearer correct-token"})
        with_image = {
            **PUSH_BODY,
            "title": "测试动态（已补图）",
            "category": "AI",
            "body_md": "正文 **markdown**\n\n![配图](https://s3.hzcat.com/halo/test.png)",
        }
        third = client.post("/api/feed-posts", json=with_image, headers={"Authorization": "Bearer correct-token"})
        fourth = client.post("/api/feed-posts", json=with_image, headers={"Authorization": "Bearer correct-token"})
        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert third.status_code == fourth.status_code == 200
        assert {result.json()["data"]["id"] for result in (first, second, third, fourth)} == {str(created_id)}
        assert first.json()["data"]["created"] is True
        assert second.json()["data"]["created"] is False
        assert second.json()["data"]["updated"] is False
        assert third.json()["data"]["created"] is False
        assert third.json()["data"]["updated"] is True
        assert fourth.json()["data"]["updated"] is False
        assert existing.title == with_image["title"]
        assert existing.category == with_image["category"]
        assert existing.body_md == with_image["body_md"]
        assert session.commits == 2
    finally:
        app.dependency_overrides.clear()


def test_push_cannot_restore_deleted_post(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = hashlib.sha256(b"correct-token").hexdigest()
    monkeypatch.setattr(feed, "get_settings", lambda: SimpleNamespace(feed_posts_token_hash=SecretStr(expected)))

    async def no_limit(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(feed, "enforce_rate_limit", no_limit)
    deleted = SimpleNamespace(id=uuid4(), isvalid=False)
    session = FakeSession([None, deleted])

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    try:
        response = TestClient(app, base_url="http://localhost", raise_server_exceptions=False).post(
            "/api/feed-posts", json=PUSH_BODY, headers={"Authorization": "Bearer correct-token"}
        )
        assert response.status_code == 409, response.text
        assert session.commits == 0
    finally:
        app.dependency_overrides.clear()


def test_feed_image_upload_requires_token_and_returns_public_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = hashlib.sha256(b"correct-token").hexdigest()
    monkeypatch.setattr(feed, "get_settings", lambda: SimpleNamespace(
        feed_posts_token_hash=SecretStr(expected),
        s3_public_base_url="https://s3.hzcat.com/halo",
        s3_bucket="halo",
    ))

    async def no_limit(*_args: object, **_kwargs: object) -> None:
        return None

    uploaded: list[tuple[str, bytes, str]] = []

    def fake_upload(key: str, content: object, mime_type: str) -> None:
        content.seek(0)
        uploaded.append((key, content.read(), mime_type))

    monkeypatch.setattr(feed, "enforce_rate_limit", no_limit)
    monkeypatch.setattr(feed, "_scan_upload", lambda _content: None)
    monkeypatch.setattr(feed, "_put_s3_object", fake_upload)
    monkeypatch.setattr(feed, "_public_media_url", lambda key: f"https://s3.hzcat.com/halo/{key}")
    session = FakeSession([None, None])

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    image = png_image()
    headers = {"Authorization": "Bearer correct-token", "Content-Type": "image/png"}
    try:
        assert client.post("/api/feed-images", content=image, headers={"Content-Type": "image/png"}).status_code == 401
        assert client.post("/api/feed-images", content=b"not an image", headers=headers).status_code == 422
        response = client.post("/api/feed-images", content=image, headers=headers)
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["url"].startswith("https://s3.hzcat.com/halo/public/")
        assert data["markdown"] == f"![图片]({data['url']})"
        assert data["mime_type"] == "image/webp"
        assert data["group_id"] is None
        assert len(uploaded) == 2
        assert data["thumbnail_url"].endswith(".thumb-640.webp")
        assert Image.open(BytesIO(uploaded[0][1])).size == (1600, 800)
        assert Image.open(BytesIO(uploaded[1][1])).size == (640, 320)
        assert all(upload[2] == "image/webp" for upload in uploaded)
        assert len(session.added) == 2
        media = session.added[0]
        assert media.source_url == data["url"]
        assert media.checksum == hashlib.sha256(image).hexdigest()
        assert media.source_platform == "feed"
        assert session.commits == 1
        media.isvalid = True  # The fake session does not apply database defaults.
        group_id = uuid4()
        session.objects[group_id] = SimpleNamespace(id=group_id, isvalid=True, collection_kind="attachment_group")
        session.results.extend([media, None])
        duplicate = client.post(f"/api/feed-images?group_id={group_id}", content=image, headers=headers)
        assert duplicate.status_code == 201
        assert duplicate.json()["data"]["url"] == data["url"]
        assert duplicate.json()["data"]["group_id"] == str(group_id)
        assert len(uploaded) == 2
        assert duplicate.json()["data"]["thumbnail_url"] == data["thumbnail_url"]
        assert session.added[2].gallery_id == group_id
        assert session.added[2].media_id == media.id
        assert session.commits == 2
        session.added[2].isvalid = True
        session.results.extend([media, session.added[2]])
        repeated = client.post(f"/api/feed-images?group_id={group_id}", content=image, headers=headers)
        assert repeated.status_code == 201
        assert len(session.added) == 3
        assert session.commits == 2
        invalid = client.post(f"/api/feed-images?group_id={uuid4()}", content=image, headers=headers)
        assert invalid.status_code == 404
        gallery_id = uuid4()
        session.objects[gallery_id] = SimpleNamespace(id=gallery_id, isvalid=True, collection_kind="gallery")
        wrong_kind = client.post(f"/api/feed-images?group_id={gallery_id}", content=image, headers=headers)
        assert wrong_kind.status_code == 404
        assert len(uploaded) == 2
    finally:
        app.dependency_overrides.clear()


def test_new_feed_image_can_be_uploaded_directly_to_group(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = hashlib.sha256(b"correct-token").hexdigest()
    monkeypatch.setattr(feed, "get_settings", lambda: SimpleNamespace(
        feed_posts_token_hash=SecretStr(expected),
        s3_public_base_url="https://s3.hzcat.com/halo",
        s3_bucket="halo",
    ))

    async def no_limit(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(feed, "enforce_rate_limit", no_limit)
    monkeypatch.setattr(feed, "_scan_upload", lambda _content: None)
    monkeypatch.setattr(feed, "_put_s3_object", lambda *_args: None)
    monkeypatch.setattr(feed, "_public_media_url", lambda key: f"https://s3.hzcat.com/halo/{key}")
    group_id = uuid4()
    session = FakeSession([None, None, None])
    session.objects[group_id] = SimpleNamespace(id=group_id, isvalid=True, collection_kind="attachment_group")

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    try:
        image = png_image()
        response = TestClient(app, base_url="http://localhost", raise_server_exceptions=False).post(
            f"/api/feed-images?group_id={group_id}",
            content=image,
            headers={"Authorization": "Bearer correct-token", "Content-Type": "image/png"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["data"]["group_id"] == str(group_id)
        assert len(session.added) == 3
        assert session.added[2].gallery_id == group_id
        assert session.added[2].media_id == session.added[0].id
        assert session.commits == 1
    finally:
        app.dependency_overrides.clear()


def test_feed_image_upload_rejects_internal_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = hashlib.sha256(b"correct-token").hexdigest()
    monkeypatch.setattr(feed, "get_settings", lambda: SimpleNamespace(
        feed_posts_token_hash=SecretStr(expected),
        s3_public_base_url="http://minio:9000/halo",
        s3_bucket="halo",
    ))
    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    response = client.post("/api/feed-images", content=b"image", headers={"Authorization": "Bearer correct-token", "Content-Type": "image/png"})
    assert response.status_code == 503
