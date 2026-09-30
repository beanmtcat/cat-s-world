"""Add named menus and typed navigation targets.

Revision ID: 0004_nav_menus_targets
Revises: 0003_visit_event_ip_address
Create Date: 2026-09-27
"""

from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_nav_menus_targets"
down_revision = "0003_visit_event_ip_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "mmcat_navigation_menus" not in inspector.get_table_names():
        op.create_table(
            "mmcat_navigation_menus",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("locale", sa.String(length=10), nullable=False, server_default="zh-CN"),
            sa.Column("menu_key", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("location", sa.String(length=20), nullable=False, server_default="header"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ctime", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("mtime", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("isvalid", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.UniqueConstraint("locale", "menu_key"),
        )
        op.create_index("ix_mmcat_navigation_menus_menu_key", "mmcat_navigation_menus", ["menu_key"])

    columns = {item["name"] for item in inspector.get_columns("mmcat_navigation_items")}
    if "menu_id" not in columns:
        op.add_column("mmcat_navigation_items", sa.Column("menu_id", postgresql.UUID(as_uuid=True)))
        op.create_index("ix_mmcat_navigation_items_menu_id", "mmcat_navigation_items", ["menu_id"])
    if "target_type" not in columns:
        op.add_column(
            "mmcat_navigation_items",
            sa.Column("target_type", sa.String(length=20), nullable=False, server_default="custom"),
        )
    if "target_ids" not in columns:
        op.add_column(
            "mmcat_navigation_items",
            sa.Column("target_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        )

    rows = bind.execute(
        sa.text(
            "SELECT DISTINCT locale, location FROM mmcat_navigation_items "
            "WHERE isvalid = true ORDER BY locale, location"
        )
    ).mappings()
    for index, row in enumerate(rows):
        locale, location = row["locale"], row["location"]
        key = "primary" if location == "header" else "footer"
        name = "主菜单" if location == "header" else "页脚菜单"
        menu_id = uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO mmcat_navigation_menus "
                "(id, locale, menu_key, name, location, sort_order, ctime, mtime, isvalid) "
                "VALUES (:id, :locale, :menu_key, :name, :location, :sort_order, :now, :now, true) "
                "ON CONFLICT (locale, menu_key) DO NOTHING"
            ),
            {
                "id": menu_id,
                "locale": locale,
                "menu_key": key,
                "name": name,
                "location": location,
                "sort_order": index,
                "now": datetime.now(),
            },
        )
        actual_id = bind.execute(
            sa.text(
                "SELECT id FROM mmcat_navigation_menus WHERE locale = :locale AND menu_key = :menu_key"
            ),
            {"locale": locale, "menu_key": key},
        ).scalar_one()
        bind.execute(
            sa.text(
                "UPDATE mmcat_navigation_items SET menu_id = :menu_id "
                "WHERE locale = :locale AND location = :location AND isvalid = true"
            ),
            {"menu_id": actual_id, "locale": locale, "location": location},
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("mmcat_navigation_items")}
    if "menu_id" in columns:
        op.drop_index("ix_mmcat_navigation_items_menu_id", table_name="mmcat_navigation_items")
        op.drop_column("mmcat_navigation_items", "menu_id")
    if "target_ids" in columns:
        op.drop_column("mmcat_navigation_items", "target_ids")
    if "target_type" in columns:
        op.drop_column("mmcat_navigation_items", "target_type")
    if "mmcat_navigation_menus" in inspector.get_table_names():
        op.drop_index("ix_mmcat_navigation_menus_menu_key", table_name="mmcat_navigation_menus")
        op.drop_table("mmcat_navigation_menus")
