from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator

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
    category_ids: list[UUID] = Field(default_factory=list, max_length=16)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=48)


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
    category_ids: list[UUID] | None = Field(default=None, max_length=16)
    tag_ids: list[UUID] | None = Field(default=None, max_length=48)


class StudioTaxonomyInput(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str | None = Field(default=None, max_length=4000)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    sort_order: int = Field(default=0, ge=0, le=100000)
    is_home_visible: bool = True


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


class StudioNavigationInput(ApiModel):
    menu_id: UUID | None = None
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    location: Literal["header", "footer"] = "header"
    label: str = Field(min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=2048)
    target_type: Literal["custom", "post", "page", "categories", "tags"] = "custom"
    target_ids: list[UUID] = Field(default_factory=list, max_length=1)
    icon_key: str | None = Field(default=None, max_length=120)
    parent_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=100000)
    open_new_tab: bool = False


class StudioNavigationMenuInput(ApiModel):
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    menu_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    location: Literal["header", "footer"] = "header"
    sort_order: int = Field(default=0, ge=0, le=100000)


class StudioStatusNodeInput(ApiModel):
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name_zh: str = Field(min_length=1, max_length=120)
    display_name_en: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=80)
    endpoint: str = Field(min_length=1, max_length=2048)
    check_type: Literal["http", "tcp"] = "http"
    http_method: Literal["HEAD", "GET"] = "HEAD"
    expected_status: int = Field(default=200, ge=100, le=599)
    timeout_ms: int = Field(default=5000, ge=100, le=60000)
    interval_seconds: int = Field(default=60, ge=15, le=86400)
    sort_order: int = Field(default=0, ge=0, le=100000)


class StudioFriendLinkInput(ApiModel):
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)
    logo_url: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=500)
    rel: str = Field(default="noopener noreferrer", max_length=80)
    target: Literal["_self", "_blank"] = "_blank"
    sort_order: int = Field(default=0, ge=0, le=100000)

    @field_validator("url", "logo_url")
    @classmethod
    def validate_external_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("链接必须是完整的 http 或 https URL。")
        return value


class StudioGalleryInput(ApiModel):
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    slug: str = Field(min_length=1, max_length=180, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    cover_url: str | None = Field(default=None, max_length=2048)
    status: Literal["draft", "published", "archived"] = "draft"
    visibility: Literal["public", "unlisted", "private"] = "public"
    sort_order: int = Field(default=0, ge=0, le=100000)
    media_ids: list[UUID] = Field(default_factory=list, max_length=200)


class StudioUserUpdate(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Literal["member", "author", "editor", "admin"] | None = None
    status: Literal["pending_verification", "active", "disabled"] | None = None
    avatar_media_id: UUID | None = None


class StudioMediaUpdate(ApiModel):
    original_name: str | None = Field(default=None, max_length=1000)
    post_id: UUID | None = None


class StudioCommentReply(ApiModel):
    content: str = Field(min_length=1, max_length=5000)


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
    posts_per_page: int | None = Field(default=None, ge=1, le=100)
    category_posts_per_page: int | None = Field(default=None, ge=1, le=100)
    tag_posts_per_page: int | None = Field(default=None, ge=1, le=100)
    site_keywords: str | None = Field(default=None, max_length=1000)
    seo_noindex: bool | None = None
    registration_enabled: bool | None = None
    comments_moderation_enabled: bool | None = None


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
    posts_per_page: int
    category_posts_per_page: int
    tag_posts_per_page: int
    site_keywords: str | None
    seo_noindex: bool
    registration_enabled: bool
