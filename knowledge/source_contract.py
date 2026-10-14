from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any, Final

NEO_SOURCE_DOCUMENT_SCHEMA_ID: Final[str] = "neo.knowledge.source_document.v1"
NEO_SOURCE_BLOCK_SCHEMA_ID: Final[str] = "neo.knowledge.source_block.v1"
NEO_SOURCE_PARSER_SCHEMA_ID: Final[str] = "neo.knowledge.source_parser.v1"
NEO_SOURCE_REVISION_SCHEMA_ID: Final[str] = "neo.knowledge.source_revision.v1"

VALID_SOURCE_STATES: Final[frozenset[str]] = frozenset({"extracted", "partial", "stored_only", "empty", "error"})


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_value(value: Any) -> str:
    if isinstance(value, bytes):
        return _sha256_bytes(value)
    if isinstance(value, str):
        return _sha256_bytes(value.encode("utf-8", errors="ignore"))
    return _sha256_bytes(_canonical_json(value).encode("utf-8", errors="ignore"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _stable_block_id(*, kind: str, text: str, heading_path: list[str], locator: dict[str, Any], index: int) -> str:
    payload = {"kind": kind, "text": text, "heading_path": heading_path, "locator": locator, "index": index}
    return f"srcblk_{_sha256_value(payload)[:20]}"


@dataclass(slots=True)
class NeoSourceBlock:
    block_id: str
    kind: str
    text: str
    heading_path: list[str] = field(default_factory=list)
    locator: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_id": NEO_SOURCE_BLOCK_SCHEMA_ID,
            **asdict(self),
            "char_count": len(self.text),
        }


@dataclass(slots=True)
class NeoSourceParser:
    parser_id: str
    version: str = ""
    mode: str = "lightweight"
    attempts: list[dict[str, Any]] = field(default_factory=list)
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"schema_id": NEO_SOURCE_PARSER_SCHEMA_ID, **asdict(self)}


@dataclass(slots=True)
class NeoSourceRevision:
    content_sha256: str
    revision_id: str
    size_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"schema_id": NEO_SOURCE_REVISION_SCHEMA_ID, **asdict(self)}


@dataclass(slots=True)
class NeoSourceDocument:
    status: str
    source_id: str
    source_name: str
    source_path: str
    suffix: str
    media_type: str
    parser: NeoSourceParser
    revision: NeoSourceRevision
    blocks: list[NeoSourceBlock] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        blocks = [block.as_dict() for block in self.blocks]
        chars = sum(int(block.get("char_count") or 0) for block in blocks)
        payload = {
            "schema_id": NEO_SOURCE_DOCUMENT_SCHEMA_ID,
            "status": self.status,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_path": self.source_path,
            # Compatibility aliases retained for the existing NKB-7 fragment/manifest path.
            "path": self.source_path,
            "suffix": self.suffix,
            "media_type": self.media_type,
            "method": self.parser.parser_id,
            "parser": self.parser.as_dict(),
            "revision": self.revision.as_dict(),
            "content_hash": self.revision.content_sha256,
            "revision_id": self.revision.revision_id,
            "block_count": len(blocks),
            "chars": chars,
            "truncated": bool(self.truncated),
            "blocks": blocks,
            "capabilities": dict(self.capabilities),
            "metadata": dict(self.metadata),
        }
        # Existing consumers historically read a handful of extractor-specific
        # counters at the top level. Mirror scalar metadata there during ACI-2
        # without making those fields part of the canonical source contract.
        for key, value in self.metadata.items():
            if key not in payload and isinstance(value, (str, int, float, bool, type(None))):
                payload[key] = value
        return payload


def normalize_source_blocks(blocks: list[dict[str, Any]] | None) -> list[NeoSourceBlock]:
    normalized: list[NeoSourceBlock] = []
    for index, raw in enumerate(blocks or [], start=1):
        if not isinstance(raw, dict):
            continue
        text = _clean_text(raw.get("text"))
        if not text:
            continue
        kind = str(raw.get("kind") or "paragraph").strip() or "paragraph"
        heading_path = [_clean_text(item) for item in (raw.get("heading_path") or []) if _clean_text(item)]
        locator = dict(raw.get("locator") or {})
        block_id = str(raw.get("block_id") or "").strip() or _stable_block_id(
            kind=kind,
            text=text,
            heading_path=heading_path,
            locator=locator,
            index=index,
        )
        confidence = raw.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except Exception:
            confidence = None
        metadata = dict(raw.get("metadata") or {})
        normalized.append(
            NeoSourceBlock(
                block_id=block_id,
                kind=kind,
                text=text,
                heading_path=heading_path,
                locator=locator,
                confidence=confidence,
                metadata=metadata,
            )
        )
    return normalized


def build_source_document(
    *,
    path: Path | str | None,
    blocks: list[dict[str, Any]] | None,
    parser_id: str,
    parser_version: str = "",
    parser_mode: str = "lightweight",
    status: str = "",
    source_name: str = "",
    suffix: str = "",
    media_type: str = "",
    content_hash: str = "",
    content_bytes: bytes | None = None,
    attempts: list[dict[str, Any]] | None = None,
    fallback_used: bool = False,
    warnings: list[str] | None = None,
    capabilities: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    source_path = ""
    resolved_path: Path | None = None
    if path not in (None, ""):
        try:
            resolved_path = Path(path).resolve()
            source_path = str(resolved_path)
        except Exception:
            source_path = str(path)
    resolved_name = str(source_name or (resolved_path.name if resolved_path else "source"))
    resolved_suffix = str(suffix or (resolved_path.suffix if resolved_path else "")).lower()
    resolved_media_type = str(media_type or mimetypes.guess_type(resolved_name)[0] or "application/octet-stream")

    size_bytes = 0
    if content_bytes is not None:
        size_bytes = len(content_bytes)
        resolved_hash = str(content_hash or _sha256_bytes(content_bytes))
    elif resolved_path is not None and resolved_path.exists() and resolved_path.is_file():
        try:
            size_bytes = int(resolved_path.stat().st_size)
            resolved_hash = str(content_hash or file_sha256(resolved_path))
        except Exception:
            resolved_hash = str(content_hash or "")
    else:
        resolved_hash = str(content_hash or _sha256_value(blocks or []))
    if not resolved_hash:
        resolved_hash = _sha256_value({"source": resolved_name, "blocks": blocks or []})

    normalized_blocks = normalize_source_blocks(blocks)
    resolved_status = str(status or ("extracted" if normalized_blocks else "stored_only"))
    if resolved_status not in VALID_SOURCE_STATES:
        resolved_status = "partial" if normalized_blocks else "error"
    source_id = f"source_{_sha256_value({'path': source_path, 'name': resolved_name, 'hash': resolved_hash})[:24]}"
    revision = NeoSourceRevision(content_sha256=resolved_hash, revision_id=f"rev_{resolved_hash[:24]}", size_bytes=size_bytes)
    parser = NeoSourceParser(
        parser_id=str(parser_id or "unknown"),
        version=str(parser_version or ""),
        mode=str(parser_mode or "lightweight"),
        attempts=list(attempts or []),
        fallback_used=bool(fallback_used),
        warnings=[str(item) for item in (warnings or []) if str(item or "").strip()],
    )
    document = NeoSourceDocument(
        status=resolved_status,
        source_id=source_id,
        source_name=resolved_name,
        source_path=source_path,
        suffix=resolved_suffix,
        media_type=resolved_media_type,
        parser=parser,
        revision=revision,
        blocks=normalized_blocks,
        capabilities=dict(capabilities or {}),
        metadata=dict(metadata or {}),
        truncated=bool(truncated),
    )
    return document.as_dict()


def is_neo_source_document(value: Any) -> bool:
    return isinstance(value, dict) and str(value.get("schema_id") or "") == NEO_SOURCE_DOCUMENT_SCHEMA_ID
