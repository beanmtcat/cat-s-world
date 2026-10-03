"""Add indexes for view totals, daily uniqueness checks and the audit log list.

Revision ID: 0013_query_indexes
Revises: 0012_site_settings_sections
Create Date: 2026-10-02
"""

import sqlalchemy as sa

from alembic import op

revision = "0013_query_indexes"
down_revision = "0012_site_settings_sections"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_mmcat_visit_daily_path_locale", "mmcat_visit_daily", ["path", "locale"]),
    ("ix_mmcat_visit_events_path_occurred_at", "mmcat_visit_events", ["path", "occurred_at"]),
    ("ix_mmcat_audit_logs_ctime", "mmcat_audit_logs", ["ctime"]),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, columns in INDEXES:
        # 0001 builds the schema from current metadata, so a fresh database
        # already has these indexes.
        if name not in {item["name"] for item in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, _ in INDEXES:
        if name in {item["name"] for item in inspector.get_indexes(table)}:
            op.drop_index(name, table_name=table)
