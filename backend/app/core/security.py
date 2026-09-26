from __future__ import annotations

import hashlib
import hmac

from pwdlib import PasswordHash

from app.core.config import get_settings

_password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Use pwdlib's Argon2id default, never reversible encryption."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hasher.verify(password, password_hash)


def hash_identifier(value: str) -> str:
    secret = get_settings().hash_ip_secret.get_secret_value().encode()
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
