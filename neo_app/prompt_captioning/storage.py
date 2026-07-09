from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import shutil

from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "neo_data" / "prompt_captioning"
PROMPTS_PATH = DATA_DIR / "saved_prompts.json"
HISTORY_PATH = DATA_DIR / "prompt_history.json"
CAPTIONS_PATH = DATA_DIR / "saved_captions.json"
CAPTION_HISTORY_PATH = DATA_DIR / "caption_history.json"
CAPTION_ASSETS_DIR = DATA_DIR / "caption_assets"
CAPTION_LIBRARY_DIR = DATA_DIR / "caption_library"
CAPTION_SINGLE_DIR = CAPTION_LIBRARY_DIR / "single_image"
CAPTION_BATCH_DIR = CAPTION_LIBRARY_DIR / "batch_captioning"
CAPTION_SINGLE_IMAGES_DIR = CAPTION_SINGLE_DIR / "images"
CAPTION_SINGLE_CARDS_DIR = CAPTION_SINGLE_DIR / "metadata_cards"
CAPTION_BATCH_IMAGES_DIR = CAPTION_BATCH_DIR / "images"
CAPTION_BATCH_CARDS_DIR = CAPTION_BATCH_DIR / "metadata_cards"
CAPTION_PRESETS_PATH = DATA_DIR / "caption_presets.json"
CAPTION_COMPONENTS_PATH = DATA_DIR / "caption_components.json"
CAPTION_BATCH_RESULTS_PATH = DATA_DIR / "caption_batch_results.json"
HANDOFF_HISTORY_PATH = DATA_DIR / "handoff_history.json"
RESULT_METADATA_PATH = DATA_DIR / "result_metadata.json"
CATEGORIES_PATH = DATA_DIR / "categories.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        values = data.get("records") or data.get("items") or []
    else:
        values = data
    return values if isinstance(values, list) else []


def _write_list(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"records": records}, indent=2), encoding="utf-8")


def append_prompt_history(record: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(HISTORY_PATH)
    item = {"history_id": f"prompt_hist_{uuid4().hex[:12]}", "created_at": _now(), **record}
    records.insert(0, item)
    _write_list(HISTORY_PATH, records[:100])
    return item


def save_prompt_record(payload: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(PROMPTS_PATH)
    name = str(payload.get("name") or "Untitled Prompt").strip() or "Untitled Prompt"
    prompt = str(payload.get("prompt") or payload.get("output_text") or "").strip()
    if not prompt:
        return {"ok": False, "errors": ["Prompt text is empty."]}
    prompt_id = str(payload.get("prompt_id") or f"prompt_{uuid4().hex[:12]}")
    now = _now()
    record = {
        "prompt_id": prompt_id,
        "name": name,
        "category": str(payload.get("category") or "General").strip() or "General",
        "prompt": prompt,
        "negative_prompt": str(payload.get("negative_prompt") or ""),
        "source_text": str(payload.get("source_text") or ""),
        "style": str(payload.get("style") or ""),
        "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [item.strip() for item in str(payload.get("tags") or "").split(",") if item.strip()],
        "notes": str(payload.get("notes") or ""),
        "settings": payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "created_at": now,
        "updated_at": now,
    }
    replaced = False
    for index, item in enumerate(records):
        if item.get("prompt_id") == prompt_id:
            record["created_at"] = item.get("created_at") or now
            records[index] = record
            replaced = True
            break
    if not replaced:
        records.insert(0, record)
    _write_list(PROMPTS_PATH, records)
    try:
        save_category({"label": record.get("category") or "General", "used_by": "prompt"})
    except Exception:
        pass
    return {"ok": True, "record": record, "records": records}


def list_prompt_records() -> dict[str, Any]:
    records = _read_list(PROMPTS_PATH)
    return {"ok": True, "records": records, "count": len(records)}

PRESETS_PATH = DATA_DIR / "prompt_presets.json"
CHARACTERS_PATH = DATA_DIR / "character_library.json"


def _filter_records(records: list[dict[str, Any]], query: str = "", category: str = "") -> list[dict[str, Any]]:
    q = str(query or "").strip().lower()
    c = str(category or "").strip().lower()
    out: list[dict[str, Any]] = []
    for item in records:
        haystack = " ".join(str(item.get(key) or "") for key in ("name", "category", "prompt", "notes", "style", "source_text"))
        if item.get("tags"):
            haystack += " " + " ".join(str(tag) for tag in item.get("tags") or [])
        if q and q not in haystack.lower():
            continue
        if c and str(item.get("category") or "").lower() != c:
            continue
        out.append(item)
    return out


def list_prompt_history(limit: int = 25) -> dict[str, Any]:
    records = _read_list(HISTORY_PATH)
    return {"ok": True, "records": records[: max(1, int(limit or 25))], "count": len(records)}


def list_prompt_presets(query: str = "", category: str = "") -> dict[str, Any]:
    records = _filter_records(_read_list(PRESETS_PATH), query, category)
    return {"ok": True, "records": records, "count": len(records)}


def save_prompt_preset(payload: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(PRESETS_PATH)
    name = str(payload.get("name") or "Untitled Preset").strip() or "Untitled Preset"
    preset_id = str(payload.get("preset_id") or f"prompt_preset_{uuid4().hex[:12]}")
    now = _now()
    item = {
        "preset_id": preset_id,
        "name": name,
        "category": str(payload.get("category") or "General").strip() or "General",
        "style": str(payload.get("style") or ""),
        "subject": str(payload.get("subject") or ""),
        "mood": str(payload.get("mood") or ""),
        "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [part.strip() for part in str(payload.get("tags") or "").split(",") if part.strip()],
        "notes": str(payload.get("notes") or ""),
        "default_positive": str(payload.get("default_positive") or payload.get("prompt") or ""),
        "default_negative": str(payload.get("default_negative") or payload.get("negative_prompt") or ""),
        "settings": payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
        "favorite": bool(payload.get("favorite", False)),
        "created_at": now,
        "updated_at": now,
    }
    replaced = False
    for index, record in enumerate(records):
        if record.get("preset_id") == preset_id:
            item["created_at"] = record.get("created_at") or now
            if "favorite" not in payload:
                item["favorite"] = bool(record.get("favorite", False))
            records[index] = item
            replaced = True
            break
    if not replaced:
        records.insert(0, item)
    _write_list(PRESETS_PATH, records)
    return {"ok": True, "record": item, "records": records}


def delete_prompt_preset(preset_id: str) -> dict[str, Any]:
    records = _read_list(PRESETS_PATH)
    kept = [item for item in records if item.get("preset_id") != preset_id]
    if len(kept) == len(records):
        return {"ok": False, "errors": ["Prompt preset not found."]}
    _write_list(PRESETS_PATH, kept)
    return {"ok": True, "records": kept}


def duplicate_prompt_preset(preset_id: str) -> dict[str, Any]:
    records = _read_list(PRESETS_PATH)
    source = next((item for item in records if item.get("preset_id") == preset_id), None)
    if not source:
        return {"ok": False, "errors": ["Prompt preset not found."]}
    clone = dict(source)
    clone["preset_id"] = f"prompt_preset_{uuid4().hex[:12]}"
    clone["name"] = f"{source.get('name') or 'Preset'} Copy"
    clone["favorite"] = False
    clone["created_at"] = _now()
    clone["updated_at"] = clone["created_at"]
    records.insert(0, clone)
    _write_list(PRESETS_PATH, records)
    return {"ok": True, "record": clone, "records": records}


def toggle_prompt_preset_favorite(preset_id: str) -> dict[str, Any]:
    records = _read_list(PRESETS_PATH)
    target = None
    for item in records:
        if item.get("preset_id") == preset_id:
            item["favorite"] = not bool(item.get("favorite", False))
            item["updated_at"] = _now()
            target = item
            break
    if not target:
        return {"ok": False, "errors": ["Prompt preset not found."]}
    _write_list(PRESETS_PATH, records)
    return {"ok": True, "record": target, "records": records}


def list_character_records(query: str = "") -> dict[str, Any]:
    records = _filter_records(_read_list(CHARACTERS_PATH), query, "")
    return {"ok": True, "records": records, "count": len(records)}


def save_character_record(payload: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(CHARACTERS_PATH)
    character_id = str(payload.get("character_id") or f"character_{uuid4().hex[:12]}")
    now = _now()
    item = {
        "character_id": character_id,
        "name": str(payload.get("name") or "Untitled Character").strip() or "Untitled Character",
        "traits": str(payload.get("traits") or ""),
        "appearance": str(payload.get("appearance") or ""),
        "outfit": str(payload.get("outfit") or ""),
        "identity_fragments": str(payload.get("identity_fragments") or payload.get("prompt") or ""),
        "notes": str(payload.get("notes") or ""),
        "created_at": now,
        "updated_at": now,
    }
    replaced = False
    for index, record in enumerate(records):
        if record.get("character_id") == character_id:
            item["created_at"] = record.get("created_at") or now
            records[index] = item
            replaced = True
            break
    if not replaced:
        records.insert(0, item)
    _write_list(CHARACTERS_PATH, records)
    return {"ok": True, "record": item, "records": records}


def delete_prompt_record(prompt_id: str) -> dict[str, Any]:
    records = _read_list(PROMPTS_PATH)
    kept = [item for item in records if item.get("prompt_id") != prompt_id]
    if len(kept) == len(records):
        return {"ok": False, "errors": ["Prompt record not found."]}
    _write_list(PROMPTS_PATH, kept)
    return {"ok": True, "records": kept}


def duplicate_prompt_record(prompt_id: str) -> dict[str, Any]:
    records = _read_list(PROMPTS_PATH)
    source = next((item for item in records if item.get("prompt_id") == prompt_id), None)
    if not source:
        return {"ok": False, "errors": ["Prompt record not found."]}
    clone = dict(source)
    clone["prompt_id"] = f"prompt_{uuid4().hex[:12]}"
    clone["name"] = f"{source.get('name') or 'Prompt'} Copy"
    clone["created_at"] = _now()
    clone["updated_at"] = clone["created_at"]
    records.insert(0, clone)
    _write_list(PROMPTS_PATH, records)
    return {"ok": True, "record": clone, "records": records}




def _caption_storage_dirs(origin: str = "single") -> tuple[Path, Path, str]:
    text = str(origin or "single").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"batch", "batch_captioning", "library_batch"}:
        return CAPTION_BATCH_IMAGES_DIR, CAPTION_BATCH_CARDS_DIR, "batch_captioning"
    return CAPTION_SINGLE_IMAGES_DIR, CAPTION_SINGLE_CARDS_DIR, "single_image"


def _caption_asset_url(filename: str = "", origin: str = "single") -> str:
    safe = Path(str(filename or "")).name
    if not safe:
        return ""
    return f"/api/prompt-captioning/caption/asset/{safe}"


def _copy_caption_source_image(record: dict[str, Any], origin: str = "single") -> dict[str, Any]:
    """Copy the original caption image into the caption library image folder.

    Older builds only kept a path to the staged upload or external batch image.
    That made Caption Browser unreliable after restarts/moves.  Each saved
    caption now gets a local image copy plus a JSON metadata card.
    """
    src = str(record.get("source_image") or record.get("asset_ref") or record.get("path") or "").strip()
    if not src:
        return {}
    source = Path(src)
    if not source.exists() or not source.is_file():
        return {}
    images_dir, _cards_dir, normalized_origin = _caption_storage_dirs(origin)
    images_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or ".png"
    caption_id = str(record.get("caption_id") or f"caption_{uuid4().hex[:12]}")
    dest_name = f"{caption_id}{suffix.lower()}"
    dest = images_dir / dest_name
    counter = 2
    while dest.exists() and dest.resolve() != source.resolve():
        dest_name = f"{caption_id}_{counter}{suffix.lower()}"
        dest = images_dir / dest_name
        counter += 1
    if dest.resolve() != source.resolve():
        shutil.copyfile(source, dest)
    return {
        "library_origin": normalized_origin,
        "library_image": str(dest),
        "library_image_filename": dest.name,
        "library_image_url": _caption_asset_url(dest.name, normalized_origin),
        "stored_image": str(dest),
        "stored_image_url": _caption_asset_url(dest.name, normalized_origin),
        "image_filename": dest.name,
    }


def _write_caption_metadata_card(record: dict[str, Any], origin: str = "single") -> dict[str, Any]:
    _images_dir, cards_dir, normalized_origin = _caption_storage_dirs(origin)
    cards_dir.mkdir(parents=True, exist_ok=True)
    caption_id = str(record.get("caption_id") or f"caption_{uuid4().hex[:12]}")
    card_path = cards_dir / f"{caption_id}.json"
    card = {
        "card_id": f"caption_card_{caption_id}",
        "caption_id": caption_id,
        "origin": normalized_origin,
        "name": record.get("name") or "Untitled Caption",
        "category": record.get("category") or "General",
        "caption": record.get("caption") or "",
        "source_image": record.get("source_image") or "",
        "library_image": record.get("library_image") or record.get("stored_image") or "",
        "library_image_url": record.get("library_image_url") or record.get("stored_image_url") or "",
        "caption_mode": record.get("caption_mode") or "",
        "tags": record.get("tags") if isinstance(record.get("tags"), list) else [],
        "settings": record.get("settings") if isinstance(record.get("settings"), dict) else {},
        "metadata": record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
        "created_at": record.get("created_at") or _now(),
        "updated_at": record.get("updated_at") or _now(),
    }
    card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
    return {"metadata_card": str(card_path), "metadata_card_filename": card_path.name}


def persist_caption_library_assets(record: dict[str, Any], origin: str = "single") -> dict[str, Any]:
    enriched = dict(record)
    media = _copy_caption_source_image(enriched, origin)
    enriched.update(media)
    card = _write_caption_metadata_card(enriched, origin)
    enriched.update(card)
    return enriched


def append_caption_history(record: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(CAPTION_HISTORY_PATH)
    item = {"history_id": f"caption_hist_{uuid4().hex[:12]}", "created_at": _now(), **record}
    records.insert(0, item)
    _write_list(CAPTION_HISTORY_PATH, records[:100])
    return item


def list_caption_history(limit: int = 25) -> dict[str, Any]:
    records = _read_list(CAPTION_HISTORY_PATH)
    return {"ok": True, "records": records[: max(1, int(limit or 25))], "count": len(records)}


def save_caption_asset(src_path: str, original_name: str = "") -> dict[str, Any]:
    source = Path(str(src_path or ""))
    if not source.exists() or not source.is_file():
        return {"ok": False, "errors": ["Caption source image not found."]}
    CAPTION_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or Path(original_name or "image.png").suffix or ".png"
    asset_id = f"caption_asset_{uuid4().hex[:12]}"
    filename = f"{asset_id}{suffix}"
    dest = CAPTION_ASSETS_DIR / filename
    shutil.copyfile(source, dest)
    size = dest.stat().st_size if dest.exists() else 0
    # Phase P6 compatibility: "url": f"/api/prompt-captioning/caption/asset/{filename}"
    preview_url = f"/api/prompt-captioning/caption/asset/{filename}"
    asset_ref = str(dest)
    return {
        "ok": True,
        "asset_id": asset_id,
        "asset_ref": asset_ref,
        "assetRef": asset_ref,
        "path": asset_ref,
        "filename": filename,
        "stored_filename": filename,
        "storedFilename": filename,
        "original_name": original_name or source.name,
        "originalName": original_name or source.name,
        "url": preview_url,
        "preview_url": preview_url,
        "previewUrl": preview_url,
        "selectedImageUrl": preview_url,
        "mime_type": "image/" + (dest.suffix.lower().lstrip(".") or "png").replace("jpg", "jpeg"),
        "size": size,
        "size_bytes": size,
    }


def save_caption_record(payload: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(CAPTIONS_PATH)
    caption = str(payload.get("caption") or payload.get("output_caption") or "").strip()
    if not caption:
        return {"ok": False, "errors": ["Caption text is empty."]}
    now = _now()
    caption_id = str(payload.get("caption_id") or f"caption_{uuid4().hex[:12]}")
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else [part.strip() for part in str(payload.get("tags") or "").split(",") if part.strip()]
    record = {
        "caption_id": caption_id,
        "name": str(payload.get("name") or "Untitled Caption").strip() or "Untitled Caption",
        "category": str(payload.get("category") or "General").strip() or "General",
        "caption": caption,
        "source_image": str(payload.get("source_image") or ""),
        "source_image_url": str(payload.get("source_image_url") or ""),
        "caption_style": str(payload.get("caption_style") or ""),
        "caption_length": str(payload.get("caption_length") or ""),
        "output_style": str(payload.get("output_style") or ""),
        "component_type": str(payload.get("component_type") or ""),
        "caption_mode": str(payload.get("caption_mode") or ""),
        "detail_level": str(payload.get("detail_level") or ""),
        "tags": tags,
        "notes": str(payload.get("notes") or ""),
        "settings": payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "created_at": now,
        "updated_at": now,
    }
    origin = str(payload.get("origin") or payload.get("source_origin") or payload.get("library_origin") or "single").strip() or "single"
    try:
        record = persist_caption_library_assets(record, origin)
    except Exception as exc:  # keep saving caption text even when image copy/card write fails
        meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        meta = dict(meta)
        meta["library_asset_warning"] = str(exc)
        record["metadata"] = meta
    replaced = False
    for index, item in enumerate(records):
        if item.get("caption_id") == caption_id:
            record["created_at"] = item.get("created_at") or now
            records[index] = record
            replaced = True
            break
    if not replaced:
        records.insert(0, record)
    _write_list(CAPTIONS_PATH, records)
    try:
        save_category({"label": record.get("category") or "General", "used_by": "caption"})
    except Exception:
        pass
    return {"ok": True, "record": record, "records": records}


def list_caption_records(query: str = "", category: str = "") -> dict[str, Any]:
    records = _filter_caption_records(_read_list(CAPTIONS_PATH), query, category)
    return {"ok": True, "records": records, "count": len(records)}


def _filter_caption_records(records: list[dict[str, Any]], query: str = "", category: str = "", component_type: str = "") -> list[dict[str, Any]]:
    q = str(query or "").strip().lower()
    c = str(category or "").strip().lower()
    comp = str(component_type or "").strip().lower()
    out: list[dict[str, Any]] = []
    for item in records:
        haystack = " ".join(str(item.get(key) or "") for key in ("name", "category", "caption", "notes", "caption_style", "caption_mode", "component_type", "detail_level"))
        if item.get("tags"):
            haystack += " " + " ".join(str(tag) for tag in item.get("tags") or [])
        if q and q not in haystack.lower():
            continue
        if c and str(item.get("category") or "").lower() != c:
            continue
        if comp and str(item.get("component_type") or "").lower() != comp:
            continue
        out.append(item)
    return out


def delete_caption_record(caption_id: str) -> dict[str, Any]:
    records = _read_list(CAPTIONS_PATH)
    kept = [item for item in records if item.get("caption_id") != caption_id]
    if len(kept) == len(records):
        return {"ok": False, "errors": ["Caption record not found."]}
    _write_list(CAPTIONS_PATH, kept)
    return {"ok": True, "records": kept}


def duplicate_caption_record(caption_id: str) -> dict[str, Any]:
    records = _read_list(CAPTIONS_PATH)
    source = next((item for item in records if item.get("caption_id") == caption_id), None)
    if not source:
        return {"ok": False, "errors": ["Caption record not found."]}
    clone = dict(source)
    clone["caption_id"] = f"caption_{uuid4().hex[:12]}"
    clone["name"] = f"{source.get('name') or 'Caption'} Copy"
    clone["created_at"] = _now()
    clone["updated_at"] = clone["created_at"]
    records.insert(0, clone)
    _write_list(CAPTIONS_PATH, records)
    return {"ok": True, "record": clone, "records": records}


def list_caption_presets(query: str = "", category: str = "") -> dict[str, Any]:
    records = _filter_caption_records(_read_list(CAPTION_PRESETS_PATH), query, category)
    return {"ok": True, "records": records, "count": len(records)}


def save_caption_preset(payload: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(CAPTION_PRESETS_PATH)
    preset_id = str(payload.get("preset_id") or f"caption_preset_{uuid4().hex[:12]}")
    now = _now()
    item = {
        "preset_id": preset_id,
        "name": str(payload.get("name") or "Untitled Caption Preset").strip() or "Untitled Caption Preset",
        "category": str(payload.get("category") or "General").strip() or "General",
        "caption_style": str(payload.get("caption_style") or "descriptive"),
        "caption_length": str(payload.get("caption_length") or "medium"),
        "output_style": str(payload.get("output_style") or "auto"),
        "caption_mode": str(payload.get("caption_mode") or "full_image"),
        "component_type": str(payload.get("component_type") or "custom"),
        "detail_level": str(payload.get("detail_level") or "detailed"),
        "target_use": str(payload.get("target_use") or ""),
        "tone": str(payload.get("tone") or ""),
        "tag_rules": str(payload.get("tag_rules") or ""),
        "instruction": str(payload.get("instruction") or payload.get("caption_instruction") or ""),
        "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [part.strip() for part in str(payload.get("tags") or "").split(",") if part.strip()],
        "notes": str(payload.get("notes") or ""),
        "settings": payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
        "favorite": bool(payload.get("favorite", False)),
        "created_at": now,
        "updated_at": now,
    }
    replaced = False
    for index, record in enumerate(records):
        if record.get("preset_id") == preset_id:
            item["created_at"] = record.get("created_at") or now
            if "favorite" not in payload:
                item["favorite"] = bool(record.get("favorite", False))
            records[index] = item
            replaced = True
            break
    if not replaced:
        records.insert(0, item)
    _write_list(CAPTION_PRESETS_PATH, records)
    return {"ok": True, "record": item, "records": records}


def delete_caption_preset(preset_id: str) -> dict[str, Any]:
    records = _read_list(CAPTION_PRESETS_PATH)
    kept = [item for item in records if item.get("preset_id") != preset_id]
    if len(kept) == len(records):
        return {"ok": False, "errors": ["Caption preset not found."]}
    _write_list(CAPTION_PRESETS_PATH, kept)
    return {"ok": True, "records": kept}


def duplicate_caption_preset(preset_id: str) -> dict[str, Any]:
    records = _read_list(CAPTION_PRESETS_PATH)
    source = next((item for item in records if item.get("preset_id") == preset_id), None)
    if not source:
        return {"ok": False, "errors": ["Caption preset not found."]}
    clone = dict(source)
    clone["preset_id"] = f"caption_preset_{uuid4().hex[:12]}"
    clone["name"] = f"{source.get('name') or 'Caption Preset'} Copy"
    clone["favorite"] = False
    clone["created_at"] = _now()
    clone["updated_at"] = clone["created_at"]
    records.insert(0, clone)
    _write_list(CAPTION_PRESETS_PATH, records)
    return {"ok": True, "record": clone, "records": records}


def toggle_caption_preset_favorite(preset_id: str) -> dict[str, Any]:
    records = _read_list(CAPTION_PRESETS_PATH)
    target = None
    for item in records:
        if item.get("preset_id") == preset_id:
            item["favorite"] = not bool(item.get("favorite", False))
            item["updated_at"] = _now()
            target = item
            break
    if not target:
        return {"ok": False, "errors": ["Caption preset not found."]}
    _write_list(CAPTION_PRESETS_PATH, records)
    return {"ok": True, "record": target, "records": records}


def list_caption_components(query: str = "", component_type: str = "") -> dict[str, Any]:
    records = _filter_caption_records(_read_list(CAPTION_COMPONENTS_PATH), query, "", component_type)
    return {"ok": True, "records": records, "count": len(records)}


def save_caption_component(payload: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(CAPTION_COMPONENTS_PATH)
    component_id = str(payload.get("component_id") or f"caption_component_{uuid4().hex[:12]}")
    now = _now()
    item = {
        "component_id": component_id,
        "name": str(payload.get("name") or "Untitled Component").strip() or "Untitled Component",
        "component_type": str(payload.get("component_type") or "cta"),
        "caption": str(payload.get("caption") or payload.get("text") or ""),
        "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [part.strip() for part in str(payload.get("tags") or "").split(",") if part.strip()],
        "notes": str(payload.get("notes") or ""),
        "created_at": now,
        "updated_at": now,
    }
    if not item["caption"].strip():
        return {"ok": False, "errors": ["Reusable component text is empty."]}
    replaced = False
    for index, record in enumerate(records):
        if record.get("component_id") == component_id:
            item["created_at"] = record.get("created_at") or now
            records[index] = item
            replaced = True
            break
    if not replaced:
        records.insert(0, item)
    _write_list(CAPTION_COMPONENTS_PATH, records)
    return {"ok": True, "record": item, "records": records}


def delete_caption_component(component_id: str) -> dict[str, Any]:
    records = _read_list(CAPTION_COMPONENTS_PATH)
    kept = [item for item in records if item.get("component_id") != component_id]
    if len(kept) == len(records):
        return {"ok": False, "errors": ["Caption component not found."]}
    _write_list(CAPTION_COMPONENTS_PATH, kept)
    return {"ok": True, "records": kept}


def duplicate_caption_component(component_id: str) -> dict[str, Any]:
    records = _read_list(CAPTION_COMPONENTS_PATH)
    source = next((item for item in records if item.get("component_id") == component_id), None)
    if not source:
        return {"ok": False, "errors": ["Caption component not found."]}
    clone = dict(source)
    clone["component_id"] = f"caption_component_{uuid4().hex[:12]}"
    clone["name"] = f"{source.get('name') or 'Component'} Copy"
    clone["created_at"] = _now()
    clone["updated_at"] = clone["created_at"]
    records.insert(0, clone)
    _write_list(CAPTION_COMPONENTS_PATH, records)
    return {"ok": True, "record": clone, "records": records}


def append_caption_batch_result(record: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(CAPTION_BATCH_RESULTS_PATH)
    item = {"batch_id": f"caption_batch_{uuid4().hex[:12]}", "created_at": _now(), **record}
    records.insert(0, item)
    _write_list(CAPTION_BATCH_RESULTS_PATH, records[:100])
    return item


def list_caption_batch_results(limit: int = 25) -> dict[str, Any]:
    records = _read_list(CAPTION_BATCH_RESULTS_PATH)
    return {"ok": True, "records": records[: max(1, int(limit or 25))], "count": len(records)}

# Phase L — Library / Presets / History / Reuse hardening
LIBRARY_KIND_PATHS: dict[str, Path] = {
    "saved_prompts": PROMPTS_PATH,
    "prompt_history": HISTORY_PATH,
    "prompt_presets": PRESETS_PATH,
    "characters": CHARACTERS_PATH,
    "saved_captions": CAPTIONS_PATH,
    "caption_history": CAPTION_HISTORY_PATH,
    "caption_presets": CAPTION_PRESETS_PATH,
    "caption_components": CAPTION_COMPONENTS_PATH,
    "caption_batch_results": CAPTION_BATCH_RESULTS_PATH,
    "handoff_history": HANDOFF_HISTORY_PATH,
    "result_metadata": RESULT_METADATA_PATH,
}

LIBRARY_KIND_ID_KEYS: dict[str, str] = {
    "saved_prompts": "prompt_id",
    "prompt_history": "history_id",
    "prompt_presets": "preset_id",
    "characters": "character_id",
    "saved_captions": "caption_id",
    "caption_history": "history_id",
    "caption_presets": "preset_id",
    "caption_components": "component_id",
    "caption_batch_results": "batch_id",
    "handoff_history": "handoff_id",
    "result_metadata": "metadata_id",
}


def _safe_kind(kind: str) -> str:
    value = str(kind or "").strip()
    if value not in LIBRARY_KIND_PATHS:
        raise ValueError(f"Unsupported Prompt/Captioning library kind: {value or 'missing'}")
    return value


def _record_id_for_kind(kind: str, payload: dict[str, Any]) -> str:
    id_key = LIBRARY_KIND_ID_KEYS[_safe_kind(kind)]
    value = str(payload.get(id_key) or payload.get("record_id") or payload.get("id") or "").strip()
    return value



def append_handoff_history(record: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(HANDOFF_HISTORY_PATH)
    item = {"handoff_id": f"handoff_{uuid4().hex[:12]}", "created_at": _now(), **record}
    records.insert(0, item)
    _write_list(HANDOFF_HISTORY_PATH, records[:200])
    return item


def list_handoff_history(limit: int = 50) -> dict[str, Any]:
    records = _read_list(HANDOFF_HISTORY_PATH)
    safe_limit = max(1, min(200, int(limit or 50)))
    return {"ok": True, "records": records[:safe_limit], "count": len(records)}

def list_library_kind(kind: str, query: str = "", category: str = "", limit: int = 200) -> dict[str, Any]:
    safe = _safe_kind(kind)
    records = _read_list(LIBRARY_KIND_PATHS[safe])
    if safe.startswith("caption") or safe == "saved_captions":
        records = _filter_caption_records(records, query, category)
    else:
        records = _filter_records(records, query, category)
    capped = records[: max(1, min(int(limit or 200), 1000))]
    return {"ok": True, "kind": safe, "records": capped, "count": len(records), "id_key": LIBRARY_KIND_ID_KEYS[safe]}


def get_library_record(kind: str, record_id: str) -> dict[str, Any]:
    safe = _safe_kind(kind)
    key = LIBRARY_KIND_ID_KEYS[safe]
    rid = str(record_id or "").strip()
    if not rid:
        return {"ok": False, "errors": ["Record id is required."], "kind": safe}
    for item in _read_list(LIBRARY_KIND_PATHS[safe]):
        if str(item.get(key) or "") == rid:
            return {"ok": True, "kind": safe, "id_key": key, "record": item}
    return {"ok": False, "errors": ["Record not found."], "kind": safe, "id_key": key}


def update_library_record(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    safe = _safe_kind(kind)
    path = LIBRARY_KIND_PATHS[safe]
    key = LIBRARY_KIND_ID_KEYS[safe]
    record_id = _record_id_for_kind(safe, payload)
    if not record_id:
        return {"ok": False, "errors": ["Record id is required."], "kind": safe, "id_key": key}
    records = _read_list(path)
    now = _now()
    for index, item in enumerate(records):
        if str(item.get(key) or "") == record_id:
            protected = {key, "created_at"}
            merged = dict(item)
            for field, value in payload.items():
                if field in protected or field in {"kind", "record_id", "id"}:
                    continue
                merged[field] = value
            merged["updated_at"] = now
            records[index] = merged
            _write_list(path, records)
            return {"ok": True, "kind": safe, "id_key": key, "record": merged, "records": records}
    return {"ok": False, "errors": ["Record not found."], "kind": safe, "id_key": key}


def delete_library_record(kind: str, record_id: str) -> dict[str, Any]:
    safe = _safe_kind(kind)
    path = LIBRARY_KIND_PATHS[safe]
    key = LIBRARY_KIND_ID_KEYS[safe]
    rid = str(record_id or "").strip()
    records = _read_list(path)
    kept = [item for item in records if str(item.get(key) or "") != rid]
    if len(kept) == len(records):
        return {"ok": False, "errors": ["Record not found."], "kind": safe, "id_key": key}
    _write_list(path, kept)
    return {"ok": True, "kind": safe, "id_key": key, "records": kept}


def duplicate_library_record(kind: str, record_id: str) -> dict[str, Any]:
    safe = _safe_kind(kind)
    path = LIBRARY_KIND_PATHS[safe]
    key = LIBRARY_KIND_ID_KEYS[safe]
    records = _read_list(path)
    source = next((item for item in records if str(item.get(key) or "") == str(record_id or "")), None)
    if not source:
        return {"ok": False, "errors": ["Record not found."], "kind": safe, "id_key": key}
    clone = dict(source)
    prefix = key.replace("_id", "")
    clone[key] = f"{prefix}_{uuid4().hex[:12]}"
    clone["name"] = f"{source.get('name') or source.get('tool_id') or source.get('caption') or 'Record'} Copy"
    clone["created_at"] = _now()
    clone["updated_at"] = clone["created_at"]
    if "favorite" in clone:
        clone["favorite"] = False
    records.insert(0, clone)
    _write_list(path, records)
    return {"ok": True, "kind": safe, "id_key": key, "record": clone, "records": records}


def library_snapshot() -> dict[str, Any]:
    payload = {
        "schema_version": "prompt_captioning.library.v1",
        "exported_at": _now(),
        "libraries": {},
        "counts": {},
    }
    for kind, path in LIBRARY_KIND_PATHS.items():
        records = _read_list(path)
        payload["libraries"][kind] = records
        payload["counts"][kind] = len(records)
    return {"ok": True, **payload}


def import_library_snapshot(payload: dict[str, Any], merge: bool = True) -> dict[str, Any]:
    libraries = payload.get("libraries") if isinstance(payload.get("libraries"), dict) else payload
    if not isinstance(libraries, dict):
        return {"ok": False, "errors": ["Import payload must include a libraries object."]}
    imported: dict[str, int] = {}
    for kind, incoming in libraries.items():
        if kind not in LIBRARY_KIND_PATHS or not isinstance(incoming, list):
            continue
        path = LIBRARY_KIND_PATHS[kind]
        key = LIBRARY_KIND_ID_KEYS[kind]
        existing = _read_list(path) if merge else []
        by_id = {str(item.get(key) or uuid4().hex): item for item in existing}
        for item in incoming:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            if not str(record.get(key) or "").strip():
                record[key] = f"{key.replace('_id', '')}_{uuid4().hex[:12]}"
            record.setdefault("created_at", _now())
            record["updated_at"] = _now()
            by_id[str(record.get(key))] = record
        records = list(by_id.values())
        _write_list(path, records)
        imported[kind] = len(incoming)
    return {"ok": True, "imported": imported, "snapshot": library_snapshot()}


def clear_library_history(history_kind: str) -> dict[str, Any]:
    safe = _safe_kind(history_kind)
    if safe not in {"prompt_history", "caption_history", "caption_batch_results", "result_metadata"}:
        return {"ok": False, "errors": ["Only history/result libraries can be cleared through this route."], "kind": safe}
    _write_list(LIBRARY_KIND_PATHS[safe], [])
    return {"ok": True, "kind": safe, "records": []}


# Phase N — Metadata + Replay Readiness
def append_result_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    records = _read_list(RESULT_METADATA_PATH)
    item = dict(metadata or {})
    item.setdefault("metadata_id", f"pcmeta_{uuid4().hex[:12]}")
    item.setdefault("created_at", _now())
    item.setdefault("surface_id", "prompt_captioning")
    item.setdefault("workspace_app", "neo_studio")
    records.insert(0, item)
    _write_list(RESULT_METADATA_PATH, records[:500])
    return item

def list_result_metadata(limit: int = 100, tool_id: str = "") -> dict[str, Any]:
    records = _read_list(RESULT_METADATA_PATH)
    if tool_id:
        records = [item for item in records if str(item.get("tool_id") or "") == str(tool_id)]
    safe_limit = max(1, min(int(limit or 100), 500))
    return {"ok": True, "records": records[:safe_limit], "count": len(records)}

def get_result_metadata(metadata_id: str) -> dict[str, Any]:
    mid = str(metadata_id or "").strip()
    if not mid:
        return {"ok": False, "errors": ["Metadata id is required."]}
    for item in _read_list(RESULT_METADATA_PATH):
        if str(item.get("metadata_id") or "") == mid:
            return {"ok": True, "record": item}
    return {"ok": False, "errors": ["Metadata record not found."]}

def build_replay_payload_from_metadata(metadata_id: str) -> dict[str, Any]:
    found = get_result_metadata(metadata_id)
    if not found.get("ok"):
        return found
    record = found.get("record") or {}
    replay = record.get("replay_payload") if isinstance(record.get("replay_payload"), dict) else {}
    if not replay:
        return {"ok": False, "errors": ["Metadata record does not contain a replay payload."], "record": record}
    return {"ok": True, "metadata_id": metadata_id, "replay_payload": replay, "record": record}



def _category_id(label: str) -> str:
    text = str(label or "").strip().lower()
    safe = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe or "general"


def _record_categories(path: Path) -> list[str]:
    out: list[str] = []
    for item in _read_list(path):
        cat = str(item.get("category") or item.get("library_category") or "").strip()
        if cat:
            out.append(cat)
    return out


def list_categories() -> dict[str, Any]:
    labels: dict[str, dict[str, Any]] = {}
    def add(label: str, used_by: str = "shared") -> None:
        text = str(label or "").strip()
        if not text:
            return
        key = text.lower()
        existing = labels.get(key) or {"id": _category_id(text), "label": text, "used_by": [], "created_at": _now(), "updated_at": _now()}
        if used_by not in existing["used_by"]:
            existing["used_by"].append(used_by)
        existing["updated_at"] = _now()
        labels[key] = existing
    add("General", "shared")
    for item in _read_list(CATEGORIES_PATH):
        if isinstance(item, dict):
            add(str(item.get("label") or item.get("name") or item.get("id") or ""), "explicit")
        else:
            add(str(item), "explicit")
    for path, used_by in [
        (PROMPTS_PATH, "prompt"), (PRESETS_PATH, "prompt"), (CAPTIONS_PATH, "caption"),
        (CAPTION_PRESETS_PATH, "caption"), (CAPTION_COMPONENTS_PATH, "caption"), (CAPTION_BATCH_RESULTS_PATH, "batch"),
    ]:
        for cat in _record_categories(path):
            add(cat, used_by)
    records = sorted(labels.values(), key=lambda item: str(item.get("label") or "").lower())
    return {"ok": True, "categories": records, "count": len(records)}


def save_category(payload: dict[str, Any]) -> dict[str, Any]:
    label = str(payload.get("label") or payload.get("name") or payload.get("category") or "").strip()
    if not label:
        return {"ok": False, "errors": ["Category name is empty."]}
    used_by = payload.get("used_by") if isinstance(payload.get("used_by"), list) else [str(payload.get("used_by") or "shared")]
    records = _read_list(CATEGORIES_PATH)
    now = _now()
    cid = _category_id(label)
    updated = {"id": cid, "label": label, "used_by": used_by, "created_at": now, "updated_at": now}
    replaced = False
    for idx, item in enumerate(records):
        old = str((item or {}).get("label") or (item or {}).get("name") or (item or {}).get("id") or "").lower() if isinstance(item, dict) else str(item).lower()
        if old == label.lower():
            if isinstance(item, dict):
                updated["created_at"] = item.get("created_at") or now
                old_used = item.get("used_by") if isinstance(item.get("used_by"), list) else []
                updated["used_by"] = sorted(set(old_used + used_by))
            records[idx] = updated
            replaced = True
            break
    if not replaced:
        records.insert(0, updated)
    _write_list(CATEGORIES_PATH, records)
    data = list_categories()
    data["record"] = updated
    return data
