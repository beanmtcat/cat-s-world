from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import yaml

from app.core.errors import AppError

_FRONT_MATTER = re.compile(r"\A---\s*\n(?P<metadata>.*?)\n---\s*\n(?P<content>.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class MarkdownDocument:
    metadata: dict[str, object]
    content: str
    content_hash: str


def parse_markdown(raw: str) -> MarkdownDocument:
    """Parse a bounded Markdown document without executing front-matter tags."""
    if not raw or len(raw.encode("utf-8")) > 2 * 1024 * 1024:
        raise AppError("MARKDOWN_INVALID", "Markdown 内容为空或超过 2 MiB。", 422)
    match = _FRONT_MATTER.match(raw.replace("\r\n", "\n"))
    if match is None:
        metadata: dict[str, object] = {}
        content = raw.strip()
    else:
        parsed = yaml.safe_load(match.group("metadata")) or {}
        if not isinstance(parsed, dict):
            raise AppError("MARKDOWN_FRONT_MATTER_INVALID", "Front Matter 必须是对象。", 422)
        metadata = parsed
        content = match.group("content").strip()
    if not content:
        raise AppError("MARKDOWN_CONTENT_REQUIRED", "正文不能为空。", 422)
    return MarkdownDocument(
        metadata=metadata,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def dump_markdown(metadata: dict[str, object], content: str) -> str:
    """Export stable UTF-8 Markdown; the database remains the source of truth."""
    safe_metadata = {key: value for key, value in metadata.items() if value is not None}
    front_matter = yaml.safe_dump(safe_metadata, allow_unicode=True, sort_keys=True).strip()
    return f"---\n{front_matter}\n---\n\n{content.rstrip()}\n"
