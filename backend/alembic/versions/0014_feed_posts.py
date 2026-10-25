"""Add short-form feed posts.

Revision ID: 0014_feed_posts
Revises: 0013_query_indexes
Create Date: 2026-10-03
"""

import sqlalchemy as sa

from alembic import op

revision = "0014_feed_posts"
down_revision = "0013_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    def create_table(name: str, *columns) -> None:
        if not inspector.has_table(name):
            op.create_table(name, *columns)

    def create_index(name: str, table: str, columns: list[str]) -> None:
        if name not in {item["name"] for item in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)

    create_table(
        "mmcat_feed_posts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ctime", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "mtime", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("isvalid", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("unit_id", sa.String(160), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("kicker", sa.String(200)),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), server_default="api", nullable=False),
        sa.UniqueConstraint("unit_id", name="uq_mmcat_feed_posts_unit_id"),
    )
    create_index("ix_mmcat_feed_posts_category", "mmcat_feed_posts", ["category"])
    create_index("ix_mmcat_feed_posts_published_at", "mmcat_feed_posts", ["published_at"])
    create_index(
        "ix_mmcat_feed_posts_public_order", "mmcat_feed_posts", ["isvalid", "published_at"]
    )
    create_table(
        "mmcat_feed_likes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ctime", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "mtime", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("isvalid", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("post_id", "user_id", name="uq_mmcat_feed_likes_post_id"),
    )
    create_index("ix_mmcat_feed_likes_post_id", "mmcat_feed_likes", ["post_id"])
    create_index("ix_mmcat_feed_likes_user_id", "mmcat_feed_likes", ["user_id"])
    create_table(
        "mmcat_feed_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ctime", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "mtime", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("isvalid", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("author_name", sa.String(120), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
    )
    create_index("ix_mmcat_feed_comments_post_id", "mmcat_feed_comments", ["post_id"])
    create_index("ix_mmcat_feed_comments_user_id", "mmcat_feed_comments", ["user_id"])
    create_index("ix_mmcat_feed_comments_post_status", "mmcat_feed_comments", ["post_id", "status"])


def downgrade() -> None:
    op.drop_table("mmcat_feed_comments")
    op.drop_table("mmcat_feed_likes")
    op.drop_table("mmcat_feed_posts")
