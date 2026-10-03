"""Review and apply the 2026-10-03 post summary/taxonomy curation.

Run from backend/ with ``python -m app.cli.rewrite_post_metadata inventory|preview|apply``.
The inventory pins article IDs and content hashes so ordinal-based proposals cannot
silently attach to a different article after the database changes.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.core import Category, Post, Tag
from app.models.supplemental import PostCategory, PostTag

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "content" / "post-metadata-inventory-20261003.json"
PROPOSALS = ROOT / "content" / "post-metadata-proposals-20261003.tsv"


def ordered_posts():
    return select(Post).order_by(Post.published_at.desc().nulls_last(), Post.title)


def read_proposals() -> dict[int, dict]:
    with PROPOSALS.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    result = {}
    for fields in rows:
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) != 4:
            raise ValueError(f"Expected 4 tab-separated fields, got {len(fields)}: {fields!r}")
        ordinal = int(fields[0])
        if ordinal in result:
            raise ValueError(f"Duplicate article number: {ordinal}")
        summary, categories, tags = fields[1:]
        if summary != "SKIP" and not (20 <= len(summary) <= 300):
            raise ValueError(f"Summary length is invalid for article {ordinal}: {len(summary)}")
        result[ordinal] = {
            "summary": summary,
            "categories": [x for x in categories.split(",") if x],
            "tags": [x for x in tags.split(",") if x],
        }
    return result


async def run(mode: str) -> None:
    async with SessionLocal() as session:
        posts = (await session.scalars(ordered_posts())).all()
        if mode == "inventory":
            rows = [
                {"number": n, "id": str(post.id), "title": post.title,
                 "content_hash": post.content_hash, "status": post.status,
                 "content_length": len(post.content or "")}
                for n, post in enumerate(posts, 1)
            ]
            with INVENTORY.open("x", encoding="utf-8") as stream:
                json.dump(rows, stream, ensure_ascii=False, indent=2)
            print(f"Pinned {len(rows)} articles to {INVENTORY}")
            return

        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        proposals = read_proposals()
        if len(posts) != len(inventory) or set(proposals) != set(range(1, len(posts) + 1)):
            raise ValueError("Article count or proposal numbers do not match the inventory")

        categories = (await session.scalars(select(Category).where(Category.isvalid.is_(True)))).all()
        tags = (await session.scalars(select(Tag).where(Tag.isvalid.is_(True)))).all()
        category_ids = {(item.locale, item.slug): item.id for item in categories}
        tag_ids = {(item.locale, item.slug): item.id for item in tags}

        current_categories = (await session.scalars(select(PostCategory))).all()
        current_tags = (await session.scalars(select(PostTag))).all()
        category_links = {}
        tag_links = {}
        for link in current_categories:
            if link.isvalid:
                category_links.setdefault(link.post_id, set()).add(link.category_id)
        for link in current_tags:
            if link.isvalid:
                tag_links.setdefault(link.post_id, set()).add(link.tag_id)

        planned = []
        for number, (post, pinned) in enumerate(zip(posts, inventory, strict=True), 1):
            if (number != pinned["number"] or str(post.id) != pinned["id"]
                    or post.title != pinned["title"] or post.content_hash != pinned["content_hash"]):
                raise ValueError(f"Article {number} changed since inventory creation")
            proposal = proposals[number]
            if proposal["summary"] == "SKIP":
                if re.sub(r"<[^>]*>", "", post.content or "").strip():
                    raise ValueError(f"Article {number} has content; do not skip it")
                continue
            try:
                desired_categories = {category_ids[(post.locale, slug)] for slug in proposal["categories"]}
                desired_tags = {tag_ids[(post.locale, slug)] for slug in proposal["tags"]}
            except KeyError as exc:
                raise ValueError(f"Unknown taxonomy or locale for article {number}: {exc}") from exc
            if not desired_categories:
                raise ValueError(f"Article {number} needs a category")
            planned.append((post, proposal["summary"], desired_categories, desired_tags))

        summary_changes = sum(post.summary != summary for post, summary, _, _ in planned)
        category_changes = sum(category_links.get(post.id, set()) != desired for post, _, desired, _ in planned)
        tag_changes = sum(tag_links.get(post.id, set()) != desired for post, _, _, desired in planned)
        distribution = Counter(
            item.slug for _, _, desired, _ in planned for item in categories if item.id in desired
        )
        print(f"Articles: {len(posts)}; curated: {len(planned)}; skipped empty drafts: {len(posts) - len(planned)}")
        print(f"Changes: summaries {summary_changes}, categories {category_changes}, tags {tag_changes}")
        print(f"Category distribution: {dict(sorted(distribution.items()))}")
        for post, summary, desired, labels in planned[:5]:
            print(f"  {post.title}: {summary} ({len(desired)} categories, {len(labels)} tags)")
        if mode == "preview":
            return

        # Lock before saving the exact pre-change state. The backup is created
        # before the first write and is retained even if the transaction fails.
        await session.execute(select(Post.id).where(Post.id.in_([post.id for post, *_ in planned])).with_for_update())
        backup = [
            {"id": str(post.id), "title": post.title, "summary": post.summary,
             "category_ids": sorted(str(x) for x in category_links.get(post.id, set())),
             "tag_ids": sorted(str(x) for x in tag_links.get(post.id, set()))}
            for post, *_ in planned
        ]
        backup_path = ROOT / "content" / f"post-metadata-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        with backup_path.open("x", encoding="utf-8") as stream:
            json.dump(backup, stream, ensure_ascii=False, indent=2)
        print(f"Backup: {backup_path}")

        for post, summary, desired_categories, desired_tags in planned:
            post.summary = summary
            await session.execute(delete(PostCategory).where(PostCategory.post_id == post.id))
            await session.execute(delete(PostTag).where(PostTag.post_id == post.id))
            session.add_all(PostCategory(post_id=post.id, category_id=category_id) for category_id in desired_categories)
            session.add_all(PostTag(post_id=post.id, tag_id=tag_id) for tag_id in desired_tags)
        await session.commit()
        print("Database transaction committed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inventory", "preview", "apply"))
    args = parser.parse_args()
    asyncio.run(run(args.mode))


if __name__ == "__main__":
    main()
