"""Store source IP addresses for first-party visit events.

Revision ID: 0003_visit_event_ip_address
Revises: 0002_halo_import_identity
Create Date: 2026-09-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_visit_event_ip_address"
down_revision = "0002_halo_import_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("mmcat_visit_events")}
    if "ip_address" not in columns:
        op.add_column("mmcat_visit_events", sa.Column("ip_address", sa.String(length=64)))
        op.execute(
            "COMMENT ON COLUMN mmcat_visit_events.ip_address IS "
            "'访问客户端 IP；仅用于站点自有访问统计与安全分析。'"
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "ip_address" in {item["name"] for item in inspector.get_columns("mmcat_visit_events")}:
        op.drop_column("mmcat_visit_events", "ip_address")
