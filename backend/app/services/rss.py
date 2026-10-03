"""Generate a small RSS 2.0 feed from public article metadata."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET

from app.models.core import Post

ATOM_NS = "http://www.w3.org/2005/Atom"
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _text(value: str | None) -> str:
    return _INVALID_XML.sub("", html.unescape(value or "")).strip()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _date(value: datetime) -> str:
    return format_datetime(_as_utc(value), usegmt=True)


def build_rss_feed(
    *,
    site_title: str,
    site_description: str | None,
    site_url: str,
    feed_url: str,
    posts: list[tuple[Post, str | None]],
) -> bytes:
    """Serialize already-filtered public posts; XML escaping is handled by ElementTree."""
    base_url = site_url.rstrip("/")
    ET.register_namespace("atom", ATOM_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = _text(site_title)
    ET.SubElement(channel, "link").text = base_url
    ET.SubElement(channel, "description").text = _text(site_description) or "猫子的世界最新文章"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {"href": feed_url, "rel": "self", "type": "application/rss+xml"})
    ET.SubElement(channel, "ttl").text = "5"
    if posts:
        newest = max(_as_utc(getattr(post, "mtime", None) or post.published_at or post.ctime) for post, _ in posts)
        ET.SubElement(channel, "lastBuildDate").text = _date(newest)

    for post, summary in posts:
        item = ET.SubElement(channel, "item")
        url = f"{base_url}/archives/{quote(post.slug, safe='-._~')}"
        ET.SubElement(item, "title").text = _text(post.title)
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = url
        # Summaries are plain text; never put an imported HTML body in the feed.
        description = re.sub(r"<[^>]*>", " ", summary or "")
        ET.SubElement(item, "description").text = _text(description)
        ET.SubElement(item, "pubDate").text = _date(post.published_at or post.ctime)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
