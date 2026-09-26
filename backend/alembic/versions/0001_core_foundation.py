"""Create the first executable core schema.

Revision ID: 0001_core_foundation
Revises:
Create Date: 2026-09-26
"""

import app.models  # noqa: F401  Register models before metadata creation.
from alembic import op
from app.db.base import Base

revision = "0001_core_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Initial development baseline. Subsequent revisions must use explicit Alembic operations.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
