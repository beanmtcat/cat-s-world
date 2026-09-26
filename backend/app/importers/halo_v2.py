from __future__ import annotations

import base64
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from io import BytesIO
from pathlib import PurePosixPath

import yaml

from app.core.errors import AppError
from app.services.markdown import MarkdownDocument, parse_markdown

# Halo exports often include original media.  We stream the archive and only
# parse content metadata, so the overall limits may accommodate real backups
# without loading their media payloads into memory.
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
MAX_METADATA_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 10_000


@dataclass(frozen=True)
class HaloCandidate:
    source_id: str
    source_url: str | None
    kind: str
    slug: str
    title: str
    markdown: MarkdownDocument
    cover_url: str | None = None
    category_source_ids: tuple[str, ...] = field(default_factory=tuple)
    tag_source_ids: tuple[str, ...] = field(default_factory=tuple)
    is_published: bool = False
    published_at: datetime | None = None
    summary: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class HaloWorkdirBundle:
    """All first-party Halo resources that this importer can migrate.

    Attachments remain external records: importing them never downloads or
    uploads bytes, which keeps the migration safe for large Halo workdirs.
    """

    candidates: list[HaloCandidate] = field(default_factory=list)
    taxonomy: dict[str, dict[str, dict[str, object]]] = field(
        default_factory=lambda: {"category": {}, "tag": {}}
    )
    attachments: list[dict[str, object]] = field(default_factory=list)
    comments: list[dict[str, object]] = field(default_factory=list)
    menu_items: list[dict[str, object]] = field(default_factory=list)
    friend_links: list[dict[str, object]] = field(default_factory=list)
    site_settings: list[dict[str, object]] = field(default_factory=list)
    statistics: list[dict[str, object]] = field(default_factory=list)


def _safe_members(archive: zipfile.ZipFile, archive_size: int) -> list[zipfile.ZipInfo]:
    if archive_size > MAX_ARCHIVE_BYTES:
        raise AppError("IMPORT_ARCHIVE_TOO_LARGE", "导入包超过 1 GiB 限制。", 422)
    members = archive.infolist()
    if len(members) > MAX_FILES:
        raise AppError("IMPORT_TOO_MANY_FILES", "导入包文件数超过限制。", 422)
    total = 0
    for item in members:
        path = PurePosixPath(item.filename)
        if path.is_absolute() or ".." in path.parts:
            raise AppError("IMPORT_UNSAFE_ARCHIVE", "导入包包含不安全路径。", 422)
        total += item.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise AppError("IMPORT_ARCHIVE_TOO_LARGE", "解压后的导入包超过限制。", 422)
    return members


def _read_mappings(name: str, payload: bytes) -> list[dict[str, object]]:
    try:
        values = (
            [json.loads(payload)] if name.endswith(".json") else list(yaml.safe_load_all(payload))
        )
    except (json.JSONDecodeError, yaml.YAMLError):
        # Theme/plugin files may use YAML features unrelated to exported
        # content. Content documents are detected by their apiVersion below.
        return []
    return [value for value in values if isinstance(value, dict)]


def _slug(value: str) -> str:
    candidate = value.strip().strip("/")
    if (
        not candidate
        or len(candidate) > 180
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]*", candidate)
    ):
        raise AppError("HALO_SLUG_INVALID", "Halo 内容包含无效 slug。", 422)
    return candidate


def _safe_halo_slug(value: str, source_id: str) -> tuple[str, tuple[str, ...]]:
    """Keep valid legacy slugs; give incompatible ones a stable new address."""
    try:
        return _slug(value), ()
    except AppError:
        return f"halo-{source_id}", (
            f"旧 slug {value!r} 不兼容，已改为 halo-{source_id}；旧地址将 301 跳转。",
        )


def _halo_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _html_to_markdown(raw: str) -> str:
    """Convert the safe subset of Halo's rich-text HTML into portable Markdown.

    Halo v2 stores the editor's `rawType: HTML` snapshots separately from the
    rendered document.  Keeping the generated Markdown here means the new
    system never needs to execute or inject the legacy HTML in the browser.
    """
    value = raw.replace("\r\n", "\n")
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(
        r'<img\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>',
        lambda match: f"![]({match.group(1)})",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda match: f"[{re.sub(r'<[^>]+>', '', match.group(2)).strip()}]({match.group(1)})",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for level in range(6, 0, -1):
        value = re.sub(
            rf"<h{level}\b[^>]*>(.*?)</h{level}>",
            lambda match, heading_level=level: f"\n\n{'#' * heading_level} {match.group(1).strip()}\n\n",
            value,
            flags=re.IGNORECASE | re.DOTALL,
        )
    value = re.sub(
        r"<pre\b[^>]*>\s*<code\b[^>]*>(.*?)</code>\s*</pre>",
        lambda match: f"\n\n```\n{match.group(1).strip()}\n```\n\n",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(r"<li\b[^>]*>(.*?)</li>", r"\n- \1", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"</?(?:ul|ol)\b[^>]*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:p|div|figure|section|blockquote)\b[^>]*>", "\n\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", r"**\1**", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>", r"*\1*", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<code\b[^>]*>(.*?)</code>", r"`\1`", value, flags=re.IGNORECASE | re.DOTALL)
    value = unescape(re.sub(r"<[^>]+>", "", value))
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _snapshot_document(snapshot_id: str, snapshots: dict[str, dict[str, object]]) -> str | None:
    """Replay Halo Snapshot rawPatch records from the root snapshot forward."""
    chain: list[dict[str, object]] = []
    seen: set[str] = set()
    current_id: str | None = snapshot_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        snapshot = snapshots.get(current_id)
        if snapshot is None:
            return None
        spec = snapshot.get("spec")
        if not isinstance(spec, dict):
            return None
        chain.append(spec)
        parent = spec.get("parentSnapshotName")
        current_id = str(parent) if parent else None

    lines: list[str] = []
    for spec in reversed(chain):
        patch = spec.get("rawPatch")
        if not isinstance(patch, str) or not patch:
            continue
        try:
            operations = json.loads(patch)
        except json.JSONDecodeError:
            # The root snapshot stores the complete HTML directly; child
            # snapshots store JSON line patches against that document.
            if lines:
                return None
            lines = patch.splitlines()
            continue
        if not isinstance(operations, list):
            return None
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            source, target = operation.get("source"), operation.get("target")
            if not isinstance(source, dict) or not isinstance(target, dict):
                continue
            position = source.get("position")
            old_lines, new_lines = source.get("lines"), target.get("lines")
            if (
                not isinstance(position, int)
                or position < 0
                or not isinstance(old_lines, list)
                or not isinstance(new_lines, list)
                or not all(isinstance(line, str) for line in old_lines + new_lines)
            ):
                return None
            # `position` is a line offset.  Halo's first patch may begin at
            # the end of an empty document; pad only that harmless gap.
            if position > len(lines):
                lines.extend([""] * (position - len(lines)))
            lines[position : position + len(old_lines)] = new_lines
    document = "\n".join(lines).strip()
    return document or None


def _read_halo_v2_archive(archive: zipfile.ZipFile, archive_size: int) -> list[HaloCandidate]:
    """Read only supported Halo v2 metadata; never extract or evaluate media."""
    members = _safe_members(archive, archive_size)
    candidates: list[HaloCandidate] = []
    for member in members:
        if member.is_dir() or not member.filename.endswith((".yaml", ".yml", ".json")):
            continue
        path = PurePosixPath(member.filename)
        # Finder resource forks are binary files named like `._theme.yaml`;
        # they are not Halo resources and must never reach the YAML parser.
        if "__MACOSX" in path.parts or path.name.startswith("._"):
            continue
        if member.file_size > MAX_METADATA_FILE_BYTES:
            raise AppError(
                "HALO_METADATA_TOO_LARGE", f"{member.filename} 超过元数据大小限制。", 422
            )
        for metadata in _read_mappings(member.filename, archive.read(member)):
            if metadata.get("apiVersion") != "content.halo.run/v1alpha1":
                continue
            kind = str(metadata.get("kind", ""))
            if kind not in {"Post", "SinglePage"}:
                continue
            document_meta = metadata.get("metadata")
            spec = metadata.get("spec")
            status = metadata.get("status") or {}
            if (
                not isinstance(document_meta, dict)
                or not isinstance(spec, dict)
                or not isinstance(status, dict)
            ):
                raise AppError(
                    "HALO_METADATA_INVALID", f"{member.filename} 缺少 Halo v2 字段。", 422
                )
            source_id = str(document_meta.get("name", "")).strip()
            title = str(spec.get("title", "")).strip()
            slug = _slug(str(spec.get("slug", "")))
            raw_content = str(spec.get("content", "")).strip()
            if not source_id or not title:
                raise AppError(
                    "HALO_METADATA_INVALID", f"{member.filename} 缺少 name 或 title。", 422
                )
            document = parse_markdown(raw_content)
            permalink = status.get("permalink")
            candidates.append(
                HaloCandidate(
                    source_id=source_id,
                    source_url=str(permalink) if permalink else None,
                    kind="post" if kind == "Post" else "page",
                    slug=slug,
                    title=title,
                    markdown=document,
                    cover_url=str(spec.get("cover")) if spec.get("cover") else None,
                    category_source_ids=tuple(
                        str(value) for value in spec.get("categories", []) if value
                    ),
                    tag_source_ids=tuple(str(value) for value in spec.get("tags", []) if value),
                    is_published=bool(spec.get("publish", False)),
                    published_at=_halo_datetime(status.get("publishTime")),
                    summary=str(status.get("excerpt") or "") or None,
                )
            )
    if not candidates:
        member_names = {member.filename for member in members}
        if "extensions.data" in member_names and any(
            name.startswith("workdir/") for name in member_names
        ):
            raise AppError(
                "HALO_WORKDIR_BACKUP_UNSUPPORTED",
                "该 ZIP 是 Halo 运行目录备份，只含主题、插件、附件或扩展数据，不含文章数据库导出；请提供 Halo 内容导出包或原数据库 SQL 备份。",
                422,
            )
        raise AppError(
            "HALO_CONTENT_NOT_FOUND", "导入包中没有可识别的 Halo v2 Post 或 SinglePage。", 422
        )
    return candidates


def read_halo_v2_archive(blob: bytes) -> list[HaloCandidate]:
    """Compatibility helper for API uploads and small test fixtures."""
    try:
        with zipfile.ZipFile(BytesIO(blob)) as archive:
            return _read_halo_v2_archive(archive, len(blob))
    except zipfile.BadZipFile as exc:
        raise AppError("HALO_ARCHIVE_INVALID", "Halo 导入包不是有效 ZIP。", 422) from exc


def read_halo_v2_archive_path(path: str) -> list[HaloCandidate]:
    """Read a local export without loading the complete ZIP into memory."""
    try:
        with zipfile.ZipFile(path) as archive:
            return _read_halo_v2_archive(archive, os.path.getsize(path))
    except zipfile.BadZipFile as exc:
        raise AppError("HALO_ARCHIVE_INVALID", "Halo 导入包不是有效 ZIP。", 422) from exc


_EXTENSION_RECORD = re.compile(
    r'\{\s*"name"\s*:\s*"(?P<name>(?:[^"\\]|\\.)*)"\s*,\s*'
    r'"data"\s*:\s*"(?P<data>[A-Za-z0-9+/=]+)"'
    r'(?:\s*,\s*"version"\s*:\s*\d+)?\s*\}'
)


def _extension_records(archive: zipfile.ZipFile):
    """Yield Halo's `{name,data,version}` records without materialising the array.

    Halo's workdir backup stores a giant JSON array whose records have a fixed
    shape and Base64 data.  Regex tokenisation here is deliberately limited to
    that envelope (the payload itself is decoded separately), avoiding the
    substantial overhead of ``json.JSONDecoder.raw_decode`` on a multi-GB file.
    """
    with io.TextIOWrapper(archive.open("extensions.data"), encoding="utf-8") as stream:
        buffer = ""
        while chunk := stream.read(4 * 1024 * 1024):
            buffer += chunk
            last_end = 0
            for match in _EXTENSION_RECORD.finditer(buffer):
                last_end = match.end()
                try:
                    name = json.loads(f'"{match["name"]}"')
                except json.JSONDecodeError:
                    continue
                yield {"name": name, "data": match["data"]}
            if last_end:
                buffer = buffer[last_end:]
            # A single base64 record may span chunks.  There is no useful
            # upper bound for a Halo attachment metadata record, but keeping
            # only the unfinished suffix prevents quadratic buffer growth.
            elif len(buffer) > 32 * 1024 * 1024:
                raise AppError(
                    "HALO_WORKDIR_RECORD_INVALID",
                    "extensions.data 包含无法解析的超大记录。",
                    422,
                )


def _decode_extension_payload(encoded: object) -> dict[str, object] | None:
    if not isinstance(encoded, str):
        return None
    try:
        value = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _resource_record(name: str, payload: dict[str, object]) -> dict[str, object] | None:
    metadata = payload.get("metadata")
    spec = payload.get("spec")
    status = payload.get("status")
    data = payload.get("data")
    if not isinstance(metadata, dict):
        return None
    # Halo ConfigMap values are stored at the top-level `data`, unlike CRD
    # resources such as Post and Setting which use `spec`.
    if not isinstance(spec, dict):
        spec = {"data": data} if isinstance(data, dict) else None
    if spec is None:
        return None
    source_id = str(metadata.get("name") or name.rsplit("/", 1)[-1]).strip()
    if not source_id:
        return None
    return {
        "source_id": source_id,
        "spec": spec,
        "status": status if isinstance(status, dict) else {},
        "metadata": metadata,
    }


def _workdir_candidate(
    kind: str, payload: dict[str, object], snapshots: dict[str, dict[str, object]]
) -> HaloCandidate | None:
    spec, status, metadata = (
        payload.get("spec", {}),
        payload.get("status", {}),
        payload.get("metadata", {}),
    )
    if not isinstance(spec, dict) or not isinstance(status, dict) or not isinstance(metadata, dict):
        return None
    snapshot_id = str(spec.get("releaseSnapshot") or spec.get("headSnapshot") or "")
    raw = _snapshot_document(snapshot_id, snapshots)
    title = str(spec.get("title", "")).strip()
    slug = str(spec.get("slug", "")).strip()
    source_id = str(metadata.get("name", "")).strip()
    if raw is None:
        snapshot_spec = snapshots.get(snapshot_id, {}).get("spec", {})
        raw = snapshot_spec.get("rawPatch") if isinstance(snapshot_spec, dict) else None
    if not title or not slug or not source_id or not isinstance(raw, str) or not raw.strip():
        return None
    resolved_slug, warnings = _safe_halo_slug(slug, source_id)
    excerpt = spec.get("excerpt")
    raw_type = snapshots.get(snapshot_id, {}).get("spec", {}).get("rawType")
    content = _html_to_markdown(raw) if raw_type == "HTML" and not raw.lstrip().startswith("[") else raw
    if not content:
        content = raw
    return HaloCandidate(
        source_id=source_id,
        source_url=str(status.get("permalink")) if status.get("permalink") else None,
        kind=kind,
        slug=resolved_slug,
        title=title,
        markdown=parse_markdown(content),
        cover_url=str(spec.get("cover")) if spec.get("cover") else None,
        category_source_ids=tuple(str(value) for value in spec.get("categories", []) if value),
        tag_source_ids=tuple(str(value) for value in spec.get("tags", []) if value),
        is_published=bool(spec.get("publish", False)),
        published_at=_halo_datetime(spec.get("publishTime")),
        summary=str(excerpt.get("raw") or "") if isinstance(excerpt, dict) else None,
        warnings=warnings,
    )


def read_halo_workdir_bundle_path(path: str) -> HaloWorkdirBundle:
    """Read a Halo workdir in two streaming passes, without touching media bytes."""
    bundle = HaloWorkdirBundle()
    contents: list[tuple[str, dict[str, object]]] = []
    with zipfile.ZipFile(path) as archive:
        if "extensions.data" not in archive.namelist():
            raise AppError("HALO_WORKDIR_BACKUP_INVALID", "备份中缺少 extensions.data。", 422)
        for record in _extension_records(archive):
            name = record.get("name")
            if not isinstance(name, str):
                continue
            is_post = name.startswith("/registry/content.halo.run/posts/")
            is_page = name.startswith("/registry/content.halo.run/singlepages/")
            if is_post or is_page:
                payload = _decode_extension_payload(record.get("data"))
                if payload is None:
                    continue
                contents.append(("post" if is_post else "page", payload))
                continue
            lowered = name.lower()
            resource_type = (
                "category"
                if name.startswith("/registry/content.halo.run/categories/")
                else "tag"
                if name.startswith("/registry/content.halo.run/tags/")
                else "attachment"
                if "/attachments/" in lowered
                else "comment"
                if "/comments/" in lowered
                else "menu"
                if "/menuitems/" in lowered
                else "friend_link"
                if "/friendlinks/" in lowered or "/links/" in lowered
                else "setting"
                if "/settings/" in lowered or "/configmaps/" in lowered
                else "statistic"
                if any(
                    segment in lowered
                    for segment in ("/statistics/", "/counters/", "/visits/", "/metrics/")
                )
                else None
            )
            if resource_type is None:
                continue
            payload = _decode_extension_payload(record.get("data"))
            if payload is None:
                continue
            resource = _resource_record(name, payload)
            if resource is None:
                continue
            if resource_type == "category":
                bundle.taxonomy["category"][str(resource["source_id"])] = resource["spec"]
            elif resource_type == "tag":
                bundle.taxonomy["tag"][str(resource["source_id"])] = resource["spec"]
            elif resource_type == "attachment":
                bundle.attachments.append(resource)
            elif resource_type == "comment":
                bundle.comments.append(resource)
            elif resource_type == "menu":
                bundle.menu_items.append(resource)
            elif resource_type == "friend_link":
                bundle.friend_links.append(resource)
            elif resource_type == "setting":
                bundle.site_settings.append(resource)
            elif resource_type == "statistic":
                bundle.statistics.append(resource)

        snapshots: dict[str, dict[str, object]] = {}
        for record in _extension_records(archive):
            name = record.get("name")
            if not isinstance(name, str) or not name.startswith(
                "/registry/content.halo.run/snapshots/"
            ):
                continue
            snapshot_id = name.rsplit("/", 1)[-1]
            payload = _decode_extension_payload(record.get("data"))
            # A released snapshot is only a diff against its parent.  Keep all
            # snapshot metadata in this pass so `_snapshot_document` can walk
            # the complete chain, while media bytes remain untouched.
            if payload is not None:
                snapshots[snapshot_id] = payload
    bundle.candidates = [
        candidate
        for kind, payload in contents
        if (candidate := _workdir_candidate(kind, payload, snapshots)) is not None
    ]
    if not bundle.candidates:
        raise AppError(
            "HALO_CONTENT_NOT_FOUND", "extensions.data 中未找到可导入的文章及其快照正文。", 422
        )
    return bundle


def read_halo_workdir_archive_path(path: str) -> list[HaloCandidate]:
    """Compatibility wrapper for callers that only need content."""
    return read_halo_workdir_bundle_path(path).candidates


def read_halo_taxonomy_workdir_path(path: str) -> dict[str, dict[str, dict[str, object]]]:
    """Compatibility wrapper for callers that only need Category and Tag."""
    return read_halo_workdir_bundle_path(path).taxonomy
