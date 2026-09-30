from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base, IdTimestampValidityMixin

Locale = Literal["zh-CN", "en-US"]


class User(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_users"
    __table_args__ = (
        CheckConstraint("role IN ('member', 'author', 'editor', 'admin')", name="role"),
        CheckConstraint("status IN ('pending_verification', 'active', 'disabled')", name="status"),
    )

    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, comment="用户邮箱，小写规范化保存。"
    )
    password_hash: Mapped[str] = mapped_column(String(512), comment="Argon2id 密码哈希。")
    display_name: Mapped[str] = mapped_column(String(120), comment="公开展示名称。")
    avatar_media_id: Mapped[UUID | None] = mapped_column(
        index=True, comment="逻辑关联附件表的头像媒体 ID。"
    )
    role: Mapped[str] = mapped_column(String(20), default="member", server_default="member")
    status: Mapped[str] = mapped_column(
        String(30), default="pending_verification", server_default="pending_verification"
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )


class UserSession(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_user_sessions"

    user_id: Mapped[UUID] = mapped_column(index=True, comment="逻辑关联用户 ID。")
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, comment="会话随机令牌的 HMAC-SHA-256 摘要。"
    )
    csrf_secret_hash: Mapped[str] = mapped_column(String(64), comment="CSRF 随机令牌摘要。")
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class SiteProperties(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_site_properties"
    __table_args__ = (
        Index(
            "mmcat_site_properties_singleton_idx",
            "isvalid",
            unique=True,
            postgresql_where=text("isvalid = true"),
        ),
    )

    site_name_zh: Mapped[str] = mapped_column(String(160), comment="中文站点名称。")
    site_name_en: Mapped[str] = mapped_column(String(160), comment="英文站点名称。")
    site_description_zh: Mapped[str | None] = mapped_column(String(500))
    site_description_en: Mapped[str | None] = mapped_column(String(500))
    logo_url: Mapped[str | None] = mapped_column(Text)
    favicon_url: Mapped[str | None] = mapped_column(Text)
    default_og_image_url: Mapped[str | None] = mapped_column(Text)
    default_locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    timezone: Mapped[str] = mapped_column(
        String(80), default="Asia/Shanghai", server_default="Asia/Shanghai"
    )
    comments_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sitemap_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class SearchEngineVerification(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_search_engine_verifications"
    __table_args__ = (
        UniqueConstraint("engine"),
        CheckConstraint(
            "engine IN ('baidu', 'sogou', 'so360', 'shenma', 'bing', 'google')", name="engine"
        ),
        CheckConstraint(
            "verification_method IN ('meta', 'html_file', 'dns')", name="verification_method"
        ),
        CheckConstraint("submission_mode IN ('none', 'sitemap', 'api')", name="submission_mode"),
    )

    engine: Mapped[str] = mapped_column(String(20), comment="搜索引擎代码。")
    verification_method: Mapped[str] = mapped_column(String(20), comment="验证方式。")
    verification_value: Mapped[str] = mapped_column(
        String(1000), comment="公开验证值，非 API 密钥。"
    )
    submission_mode: Mapped[str] = mapped_column(
        String(20), default="sitemap", server_default="sitemap"
    )
    credential_secret_ref: Mapped[str | None] = mapped_column(
        String(240), comment="密钥管理器引用。"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_submission_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(String(1000))


class Post(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_posts"
    __table_args__ = (
        UniqueConstraint("locale", "slug"),
        CheckConstraint("locale IN ('zh-CN', 'en-US')", name="locale"),
        CheckConstraint("status IN ('draft', 'scheduled', 'published', 'archived')", name="status"),
        CheckConstraint("visibility IN ('public', 'unlisted', 'private')", name="visibility"),
        Index(
            "mmcat_posts_public_idx",
            "locale",
            "published_at",
            postgresql_where=text(
                "status = 'published' AND visibility = 'public' AND isvalid = true"
            ),
        ),
    )

    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    slug: Mapped[str] = mapped_column(String(180), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(String(1000))
    content: Mapped[str] = mapped_column(Text)
    content_format: Mapped[str] = mapped_column(
        String(20), default="markdown", server_default="markdown"
    )
    file_path: Mapped[str | None] = mapped_column(Text, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    cover_url: Mapped[str | None] = mapped_column(Text)
    cover_alt_text: Mapped[str | None] = mapped_column(String(500))
    cover_caption: Mapped[str | None] = mapped_column(String(500))
    author_id: Mapped[UUID] = mapped_column(index=True, comment="逻辑关联用户 ID，不建外键。")
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    visibility: Mapped[str] = mapped_column(String(20), default="public", server_default="public")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seo_title: Mapped[str | None] = mapped_column(String(240))
    seo_description: Mapped[str | None] = mapped_column(String(500))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    og_image_url: Mapped[str | None] = mapped_column(Text)
    robots_index: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    robots_follow: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    structured_data_type: Mapped[str] = mapped_column(
        String(30), default="TechArticle", server_default="TechArticle"
    )
    source_platform: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[str | None] = mapped_column(String(240), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Page(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_pages"
    __table_args__ = (
        UniqueConstraint("locale", "slug"),
        CheckConstraint("locale IN ('zh-CN', 'en-US')", name="locale"),
        CheckConstraint("status IN ('draft', 'scheduled', 'published', 'archived')", name="status"),
        CheckConstraint("visibility IN ('public', 'unlisted', 'private')", name="visibility"),
    )

    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    slug: Mapped[str] = mapped_column(String(180), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    content_format: Mapped[str] = mapped_column(
        String(20), default="markdown", server_default="markdown"
    )
    template: Mapped[str] = mapped_column(String(40), default="default", server_default="default")
    author_id: Mapped[UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    visibility: Mapped[str] = mapped_column(String(20), default="public", server_default="public")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seo_title: Mapped[str | None] = mapped_column(String(240))
    seo_description: Mapped[str | None] = mapped_column(String(500))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    og_image_url: Mapped[str | None] = mapped_column(Text)
    robots_index: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    robots_follow: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source_platform: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[str | None] = mapped_column(String(240), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)


class ContentRevision(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_content_revisions"
    __table_args__ = (UniqueConstraint("target_type", "target_id", "revision_no"),)

    target_type: Mapped[str] = mapped_column(String(10), comment="post 或 page。")
    target_id: Mapped[UUID] = mapped_column(index=True, comment="逻辑关联内容 ID。")
    revision_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(String(1000))
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    slug: Mapped[str] = mapped_column(String(180))
    seo_title: Mapped[str | None] = mapped_column(String(240))
    seo_description: Mapped[str | None] = mapped_column(String(500))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    og_image_url: Mapped[str | None] = mapped_column(Text)
    robots_index: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    robots_follow: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    structured_data_type: Mapped[str | None] = mapped_column(String(30))
    editor_id: Mapped[UUID] = mapped_column(index=True)
    content_format: Mapped[str] = mapped_column(
        String(20), default="markdown", server_default="markdown"
    )
    cover_url: Mapped[str | None] = mapped_column(Text)
    change_summary: Mapped[str | None] = mapped_column(String(500))
    is_autosave: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class Category(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_categories"
    __table_args__ = (
        UniqueConstraint("locale", "slug"),
        CheckConstraint("locale IN ('zh-CN', 'en-US')", name="locale"),
    )

    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    slug: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[UUID | None] = mapped_column(index=True, comment="逻辑关联父分类 ID。")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Tag(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_tags"
    __table_args__ = (
        UniqueConstraint("locale", "slug"),
        CheckConstraint("locale IN ('zh-CN', 'en-US')", name="locale"),
    )

    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    slug: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)


class Comment(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_comments"
    __table_args__ = (
        CheckConstraint("num_nonnulls(post_id, page_id) = 1", name="one_target"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'spam')", name="status"),
    )

    post_id: Mapped[UUID | None] = mapped_column(index=True)
    page_id: Mapped[UUID | None] = mapped_column(index=True)
    parent_id: Mapped[UUID | None] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True, comment="已验证会员 ID，禁止游客评论。")
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    author_name: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    notify_replies: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    moderated_by: Mapped[UUID | None] = mapped_column(index=True)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderation_note: Mapped[str | None] = mapped_column(String(500))
    source_platform: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[str | None] = mapped_column(String(240), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)


class Redirect(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_redirects"
    __table_args__ = (
        UniqueConstraint("from_path"),
        CheckConstraint("status_code IN (301, 308)", name="status_code"),
    )

    from_path: Mapped[str] = mapped_column(String(2048), comment="标准化的旧 pathname 加 query。")
    to_path: Mapped[str] = mapped_column(String(2048), comment="已校验的站内绝对路径。")
    status_code: Mapped[int] = mapped_column(Integer, default=301, server_default="301")
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_audit_logs"

    actor_id: Mapped[UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(120))
    request_id: Mapped[UUID] = mapped_column(unique=True)
    actor_ip_hash: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
