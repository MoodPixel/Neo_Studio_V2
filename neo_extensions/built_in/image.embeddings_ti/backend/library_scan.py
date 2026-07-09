from __future__ import annotations

from pathlib import Path
from typing import Any

from .library_schema import SUPPORTED_EXTENSIONS, record_from_path
from .metadata_reader import infer_defaults_from_metadata, read_safetensors_metadata


def _sidecar_metadata(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for sidecar in (path.with_suffix(path.suffix + ".json"), path.with_suffix(".json")):
        if not sidecar.exists():
            continue
        try:
            import json
            loaded = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:  # noqa: BLE001 - sidecars are optional.
            pass
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        preview = path.with_suffix(suffix)
        if preview.exists():
            data.setdefault("preview_image", str(preview))
            data.setdefault("preview_images", [str(preview)])
            break
    return data


def _metadata_for_file(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if path.suffix.lower() == ".safetensors":
        result = read_safetensors_metadata(path)
        metadata.update(infer_defaults_from_metadata(result.get("metadata") or {}) if result.get("ok") else {"metadata_status": "unreadable"})
    metadata.update(_sidecar_metadata(path))
    return metadata


def scan_embeddings_folder(folder: str | Path) -> dict[str, Any]:
    root = Path(folder)
    if not str(folder or "").strip():
        return {"ok": False, "error": "Embeddings folder path is required.", "folder": str(folder), "records": [], "count": 0, "errors": ["Embeddings folder path is required."]}
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": "Embeddings folder does not exist.", "folder": str(folder), "records": [], "count": 0, "errors": ["Embeddings folder does not exist."]}
    records = []
    errors: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            records.append(record_from_path(path, root=root, metadata=_metadata_for_file(path)))
        except Exception as exc:  # noqa: BLE001 - continue scanning other embeddings.
            errors.append(f"{path}: {exc}")
    return {"ok": True, "folder": str(root), "records": records, "count": len(records), "errors": errors, "source": "local_folder"}
