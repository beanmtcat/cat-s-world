from __future__ import annotations

import base64
import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.core.errors import AppError
from app.importers.halo_v2 import read_halo_v2_archive, read_halo_workdir_bundle_path


def archive_with(name: str, content: str) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(name, content)
    return output.getvalue()


def test_reads_halo_v2_post() -> None:
    payload = archive_with(
        "posts/example.yaml",
        """apiVersion: content.halo.run/v1alpha1
kind: Post
metadata:
  name: post-001
spec:
  title: 示例文章
  slug: example-post
  content: |-
    ---
    title: 示例文章
    ---

    # 正文
status:
  permalink: https://old.example.com/archives/example-post
""",
    )
    candidates = read_halo_v2_archive(payload)
    assert len(candidates) == 1
    assert candidates[0].source_id == "post-001"
    assert candidates[0].slug == "example-post"
    assert candidates[0].markdown.content == "# 正文"


def test_rejects_zip_slip() -> None:
    payload = archive_with("../unsafe.yaml", "not content")
    with pytest.raises(AppError, match="不安全路径"):
        read_halo_v2_archive(payload)


def test_reads_workdir_resources_without_media_bytes(tmp_path) -> None:
    def record(name: str, payload: dict[str, object]) -> dict[str, str]:
        return {
            "name": name,
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
        }

    post = {
        "metadata": {"name": "post-1"},
        "spec": {
            "title": "文章",
            "slug": "post-1",
            "releaseSnapshot": "snapshot-1",
            "publish": True,
            "categories": ["category-1"],
            "tags": ["tag-1"],
        },
        "status": {"permalink": "https://old.example/posts/post-1"},
    }
    page = {
        "metadata": {"name": "page-1"},
        "spec": {
            "title": "页面",
            "slug": "about",
            "releaseSnapshot": "snapshot-2",
            "publish": True,
        },
        "status": {"permalink": "https://old.example/about"},
    }
    records = [
        record("/registry/content.halo.run/posts/post-1", post),
        record("/registry/content.halo.run/singlepages/page-1", page),
        record(
            "/registry/content.halo.run/categories/category-1",
            {"metadata": {"name": "category-1"}, "spec": {"displayName": "分类", "slug": "cat"}},
        ),
        record(
            "/registry/content.halo.run/tags/tag-1",
            {"metadata": {"name": "tag-1"}, "spec": {"displayName": "标签", "slug": "tag"}},
        ),
        record(
            "/registry/storage.halo.run/groups/group-photos",
            {
                "metadata": {"name": "group-photos"},
                "spec": {"displayName": "照片", "description": "站点图片素材"},
            },
        ),
        record(
            "/registry/storage.halo.run/attachments/image-1",
            {
                "metadata": {"name": "image-1"},
                "spec": {
                    "displayName": "cat.png",
                    "mediaType": "image/png",
                    "size": 5,
                    "groupName": "group-photos",
                },
                "status": {"permalink": "https://old.example/upload/cat.png"},
            },
        ),
        record(
            "/registry/content.halo.run/comments/comment-1",
            {
                "metadata": {"name": "comment-1"},
                "spec": {
                    "subjectRef": {"kind": "Post", "name": "post-1"},
                    "raw": "好文章",
                    "approved": True,
                },
            },
        ),
        record(
            "/registry/menu.halo.run/menuitems/menu-1",
            {
                "metadata": {"name": "menu-1"},
                "spec": {"displayName": "首页", "href": "/", "priority": 1},
            },
        ),
        record(
            "/registry/links.halo.run/links/link-1",
            {
                "metadata": {"name": "link-1"},
                "spec": {"displayName": "友站", "url": "https://friend.example"},
            },
        ),
        record(
            "/registry/settings.halo.run/settings/site",
            {
                "metadata": {"name": "site"},
                "spec": {"siteName": "猫子的世界", "commentsEnabled": False},
            },
        ),
        record(
            "/registry/metrics.halo.run/statistics/day-1",
            {
                "metadata": {"name": "day-1"},
                "spec": {"date": "2026-09-26", "path": "/", "pv": 10, "uv": 4},
            },
        ),
        record(
            "/registry/content.halo.run/snapshots/snapshot-1",
            {"metadata": {"name": "snapshot-1"}, "spec": {"rawPatch": "# 正文"}},
        ),
        record(
            "/registry/content.halo.run/snapshots/snapshot-2",
            {"metadata": {"name": "snapshot-2"}, "spec": {"rawPatch": "# 关于"}},
        ),
    ]
    path = tmp_path / "halo-workdir.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("extensions.data", json.dumps(records))

    bundle = read_halo_workdir_bundle_path(str(path))

    assert {item.kind for item in bundle.candidates} == {"post", "page"}
    assert bundle.taxonomy["category"]["category-1"]["displayName"] == "分类"
    assert bundle.attachment_groups["group-photos"]["spec"]["displayName"] == "照片"
    assert len(bundle.attachments) == len(bundle.comments) == len(bundle.menu_items) == 1
    assert len(bundle.friend_links) == len(bundle.site_settings) == len(bundle.statistics) == 1
