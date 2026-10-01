"""Add effective options for the site settings sections.

Revision ID: 0012_site_settings_sections
Revises: 0011_page_recycle_bin
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_site_settings_sections"
down_revision = "0011_page_recycle_bin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mmcat_site_properties", sa.Column("posts_per_page", sa.Integer(), nullable=False, server_default="12"))
    op.add_column("mmcat_site_properties", sa.Column("category_posts_per_page", sa.Integer(), nullable=False, server_default="12"))
    op.add_column("mmcat_site_properties", sa.Column("tag_posts_per_page", sa.Integer(), nullable=False, server_default="12"))
    op.add_column("mmcat_site_properties", sa.Column("site_keywords", sa.String(length=1000), nullable=True))
    op.add_column("mmcat_site_properties", sa.Column("seo_noindex", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("mmcat_site_properties", sa.Column("registration_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("mmcat_site_properties", sa.Column("comments_moderation_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    op.drop_column("mmcat_site_properties", "comments_moderation_enabled")
    op.drop_column("mmcat_site_properties", "registration_enabled")
    op.drop_column("mmcat_site_properties", "seo_noindex")
    op.drop_column("mmcat_site_properties", "site_keywords")
    op.drop_column("mmcat_site_properties", "tag_posts_per_page")
    op.drop_column("mmcat_site_properties", "category_posts_per_page")
    op.drop_column("mmcat_site_properties", "posts_per_page")
