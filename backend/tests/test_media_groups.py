from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request
from pydantic import ValidationError

from app.api.v1.studio import add_media_group_members, create_media_group
from app.schemas.content import StudioMediaGroupInput, StudioMediaGroupMembers


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.request_id = uuid4()
    return request


def test_media_group_name_is_required() -> None:
    assert StudioMediaGroupInput(name="  动态配图  ").name == "动态配图"
    with pytest.raises(ValidationError):
        StudioMediaGroupInput(name="   ")


@pytest.mark.asyncio
async def test_create_media_group_is_an_attachment_group() -> None:
    class Session:
        item = None

        def add(self, item: object) -> None:
            self.item = item

        async def commit(self) -> None:
            self.item.id = uuid4()
            self.item.ctime = datetime.now(UTC)

        async def refresh(self, _item: object) -> None:
            pass

    session = Session()
    result = await create_media_group(StudioMediaGroupInput(name="动态配图"), _request(), (None, None), session)
    assert session.item.name == "动态配图"
    assert session.item.collection_kind == "attachment_group"
    assert session.item.visibility == "private"
    assert result["data"]["name"] == "动态配图"


@pytest.mark.asyncio
async def test_existing_media_can_be_added_to_group() -> None:
    group_id, media_id = uuid4(), uuid4()

    class Session:
        added = None
        commits = 0
        reads = 0

        async def get(self, _model: object, _id: object) -> object:
            return SimpleNamespace(id=group_id, isvalid=True, collection_kind="attachment_group")

        async def scalars(self, _statement: object) -> list[object]:
            self.reads += 1
            return [media_id] if self.reads == 1 else []

        def add(self, item: object) -> None:
            self.added = item

        async def commit(self) -> None:
            self.commits += 1

    session = Session()
    result = await add_media_group_members(
        group_id, StudioMediaGroupMembers(mediaIds=[media_id]), _request(), (None, None), session
    )
    assert session.added.gallery_id == group_id
    assert session.added.media_id == media_id
    assert session.commits == 1
    assert result["data"]["added"] == 1
