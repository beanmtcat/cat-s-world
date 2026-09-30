"""Link users to avatar assets managed by the media library.

Revision ID: 0005_user_avatar_media
Revises: 0004_nav_menus_targets
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_user_avatar_media"
down_revision = "0004_nav_menus_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("mmcat_users")}
    if "avatar_media_id" not in columns:
        op.add_column("mmcat_users", sa.Column("avatar_media_id", postgresql.UUID(as_uuid=True)))
        op.create_index("ix_mmcat_users_avatar_media_id", "mmcat_users", ["avatar_media_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("mmcat_users")}
    if "avatar_media_id" in columns:
        op.drop_index("ix_mmcat_users_avatar_media_id", table_name="mmcat_users")
        op.drop_column("mmcat_users", "avatar_media_id")
