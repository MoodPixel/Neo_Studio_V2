from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ACI1_MANIFEST_SCHEMA_ID = "neo.aci1.fixture_manifest.v1"
ACI1_RESULT_SCHEMA_ID = "neo.aci1.context_preparation_bakeoff.v1"
ACI1_EXTRACTION_SCHEMA_ID = "neo.aci1.normalized_extraction.v1"
ACI1_ARM_CURRENT = "current_neo_assistant"
ACI1_ARM_DOCLING = "docling_assisted"


def stable_hash(value: Any, length: int = 24) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def clean_text(value: Any) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


def normalized_extraction(
    *,
    arm: str,
    source_path: Path,
    parser: str,
    status: str,
    blocks: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    raw_artifact: str = "",
) -> dict[str, Any]:
    clean_blocks: list[dict[str, Any]] = []
    for index, block in enumerate(blocks or [], start=1):
        if not isinstance(block, dict):
            continue
        text = clean_text(block.get("text"))
        if not text:
            continue
        heading_path = [clean_text(item) for item in (block.get("heading_path") or []) if clean_text(item)]
        locator = dict(block.get("locator") or {})
        clean_blocks.append(
            {
                "block_id": str(block.get("block_id") or f"aci1_{stable_hash({'i': index, 'text': text, 'locator': locator}, 20)}"),
                "kind": str(block.get("kind") or "paragraph"),
                "text": text,
                "heading_path": heading_path,
                "locator": locator,
                "char_count": len(text),
            }
        )
    chars = sum(int(item.get("char_count") or 0) for item in clean_blocks)
    return {
        "schema_id": ACI1_EXTRACTION_SCHEMA_ID,
        "arm": arm,
        "status": str(status or ("extracted" if clean_blocks else "stored_only")),
        "parser": str(parser or "unknown"),
        "source_path": str(source_path),
        "source_name": source_path.name,
        "suffix": source_path.suffix.lower(),
        "block_count": len(clean_blocks),
        "chars": chars,
        "blocks": clean_blocks,
        "metadata": dict(metadata or {}),
        "raw_artifact": str(raw_artifact or ""),
    }


def load_manifest(path: Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ACI-1 fixture manifest must be a JSON object")
    schema_id = str(payload.get("schema_id") or "")
    if schema_id and schema_id != ACI1_MANIFEST_SCHEMA_ID:
        raise ValueError(f"Unsupported ACI-1 manifest schema: {schema_id}")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("ACI-1 fixture manifest requires a non-empty 'fixtures' list")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in fixtures:
        if not isinstance(raw, dict):
            raise ValueError("Every ACI-1 fixture must be an object")
        fixture_id = str(raw.get("id") or "").strip()
        source = str(raw.get("path") or "").strip()
        if not fixture_id or not source:
            raise ValueError("Every ACI-1 fixture requires 'id' and 'path'")
        if fixture_id in ids:
            raise ValueError(f"Duplicate ACI-1 fixture id: {fixture_id}")
        ids.add(fixture_id)
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = (manifest_path.parent / source_path).resolve()
        truth = raw.get("truth") if isinstance(raw.get("truth"), dict) else {}
        queries = truth.get("queries") if isinstance(truth.get("queries"), list) else []
        normalized.append(
            {
                **raw,
                "id": fixture_id,
                "path": str(source_path),
                "truth": {**truth, "queries": [item for item in queries if isinstance(item, dict)]},
            }
        )
    return {
        **payload,
        "schema_id": ACI1_MANIFEST_SCHEMA_ID,
        "manifest_path": str(manifest_path),
        "fixtures": normalized,
    }
