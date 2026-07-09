from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT_DIR / "neo_data" / "ui_state"
STATE_PATH = STATE_DIR / "ui_state.json"

DEFAULT_UI_STATE: dict[str, Any] = {
    "activeSurfaceId": "image",
    "activeSubtabId": None,
    "activeSubtabsBySurface": {},
    "activeWorkspaceAppId": "generations",
    "activeBackendProfileId": None,
    "activeBackendProfileIdsBySurface": {},
    "detailMode": "guided",
    "imageDraft": {},
    "imageResults": [],
    "activeResultIndex": 0,
    "imageCustomSizePresets": [],
    "activeSavedOutputFileId": "",
}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def read_ui_state() -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        return dict(DEFAULT_UI_STATE)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        clean = dict(DEFAULT_UI_STATE)
        clean.update(_safe_dict(data))
        return _apply_result_cache_integrity(clean)
    except Exception:
        return dict(DEFAULT_UI_STATE)


def _output_ref_exists(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    url = str(item.get("url") or "")
    # Runtime/backend URLs are not Neo cache; do not treat them as durable.
    if not url.startswith("/api/image/output-file"):
        return False
    path_value = str(item.get("path") or item.get("local_path") or "")
    if not path_value:
        return False
    path = (ROOT_DIR / path_value).resolve()
    output_root = (ROOT_DIR / "neo_data" / "outputs" / "image").resolve()
    return output_root in path.parents and path.exists() and path.is_file()


def _apply_result_cache_integrity(state: dict[str, Any]) -> dict[str, Any]:
    """Drop stale preview/result references after users manually wipe Neo_Data.

    This keeps server UI state authoritative without letting old output refs force
    the browser into broken-image reload loops.
    """
    image_results = state.get("imageResults")
    if isinstance(image_results, list):
        valid_results = [item for item in image_results if _output_ref_exists(item)]
        state["imageResults"] = valid_results
        if not valid_results:
            state["activeResultIndex"] = 0
        else:
            index = state.get("activeResultIndex") if isinstance(state.get("activeResultIndex"), int) else 0
            state["activeResultIndex"] = min(max(index, 0), len(valid_results) - 1)
    else:
        state["imageResults"] = []
        state["activeResultIndex"] = 0
    return state



def _clean_backend_selection_map(value: Any) -> dict[str, str]:
    """Keep only safe surface -> profile id strings for persisted backend selection.

    This is intentionally narrower than full UI state restore. Pass U uses this
    for backend selection persistence without re-enabling broad silent UI autosave.
    """
    clean: dict[str, str] = {}
    if not isinstance(value, dict):
        return clean
    for raw_surface, raw_profile in value.items():
        surface = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(raw_surface or "").strip().lower()).strip("_")
        profile_id = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in str(raw_profile or "").strip()).strip("_")
        if surface and profile_id:
            clean[surface] = profile_id
    return clean


def read_backend_profile_selection_state() -> dict[str, Any]:
    state = read_ui_state()
    selection = _clean_backend_selection_map(state.get("activeBackendProfileIdsBySurface"))
    active_profile_id = str(state.get("activeBackendProfileId") or "").strip()
    if active_profile_id:
        active_profile_id = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in active_profile_id).strip("_")
    return {
        "schema_id": "neo.ui_state.backend_profile_selection.v1",
        "activeBackendProfileId": active_profile_id,
        "activeBackendProfileIdsBySurface": selection,
        "persisted_in": "neo_data/ui_state/ui_state.json",
        "selection_only": True,
    }


def write_backend_profile_selection_state(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(payload)
    selection = _clean_backend_selection_map(
        payload.get("activeBackendProfileIdsBySurface")
        or payload.get("selection")
        or payload.get("selected_profiles_by_surface")
        or {}
    )
    surface = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(payload.get("surface") or "").strip().lower()).strip("_")
    profile_id = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in str(payload.get("profile_id") or payload.get("activeBackendProfileId") or "").strip()).strip("_")
    if surface and profile_id:
        selection[surface] = profile_id
    active_profile_id = profile_id or str(payload.get("activeBackendProfileId") or "").strip()
    state = write_ui_state({
        "activeBackendProfileId": active_profile_id,
        "activeBackendProfileIdsBySurface": selection,
    })
    return read_backend_profile_selection_state()


def write_ui_state(payload: dict[str, Any]) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    allowed = set(DEFAULT_UI_STATE.keys())
    current = read_ui_state()
    for key, value in _safe_dict(payload).items():
        if key in allowed:
            current[key] = value
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)
    return current
