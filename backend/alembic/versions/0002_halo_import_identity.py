"""Add source identity and Halo taxonomy metadata fields.

Revision ID: 0002_halo_import_identity
Revises: 0001_core_foundation
Create Date: 2026-09-26
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_halo_import_identity"
down_revision = "0001_core_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    def add_column(table: str, column: sa.Column) -> None:
        if column.name not in {item["name"] for item in inspector.get_columns(table)}:
            op.add_column(table, column)

    def add_index(name: str, table: str, columns: list[str]) -> None:
        if name not in {item["name"] for item in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)

    for column in (
        sa.Column("source_platform", sa.String(length=30)),
        sa.Column("source_id", sa.String(length=240)),
        sa.Column("source_url", sa.Text()),
    ):
        add_column("mmcat_pages", column)
    add_index("ix_mmcat_pages_source_id", "mmcat_pages", ["source_id"])

    add_column("mmcat_categories", sa.Column("description", sa.Text()))
    add_column("mmcat_categories", sa.Column("cover_url", sa.Text()))
    add_column("mmcat_tags", sa.Column("description", sa.Text()))

    for column in (
        sa.Column("source_platform", sa.String(length=30)),
        sa.Column("source_id", sa.String(length=240)),
        sa.Column("source_url", sa.Text()),
    ):
        add_column("mmcat_comments", column)
    add_index("ix_mmcat_comments_source_id", "mmcat_comments", ["source_id"])

    for table in ("mmcat_friend_links", "mmcat_navigation_items"):
        add_column(table, sa.Column("source_platform", sa.String(length=30)))
        add_column(table, sa.Column("source_id", sa.String(length=240)))
        add_index(f"ix_{table}_source_id", table, ["source_id"])

    add_column("mmcat_media", sa.Column("source_id", sa.String(length=240)))
    add_index("ix_mmcat_media_source_id", "mmcat_media", ["source_id"])
    add_column("mmcat_galleries", sa.Column("source_platform", sa.String(length=30)))
    add_column("mmcat_galleries", sa.Column("source_id", sa.String(length=240)))
    add_index("ix_mmcat_galleries_source_id", "mmcat_galleries", ["source_id"])


def downgrade() -> None:
    for table in (
        "mmcat_galleries",
        "mmcat_media",
        "mmcat_navigation_items",
        "mmcat_friend_links",
        "mmcat_comments",
        "mmcat_pages",
    ):
        index = {
            "mmcat_galleries": "ix_mmcat_galleries_source_id",
            "mmcat_media": "ix_mmcat_media_source_id",
            "mmcat_navigation_items": "ix_mmcat_navigation_items_source_id",
            "mmcat_friend_links": "ix_mmcat_friend_links_source_id",
            "mmcat_comments": "ix_mmcat_comments_source_id",
            "mmcat_pages": "ix_mmcat_pages_source_id",
        }[table]
        op.drop_index(index, table_name=table)
    op.drop_column("mmcat_galleries", "source_id")
    op.drop_column("mmcat_galleries", "source_platform")
    op.drop_column("mmcat_media", "source_id")
    for table in ("mmcat_navigation_items", "mmcat_friend_links"):
        op.drop_column(table, "source_id")
        op.drop_column(table, "source_platform")
    for column in ("source_url", "source_id", "source_platform"):
        op.drop_column("mmcat_comments", column)
        op.drop_column("mmcat_pages", column)
    op.drop_column("mmcat_tags", "description")
    op.drop_column("mmcat_categories", "cover_url")
    op.drop_column("mmcat_categories", "description")
