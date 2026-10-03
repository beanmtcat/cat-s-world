from datetime import UTC, datetime
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from app.services.rss import build_rss_feed


def test_rss_feed_contains_valid_xml_and_canonical_article_links() -> None:
    published = datetime(2026, 10, 3, 8, 30, tzinfo=UTC)
    post = SimpleNamespace(
        slug="hello-world.html",
        title="猫 & 技术 <随笔>",
        published_at=published,
        ctime=published,
    )

    xml = build_rss_feed(
        site_title="猫子的世界",
        site_description="记录技术 & 生活",
        site_url="https://www.hzcat.net/",
        feed_url="https://api.hzcat.net/api/v1/rss.xml",
        posts=[(post, "介绍 <b>RSS</b> & 订阅\x01方法")],
    )

    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert root.attrib["version"] == "2.0"
    assert channel.findtext("description") == "记录技术 & 生活"
    assert channel.findtext("item/title") == "猫 & 技术 <随笔>"
    assert channel.findtext("item/link") == "https://www.hzcat.net/archives/hello-world.html"
    assert channel.findtext("item/description") == "介绍  RSS  & 订阅方法"
    assert channel.findtext("item/pubDate") == "Sat, 03 Oct 2026 08:30:00 GMT"
    assert channel.find("{http://www.w3.org/2005/Atom}link").attrib["rel"] == "self"
