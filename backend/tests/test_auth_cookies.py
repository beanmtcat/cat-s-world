from __future__ import annotations

from fastapi import Response

from app.api.v1.auth import _set_session_cookies
from app.core.config import get_settings


def test_development_session_cookie_is_not_host_prefixed() -> None:
    """`__Host-` cookies require HTTPS and browsers reject them over local HTTP."""
    get_settings.cache_clear()
    response = Response()
    _set_session_cookies(response, "session-token", "csrf-token")

    cookies = response.headers.getlist("set-cookie")
    assert any(cookie.startswith("mmcat-session=session-token") for cookie in cookies)
    assert not any(cookie.startswith("__Host-") for cookie in cookies)
    assert not any("; Secure" in cookie for cookie in cookies)
