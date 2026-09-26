from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.services.redirects import normalize_legacy_path


def test_normalizes_legacy_query_order() -> None:
    assert normalize_legacy_path("/archives/example/?b=2&a=1") == "/archives/example?a=1&b=2"


@pytest.mark.parametrize("value", ["https://evil.example/path", "/a/../secret", "/path#fragment"])
def test_rejects_unsafe_legacy_path(value: str) -> None:
    with pytest.raises(AppError):
        normalize_legacy_path(value)
