from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import require_csrf
from app.db.session import get_session
from app.main import app
from app.models.core import ContentRevision, Post
from app.schemas.content import StudioPostCreate, StudioPostUpdate


@pytest.mark.parametrize("schema", [StudioPostCreate, StudioPostUpdate])
@pytest.mark.parametrize("slug", ["new-post", "1737100817235.html", "old-post-2.html"])
def test_post_save_accepts_current_and_legacy_addresses(schema, slug):
    body = schema(title="文章", slug=slug, content="正文")
    assert body.slug == slug


@pytest.mark.parametrize("schema", [StudioPostCreate, StudioPostUpdate])
@pytest.mark.parametrize(
    "slug", ["../post.html", "a/b.html", "post.html.html", "post.htm", "a?b", "a#b"]
)
def test_post_save_rejects_unsafe_or_unsupported_addresses(schema, slug):
    with pytest.raises(ValidationError):
        schema(title="文章", slug=slug, content="正文")


def test_saving_legacy_post_through_api_preserves_address_and_records_revision(monkeypatch):
    post = Post(
        id=uuid4(),
        title="Ubuntu 24.04 SSH Server 修改默认端口重启无效",
        slug="1737100817235.html",
        content="旧正文",
        content_hash="a" * 64,
        content_format="markdown",
        locale="zh-CN",
        author_id=uuid4(),
        status="published",
        visibility="public",
        isvalid=True,
    )

    class Session:
        commits = 0

        def __init__(self):
            self.added = []
            self.results = iter([post, 1])

        async def scalar(self, statement):
            return next(self.results)

        async def scalars(self, statement):
            return []

        async def execute(self, statement):
            pass

        def add(self, item):
            self.added.append(item)

        def add_all(self, items):
            self.added.extend(items)

        async def flush(self):
            pass

        async def commit(self):
            self.commits += 1

        async def refresh(self, item):
            pass

    session = Session()

    async def fake_session():
        yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, fake_session)
    monkeypatch.setitem(
        app.dependency_overrides, require_csrf,
        lambda: (SimpleNamespace(id=post.author_id, role="admin"), None),
    )
    client = TestClient(app, base_url="http://localhost")
    response = client.patch(
        f"/api/v1/studio/posts/{post.id}",
        json={
            "title": post.title,
            "slug": post.slug,
            "content": "修改后的正文",
            "contentFormat": "markdown",
            "summary": "更新的摘要",
            "coverUrl": "https://images.example/old-cover.png",
            "locale": "zh-CN",
            "categoryIds": [],
            "tagIds": [],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["slug"] == "1737100817235.html"
    assert session.commits == 1
    assert post.content == "修改后的正文"
    assert post.status == "published"
    revision = next(item for item in session.added if isinstance(item, ContentRevision))
    assert revision.slug == post.slug
    assert revision.content == post.content
    assert revision.revision_no == 2
