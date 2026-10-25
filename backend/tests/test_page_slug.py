from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.content import StudioPageInput


@pytest.mark.parametrize("slug", ["about", "disclaimer.html", "old-page-2.html"])
def test_page_input_accepts_current_and_legacy_html_slugs(slug: str) -> None:
    page = StudioPageInput(title="关于", slug=slug, content="正文")
    assert page.slug == slug


def test_legacy_published_html_page_can_be_saved_without_changing_its_address() -> None:
    page = StudioPageInput.model_validate(
        {
            "title": "免责声明",
            "slug": "disclaimer.html",
            "content": "<p>更新后的正文</p>",
            "contentFormat": "html",
            "status": "published",
            "visibility": "public",
            "locale": "zh-CN",
        }
    )
    assert page.slug == "disclaimer.html"
    assert page.content_format == "html"


@pytest.mark.parametrize("slug", ["about.htm", "about.html.html", "a/b.html", "../about.html", "a?b"])
def test_page_input_rejects_other_extensions_and_unsafe_slugs(slug: str) -> None:
    with pytest.raises(ValidationError):
        StudioPageInput(title="关于", slug=slug, content="正文")
