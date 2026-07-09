from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

LORA_DIR_NAMES = ("loras", "lora", "LoRA", "Loras")


def _as_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text)


def _profile_portable_paths(root: str | Path) -> list[Path]:
    path = Path(root) / "neo_app" / "providers" / "backend_profiles.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Path] = []
    for profile in data.get("profiles", []) if isinstance(data, dict) else []:
        if not isinstance(profile, dict):
            continue
        connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
        for key in ("portable_path", "comfy_root", "models_root"):
            candidate = _as_path(connection.get(key) or profile.get(key))
            if candidate:
                out.append(candidate)
    return out


def lora_search_roots(root: str | Path) -> list[Path]:
    """Return candidate local LoRA folders for metadata hydration.

    Comfy's /object_info normally exposes only the loader-facing LoRA name, not
    the absolute file path. V1 could read safetensors metadata because it knew
    the folder path. V2 removed that manual path from the UI, so the backend has
    to infer likely local paths from backend profile portable paths and common
    Comfy/Neo model folders.
    """
    root = Path(root)
    candidates: list[Path] = []
    env_values = [
        os.environ.get("NEO_LORA_DIRS"),
        os.environ.get("COMFYUI_LORA_DIRS"),
        os.environ.get("COMFYUI_ROOT"),
        os.environ.get("COMFY_ROOT"),
        os.environ.get("NEO_COMFY_ROOT"),
    ]
    for value in env_values:
        if not value:
            continue
        for part in str(value).split(os.pathsep):
            p = _as_path(part)
            if p:
                candidates.append(p)

    base_roots = [root, root.parent, *_profile_portable_paths(root)]
    for base in base_roots:
        candidates.extend([
            base / "models" / "loras",
            base / "models" / "Loras",
            base / "models" / "lora",
            base / "ComfyUI" / "models" / "loras",
            base / "ComfyUI" / "models" / "Loras",
            base / "ComfyUI" / "models" / "lora",
        ])

    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate).casefold()
        if text not in seen:
            seen.add(text)
            out.append(candidate)
    return out


def resolve_lora_file_path(root: str | Path, catalog_name: str) -> Path | None:
    name = str(catalog_name or "").replace("\\", "/").strip().lstrip("/")
    if not name:
        return None
    direct = Path(name)
    if direct.is_absolute() and direct.exists() and direct.suffix.lower() == ".safetensors":
        return direct
    for search_root in lora_search_roots(root):
        candidate = search_root / name
        if candidate.exists() and candidate.suffix.lower() == ".safetensors":
            return candidate
        # Comfy catalog usually includes extension; keep fallback for extensionless saved records.
        if candidate.suffix == "":
            safe = candidate.with_suffix(".safetensors")
            if safe.exists():
                return safe
    return None


def lora_path_resolution_payload(root: str | Path, catalog_name: str) -> dict[str, Any]:
    path = resolve_lora_file_path(root, catalog_name)
    return {
        "resolved": bool(path),
        "path": str(path) if path else "",
        "search_roots": [str(item) for item in lora_search_roots(root)],
    }
