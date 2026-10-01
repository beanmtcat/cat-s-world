"""Keep imported legacy comment authors separate from local user accounts.

Revision ID: 0008_legacy_comment_owners
Revises: 0007_gallery_collection_kind
Create Date: 2026-10-01
"""

from alembic import op

revision = "0008_legacy_comment_owners"
down_revision = "0007_gallery_collection_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("mmcat_comments", "user_id", nullable=True)


def downgrade() -> None:
    # A downgrade is unsafe once historical visitor rows have no local user.
    # Keep the schema usable rather than fabricating an administrator identity.
    pass
