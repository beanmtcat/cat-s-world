from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(item.capitalize() for item in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


def clean_web_url(value: str | None, *, allow_site_path: bool = True) -> str | None:
    """Accept only http(s) URLs or site-rooted paths; blank values become None.

    These values end up in `href`/`src` attributes, so executable schemes such
    as `javascript:` and protocol-relative `//host` forms are rejected.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if allow_site_path and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("链接必须是完整的 http 或 https URL。")
    return value


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so input matches literally; pair with a backslash escape char."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
