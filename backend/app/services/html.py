"""Server-side HTML sanitizing for rich-text content."""

from __future__ import annotations

import nh3

# Mirrors what the public renderer displays.  Sanitizing at write time means
# stored content is safe even if a client-side filter is bypassed or removed.
_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "strong", "em", "b", "i", "s", "u", "del", "code", "pre", "blockquote",
    "a", "img", "table", "thead", "tbody", "tr", "th", "td", "colgroup", "col",
}
_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "code": {"class"},
    "ol": {"start"},
}


def sanitize_html(content: str) -> str:
    return nh3.clean(
        content,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )
