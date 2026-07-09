from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any


def read_safetensors_metadata(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if target.suffix.lower() != ".safetensors":
        return {"ok": False, "metadata": {}, "error": "Only safetensors metadata can be read without loading tensors."}
    try:
        with target.open("rb") as handle:
            raw_len = handle.read(8)
            if len(raw_len) != 8:
                return {"ok": False, "metadata": {}, "error": "Invalid safetensors header."}
            header_len = struct.unpack("<Q", raw_len)[0]
            if header_len <= 0 or header_len > 32_000_000:
                return {"ok": False, "metadata": {}, "error": "Invalid safetensors header length."}
            header = json.loads(handle.read(header_len).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - metadata is optional.
        return {"ok": False, "metadata": {}, "error": str(exc)}
    metadata = header.get("__metadata__") if isinstance(header, dict) else {}
    return {"ok": isinstance(metadata, dict), "metadata": metadata if isinstance(metadata, dict) else {}, "error": ""}


def _split_values(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value).replace(";", ",").replace("\n", ",").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def infer_defaults_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    metadata = metadata or {}
    def first(*keys: str) -> str:
        for key in keys:
            value = metadata.get(key)
            if value not in (None, "", []):
                return str(value)
        return ""
    triggers = _split_values(first("ss_tag_frequency", "trigger_words", "trained_words", "trainedWords", "activation text", "activation_text"))
    base_model = first("ss_base_model_version", "base_model", "baseModel", "modelspec.architecture")
    notes = first("description", "notes", "modelspec.description")
    example_prompt = first("example_prompt", "sample_prompt", "prompt")
    out: dict[str, Any] = {"metadata_status": "readable", "field_sources": {}}
    if triggers:
        out["trigger_words"] = triggers
        out["field_sources"]["trigger_words"] = "safetensors:metadata"
    if base_model:
        out["base_model"] = base_model
        out["field_sources"]["base_model"] = "safetensors:metadata"
    if notes:
        out["notes"] = notes
        out["field_sources"]["notes"] = "safetensors:metadata"
    if example_prompt:
        out["example_prompt"] = example_prompt
        out["field_sources"]["example_prompt"] = "safetensors:metadata"
    return out
