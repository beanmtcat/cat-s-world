from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ApiModel


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("此字段不能为空。")
    return value


class FeedPostPush(BaseModel):
    """Wire format for POST /api/feed-posts; field names are intentionally snake_case."""

    unit_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    title: str = Field(min_length=1, max_length=300)
    kicker: str | None = Field(default=None, max_length=200)
    category: str = Field(min_length=1, max_length=80)
    body_md: str = Field(min_length=1, max_length=100_000)
    published_at: datetime

    @field_validator("title", "category", "body_md")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("kicker")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at 必须包含时区，例如 +08:00。")
        return value


class FeedPostStudio(ApiModel):
    unit_id: str | None = Field(default=None, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    kicker: str | None = Field(default=None, max_length=200)
    category: str = Field(min_length=1, max_length=80)
    body_md: str = Field(min_length=1, max_length=100_000)
    published_at: datetime

    @field_validator("title", "category", "body_md")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("发布时间必须包含时区。")
        return value


class FeedCommentCreate(ApiModel):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        return _required_text(value)


class FeedCommentModeration(ApiModel):
    status: Literal["approved", "rejected"]
