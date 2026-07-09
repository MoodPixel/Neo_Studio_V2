"""Phase F backend queue route for Image Upscale.

The route owns request parsing, provider/profile gating, upload handoff,
per-image queue orchestration, metadata context recording, and partial batch
failure reporting. The detailed Comfy graph builder remains extension-owned and
can be swapped in tests; Phase G hardens the graph itself.
"""
from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Callable
from urllib import request as urlrequest
from uuid import uuid4

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile

from .constants import EXTENSION_ID, QUEUE_ENDPOINT, SUPPORTED_COMFY_BACKENDS, SEEDVR2_DIT_DEFAULT, SEEDVR2_VAE_DEFAULT, SEEDVR2_ENGINE_ID
from .metadata import build_image_upscale_extension_usage, build_image_upscale_metadata
from .payload_schema import PayloadContractError, build_payload_block, compute_seedvr2_resolution_contract, normalize_settings, validate_payload_settings
from .support_matrix import ACTIVE_ROUTE_STATES, support_for_route, support_with_nodes
from .workflow import build_image_upscale_workflow

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ProfileProviderResolver = Callable[[str], tuple[Any, dict[str, Any]]]
ContextRecorder = Callable[[str, dict[str, Any]], None]
WorkflowBuilder = Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any], list[str]]]



def _extract_model_names_from_endpoint_payload(payload: Any) -> list[str]:
    """Normalize Comfy /models/<folder> responses into model names.

    Comfy and custom nodes are not consistent here. Depending on the route, the
    payload can be a raw list, a dict under `models`/`files`, a dict under the
    exact folder name such as `facerestore_models`, or a filename-keyed map.
    """
    values: list[Any] = []

    def collect(value: Any) -> None:
        if isinstance(value, list):
            values.extend(value)
            return
        if isinstance(value, dict):
            named_keys = (
                "models", "files", "items", "names", "data",
                "SEEDVR2", "SeedVR2", "seedvr2",
                "facerestore_models", "face_restore_models", "facerestore", "face_restore",
                "facerestore_model", "face_restore_model", "restore_model",
                "restore_models", "codeformer", "codeformer_models",
            )
            found_list = False
            for key in named_keys:
                raw = value.get(key)
                if isinstance(raw, list):
                    values.extend(raw)
                    found_list = True
            if found_list:
                return
            for key, raw in value.items():
                if isinstance(raw, list):
                    values.extend(raw)
                    found_list = True
            if found_list:
                return
            # Some endpoints return {"filename.ext": {...}}. Treat filename-ish
            # keys as model names, but avoid adding container keys as fake models.
            for key in value.keys():
                if Path(str(key)).suffix:
                    values.append(key)

    collect(payload)
    names: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            raw = item.get("name") or item.get("filename") or item.get("file") or item.get("path") or item.get("value")
        else:
            raw = item
        name = str(raw or "").strip().replace("\\", "/")
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _extract_comfy_choices(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item).strip() for item in first if str(item).strip()]
    if all(isinstance(item, str) for item in value):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _node_choices(object_info: dict[str, Any], node_name: str, *input_names: str) -> list[str]:
    required = (((object_info.get(node_name) or {}).get("input") or {}).get("required") or {})
    optional = (((object_info.get(node_name) or {}).get("input") or {}).get("optional") or {})
    names: list[str] = []
    seen: set[str] = set()
    for input_name in input_names:
        for item in _extract_comfy_choices(required.get(input_name)) + _extract_comfy_choices(optional.get(input_name)):
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                names.append(item)
    return names


def _query_comfy_model_folders(provider: Any, folder_names: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    if not hasattr(provider, "_get_json"):
        return names
    for folder in folder_names:
        try:
            payload = provider._get_json(f"/models/{folder}")  # noqa: SLF001 - Comfy catalog bridge.
        except Exception:
            continue
        for name in _extract_model_names_from_endpoint_payload(payload):
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names


SEEDVR2_FOLDER_NAMES = {"SEEDVR2", "SeedVR2", "seedvr2"}
FACERESTORE_FOLDER_NAMES = {"facerestore_models", "face_restore_models", "facerestore", "face_restore", "codeformer", "CodeFormer"}
CODEFORMER_NODE_NAMES = {"CodeFormer", "CodeFormerLoader", "CodeFormerModelLoader", "FaceRestoreModelLoader", "FaceRestoreCFWithModel", "ImageFaceRestore"}
CODEFORMER_CONVENTIONAL_MODEL = "codeformer.pth"
MODEL_ROOT_NAMES = {"models", "model"}


def _candidate_comfy_roots(root_dir: Path, profile: dict[str, Any]) -> list[Path]:
    connection = profile.get("connection", {}) or {}
    runtime = profile.get("runtime", {}) or {}
    raw_roots = [
        connection.get("portable_path"),
        connection.get("comfy_root_path"),
        connection.get("comfyui_path"),
        connection.get("model_root_path"),
        connection.get("models_path"),
        runtime.get("portable_path"),
        runtime.get("comfy_root_path"),
        runtime.get("comfyui_path"),
        runtime.get("model_root_path"),
        runtime.get("models_path"),
        root_dir,
        Path.cwd(),
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    known_model_subfolders = SEEDVR2_FOLDER_NAMES | FACERESTORE_FOLDER_NAMES
    for raw in raw_roots:
        if not raw:
            continue
        try:
            base = Path(str(raw)).expanduser()
        except Exception:
            continue
        base_name = base.name.casefold()
        candidates = [base, base / "ComfyUI"]

        if base_name in MODEL_ROOT_NAMES:
            candidates.extend([base.parent, base.parent / "ComfyUI"])
        elif base_name in known_model_subfolders:
            # Support users pasting a direct model-folder path such as
            # .../ComfyUI/models/facerestore_models or .../models/SEEDVR2.
            candidates.append(base.parent)
            if base.parent.name.casefold() in MODEL_ROOT_NAMES:
                candidates.extend([base.parent.parent, base.parent.parent / "ComfyUI"])
        else:
            # Also support paths one level below ComfyUI_windows_portable.
            candidates.extend([base.parent, base.parent / "ComfyUI"])

        for candidate in candidates:
            key = str(candidate).casefold()
            if key not in seen:
                seen.add(key)
                roots.append(candidate)
    return roots


def _candidate_model_scan_folders(roots: list[Path], relative_dirs: list[str], direct_folder_names: set[str] | None = None) -> list[Path]:
    folder_name_values = [str(item) for item in (direct_folder_names or set())]
    direct_names = {item.casefold() for item in folder_name_values}
    folders: list[Path] = []
    seen: set[str] = set()

    def push(path: Path) -> None:
        # Keep exact case variants. Windows paths are case-insensitive, but tests
        # and Linux-hosted portable folders can distinguish SEEDVR2 from SeedVR2.
        key = str(path)
        if key not in seen:
            seen.add(key)
            folders.append(path)

    for root in roots:
        root_name = root.name.casefold()
        for rel in relative_dirs:
            push(root / rel)
        if root_name in direct_names:
            push(root)
        if root_name in MODEL_ROOT_NAMES:
            for folder_name in folder_name_values:
                push(root / folder_name)
    return folders


def _scan_model_dirs(roots: list[Path], relative_dirs: list[str], suffixes: set[str], direct_folder_names: set[str] | None = None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for folder in _candidate_model_scan_folders(roots, relative_dirs, direct_folder_names):
        if not folder.exists() or not folder.is_dir():
            continue
        try:
            files = [item for item in folder.rglob("*") if item.is_file() and item.suffix.casefold() in suffixes]
        except Exception:
            continue
        for file_path in sorted(files, key=lambda item: str(item).casefold()):
            try:
                name = file_path.relative_to(folder).as_posix()
            except Exception:
                name = file_path.name
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _split_seedvr2_models(names: list[str]) -> tuple[list[str], list[str]]:
    dit: list[str] = []
    vae: list[str] = []
    seen_dit: set[str] = set()
    seen_vae: set[str] = set()
    for raw in names:
        name = str(raw or "").strip().replace("\\", "/")
        lowered = name.casefold()
        if not name:
            continue
        if "vae" in lowered:
            if lowered not in seen_vae:
                seen_vae.add(lowered)
                vae.append(name)
        elif "seedvr2" in lowered or lowered.endswith((".gguf", ".safetensors", ".pt", ".pth")):
            if lowered not in seen_dit:
                seen_dit.add(lowered)
                dit.append(name)
    return dit, vae


def _dedupe_names(names: list[str]) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = str(raw or "").strip().replace("\\", "/")
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        clean.append(name)
    return clean


def _profile_model_bucket_names(profile: dict[str, Any], bucket_names: list[str]) -> list[str]:
    """Read model names already captured on the backend profile runtime.

    Connect/Test can cache Comfy model buckets in `profile.runtime.models`. Use
    those as a read-only catalog hint before falling back to conventional names.
    """
    runtime = profile.get("runtime", {}) if isinstance(profile.get("runtime"), dict) else {}
    models = runtime.get("models") if isinstance(runtime.get("models"), dict) else {}
    names: list[str] = []
    for bucket in bucket_names:
        values = models.get(bucket) if isinstance(models, dict) else []
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                raw = item.get("name") or item.get("filename") or item.get("file") or item.get("path") or item.get("value")
            else:
                raw = item
            name = str(raw or "").strip().replace("\\", "/")
            if name:
                names.append(name)
    return _dedupe_names(names)


def _has_codeformer_restore_nodes(info: dict[str, Any]) -> bool:
    if not isinstance(info, dict) or not info:
        return False
    available = {str(key) for key in info.keys()}
    return bool({"FaceRestoreModelLoader", "FaceRestoreCFWithModel"}.issubset(available) or (available & CODEFORMER_NODE_NAMES))


def _has_basic_comfy_image_nodes(info: dict[str, Any]) -> bool:
    if not isinstance(info, dict) or not info:
        return False
    available = {str(key) for key in info.keys()}
    return bool({"LoadImage", "ImageScaleBy", "SaveImage"}.issubset(available))


def _profile_looks_connected(profile: dict[str, Any]) -> bool:
    runtime = profile.get("runtime", {}) if isinstance(profile.get("runtime"), dict) else {}
    status = str(runtime.get("status") or runtime.get("runtime_status") or "").strip().lower()
    return bool(runtime.get("reachable") is True or status == "connected")


def _codeformer_choices_from_object_info(info: dict[str, Any], choices: list[str]) -> list[str]:
    """Trust CodeFormer-ish object_info choices as selectable restore models.

    SeedVR2 nodes can advertise example filenames that are not installed. For
    CodeFormer, the common FaceRestore loader choices normally mirror the
    facerestore model folder. Only CodeFormer-named choices are exposed.
    """
    if not _has_codeformer_restore_nodes(info):
        return []
    selected: list[str] = []
    for name in _dedupe_names(choices):
        clean = str(name or "").strip().replace("\\", "/")
        if clean and "codeformer" in clean.casefold():
            selected.append(clean)
    return _dedupe_names(selected)


def _codeformer_conventional_fallback(info: dict[str, Any], profile: dict[str, Any], *, seed_models_found: bool = False) -> tuple[list[str], str]:
    """Expose the standard CodeFormer filename as a local Comfy fallback.

    A connected Comfy install can have `models/facerestore_models/codeformer.pth`
    on disk while `/models/facerestore_models` and object_info restore choices do
    not report it. The UI should still offer the conventional filename so users
    with a normal CodeFormer install are not blocked by a catalog blind spot.

    This never hardcodes an absolute user path. It only exposes the conventional
    model filename. Queue-time node gating still validates whether the selected
    Comfy install can actually run CodeFormer restore.
    """
    provider_id = str(profile.get("provider_id") or "").strip()
    if provider_id not in SUPPORTED_COMFY_BACKENDS:
        return [], ""
    if _has_codeformer_restore_nodes(info):
        return [CODEFORMER_CONVENTIONAL_MODEL], "restore_nodes_detected"
    if _has_basic_comfy_image_nodes(info):
        return [CODEFORMER_CONVENTIONAL_MODEL], "basic_comfy_nodes_detected_unverified"
    if seed_models_found:
        return [CODEFORMER_CONVENTIONAL_MODEL], "seedvr2_catalog_detected_unverified"
    if _profile_looks_connected(profile):
        return [CODEFORMER_CONVENTIONAL_MODEL], "connected_comfy_profile_unverified"
    return [], ""


def _build_model_catalog(root_dir: Path, provider: Any, profile: dict[str, Any]) -> dict[str, Any]:
    """Build the Image Upscale model catalog from real model folders.

    SeedVR2's Comfy node object_info can expose baked-in example filenames.
    Those are *not* proof that files exist, so this catalog intentionally uses
    only Comfy's /models endpoints and filesystem scans for dropdown values.
    object_info is kept only as diagnostics/node capability evidence.
    """
    info = _object_info(provider)
    node_declared_seed_names: list[str] = []
    node_declared_codeformer_names: list[str] = []
    seed_names: list[str] = []
    codeformer_names: list[str] = []
    sources: list[str] = []

    if info:
        node_declared_seed_names.extend(_node_choices(info, "SeedVR2LoadDiTModel", "model", "model_name", "dit", "dit_model"))
        node_declared_seed_names.extend(_node_choices(info, "SeedVR2LoadVAEModel", "model", "model_name", "vae", "vae_model"))
        for node_name in sorted(CODEFORMER_NODE_NAMES):
            node_declared_codeformer_names.extend(_node_choices(info, node_name, "model", "model_name", "facerestore_model", "restore_model"))
        sources.append("comfy_object_info_nodes")

    queried_seed = _query_comfy_model_folders(provider, ["SEEDVR2", "SeedVR2", "seedvr2"])
    queried_restore = _query_comfy_model_folders(provider, ["facerestore_models", "face_restore_models", "facerestore", "face_restore", "codeformer", "CodeFormer"])
    if queried_seed:
        seed_names.extend(queried_seed)
        sources.append("comfy_models_endpoint_seedvr2")
    if queried_restore:
        codeformer_names.extend(queried_restore)
        sources.append("comfy_models_endpoint_facerestore")

    profile_restore = _profile_model_bucket_names(profile, ["facerestore_models", "face_restore_models", "facerestore", "face_restore", "codeformer", "codeformer_models"])
    if profile_restore:
        codeformer_names.extend(profile_restore)
        sources.append("profile_runtime_facerestore")

    roots = _candidate_comfy_roots(root_dir, profile)
    scanned_seed = _scan_model_dirs(
        roots,
        ["models/SEEDVR2", "models/SeedVR2", "models/seedvr2"],
        {".gguf", ".safetensors", ".pt", ".pth", ".ckpt", ".bin"},
        SEEDVR2_FOLDER_NAMES,
    )
    scanned_restore = _scan_model_dirs(
        roots,
        ["models/facerestore_models", "models/face_restore_models", "models/facerestore", "models/face_restore", "models/codeformer", "models/CodeFormer"],
        {".pth", ".pt", ".safetensors", ".ckpt", ".bin"},
        FACERESTORE_FOLDER_NAMES,
    )
    if scanned_seed:
        seed_names.extend(scanned_seed)
        sources.append("filesystem_seedvr2")
    if scanned_restore:
        codeformer_names.extend(scanned_restore)
        sources.append("filesystem_facerestore")

    seed_dit, seed_vae = _split_seedvr2_models(_dedupe_names(seed_names))
    node_seed_dit, node_seed_vae = _split_seedvr2_models(_dedupe_names(node_declared_seed_names))
    # Only expose CodeFormer-ish restore files in this utility dropdown.
    codeformer: list[str] = []
    seen_cf: set[str] = set()
    for name in _dedupe_names(codeformer_names):
        clean = str(name or "").strip().replace("\\", "/")
        key = clean.casefold()
        if not clean or key in seen_cf or "codeformer" not in key:
            continue
        seen_cf.add(key)
        codeformer.append(clean)

    object_info_codeformer = _codeformer_choices_from_object_info(info, node_declared_codeformer_names)
    if not codeformer and object_info_codeformer:
        codeformer.extend(object_info_codeformer)
        sources.append("comfy_object_info_codeformer_choices")

    used_conventional_codeformer_fallback = False
    conventional_codeformer_fallback_reason = ""
    if not codeformer:
        fallback_names, conventional_codeformer_fallback_reason = _codeformer_conventional_fallback(
            info,
            profile,
            seed_models_found=bool(seed_dit or seed_vae),
        )
        if fallback_names:
            codeformer.extend(fallback_names)
            sources.append("conventional_codeformer_filename_fallback")
            if conventional_codeformer_fallback_reason.endswith("_unverified"):
                sources.append("unverified_codeformer_filename_fallback")
            used_conventional_codeformer_fallback = True

    warnings: list[str] = []
    if node_seed_dit and not seed_dit:
        warnings.append("SeedVR2 nodes advertise model names, but no real DiT files were discovered from /models/SEEDVR2 or filesystem scan. Dropdown will stay empty until real files are found.")
    portable_path = str((profile.get("connection", {}) or {}).get("portable_path") or "").strip()
    if not seed_dit:
        warnings.append("No real SeedVR2 DiT models were discovered. Expected files in ComfyUI/models/SEEDVR2/.")
    if not seed_vae:
        warnings.append("No real SeedVR2 VAE models were discovered. Expected ema_vae_fp16.safetensors in ComfyUI/models/SEEDVR2/.")
    if used_conventional_codeformer_fallback:
        if conventional_codeformer_fallback_reason.endswith("_unverified"):
            warnings.append("Comfy did not expose facerestore_models through /models and no local file scan confirmed CodeFormer. Neo is showing the standard codeformer.pth filename fallback because this is a connected Comfy backend; if queueing fails, install/enable the FaceRestore CodeFormer nodes or set Portable Path for filesystem confirmation.")
        else:
            warnings.append("Comfy did not expose facerestore_models through /models and no local file scan confirmed CodeFormer. Neo is showing the standard codeformer.pth fallback because CodeFormer restore nodes are installed; keep codeformer.pth in ComfyUI/models/facerestore_models/.")
    elif not codeformer:
        warnings.append("No real CodeFormer restore models were discovered from facerestore model endpoints or configured portable_path filesystem scan.")
    if (not seed_dit or not seed_vae or (not codeformer or used_conventional_codeformer_fallback)) and not portable_path:
        warnings.append("Portable Path is blank on the selected Comfy backend profile. Set Admin > Backends > ComfyUI Portable > Portable Path to your ComfyUI_windows_portable or ComfyUI folder so Neo can scan local model folders when Comfy does not expose them through /models.")

    return {
        "ok": True,
        "seedvr2_dit_models": seed_dit,
        "seedvr2_vae_models": seed_vae,
        "codeformer_models": codeformer,
        "defaults": {
            "seedvr2_dit_model": seed_dit[0] if seed_dit else "",
            "seedvr2_vae_model": seed_vae[0] if seed_vae else "",
        },
        "diagnostics": {
            "node_declared_seedvr2_dit_models": node_seed_dit,
            "node_declared_seedvr2_vae_models": node_seed_vae,
            "node_declared_codeformer_models": _dedupe_names(node_declared_codeformer_names),
            "object_info_codeformer_models": object_info_codeformer,
            "codeformer_conventional_fallback_used": used_conventional_codeformer_fallback,
            "codeformer_conventional_fallback_reason": conventional_codeformer_fallback_reason,
            "codeformer_restore_nodes_detected": _has_codeformer_restore_nodes(info),
            "basic_comfy_image_nodes_detected": _has_basic_comfy_image_nodes(info),
            "object_info_available": bool(info),
            "real_catalog_policy": "dropdowns_use_comfy_models_endpoint_filesystem_scan_profile_runtime_or_codeformer_connected_backend_fallback",
        },
        "sources": sorted(set(sources)),
        "roots_checked": [str(item) for item in roots],
        "warnings": warnings,
    }


def _assert_selected_models_exist(root_dir: Path, provider: Any, profile: dict[str, Any], settings: dict[str, Any]) -> None:
    catalog = _build_model_catalog(root_dir, provider, profile)
    if settings.get("upscale_engine") == "seedvr2":
        dit_models = {str(item).casefold() for item in catalog.get("seedvr2_dit_models") or []}
        vae_models = {str(item).casefold() for item in catalog.get("seedvr2_vae_models") or []}
        dit = str(settings.get("seedvr2_dit_model") or "").casefold()
        vae = str(settings.get("seedvr2_vae_model") or "").casefold()
        if dit_models and dit not in dit_models:
            raise HTTPException(status_code=400, detail=f"Selected SeedVR2 DiT model was not discovered in Comfy: {settings.get('seedvr2_dit_model')}. Refresh Image Upscale models or check ComfyUI/models/SEEDVR2/.")
        if vae_models and vae not in vae_models:
            raise HTTPException(status_code=400, detail=f"Selected SeedVR2 VAE model was not discovered in Comfy: {settings.get('seedvr2_vae_model')}. Refresh Image Upscale models or check ComfyUI/models/SEEDVR2/.")
    if settings.get("restore_assist") == "codeformer":
        restore_models = {str(item).casefold() for item in catalog.get("codeformer_models") or []}
        restore = str(settings.get("restore_model") or "").casefold()
        if restore_models and restore not in restore_models:
            raise HTTPException(status_code=400, detail=f"Selected CodeFormer model was not discovered in Comfy: {settings.get('restore_model')}. Refresh Image Upscale models or check ComfyUI/models/facerestore_models/.")

def _parse_settings(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Image Upscale settings_json: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Image Upscale settings_json must be a JSON object.")
    return data


def _safe_upload_name(upload: UploadFile, index: int) -> tuple[str, str]:
    original = Path(upload.filename or f"image_{index}.png").name
    suffix = Path(original).suffix.lower() or ".png"
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported image type. Use PNG, JPG, JPEG, WEBP, or BMP.")
    return original, suffix


async def _save_upload(upload: UploadFile, root_dir: Path, index: int) -> dict[str, Any]:
    original, suffix = _safe_upload_name(upload, index)
    target_dir = root_dir / "neo_data" / "inputs" / "image_upscale"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = f"upscale_{uuid4().hex[:12]}{suffix}"
    target = target_dir / stored
    with target.open("wb") as handle:
        # UploadFile.file may be backed by SpooledTemporaryFile; shutil keeps this
        # memory-safe for larger files.
        upload.file.seek(0)
        shutil.copyfileobj(upload.file, handle)
    return {
        "filename": original,
        "stored_filename": stored,
        "path": str(target),
        "storage": "neo_data/inputs/image_upscale",
    }



def _read_image_dimensions(path: Path) -> tuple[int, int]:
    """Best-effort image dimension probe for per-file SeedVR2 scale math."""
    try:
        from PIL import Image  # type: ignore
        with Image.open(path) as image:
            width, height = image.size
            return int(width), int(height)
    except Exception:
        return 0, 0


def _settings_for_source_image(settings: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    clean = dict(settings or {})
    width, height = _read_image_dimensions(Path(str(asset.get("path") or "")))
    if width and height:
        clean["seedvr2_source_width"] = width
        clean["seedvr2_source_height"] = height
    if str(clean.get("upscale_engine") or "").strip().lower() == SEEDVR2_ENGINE_ID:
        clean = compute_seedvr2_resolution_contract(clean)
    return normalize_settings(clean)

def _provider_id(provider: Any, profile: dict[str, Any]) -> str:
    return str(profile.get("provider_id") or getattr(getattr(provider, "manifest", None), "provider_id", "")).strip()


def _route_for_profile(provider: Any, profile: dict[str, Any]) -> dict[str, Any]:
    provider_id = _provider_id(provider, profile)
    backend = "comfyui_portable" if provider_id == "comfyui_portable" else provider_id or "unknown"
    return {
        "backend": backend,
        "provider_id": provider_id,
        "workspace": "image",
        "workspace_app": "finish",
        "mode": "image_upscale",
        "family": "any",
        "loader": "any",
    }


def _object_info(provider: Any) -> dict[str, Any]:
    if hasattr(provider, "_get_json"):
        try:
            data = provider._get_json("/object_info")  # noqa: SLF001 - extension bridge to provider-owned Comfy adapter.
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _available_nodes(provider: Any) -> set[str] | None:
    info = _object_info(provider)
    if not info:
        # Unknown object_info should not block queue preflight; Comfy itself will
        # validate the prompt. This preserves V1's pragmatic behavior when object
        # discovery is unavailable.
        return None
    return {str(key) for key in info.keys()}


def _assert_route_ready(provider: Any, profile: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    route = _route_for_profile(provider, profile)
    provider_id = _provider_id(provider, profile)
    if provider_id not in SUPPORTED_COMFY_BACKENDS:
        support = support_for_route(route)
        raise HTTPException(status_code=400, detail=support.get("reason") or "Image Upscale requires a Comfy-compatible backend profile.")
    nodes = _available_nodes(provider)
    support = support_for_route(route) if nodes is None else support_with_nodes(route, available_nodes=nodes)
    if support.get("state") not in ACTIVE_ROUTE_STATES:
        raise HTTPException(status_code=400, detail=support.get("reason") or "Image Upscale route is gated.")
    node_status = support.get("node_status") or {}
    optional = node_status.get("optional_groups") if isinstance(node_status, dict) else {}
    if settings.get("upscale_model") and isinstance(optional, dict):
        model_group = optional.get("model_upscale") or {}
        if model_group and not model_group.get("ready", True):
            missing = ", ".join(model_group.get("missing_nodes") or [])
            raise HTTPException(status_code=400, detail=f"Selected upscaler model requires missing Comfy node(s): {missing}")
    if settings.get("restore_assist") == "codeformer" and isinstance(optional, dict):
        restore_group = optional.get("codeformer_restore") or {}
        if restore_group and not restore_group.get("ready", True):
            missing = ", ".join(restore_group.get("missing_nodes") or [])
            raise HTTPException(status_code=400, detail=f"CodeFormer restore requires missing Comfy node(s): {missing}. Put CodeFormer models in ComfyUI/models/facerestore_models/.")
    if settings.get("upscale_engine") == "seedvr2" and isinstance(optional, dict):
        seedvr2_group = optional.get("seedvr2_experimental") or {}
        if seedvr2_group and not seedvr2_group.get("ready", True):
            missing = ", ".join(seedvr2_group.get("missing_nodes") or [])
            raise HTTPException(status_code=400, detail=f"SeedVR2 experimental engine requires missing Comfy node(s): {missing}. Install ComfyUI-SeedVR2_VideoUpscaler and use ComfyUI/models/SEEDVR2/.")
    return support


def _upload_to_comfy(provider: Any, local_path: Path) -> str:
    if hasattr(provider, "_upload_image_to_comfy_input"):
        return provider._upload_image_to_comfy_input(str(local_path))  # noqa: SLF001
    base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("Selected provider cannot upload images to Comfy input.")
    boundary = f"----NeoStudioBoundary{uuid4().hex}"
    filename = f"neo_upscale_{uuid4().hex[:8]}_{local_path.name}"
    content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode())
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.extend(local_path.read_bytes())
    body.extend(f"\r\n--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="type"\r\n\r\ninput')
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    req = urlrequest.Request(
        f"{base_url}/upload/image",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=max(float(getattr(provider, "timeout", 3)), 10)) as response:
        raw = response.read().decode("utf-8").strip()
        if raw:
            try:
                payload = json.loads(raw)
                return str(payload.get("name") or filename)
            except json.JSONDecodeError:
                return filename
    return filename


def _post_prompt(provider: Any, prompt: dict[str, Any], client_id: str) -> dict[str, Any]:
    payload = {"prompt": prompt, "client_id": client_id}
    if hasattr(provider, "_post_json"):
        result = provider._post_json("/prompt", payload)  # noqa: SLF001
        return result if isinstance(result, dict) else {}
    raise RuntimeError("Selected provider cannot queue Comfy prompts.")



def _workflow_class_types(workflow: dict[str, Any]) -> list[str]:
    classes: list[str] = []
    for node in workflow.values():
        if isinstance(node, dict):
            class_type = str(node.get("class_type") or "").strip()
            if class_type:
                classes.append(class_type)
    return classes


def _assert_standalone_image_upscale_workflow(workflow: dict[str, Any], settings: dict[str, Any]) -> None:
    """Guard the queue route against leaking into the normal image compiler.

    Image Upscale is a standalone utility route. SeedVR2 especially must compile
    to a tiny source-image graph, not SDXL/img2img + High-Res/ADetailer patches.
    This guard makes that contract fail loudly instead of fake-completing.
    """
    classes = _workflow_class_types(workflow)
    if not classes:
        raise RuntimeError("Image Upscale standalone route produced an empty workflow.")
    forbidden_generation_nodes = {
        "CheckpointLoaderSimple",
        "CheckpointLoader",
        "KSampler",
        "KSamplerAdvanced",
        "UltimateSDUpscale",
        "UltimateSDUpscaleCustomSample",
        "SEGSDetailer",
        "FaceDetailer",
        "NeoSceneDirectorV053",
        "NeoSceneDirectorV052",
        "NeoSceneDirectorV051",
        "NeoSceneDirectorV05",
        "LoraLoader",
        "IPAdapter",
        "IPAdapterFaceID",
        "ControlNetApply",
        "ControlNetApplyAdvanced",
    }
    leaked = sorted({item for item in classes if item in forbidden_generation_nodes})
    if leaked:
        raise RuntimeError(
            "Image Upscale standalone route leaked normal Image workflow node(s): "
            + ", ".join(leaked)
            + ". Use /api/extensions/image-upscale/queue with the standalone Image Upscale compiler."
        )
    if str(settings.get("upscale_engine") or "").strip().lower() == SEEDVR2_ENGINE_ID:
        required = {"SeedVR2LoadDiTModel", "SeedVR2LoadVAEModel", "SeedVR2VideoUpscaler"}
        missing = sorted(required.difference(classes))
        if missing:
            raise RuntimeError(
                "SeedVR2 route failed: standalone Image Upscale workflow is missing SeedVR2 node(s): "
                + ", ".join(missing)
            )
        disallowed_seedvr2 = sorted({item for item in classes if item in {"ImageUpscaleWithModel", "ImageScaleBy", "ImageScale", "UpscaleModelLoader"}})
        if disallowed_seedvr2:
            raise RuntimeError(
                "SeedVR2 route failed: workflow mixed SeedVR2 with non-SeedVR2 upscale node(s): "
                + ", ".join(disallowed_seedvr2)
            )

def _context_for_job(
    *,
    prompt_id: str,
    profile_id: str,
    profile: dict[str, Any],
    route: dict[str, Any],
    payload_block: dict[str, Any],
    metadata: dict[str, Any],
    normalized: dict[str, Any],
    notes: list[str],
    asset: dict[str, Any],
) -> dict[str, Any]:
    return {
        "job_id": prompt_id,
        "profile_id": profile_id,
        "backend_profile_id": profile_id,
        "provider_id": profile.get("provider_id") or "",
        "backend_output_root": "",
        "subtab": "finish",
        "mode": "image_upscale_finish",
        "prompt": "",
        "positive_prompt": "",
        "negative_prompt": "",
        "params": {**normalized, "compile_notes": notes, "source_asset": asset},
        "model": {"family": "standalone", "loader": "none", "model": "", "vae": ""},
        "extensions": {
            "used": [build_image_upscale_extension_usage(
                params=normalized,
                route=route,
                node_status=metadata.get("node_status") if isinstance(metadata.get("node_status"), dict) else {},
            )],
            "payloads": {EXTENSION_ID: payload_block},
            "workflow_patches": [],
            "validation": [{"extension_id": EXTENSION_ID, "level": "info", "message": "Standalone Image Upscale utility graph queued."}],
            "replay_payloads": {EXTENSION_ID: metadata.get("replay_payload") or {}},
            "assistant_summaries": {EXTENSION_ID: metadata.get("assistant_summary") or ""},
            "memory_events": {EXTENSION_ID: metadata},
        },
    }


def create_image_upscale_api_router(
    root_dir: Path,
    *,
    profile_provider_resolver: ProfileProviderResolver,
    model_catalog_provider_resolver: ProfileProviderResolver | None = None,
    context_recorder: ContextRecorder | None = None,
    workflow_builder: WorkflowBuilder | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/extensions/image-upscale", tags=["image-upscale"])
    workflow_builder = workflow_builder or build_image_upscale_workflow
    catalog_resolver = model_catalog_provider_resolver or profile_provider_resolver

    @router.get("/models")
    async def image_upscale_models(profile_id: str = "") -> dict[str, Any]:
        if not profile_id:
            raise HTTPException(status_code=400, detail="profile_id is required for Image Upscale model discovery.")
        try:
            provider, profile = catalog_resolver(profile_id)
            catalog = _build_model_catalog(root_dir, provider, profile)
            catalog["profile_id"] = profile_id
            catalog["extension_id"] = EXTENSION_ID
            return catalog
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - model discovery should return a readable UI error.
            raise HTTPException(status_code=500, detail=f"Image Upscale model catalog scan failed: {exc}") from exc

    @router.post("/queue")
    async def queue_image_upscale(
        profile_id: str = Form(""),
        settings_json: str = Form("{}"),
        image_files: list[UploadFile] = File(default=[]),
        image_file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        try:
            raw_settings = _parse_settings(settings_json)
            settings = normalize_settings(raw_settings)
            validation = validate_payload_settings(settings, require_source=True, source_images=[f.filename for f in image_files] + ([image_file.filename] if image_file else []))
        except PayloadContractError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if image_file is not None:
            image_files = [*image_files, image_file]
        if not image_files:
            raise HTTPException(status_code=400, detail="Pick at least one source image for Image Upscale.")
        if not validation.get("ok"):
            raise HTTPException(status_code=400, detail="; ".join(validation.get("errors") or ["Image Upscale validation failed."]))
        if not profile_id:
            raise HTTPException(status_code=400, detail="profile_id is required for Image Upscale.")

        provider, profile = profile_provider_resolver(profile_id)
        support = _assert_route_ready(provider, profile, settings)
        _assert_selected_models_exist(root_dir, provider, profile, settings)
        route = {**support.get("route", {}), "provider_id": _provider_id(provider, profile), "route_state": support.get("state")}

        queued: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for index, upload in enumerate(image_files, start=1):
            try:
                asset = await _save_upload(upload, root_dir, index)
                per_source_settings = _settings_for_source_image(settings, asset)
                per_source_asset = {**asset, "width": per_source_settings.get("seedvr2_source_width", 0), "height": per_source_settings.get("seedvr2_source_height", 0)}
                per_source_payload_block = build_payload_block(
                    per_source_settings,
                    enabled=True,
                    route=route,
                    source_images=[per_source_asset],
                )
                comfy_name = _upload_to_comfy(provider, Path(asset["path"]))
                workflow, normalized, notes = workflow_builder(comfy_name, per_source_settings)
                if not isinstance(workflow, dict) or not workflow:
                    raise RuntimeError("Image Upscale workflow builder returned an empty graph.")
                _assert_standalone_image_upscale_workflow(workflow, normalized if isinstance(normalized, dict) else per_source_settings)
                client_id = f"neo-image-upscale-{uuid4().hex[:10]}"
                response = _post_prompt(provider, workflow, client_id)
                prompt_id = str(response.get("prompt_id") or f"image-upscale-{uuid4().hex[:10]}")
                metadata = build_image_upscale_metadata(
                    route=route,
                    params=normalized,
                    assets={"source_images": [{**asset, "width": normalized.get("seedvr2_source_width", 0), "height": normalized.get("seedvr2_source_height", 0)}], "comfy_source_image_name": comfy_name},
                    payload_block=per_source_payload_block,
                    workflow_summary="; ".join(notes),
                    node_status=support.get("node_status") if isinstance(support.get("node_status"), dict) else {},
                    compile_notes=notes,
                )
                if context_recorder:
                    context_recorder(prompt_id, _context_for_job(
                        prompt_id=prompt_id,
                        profile_id=profile_id,
                        profile=profile,
                        route=route,
                        payload_block=per_source_payload_block,
                        metadata=metadata,
                        normalized=normalized,
                        notes=notes,
                        asset=asset,
                    ))
                queued.append({
                    "job_id": prompt_id,
                    "prompt_id": prompt_id,
                    "profile_id": profile_id,
                    "provider_id": route.get("provider_id"),
                    "client_id": client_id,
                    "source": asset["filename"],
                    "stored_source": asset["stored_filename"],
                    "comfy_source_image_name": comfy_name,
                    "compile_notes": notes,
                    "source_dimensions": {"width": normalized.get("seedvr2_source_width", 0), "height": normalized.get("seedvr2_source_height", 0)},
                    "computed_output_dimensions": {"width": normalized.get("seedvr2_output_width", 0), "height": normalized.get("seedvr2_output_height", 0)},
                    "metadata": metadata,
                })
            except HTTPException as exc:
                failed.append({"index": index, "name": upload.filename or f"image_{index}", "error": str(exc.detail)})
            except NotImplementedError as exc:
                failed.append({"index": index, "name": upload.filename or f"image_{index}", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                failed.append({"index": index, "name": upload.filename or f"image_{index}", "error": str(exc)})

        if not queued:
            detail = failed[0]["error"] if failed else "Could not queue Image Upscale."
            status = 501 if "workflow builder" in detail.lower() or "not active" in detail.lower() else 502
            raise HTTPException(status_code=status, detail=detail)
        return {
            "ok": True,
            "extension_id": EXTENSION_ID,
            "endpoint": QUEUE_ENDPOINT,
            "profile_id": profile_id,
            "route": route,
            "jobs": queued,
            "queued_count": len(queued),
            "failed": failed,
            "failed_count": len(failed),
            "message": f"Queued Image Upscale for {len(queued)} image{'s' if len(queued) != 1 else ''}.",
        }

    return router


def register_image_upscale_api_routes(
    app: FastAPI,
    root_dir: Path,
    *,
    profile_provider_resolver: ProfileProviderResolver,
    model_catalog_provider_resolver: ProfileProviderResolver | None = None,
    context_recorder: ContextRecorder | None = None,
    workflow_builder: WorkflowBuilder | None = None,
) -> APIRouter:
    router = create_image_upscale_api_router(
        root_dir,
        profile_provider_resolver=profile_provider_resolver,
        model_catalog_provider_resolver=model_catalog_provider_resolver,
        context_recorder=context_recorder,
        workflow_builder=workflow_builder,
    )
    app.include_router(router)
    return router
