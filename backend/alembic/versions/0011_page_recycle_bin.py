"""Track when standalone pages enter the recycle bin.

Revision ID: 0011_page_recycle_bin
Revises: 0010_category_home_visibility
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_page_recycle_bin"
down_revision = "0010_category_home_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mmcat_pages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("mmcat_pages", "deleted_at")
