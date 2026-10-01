"""Add an explicit home-page visibility setting to categories.

Revision ID: 0010_category_home_visibility
Revises: 0009_comment_reply_counts
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_category_home_visibility"
down_revision = "0009_comment_reply_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mmcat_categories",
        sa.Column("is_home_visible", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("mmcat_categories", "is_home_visible")
