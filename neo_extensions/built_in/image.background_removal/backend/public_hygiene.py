from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import re
from typing import Any, Iterable
from urllib.parse import urlparse


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:/")
_SERVER_ONLY_KEYS = {
    "path",
    "root",
    "models_root",
    "comfy_root",
    "comfy_root_path",
    "native_output_root",
    "backend_output_root",
    "target_root",
    "configured_models_root",
    "configured_comfy_root",
    "custom_detector_root",
    "custom_sam_root",
    "detector_root",
    "sam_root",
}
_ROLE_MARKERS = {
    "birefnet": ("/models/birefnet/",),
    "rmbg": ("/models/rmbg/",),
    "sam": ("/models/sams/",),
    "bbox": ("/models/ultralytics/bbox/", "/models/adetailer/"),
    "segm": ("/models/ultralytics/segm/", "/models/adetailer/"),
}


def _normalized(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _absolute_or_uri(value: str) -> bool:
    folded = value.casefold()
    return bool(
        _WINDOWS_DRIVE.match(value)
        or value.startswith(("/", "//", "~/"))
        or folded.startswith(("file:/", "http://", "https://"))
    )


def portable_model_identifier(value: Any, role: str = "") -> str:
    """Return a browser-safe model identifier without exposing a machine root."""

    normalized = _normalized(value)
    if not normalized or "\x00" in normalized:
        return ""
    folded = normalized.casefold()
    for marker in _ROLE_MARKERS.get(str(role or "").casefold(), ()):
        index = folded.rfind(marker)
        if index >= 0:
            normalized = normalized[index + len(marker):]
            folded = normalized.casefold()
            break
    if folded.startswith(("http://", "https://", "file:/")):
        normalized = PurePosixPath(urlparse(normalized).path).name
    elif _absolute_or_uri(normalized):
        normalized = PurePosixPath(normalized).name
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts:
        return ""
    if any(part == ".." for part in parts):
        return PurePosixPath(normalized).name
    return "/".join(parts)


def portable_model_identifiers(values: Iterable[Any] | None, role: str = "") -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        portable = portable_model_identifier(value, role)
        key = portable.casefold()
        if not portable or key in seen:
            continue
        seen.add(key)
        rows.append(portable)
    return rows


def _drop_server_only_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_drop_server_only_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        str(key): _drop_server_only_fields(item)
        for key, item in value.items()
        if str(key).casefold() not in _SERVER_ONLY_KEYS
    }


def public_model_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact server roots and normalize every browser-visible model list."""

    clean = _drop_server_only_fields(deepcopy(payload or {}))
    clean["models"] = portable_model_identifiers(clean.get("models"), "birefnet")
    clean["rmbg_models"] = portable_model_identifiers(clean.get("rmbg_models"), "rmbg")
    rmbg_node = clean.get("rmbg_node") if isinstance(clean.get("rmbg_node"), dict) else {}
    rmbg_node["model_choices"] = portable_model_identifiers(rmbg_node.get("model_choices"), "rmbg")
    rmbg_node["path_policy"] = "portable_identifiers_only"
    clean["rmbg_node"] = rmbg_node
    shared = clean.get("shared_sam") if isinstance(clean.get("shared_sam"), dict) else {}
    shared["models"] = portable_model_identifiers(shared.get("models"), "sam")
    shared["bbox_models"] = portable_model_identifiers(shared.get("bbox_models"), "bbox")
    shared["segm_models"] = portable_model_identifiers(shared.get("segm_models"), "segm")
    shared["birefnet_models"] = portable_model_identifiers(shared.get("birefnet_models"), "birefnet")
    shared["path_policy"] = "absolute_paths_server_side_only"
    clean["shared_sam"] = shared
    inventory = clean.get("rmbg_inventory") if isinstance(clean.get("rmbg_inventory"), dict) else {}
    model_catalogs = inventory.get("model_catalogs") if isinstance(inventory.get("model_catalogs"), dict) else {}
    inventory["model_catalogs"] = {
        str(role): portable_model_identifiers(values, str(role))
        for role, values in model_catalogs.items()
        if isinstance(values, list)
    }
    inventory["safety"] = dict(inventory.get("safety") or {})
    inventory["safety"]["path_policy"] = "portable_identifiers_only"
    inventory["path_policy"] = "absolute_paths_server_side_only"
    clean["rmbg_inventory"] = inventory
    engine_catalog = clean.get("engine_catalog") if isinstance(clean.get("engine_catalog"), dict) else {}
    engine_rows = engine_catalog.get("engines") if isinstance(engine_catalog.get("engines"), list) else []
    for row in engine_rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id") or "").casefold()
        role = "sam" if row_id == "comfy_sam" else ("rmbg" if row_id == "comfy_rmbg" else "birefnet")
        row["models"] = portable_model_identifiers(row.get("models"), role)
        row["path_policy"] = "portable_identifiers_only"
    by_workflow = engine_catalog.get("by_workflow") if isinstance(engine_catalog.get("by_workflow"), dict) else {}
    for rows in by_workflow.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("id") or "").casefold()
            role = "sam" if row_id == "comfy_sam" else ("rmbg" if row_id == "comfy_rmbg" else "birefnet")
            row["models"] = portable_model_identifiers(row.get("models"), role)
            row["path_policy"] = "portable_identifiers_only"
    engine_catalog["path_policy"] = "portable_identifiers_only"
    clean["engine_catalog"] = engine_catalog
    segmentation_lab = clean.get("segmentation_lab") if isinstance(clean.get("segmentation_lab"), dict) else {}
    for row in segmentation_lab.get("adapters", []) if isinstance(segmentation_lab.get("adapters"), list) else []:
        if not isinstance(row, dict):
            continue
        choices = row.get("model_choices") if isinstance(row.get("model_choices"), dict) else {}
        row["model_choices"] = {
            str(input_name): portable_model_identifiers(values, "groundingdino" if "dino" in str(input_name).casefold() else ("sam2" if "sam2" in str(input_name).casefold() else "sam"))
            for input_name, values in choices.items()
            if isinstance(values, list)
        }
        row["path_policy"] = "portable_identifiers_only"
    segmentation_lab["path_policy"] = "portable_identifiers_only"
    clean["segmentation_lab"] = segmentation_lab
    clean["path_policy"] = "absolute_paths_server_side_only"
    return clean
