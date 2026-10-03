from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.common import clean_web_url, escape_like
from app.schemas.content import StudioToolInput
from app.services.html import sanitize_html


def test_validation_errors_return_422_without_echoing_input() -> None:
    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    response = client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "hunter2-secret"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "hunter2-secret" not in response.text


@pytest.mark.parametrize("value", ["javascript:alert(1)", "//evil.example/x", "data:text/html,x"])
def test_rejects_executable_or_protocol_relative_urls(value: str) -> None:
    with pytest.raises(ValueError):
        clean_web_url(value)
    with pytest.raises(ValidationError):
        StudioToolInput(name="tool", slug="tool", url=value)


def test_accepts_site_paths_and_blank_urls() -> None:
    assert clean_web_url("/upload/cover.png") == "/upload/cover.png"
    assert clean_web_url("  ") is None


def test_like_wildcards_are_escaped() -> None:
    assert escape_like("50%_off") == "50\\%\\_off"


def test_rich_text_is_sanitized_before_storage() -> None:
    cleaned = sanitize_html(
        '<p onclick="x()">hi<script>alert(1)</script>'
        '<a href="javascript:alert(1)">a</a><img src="/a.png" onerror="x()"></p>'
    )
    assert "script" not in cleaned
    assert "onclick" not in cleaned and "onerror" not in cleaned
    assert "javascript:" not in cleaned
    assert '<img src="/a.png">' in cleaned
