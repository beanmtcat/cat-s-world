from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.core.security import hash_password
from app.services import auth


class FakeSession:
    """Minimal AsyncSession stand-in: returns one user and records writes."""

    def __init__(self, user: SimpleNamespace) -> None:
        self.user = user
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> SimpleNamespace:
        return self.user

    def add(self, item: object) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        pass

    async def flush(self) -> None:
        pass


def _user(**overrides: object) -> SimpleNamespace:
    values = {
        "id": "00000000-0000-0000-0000-000000000001",
        "password_hash": hash_password("correct horse battery"),
        "status": "active",
        "failed_login_count": 0,
        "locked_until": None,
        "last_login_at": None,
    }
    return SimpleNamespace(**{**values, **overrides})


async def _attempt(user: SimpleNamespace, password: str) -> None:
    await auth.login(FakeSession(user), "member@example.com", password, None, None)


async def test_lock_is_applied_once_and_not_extended_by_later_attempts() -> None:
    user = _user()
    for _ in range(auth.LOGIN_FAILURE_LIMIT):
        with pytest.raises(AppError):
            await _attempt(user, "wrong")
    locked_until = user.locked_until
    assert locked_until is not None
    with pytest.raises(AppError):
        await _attempt(user, "wrong")
    assert user.locked_until == locked_until
    assert user.failed_login_count == auth.LOGIN_FAILURE_LIMIT
    # Even the right password is refused while the lock is active.
    with pytest.raises(AppError):
        await _attempt(user, "correct horse battery")


async def test_expired_lock_starts_a_fresh_window() -> None:
    user = _user(
        failed_login_count=auth.LOGIN_FAILURE_LIMIT,
        locked_until=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(AppError):
        await _attempt(user, "wrong")
    assert user.failed_login_count == 1
    assert user.locked_until is None
    await _attempt(user, "correct horse battery")
    assert user.failed_login_count == 0
