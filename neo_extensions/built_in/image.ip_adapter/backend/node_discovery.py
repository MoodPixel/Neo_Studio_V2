from __future__ import annotations
from pathlib import Path
from typing import Any
import os

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - PyYAML is optional at runtime.
    yaml = None

STANDARD_REQUIRED = ["CLIPVisionLoader", "IPAdapterModelLoader", "IPAdapterAdvanced"]
FACEID_REQUIRED = ["CLIPVisionLoader", "IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceID"]
OPTIONAL = ["ImageBatch", "IPAdapterInsightFaceLoader"]


MODEL_FILE_SUFFIXES = {".safetensors", ".bin", ".pt", ".pth", ".ckpt"}
FACEID_MARKERS = ("faceid", "face_id", "face-id", "insightface")
FACEID_PRESET_LABELS = {"model", "faceid", "faceid plus", "faceid plus v2", "faceid portrait", "faceid portrait unnorm"}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = str(value or "").strip().replace("\\", "/")
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _split_folder_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_split_folder_values(item))
        return values
    text = str(value).strip()
    if not text:
        return []
    return [line.strip() for line in text.replace(";", "\n").splitlines() if line.strip()]


def _candidate_extra_model_yaml_paths(backend_details: dict[str, Any] | None = None) -> list[Path]:
    backend_details = backend_details or {}
    paths: list[Path] = []
    for raw in [
        os.environ.get("COMFYUI_EXTRA_MODEL_PATHS"),
        os.environ.get("COMFYUI_EXTRA_MODEL_PATHS_YAML"),
        backend_details.get("extra_model_paths_yaml"),
    ]:
        if raw:
            paths.append(Path(str(raw)).expanduser())

    portable_path = str(backend_details.get("portable_path") or "").strip()
    if portable_path:
        root = Path(portable_path).expanduser()
        paths.extend([
            root / "ComfyUI" / "extra_model_paths.yaml",
            root / "ComfyUI" / "extra_model_paths.yml",
            root / "extra_model_paths.yaml",
            root / "extra_model_paths.yml",
        ])

    # Portable/dev fallback is intentionally relative to the running process only.
    # Do not add user-specific absolute paths here; Neo Studio is a repo/shared tool.
    # Real installs should provide either backend_details.extra_model_paths_yaml,
    # backend_details.portable_path, or COMFYUI_EXTRA_MODEL_PATHS(_YAML).
    cwd = Path.cwd()
    paths.extend([
        cwd / "extra_model_paths.yaml",
        cwd / "extra_model_paths.yml",
        cwd / "ComfyUI" / "extra_model_paths.yaml",
        cwd / "ComfyUI" / "extra_model_paths.yml",
    ])

    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _load_extra_model_paths_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    if yaml is not None:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    # Tiny fallback for the simple Comfy extra_model_paths.yaml shape.
    root: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    current_block_key = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped.endswith(":"):
            current = root.setdefault(stripped[:-1].strip(), {})
            current_block_key = ""
            continue
        if current is None or ":" not in stripped:
            if current is not None and current_block_key:
                current[current_block_key] = f"{current.get(current_block_key, '')}\n{stripped}".strip()
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "|":
            current[key] = ""
            current_block_key = key
        else:
            current[key] = value.strip('"\'')
            current_block_key = ""
    return root


def _resolve_extra_model_folders(yaml_path: Path, categories: set[str]) -> list[Path]:
    payload = _load_extra_model_paths_yaml(yaml_path)
    folders: list[Path] = []
    for group in payload.values():
        if not isinstance(group, dict):
            continue
        base = Path(str(group.get("base_path") or yaml_path.parent)).expanduser()
        for key, raw_value in group.items():
            norm_key = str(key or "").strip().casefold().replace("-", "_")
            if norm_key not in categories:
                continue
            for folder_value in _split_folder_values(raw_value):
                folder = Path(folder_value).expanduser()
                if not folder.is_absolute():
                    folder = base / folder
                folders.append(folder)
    return folders


def _scan_model_folder(folder: Path) -> list[str]:
    if not folder.exists() or not folder.is_dir():
        return []
    values: list[str] = []
    try:
        files = [item for item in folder.rglob("*") if item.is_file() and item.suffix.casefold() in MODEL_FILE_SUFFIXES]
    except Exception:
        return []
    for file_path in sorted(files, key=lambda item: str(item).casefold()):
        try:
            rel = file_path.relative_to(folder).as_posix()
        except Exception:
            rel = file_path.name
        values.append(rel)
    return _dedupe(values)


def discover_extra_model_path_inputs(backend_details: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Discover IP Adapter dropdown files from Comfy extra_model_paths.yaml.

    Some IPAdapter Plus nodes expose FaceID presets through /object_info instead
    of the actual files. This scanner uses Comfy's configured model folders as a
    fallback source so Neo can populate real model dropdowns from shared paths.
    """
    results = {"clip_vision": [], "ip_adapter": [], "faceid": []}
    for yaml_path in _candidate_extra_model_yaml_paths(backend_details):
        for folder in _resolve_extra_model_folders(yaml_path, {"clip_vision", "clip_visions", "clipvision"}):
            results["clip_vision"].extend(_scan_model_folder(folder))
        ip_files: list[str] = []
        for folder in _resolve_extra_model_folders(yaml_path, {"ipadapter", "ip_adapter", "ip_adapters"}):
            ip_files.extend(_scan_model_folder(folder))
        for name in ip_files:
            lowered = name.casefold()
            if any(marker in lowered for marker in FACEID_MARKERS):
                results["faceid"].append(name)
            else:
                results["ip_adapter"].append(name)
    return {key: _dedupe(value) for key, value in results.items()}


def merge_model_inputs(*sources: dict[str, list[str]] | None) -> dict[str, list[str]]:
    merged = {"clip_vision": [], "ip_adapter": [], "faceid": []}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in merged:
            values = source.get(key) or []
            if isinstance(values, list):
                merged[key].extend(str(item) for item in values)
    return {key: _dedupe(value) for key, value in merged.items()}


def _names(object_info: Any) -> set[str]:
    if isinstance(object_info, dict):
        return {str(k) for k in object_info.keys()}
    if isinstance(object_info, (set, list, tuple)):
        return {str(v) for v in object_info}
    return set()


def _extract_choices(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item).strip() for item in first if str(item).strip()]
    if all(isinstance(item, str) for item in value):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _node_choices(object_info: Any, node_name: str, *input_names: str) -> list[str]:
    if not isinstance(object_info, dict) or not node_name:
        return []
    node = object_info.get(node_name) or {}
    inputs = node.get("input") or {}
    required = inputs.get("required") or {}
    optional = inputs.get("optional") or {}
    values: list[str] = []
    seen: set[str] = set()
    for input_name in input_names:
        for item in _extract_choices(required.get(input_name)) + _extract_choices(optional.get(input_name)):
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                values.append(item)
    return values


def _first_existing_node(object_info: Any, aliases: list[str]) -> str:
    names = _names(object_info)
    return next((alias for alias in aliases if alias in names), "")


def _looks_like_faceid_model(value: Any) -> bool:
    lowered = str(value or "").strip().casefold()
    return bool(lowered) and any(marker in lowered for marker in FACEID_MARKERS)


def _looks_like_faceid_preset(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return text in FACEID_PRESET_LABELS or text.startswith("faceid plus -") or text.startswith("faceid plus ·") or text.startswith("faceid portrait ")


def extract_model_inputs(object_info: Any) -> dict[str, list[str]]:
    clip_node = _first_existing_node(object_info, ["CLIPVisionLoader", "CLIPVisionLoaderModelOnly"])
    ip_node = _first_existing_node(object_info, ["IPAdapterModelLoader", "IPAdapterUnifiedLoader", "IPAdapterLoader"])
    faceid_node = _first_existing_node(object_info, ["IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceIDModelLoader"])
    ip_choices = _node_choices(object_info, ip_node, "ipadapter_file", "ipadapter_name", "model", "model_name", "name")
    faceid_node_choices = [item for item in _node_choices(object_info, faceid_node, "model", "model_name", "ipadapter_file", "faceid_model") if not _looks_like_faceid_preset(item)]
    faceid_from_ip_models = [item for item in ip_choices if _looks_like_faceid_model(item)]
    standard_ip_models = [item for item in ip_choices if not _looks_like_faceid_model(item)]
    return {
        "clip_vision": _node_choices(object_info, clip_node, "clip_name", "clip_vision_name", "model_name"),
        "ip_adapter": _dedupe(standard_ip_models),
        "faceid": _dedupe(faceid_node_choices + faceid_from_ip_models),
    }


def inspect_nodes(object_info: Any) -> dict[str, Any]:
    names = _names(object_info)
    if object_info is None:
        names = set(STANDARD_REQUIRED + FACEID_REQUIRED + OPTIONAL)
    standard_missing = [node for node in STANDARD_REQUIRED if node not in names]
    faceid_missing = [node for node in FACEID_REQUIRED if node not in names]
    return {
        "schema_version": "neo.image.ip_adapter.nodes.v1",
        "standard_required": STANDARD_REQUIRED,
        "faceid_required": FACEID_REQUIRED,
        "optional": OPTIONAL,
        "available": sorted(names),
        "standard_available": not standard_missing,
        "faceid_available": not faceid_missing,
        "standard_missing": standard_missing,
        "faceid_missing": faceid_missing,
        "image_batch_available": "ImageBatch" in names,
        "model_inputs": extract_model_inputs(object_info),
        "unknown_object_info": object_info is None,
    }
