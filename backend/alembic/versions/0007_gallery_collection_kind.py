"""Separate public galleries from imported attachment groups.

Revision ID: 0007_gallery_collection_kind
Revises: 0006_audit_logs
Create Date: 2026-09-30
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_gallery_collection_kind"
down_revision = "0006_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("mmcat_galleries")}
    if "collection_kind" not in columns:
        op.add_column(
            "mmcat_galleries",
            sa.Column("collection_kind", sa.String(length=24), nullable=False, server_default="gallery"),
        )
        op.create_index("ix_mmcat_galleries_collection_kind", "mmcat_galleries", ["collection_kind"])
    op.execute(
        "UPDATE mmcat_galleries SET collection_kind = 'attachment_group' "
        "WHERE source_platform = 'halo' AND source_id LIKE 'halo-attachment-group:%'"
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("mmcat_galleries")}
    if "collection_kind" in columns:
        op.drop_index("ix_mmcat_galleries_collection_kind", table_name="mmcat_galleries")
        op.drop_column("mmcat_galleries", "collection_kind")
