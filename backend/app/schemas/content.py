from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel


class PostSummary(ApiModel):
    id: UUID
    locale: str
    slug: str
    title: str
    summary: str | None
    cover_url: str | None
    cover_alt_text: str | None
    published_at: datetime | None
    seo_title: str | None


class PostDetail(PostSummary):
    content: str
    content_format: Literal["markdown", "html"]
    canonical_url: str | None
    robots_index: bool
    robots_follow: bool
    structured_data_type: str


class StudioPostCreate(ApiModel):
    """最小可用的后台草稿写入载荷，正文统一保存为 Markdown。"""

    title: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=180, pattern=r"^[a-z0-9][a-z0-9-]*$")
    content: str = Field(min_length=1, max_length=2_000_000)
    summary: str | None = Field(default=None, max_length=1000)
    locale: str = Field(default="zh-CN", pattern="^(zh-CN|en-US)$")
    cover_url: str | None = Field(default=None, max_length=2048)
    content_format: Literal["markdown", "html"] = "markdown"


class StudioPostUpdate(ApiModel):
    """后台文章编辑与发布载荷；未给出的字段保持原值。"""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    slug: str | None = Field(default=None, min_length=1, max_length=180, pattern=r"^[a-z0-9][a-z0-9-]*$")
    content: str | None = Field(default=None, min_length=1, max_length=2_000_000)
    summary: str | None = Field(default=None, max_length=1000)
    cover_url: str | None = Field(default=None, max_length=2048)
    locale: Literal["zh-CN", "en-US"] | None = None
    status: Literal["draft", "published", "archived"] | None = None
    visibility: Literal["public", "unlisted", "private"] | None = None
    content_format: Literal["markdown", "html"] | None = None


class StudioTaxonomyInput(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str | None = Field(default=None, max_length=4000)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    sort_order: int = Field(default=0, ge=0, le=100000)


class StudioPageInput(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=180, pattern=r"^[a-z0-9][a-z0-9-]*$")
    content: str = Field(min_length=1, max_length=2_000_000)
    content_format: Literal["markdown", "html"] = "markdown"
    summary: str | None = Field(default=None, max_length=500)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    status: Literal["draft", "published", "archived"] = "draft"
    visibility: Literal["public", "unlisted", "private"] = "private"
    seo_title: str | None = Field(default=None, max_length=240)
    seo_description: str | None = Field(default=None, max_length=500)


class StudioToolInput(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    url: str = Field(min_length=1, max_length=2048)
    description: str | None = Field(default=None, max_length=500)
    provider: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    icon_key: str | None = Field(default=None, max_length=120)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    sort_order: int = Field(default=0, ge=0, le=100000)
    is_featured: bool = True


class StudioSitePropertiesUpdate(ApiModel):
    site_name_zh: str | None = Field(default=None, max_length=160)
    site_name_en: str | None = Field(default=None, max_length=160)
    site_description_zh: str | None = Field(default=None, max_length=500)
    site_description_en: str | None = Field(default=None, max_length=500)
    logo_url: str | None = Field(default=None, max_length=2048)
    favicon_url: str | None = Field(default=None, max_length=2048)
    default_og_image_url: str | None = Field(default=None, max_length=2048)
    default_locale: Literal["zh-CN", "en-US"] | None = None
    timezone: str | None = Field(default=None, max_length=80)
    comments_enabled: bool | None = None
    sitemap_enabled: bool | None = None


class StudioCommentModeration(ApiModel):
    status: Literal["pending", "approved", "rejected", "spam"]
    moderation_note: str | None = Field(default=None, max_length=500)


class CommentCreate(ApiModel):
    target_type: str = Field(pattern="^(post|page)$")
    target_id: UUID
    parent_id: UUID | None = None
    content: str = Field(min_length=1, max_length=5000)


class CommentPublic(ApiModel):
    id: UUID
    target_type: str
    target_id: UUID
    parent_id: UUID | None
    author_name: str
    content: str
    status: str
    ctime: datetime


class SitePublic(ApiModel):
    site_name: str
    site_description: str | None
    logo_url: str | None
    favicon_url: str | None
    default_og_image_url: str | None
    locale: str
    comments_enabled: bool
    sitemap_enabled: bool
