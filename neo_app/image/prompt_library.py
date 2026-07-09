from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.prompt_captioning.storage import list_prompt_presets, list_prompt_records

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "neo_data" / "image"
PROMPT_LIBRARY_PATH = DATA_DIR / "prompt_library.json"

SCHEMA = "neo.image.prompt_library.v25_9_17"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_records() -> list[dict[str, Any]]:
    try:
        if not PROMPT_LIBRARY_PATH.exists():
            return []
        data = json.loads(PROMPT_LIBRARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        records = data.get("records") or []
    else:
        records = data
    return records if isinstance(records, list) else []


def _write_records(records: list[dict[str, Any]]) -> None:
    PROMPT_LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, "records": records}
    tmp = PROMPT_LIBRARY_PATH.with_suffix(PROMPT_LIBRARY_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PROMPT_LIBRARY_PATH)


def _image_summary(record: dict[str, Any]) -> dict[str, Any]:
    prompt_id = str(record.get("prompt_pair_id") or record.get("id") or "")
    return {
        "id": f"image:{prompt_id}",
        "prompt_pair_id": prompt_id,
        "source": "image",
        "source_label": "Image",
        "readonly": False,
        "name": str(record.get("name") or "Untitled Image Prompt"),
        "positive_prompt": str(record.get("positive_prompt") or ""),
        "negative_prompt": str(record.get("negative_prompt") or ""),
        "notes": str(record.get("notes") or ""),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
    }


def _prompt_studio_saved_summary(record: dict[str, Any]) -> dict[str, Any] | None:
    source_id = str(record.get("prompt_id") or "")
    positive = str(record.get("prompt") or record.get("output_text") or "")
    negative = str(record.get("negative_prompt") or "")
    if not (positive.strip() or negative.strip()):
        return None
    return {
        "id": f"prompt_studio_saved:{source_id}",
        "source_id": source_id,
        "source": "prompt_studio_saved_prompt",
        "source_label": "Prompt Studio saved",
        "readonly": True,
        "name": str(record.get("name") or "Prompt Studio Saved Prompt"),
        "positive_prompt": positive,
        "negative_prompt": negative,
        "notes": str(record.get("notes") or ""),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
    }


def _prompt_studio_preset_summary(record: dict[str, Any]) -> dict[str, Any] | None:
    source_id = str(record.get("preset_id") or "")
    positive = str(record.get("default_positive") or record.get("prompt") or "")
    negative = str(record.get("default_negative") or record.get("negative_prompt") or "")
    if not (positive.strip() or negative.strip()):
        return None
    return {
        "id": f"prompt_studio_preset:{source_id}",
        "source_id": source_id,
        "source": "prompt_studio_preset",
        "source_label": "Prompt Studio preset",
        "readonly": True,
        "name": str(record.get("name") or "Prompt Studio Preset"),
        "positive_prompt": positive,
        "negative_prompt": negative,
        "notes": str(record.get("notes") or ""),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
    }


def _matches_query(item: dict[str, Any], query: str = "") -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return True
    haystack = " ".join(str(item.get(key) or "") for key in ("name", "source_label", "positive_prompt", "negative_prompt", "notes"))
    return q in haystack.lower()


def list_image_prompt_library(query: str = "") -> dict[str, Any]:
    image_records = [_image_summary(record) for record in _read_records()]
    prompt_studio_records: list[dict[str, Any]] = []
    try:
        for record in list_prompt_records().get("records", []):
            item = _prompt_studio_saved_summary(record)
            if item:
                prompt_studio_records.append(item)
    except Exception:
        pass
    try:
        for record in list_prompt_presets().get("records", []):
            item = _prompt_studio_preset_summary(record)
            if item:
                prompt_studio_records.append(item)
    except Exception:
        pass

    records = [item for item in [*image_records, *prompt_studio_records] if _matches_query(item, query)]
    return {
        "ok": True,
        "schema": SCHEMA,
        "records": records,
        "image_count": len(image_records),
        "prompt_studio_count": len(prompt_studio_records),
        "count": len(records),
    }


def create_image_prompt_pair(payload: dict[str, Any]) -> dict[str, Any]:
    positive = str(payload.get("positive_prompt") or payload.get("prompt") or "")
    negative = str(payload.get("negative_prompt") or "")
    if not (positive.strip() or negative.strip()):
        return {"ok": False, "errors": ["Positive and negative prompts are both empty."]}
    records = _read_records()
    now = _now()
    name = str(payload.get("name") or "Image Prompt Pair").strip()[:100] or "Image Prompt Pair"
    record = {
        "schema": SCHEMA,
        "prompt_pair_id": f"image_prompt_{uuid4().hex[:12]}",
        "name": name,
        "positive_prompt": positive,
        "negative_prompt": negative,
        "notes": str(payload.get("notes") or ""),
        "created_at": now,
        "updated_at": now,
    }
    records.insert(0, record)
    _write_records(records)
    return {"ok": True, "record": _image_summary(record), **list_image_prompt_library()}


def update_image_prompt_pair(prompt_pair_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    target_id = str(prompt_pair_id or "").replace("image:", "")
    records = _read_records()
    target: dict[str, Any] | None = None
    for index, record in enumerate(records):
        if str(record.get("prompt_pair_id") or "") == target_id:
            updated = dict(record)
            if "name" in payload:
                updated["name"] = str(payload.get("name") or updated.get("name") or "Image Prompt Pair").strip()[:100] or "Image Prompt Pair"
            if "positive_prompt" in payload or "prompt" in payload:
                updated["positive_prompt"] = str(payload.get("positive_prompt") if "positive_prompt" in payload else payload.get("prompt") or "")
            if "negative_prompt" in payload:
                updated["negative_prompt"] = str(payload.get("negative_prompt") or "")
            if "notes" in payload:
                updated["notes"] = str(payload.get("notes") or "")
            if not (str(updated.get("positive_prompt") or "").strip() or str(updated.get("negative_prompt") or "").strip()):
                return {"ok": False, "errors": ["Positive and negative prompts are both empty."]}
            updated["updated_at"] = _now()
            records[index] = updated
            target = updated
            break
    if not target:
        return {"ok": False, "errors": ["Image prompt pair not found."]}
    _write_records(records)
    return {"ok": True, "record": _image_summary(target), **list_image_prompt_library()}


def delete_image_prompt_pair(prompt_pair_id: str) -> dict[str, Any]:
    target_id = str(prompt_pair_id or "").replace("image:", "")
    records = _read_records()
    kept = [record for record in records if str(record.get("prompt_pair_id") or "") != target_id]
    if len(kept) == len(records):
        return {"ok": False, "errors": ["Image prompt pair not found."]}
    _write_records(kept)
    return {"ok": True, "deleted": target_id, **list_image_prompt_library()}
