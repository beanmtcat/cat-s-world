"""Create the persistent administration audit log.

Revision ID: 0006_audit_logs
Revises: 0005_user_avatar_media
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_audit_logs"
down_revision = "0005_user_avatar_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "mmcat_audit_logs" in inspector.get_table_names():
        return
    op.create_table(
        "mmcat_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("ctime", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("isvalid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("request_id", name="uq_mmcat_audit_logs_request_id"),
    )
    op.create_index("ix_mmcat_audit_logs_actor_id", "mmcat_audit_logs", ["actor_id"])
    op.create_index("ix_mmcat_audit_logs_action", "mmcat_audit_logs", ["action"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "mmcat_audit_logs" not in inspector.get_table_names():
        return
    op.drop_index("ix_mmcat_audit_logs_action", table_name="mmcat_audit_logs")
    op.drop_index("ix_mmcat_audit_logs_actor_id", table_name="mmcat_audit_logs")
    op.drop_table("mmcat_audit_logs")
