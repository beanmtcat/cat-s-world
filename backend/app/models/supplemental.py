"""其余首期实体。

所有跨表 ID 都是逻辑关联；数据库层刻意不声明外键，完整性由服务层和定期扫描任务保障。
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base, IdTimestampValidityMixin


class LoginLog(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_login_logs"
    request_id: Mapped[UUID] = mapped_column(default=uuid4, unique=True, index=True)
    user_id: Mapped[UUID | None] = mapped_column(index=True)
    session_id: Mapped[UUID | None] = mapped_column(index=True)
    login_account: Mapped[str] = mapped_column(String(320))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    failure_reason: Mapped[str | None] = mapped_column(String(160))
    ip_address: Mapped[str] = mapped_column(
        String(64), comment="受控代理解析的 IP，查询/导出必须脱敏。"
    )
    user_agent: Mapped[str | None] = mapped_column(Text)


class OneTimeTokenMixin:
    user_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    requested_ip: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetToken(OneTimeTokenMixin, IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_password_reset_tokens"


class EmailVerificationToken(OneTimeTokenMixin, IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_email_verification_tokens"


class WebAuthnCredential(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_webauthn_credentials"
    user_id: Mapped[UUID] = mapped_column(index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key_cose: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(default=0, server_default="0")
    transports: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    aaguid: Mapped[UUID | None] = mapped_column()
    display_name: Mapped[str] = mapped_column(String(120))
    is_backup_eligible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_backed_up: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryCode(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_recovery_codes"
    user_id: Mapped[UUID] = mapped_column(index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FriendLink(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_friend_links"
    __table_args__ = (
        UniqueConstraint("translation_group_id", "locale"),
        UniqueConstraint("url", "locale"),
    )
    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(500))
    rel: Mapped[str] = mapped_column(
        String(80), default="noopener noreferrer", server_default="noopener noreferrer"
    )
    target: Mapped[str] = mapped_column(String(20), default="_blank", server_default="_blank")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_platform: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[str | None] = mapped_column(String(240), index=True)


class NavigationItem(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_navigation_items"
    __table_args__ = (UniqueConstraint("translation_group_id", "locale"),)
    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    location: Mapped[str] = mapped_column(String(20), default="header", server_default="header")
    label: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(Text)
    menu_id: Mapped[UUID | None] = mapped_column(
        index=True, comment="逻辑关联菜单组 ID。"
    )
    target_type: Mapped[str] = mapped_column(
        String(20), default="custom", server_default="custom", comment="custom、post、page、categories 或 tags。"
    )
    target_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default="[]", comment="目标内容逻辑 ID 列表；分类和标签可选择多个。"
    )
    icon_key: Mapped[str | None] = mapped_column(String(120))
    parent_id: Mapped[UUID | None] = mapped_column(index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    open_new_tab: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source_platform: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[str | None] = mapped_column(String(240), index=True)


class NavigationMenu(IdTimestampValidityMixin, Base):
    """A named menu collection; item relations remain logical IDs, not FKs."""

    __tablename__ = "mmcat_navigation_menus"
    __table_args__ = (UniqueConstraint("locale", "menu_key"),)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    menu_key: Mapped[str] = mapped_column(String(80), comment="菜单机器名，同语言内唯一。")
    name: Mapped[str] = mapped_column(String(120), comment="后台展示的菜单名称。")
    location: Mapped[str] = mapped_column(String(20), default="header", server_default="header")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

class PostCategory(Base):
    __tablename__ = "mmcat_post_categories"
    post_id: Mapped[UUID] = mapped_column(primary_key=True)
    category_id: Mapped[UUID] = mapped_column(primary_key=True, index=True)
    ctime: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    mtime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    isvalid: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class PostTag(Base):
    __tablename__ = "mmcat_post_tags"
    post_id: Mapped[UUID] = mapped_column(primary_key=True)
    tag_id: Mapped[UUID] = mapped_column(primary_key=True, index=True)
    ctime: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    mtime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    isvalid: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class MediaUpload(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_media_uploads"
    __table_args__ = (
        CheckConstraint(
            "state IN ('initiated', 'uploaded', 'scanning', 'ready', 'failed', 'expired')",
            name="state",
        ),
    )
    created_by: Mapped[UUID] = mapped_column(index=True)
    quarantine_object_key: Mapped[str] = mapped_column(String(1024), unique=True)
    original_name: Mapped[str] = mapped_column(Text)
    declared_mime_type: Mapped[str] = mapped_column(String(160))
    expected_byte_size: Mapped[int] = mapped_column()
    expected_checksum: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="initiated", server_default="initiated")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))


class Media(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_media"
    __table_args__ = (UniqueConstraint("checksum", "mime_type"),)
    post_id: Mapped[UUID | None] = mapped_column(index=True)
    bucket_name: Mapped[str] = mapped_column(String(160))
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    source_url: Mapped[str] = mapped_column(
        Text, comment="可访问的完整 URL；Halo 迁移时保留原始 URL。"
    )
    original_name: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(160))
    byte_size: Mapped[int] = mapped_column()
    checksum: Mapped[str] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(255))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    source_platform: Mapped[str | None] = mapped_column(String(30))
    origin_url: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(String(240), index=True)


class ImportJob(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_import_jobs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('uploaded', 'validating', 'ready', 'running', 'completed', 'failed', 'rolled_back')",
            name="state",
        ),
    )
    source_platform: Mapped[str] = mapped_column(String(40), default="halo", server_default="halo")
    source_version: Mapped[str] = mapped_column(String(20), default="v2", server_default="v2")
    source_file_name: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="uploaded", server_default="uploaded")
    total_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    success_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    warning_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportItem(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_import_items"
    __table_args__ = (UniqueConstraint("job_id", "source_id"),)
    job_id: Mapped[UUID] = mapped_column(index=True)
    source_id: Mapped[str] = mapped_column(String(180))
    source_url: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(String(20), default="post", server_default="post")
    target_post_id: Mapped[UUID | None] = mapped_column(index=True)
    target_page_id: Mapped[UUID | None] = mapped_column(index=True)
    target_slug: Mapped[str | None] = mapped_column(String(180))
    state: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    warnings: Mapped[list[object]] = mapped_column(JSONB, default=list, server_default="[]")
    error_message: Mapped[str | None] = mapped_column(String(1000))


class OutboxEvent(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_outbox_events"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[UUID | None] = mapped_column(index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))


class Gallery(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_galleries"
    __table_args__ = (UniqueConstraint("locale", "slug"),)
    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    slug: Mapped[str] = mapped_column(String(180))
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    visibility: Mapped[str] = mapped_column(String(20), default="public", server_default="public")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_platform: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[str | None] = mapped_column(String(240), index=True)


class GalleryItem(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_gallery_items"
    __table_args__ = (UniqueConstraint("gallery_id", "media_id"),)
    gallery_id: Mapped[UUID] = mapped_column(index=True)
    media_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str | None] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(String(1000))
    alt_text: Mapped[str | None] = mapped_column(String(500))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class VisitEvent(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_visit_events"
    ip_address: Mapped[str | None] = mapped_column(
        String(64), comment="访问客户端 IP；仅用于站点自有访问统计与安全分析。"
    )
    visitor_hash: Mapped[str] = mapped_column(String(64), index=True)
    session_hash: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(Text, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    referrer: Mapped[str | None] = mapped_column(Text)
    utm_source: Mapped[str | None] = mapped_column(String(160))
    utm_medium: Mapped[str | None] = mapped_column(String(160))
    utm_campaign: Mapped[str | None] = mapped_column(String(160))
    device_type: Mapped[str | None] = mapped_column(String(40))
    browser: Mapped[str | None] = mapped_column(String(80))
    operating_system: Mapped[str | None] = mapped_column(String(80))
    country_code: Mapped[str | None] = mapped_column(String(2))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class VisitDaily(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_visit_daily"
    __table_args__ = (UniqueConstraint("stat_date", "path", "locale"),)
    stat_date: Mapped[date] = mapped_column(Date)
    path: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    page_views: Mapped[int] = mapped_column(default=0, server_default="0")
    unique_visitors: Mapped[int] = mapped_column(default=0, server_default="0")
    sessions: Mapped[int] = mapped_column(default=0, server_default="0")


class AITool(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_ai_tools"
    __table_args__ = (
        UniqueConstraint("translation_group_id", "locale"),
        UniqueConstraint("locale", "slug"),
    )
    translation_group_id: Mapped[UUID] = mapped_column(default=uuid4, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN", server_default="zh-CN")
    slug: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    logo_media_id: Mapped[UUID | None] = mapped_column(index=True)
    icon_key: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_featured: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class StatusNode(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_status_nodes"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    region: Mapped[str | None] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(Text, comment="仅 Worker 使用的探测地址，绝不公开返回。")
    check_type: Mapped[str] = mapped_column(String(20), default="http", server_default="http")
    http_method: Mapped[str] = mapped_column(String(10), default="HEAD", server_default="HEAD")
    expected_status: Mapped[int] = mapped_column(default=200, server_default="200")
    timeout_ms: Mapped[int] = mapped_column(default=5000, server_default="5000")
    interval_seconds: Mapped[int] = mapped_column(default=60, server_default="60")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_status: Mapped[str] = mapped_column(
        String(20), default="unknown", server_default="unknown"
    )
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StatusCheck(IdTimestampValidityMixin, Base):
    __tablename__ = "mmcat_status_checks"
    node_id: Mapped[UUID] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
