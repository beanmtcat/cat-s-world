"""Store card thumbnail URL mappings without rewriting legacy source URLs."""

import sqlalchemy as sa

from alembic import op

revision = "0015_media_thumbnails"
down_revision = "0014_feed_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("mmcat_media_thumbnails"):
        return
    op.create_table(
        "mmcat_media_thumbnails",
        sa.Column("source_url", sa.Text(), primary_key=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mmcat_media_thumbnails")
