"""Persist reply counters retained by Halo comment exports.

Revision ID: 0009_comment_reply_counts
Revises: 0008_legacy_comment_owners
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_comment_reply_counts"
down_revision = "0008_legacy_comment_owners"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mmcat_comments",
        sa.Column("legacy_reply_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("mmcat_comments", "legacy_reply_count")
