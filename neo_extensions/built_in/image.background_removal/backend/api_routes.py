from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Callable
from urllib import request as urlrequest
from uuid import uuid4

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from neo_extensions.built_in.adetailer.backend.detection_preview import preview_detailer_detections
from neo_extensions.built_in.adetailer.backend.model_catalog import (
    configured_detailer_backend_details,
    list_detailer_models,
    resolve_detailer_model_file,
)

from .constants import (
    COMMERCIAL_PROVIDER_IDS,
    DETECT_SUBJECTS_ENDPOINT,
    EXTENSION_ID,
    IMAGE_SUFFIXES,
    MODEL_FOLDER_NAMES,
    MODELS_ENDPOINT,
    MAX_SAM_SUBJECTS,
    NATIVE_MODEL_IDS,
    NATIVE_PRESET_MODELS,
    PRESET_MODEL_CANDIDATES,
    QUEUE_ENDPOINT,
    SOURCE_FILE_ENDPOINT,
    SUPPORTED_COMFY_BACKENDS,
    SAM_MODEL_VARIANTS,
    SAM_REFINEMENT_MODEL_IDS,
)
from .engine_resolver import EngineResolution, resolve_engine
from .metadata import build_background_removal_extension_usage, build_background_removal_metadata
from .native_rembg import native_rembg_status, run_native_rembg
from .native_sam import run_native_sam_selection
from .payload_schema import PayloadContractError, build_payload_block, normalize_settings, validate_payload_settings
from .public_hygiene import portable_model_identifiers, portable_model_identifier, public_model_catalog
from .shared_sam import (
    build_shared_sam_catalog,
    resolve_person_detector_choice,
    resolve_shared_sam,
    subjects_support_comfy,
)
from .workflow import build_background_removal_workflow

ProfileProviderResolver = Callable[[str], tuple[Any, dict[str, Any]]]
DetectorBackendResolver = Callable[[str | None], dict[str, Any]]
ContextRecorder = Callable[[str, dict[str, Any]], None]
WorkflowBuilder = Callable[..., tuple[dict[str, Any], dict[str, Any], list[str]]]
NativeRunner = Callable[..., dict[str, Any]]
NativeResultPersister = Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]]

SEGMENT_REQUIRED_NODES = {
    "LoadImage",
    "LoadRembgByBiRefNetModel",
    "RembgByBiRefNetAdvanced",
    "MaskToImage",
    "SaveImage",
}
REFINEMENT_REQUIRED_NODES = {
    "LoadImage",
    "ImageToMask",
    "MaskToImage",
    "SaveImage",
}
OPTIONAL_NODES = {"PreviewImage", "GrowMask", "FeatherMask", "BlurFusionForegroundEstimation", "JoinImageWithAlpha"}
REQUIRED_NODES = SEGMENT_REQUIRED_NODES
MODEL_SUFFIXES = {".safetensors", ".pth", ".pt"}


def _parse_settings(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Background Removal settings_json: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Background Removal settings_json must be a JSON object.")
    return data


def _safe_upload_name(upload: UploadFile, index: int) -> tuple[str, str]:
    original = Path(upload.filename or f"image_{index}.png").name
    suffix = Path(original).suffix.lower() or ".png"
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported image type. Use PNG, JPG, JPEG, WEBP, or BMP.")
    return original, suffix


async def _save_upload(upload: UploadFile, root_dir: Path, index: int) -> dict[str, Any]:
    original, suffix = _safe_upload_name(upload, index)
    target_dir = root_dir / "neo_data" / "inputs" / "background_removal"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = f"background_remove_{uuid4().hex[:12]}{suffix}"
    target = target_dir / stored
    with target.open("wb") as handle:
        upload.file.seek(0)
        shutil.copyfileobj(upload.file, handle)
    width = height = 0
    image_format = ""
    try:
        from PIL import Image  # type: ignore

        with Image.open(target) as image:
            width, height = image.size
            image_format = str(image.format or suffix.lstrip(".")).upper()
    except Exception:
        pass
    return {
        "filename": original,
        "stored_filename": stored,
        "path": str(target),
        "storage": "neo_data/inputs/background_removal",
        "width": int(width),
        "height": int(height),
        "format": image_format,
    }


async def _save_mask_upload(upload: UploadFile, root_dir: Path) -> dict[str, Any]:
    original, suffix = _safe_upload_name(upload, 1)
    target_dir = root_dir / "neo_data" / "inputs" / "background_removal" / "masks"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = f"background_mask_review_{uuid4().hex[:12]}{suffix}"
    target = target_dir / stored
    with target.open("wb") as handle:
        upload.file.seek(0)
        shutil.copyfileobj(upload.file, handle)
    width = height = 0
    image_format = ""
    try:
        from PIL import Image  # type: ignore

        with Image.open(target) as image:
            width, height = image.size
            image_format = str(image.format or suffix.lstrip(".")).upper()
            if width <= 0 or height <= 0:
                raise ValueError("review mask has invalid dimensions")
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Reviewed mask could not be read as an image: {exc}") from exc
    return {
        "filename": original,
        "stored_filename": stored,
        "path": str(target),
        "storage": "neo_data/inputs/background_removal/masks",
        "width": int(width),
        "height": int(height),
        "format": image_format,
        "kind": "background_removal_review_mask",
    }


def _provider_id(provider: Any, profile: dict[str, Any]) -> str:
    return str(profile.get("provider_id") or getattr(getattr(provider, "manifest", None), "provider_id", "")).strip()


def _route_for_profile(provider: Any | None, profile: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = settings if isinstance(settings, dict) else {}
    workflow_mode = str(clean.get("workflow_mode") or "segment")
    resolved_engine = str(clean.get("resolved_engine") or clean.get("engine") or "comfy_birefnet")
    if workflow_mode == "refine_mask":
        resolved_engine = "comfy_birefnet"
    native_route = resolved_engine in {"native_rembg", "native_sam"}
    commercial_route = resolved_engine == "commercial_api"
    provider_id = "neo_native" if native_route else _provider_id(provider, profile)
    if workflow_mode == "refine_mask":
        mode = "background_removal_refine"
        loader = "birefnet_refinement"
    elif workflow_mode == "interactive_sam":
        mode = "background_removal_sam_select"
        loader = "sam_impact_shared" if resolved_engine == "comfy_sam" else "sam_onnx"
    else:
        mode = "background_removal_commercial" if commercial_route else "background_removal"
        loader = "commercial_api" if commercial_route else ("rembg_onnx" if resolved_engine == "native_rembg" else "birefnet")
    return {
        "backend": "neo_native" if native_route else ("commercial_api" if commercial_route else ("comfyui_portable" if provider_id == "comfyui_portable" else provider_id or "unknown")),
        "provider_id": provider_id,
        "workspace": "image",
        "workspace_app": "finish",
        "mode": mode,
        "family": "standalone",
        "loader": loader,
        "workflow_mode": workflow_mode,
        "engine": resolved_engine,
    }


def _object_info(provider: Any) -> dict[str, Any]:
    if hasattr(provider, "_get_json"):
        try:
            payload = provider._get_json("/object_info")  # noqa: SLF001
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_choices(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item).strip().replace("\\", "/") for item in first if str(item).strip()]
    if all(isinstance(item, str) for item in value):
        return [str(item).strip().replace("\\", "/") for item in value if str(item).strip()]
    return []


def _node_choices(info: dict[str, Any], node_name: str, *input_names: str) -> list[str]:
    node = info.get(node_name) if isinstance(info.get(node_name), dict) else {}
    input_block = node.get("input") if isinstance(node.get("input"), dict) else {}
    required = input_block.get("required") if isinstance(input_block.get("required"), dict) else {}
    optional = input_block.get("optional") if isinstance(input_block.get("optional"), dict) else {}
    names: list[str] = []
    seen: set[str] = set()
    for input_name in input_names:
        for item in _extract_choices(required.get(input_name)) + _extract_choices(optional.get(input_name)):
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                names.append(item)
    return names


def _live_ultralytics_detector_choices(info: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Merge the exact model choices exposed by Impact Pack detector providers."""

    raw: list[str] = []
    seen: set[str] = set()
    for node_name in info:
        if "ultralyticsdetectorprovider" not in str(node_name).casefold():
            continue
        for item in _node_choices(info, node_name, "model_name", "model", "detector_model"):
            normalized = str(item or "").strip().replace("\\", "/")
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            raw.append(normalized)
    bbox: list[str] = []
    segm: list[str] = []
    for item in raw:
        folded = item.casefold()
        stem = Path(item).stem.casefold()
        if folded.startswith("segm/") or "/segm/" in folded or "seg" in stem or "mask" in stem:
            segm.append(item)
        else:
            bbox.append(item)
    return bbox, segm


def _extract_model_endpoint_payload(payload: Any) -> list[str]:
    values: list[Any] = []
    if isinstance(payload, list):
        values.extend(payload)
    elif isinstance(payload, dict):
        for key in ("models", "files", "items", "names", "data", "birefnet", "BiRefNet", "BIREFNET"):
            value = payload.get(key)
            if isinstance(value, list):
                values.extend(value)
        if not values:
            for key, value in payload.items():
                if isinstance(value, list):
                    values.extend(value)
                elif Path(str(key)).suffix.lower() in MODEL_SUFFIXES:
                    values.append(key)
    names: list[str] = []
    seen: set[str] = set()
    for item in values:
        raw = item.get("name") or item.get("filename") or item.get("file") or item.get("path") if isinstance(item, dict) else item
        name = str(raw or "").strip().replace("\\", "/")
        if not name or Path(name).suffix.lower() not in MODEL_SUFFIXES:
            continue
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _query_model_folders(provider: Any) -> list[str]:
    if not hasattr(provider, "_get_json"):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for folder in MODEL_FOLDER_NAMES:
        try:
            payload = provider._get_json(f"/models/{folder}")  # noqa: SLF001
        except Exception:
            continue
        for name in _extract_model_endpoint_payload(payload):
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _candidate_comfy_roots(root_dir: Path, profile: dict[str, Any]) -> list[Path]:
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    raw = [
        connection.get("portable_path"),
        connection.get("comfy_root_path"),
        connection.get("comfyui_path"),
        connection.get("models_path"),
        runtime.get("portable_path"),
        runtime.get("comfy_root_path"),
        runtime.get("comfyui_path"),
        runtime.get("models_path"),
        root_dir,
        Path.cwd(),
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        if not item:
            continue
        try:
            base = Path(str(item)).expanduser()
        except Exception:
            continue
        candidates = [base, base / "ComfyUI"]
        if base.name.casefold() == "models":
            candidates.extend([base.parent, base.parent / "ComfyUI"])
        if base.name.casefold() == "birefnet":
            candidates.extend([base.parent.parent, base.parent.parent / "ComfyUI"])
        for candidate in candidates:
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                roots.append(candidate)
    return roots


def _scan_model_folders(root_dir: Path, profile: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    folders: list[Path] = []
    for root in _candidate_comfy_roots(root_dir, profile):
        folders.extend([root / "models" / "BiRefNet", root / "models" / "birefnet"])
        if root.name.casefold() == "models":
            folders.extend([root / "BiRefNet", root / "birefnet"])
        if root.name.casefold() == "birefnet":
            folders.append(root)
    checked: set[str] = set()
    for folder in folders:
        key = str(folder)
        if key in checked:
            continue
        checked.add(key)
        if not folder.exists() or not folder.is_dir():
            continue
        try:
            files = sorted((item for item in folder.rglob("*") if item.is_file() and item.suffix.lower() in MODEL_SUFFIXES), key=lambda item: str(item).casefold())
        except Exception:
            continue
        for path in files:
            try:
                name = path.relative_to(folder).as_posix()
            except Exception:
                name = path.name
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                names.append(name)
    return names


def _build_model_catalog(
    root_dir: Path,
    provider: Any,
    profile: dict[str, Any],
    detector_backend_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detailer_backend = configured_detailer_backend_details(detector_backend_details or profile)
    backend_supplied_object_info = "object_info" in detailer_backend
    info = detailer_backend.get("object_info") if isinstance(detailer_backend.get("object_info"), dict) else {}
    if not info and not backend_supplied_object_info:
        info = _object_info(provider)
    object_models = portable_model_identifiers(_node_choices(info, "LoadRembgByBiRefNetModel", "model"), "birefnet")
    endpoint_models = portable_model_identifiers(_query_model_folders(provider), "birefnet")
    filesystem_models = _scan_model_folders(root_dir, profile)
    models: list[str] = []
    seen: set[str] = set()
    sources: list[str] = []
    for source_name, items in (("comfy_object_info", object_models), ("comfy_models_endpoint", endpoint_models), ("filesystem_scan", filesystem_models)):
        if items:
            sources.append(source_name)
        for name in items:
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                models.append(name)
    available_nodes = sorted(info.keys()) if info else []
    available_set = set(available_nodes)
    missing_nodes = sorted(SEGMENT_REQUIRED_NODES.difference(available_set)) if info else []
    missing_refinement_nodes = sorted(REFINEMENT_REQUIRED_NODES.difference(available_set)) if info else []
    live_sam_models = _node_choices(
        info,
        "SAMLoader",
        "model_name",
        "model",
        "sam_model",
        "sam_model_name",
    )
    live_sam_models = portable_model_identifiers([
        name for name in live_sam_models
        if Path(name).suffix.casefold() in {".pt", ".pth", ".safetensors"}
    ], "sam")
    live_bbox_models, live_segm_models = _live_ultralytics_detector_choices(info)
    live_bbox_models = portable_model_identifiers(live_bbox_models, "bbox")
    live_segm_models = portable_model_identifiers(live_segm_models, "segm")
    shared_sam = build_shared_sam_catalog(
        available_nodes=available_nodes,
        birefnet_models=models,
        live_sam_models=live_sam_models,
        live_bbox_models=live_bbox_models,
        live_segm_models=live_segm_models,
        backend_details=detailer_backend,
    )
    return {
        "models": models,
        "sources": sources,
        "object_info_available": bool(info),
        "available_nodes": available_nodes,
        "required_nodes": sorted(SEGMENT_REQUIRED_NODES),
        "refinement_required_nodes": sorted(REFINEMENT_REQUIRED_NODES),
        "optional_nodes": sorted(OPTIONAL_NODES),
        "missing_nodes": missing_nodes,
        "missing_refinement_nodes": missing_refinement_nodes,
        "nodes_ready": not missing_nodes if info else None,
        "refinement_nodes_ready": not missing_refinement_nodes if info else None,
        "model_folder": "ComfyUI/models/BiRefNet/",
        "presets": {key: list(value) for key, value in PRESET_MODEL_CANDIDATES.items()},
        "native": native_rembg_status(),
        "native_models": list(NATIVE_MODEL_IDS),
        "native_preset_models": dict(NATIVE_PRESET_MODELS),
        "engines": ["smart", "comfy_birefnet", "native_rembg", "native_sam", "comfy_sam", "commercial_api"],
        "shared_sam": shared_sam,
    }


def _resolve_model_for_preset(settings: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    clean = dict(settings)
    models = [str(item) for item in catalog.get("models") or [] if str(item or "").strip()]
    selected = str(clean.get("model") or "").strip()
    by_casefold = {item.casefold(): item for item in models}
    if selected:
        if models and selected.casefold() not in by_casefold:
            raise HTTPException(status_code=400, detail=f"Selected BiRefNet model was not discovered in Comfy: {selected}. Refresh models or check ComfyUI/models/BiRefNet/.")
        clean["model"] = by_casefold.get(selected.casefold(), selected)
        return clean
    preset = str(clean.get("preset") or "smart_auto")
    for candidate in PRESET_MODEL_CANDIDATES.get(preset, PRESET_MODEL_CANDIDATES["smart_auto"]):
        actual = by_casefold.get(candidate.casefold())
        if actual:
            clean["model"] = actual
            return clean
    if len(models) == 1:
        clean["model"] = models[0]
        return clean
    if not models:
        raise HTTPException(status_code=400, detail="No BiRefNet models were discovered. Install ComfyUI_BiRefNet_ll and place a model in ComfyUI/models/BiRefNet/.")
    raise HTTPException(status_code=400, detail=f"Choose an installed BiRefNet model for the {preset.replace('_', ' ')} preset.")


def _assert_route_ready(provider: Any, profile: dict[str, Any], settings: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    provider_id = _provider_id(provider, profile)
    if provider_id not in SUPPORTED_COMFY_BACKENDS:
        raise HTTPException(status_code=400, detail="Background Removal requires a ComfyUI or ComfyUI Portable backend profile.")
    if not catalog.get("object_info_available"):
        raise HTTPException(status_code=400, detail="Background Removal could not verify the Comfy node catalog. Check the backend connection, restart ComfyUI, and refresh models.")

    workflow_mode = str(settings.get("workflow_mode") or "segment")
    available_nodes = set(catalog.get("available_nodes") or [])
    required = set(REFINEMENT_REQUIRED_NODES if workflow_mode == "refine_mask" else SEGMENT_REQUIRED_NODES)
    expand = int(settings.get("mask_expand") or 0)
    feather = int(settings.get("mask_feather") or 0)
    if expand:
        required.add("GrowMask")
    if feather:
        required.add("FeatherMask")
    needs_recompose = workflow_mode == "refine_mask" or bool(expand or feather) or not settings.get("foreground_estimation")
    if needs_recompose:
        required.add("BlurFusionForegroundEstimation" if settings.get("foreground_estimation") else "JoinImageWithAlpha")
    missing = sorted(required.difference(available_nodes))
    if missing:
        label = "Mask Review refinement" if workflow_mode == "refine_mask" else "BiRefNet Background Removal"
        raise HTTPException(
            status_code=400,
            detail=label + " requires missing Comfy node(s): " + ", ".join(missing) + ". Install/update ComfyUI_BiRefNet_ll or update ComfyUI built-in mask nodes, then restart ComfyUI.",
        )
    return {
        "state": "available",
        "route": _route_for_profile(provider, profile, settings),
        "node_status": {
            "ready": not missing,
            "workflow_mode": workflow_mode,
            "available_nodes": sorted(available_nodes),
            "missing_nodes": missing,
            "required_nodes": sorted(required),
        },
    }


def _upload_to_comfy(provider: Any, local_path: Path) -> str:
    if hasattr(provider, "_upload_image_to_comfy_input"):
        return provider._upload_image_to_comfy_input(str(local_path))  # noqa: SLF001
    base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("Selected provider cannot upload images to Comfy input.")
    boundary = f"----NeoStudioBoundary{uuid4().hex}"
    filename = f"neo_background_remove_{uuid4().hex[:8]}_{local_path.name}"
    content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode())
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.extend(local_path.read_bytes())
    body.extend(f"\r\n--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="type"\r\n\r\ninput')
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    req = urlrequest.Request(f"{base_url}/upload/image", data=bytes(body), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urlrequest.urlopen(req, timeout=max(float(getattr(provider, "timeout", 3)), 10)) as response:
        raw = response.read().decode("utf-8").strip()
        if raw:
            try:
                payload = json.loads(raw)
                return str(payload.get("name") or filename)
            except json.JSONDecodeError:
                pass
    return filename


def _post_prompt(provider: Any, prompt: dict[str, Any], client_id: str) -> dict[str, Any]:
    if hasattr(provider, "_post_json"):
        payload = provider._post_json("/prompt", {"prompt": prompt, "client_id": client_id})  # noqa: SLF001
        return payload if isinstance(payload, dict) else {}
    raise RuntimeError("Selected provider cannot queue Comfy prompts.")


def _assert_standalone_workflow(workflow: dict[str, Any], settings: dict[str, Any]) -> None:
    classes = {str(node.get("class_type") or "") for node in workflow.values() if isinstance(node, dict)}
    workflow_mode = str(settings.get("workflow_mode") or "segment")
    node_map = settings.get("sam_node_map") if isinstance(settings.get("sam_node_map"), dict) else {}
    if workflow_mode == "refine_mask":
        graph_required = {"LoadImage", "ImageToMask", "SaveImage"}
    elif workflow_mode == "interactive_sam" and settings.get("resolved_engine") == "comfy_sam":
        graph_required = {
            "LoadImage",
            str(node_map.get("SAMLoader") or "SAMLoader"),
            str(node_map.get("SAMDetectorCombined") or "SAMDetectorCombined"),
            str(node_map.get("MaskToSEGS") or "MaskToSEGS"),
            str(node_map.get("SolidMask") or "SolidMask"),
            str(node_map.get("MaskComposite") or "MaskComposite"),
            "SaveImage",
        }
    else:
        graph_required = {"LoadImage", "LoadRembgByBiRefNetModel", "RembgByBiRefNetAdvanced", "SaveImage"}
    if settings.get("save_mask"):
        graph_required.add(str(node_map.get("MaskToImage") or "MaskToImage"))
    if int(settings.get("mask_expand") or 0):
        graph_required.add(str(node_map.get("GrowMask") or "GrowMask"))
    if int(settings.get("mask_feather") or 0):
        graph_required.add(str(node_map.get("FeatherMask") or "FeatherMask"))
    if workflow_mode == "interactive_sam" and settings.get("resolved_engine") == "comfy_sam" and settings.get("sam_shared_refine_enabled"):
        graph_required.update({
            str(node_map.get("LoadRembgByBiRefNetModel") or "LoadRembgByBiRefNetModel"),
            str(node_map.get("GetMaskByBiRefNet") or "GetMaskByBiRefNet"),
        })
        if int(settings.get("sam_gate_expand") or 0):
            graph_required.add(str(node_map.get("GrowMask") or "GrowMask"))
        if int(settings.get("sam_gate_feather") or 0):
            graph_required.add(str(node_map.get("FeatherMask") or "FeatherMask"))
    if workflow_mode == "interactive_sam" and settings.get("resolved_engine") == "comfy_sam":
        if settings.get("foreground_estimation") and node_map.get("BlurFusionForegroundEstimation"):
            graph_required.add(str(node_map.get("BlurFusionForegroundEstimation")))
        else:
            graph_required.add(str(node_map.get("JoinImageWithAlpha") or "JoinImageWithAlpha"))
    else:
        graph_required.add("BlurFusionForegroundEstimation" if settings.get("foreground_estimation") else "JoinImageWithAlpha")
        if workflow_mode != "refine_mask" and not int(settings.get("mask_expand") or 0) and not int(settings.get("mask_feather") or 0) and settings.get("foreground_estimation"):
            graph_required.discard("BlurFusionForegroundEstimation")
    missing = graph_required.difference(classes)
    if missing:
        raise RuntimeError("Background Removal workflow is missing node(s): " + ", ".join(sorted(missing)))
    forbidden = {"CheckpointLoaderSimple", "KSampler", "UNETLoader", "CLIPLoader", "VAELoader", "FaceDetailer", "SEGSDetailer"}
    leaked = classes.intersection(forbidden)
    if leaked:
        raise RuntimeError("Background Removal standalone route leaked generation node(s): " + ", ".join(sorted(leaked)))
    if workflow_mode == "refine_mask" and {"LoadRembgByBiRefNetModel", "RembgByBiRefNetAdvanced"}.intersection(classes):
        raise RuntimeError("Mask Review refinement must not rerun BiRefNet segmentation.")
    if not settings.get("save_mask") and "MaskToImage" in classes:
        raise RuntimeError("Background Removal workflow generated a mask output while save_mask is disabled.")


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
    mask_asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow_mode = str(normalized.get("workflow_mode") or "segment")
    params = {**normalized, "compile_notes": notes, "source_asset": asset}
    if mask_asset:
        params["review_mask_asset"] = mask_asset
    if normalized.get("parent_result_id"):
        params["_neo_source_result_id"] = normalized.get("parent_result_id")
    if normalized.get("parent_file_id"):
        params["_neo_source_output_id"] = normalized.get("parent_file_id")
    if workflow_mode in {"refine_mask", "interactive_sam"}:
        params["_neo_preview_action"] = {
            "action": "background_removal_sam_select" if workflow_mode == "interactive_sam" else "background_removal_mask_refine",
            "result_id": normalized.get("parent_result_id") or "",
            "file_id": normalized.get("parent_file_id") or "",
            "source_is_output_image": True,
        }
    return {
        "job_id": prompt_id,
        "profile_id": profile_id,
        "backend_profile_id": profile_id,
        "provider_id": route.get("provider_id") or profile.get("provider_id") or "",
        "backend_output_root": str(normalized.get("native_output_root") or ""),
        "subtab": "finish",
        "mode": "background_removal_refine_finish" if workflow_mode == "refine_mask" else ("background_removal_sam_finish" if workflow_mode == "interactive_sam" else ("background_removal_commercial_finish" if normalized.get("resolved_engine") == "commercial_api" else "background_removal_finish")),
        "prompt": "",
        "positive_prompt": "",
        "negative_prompt": "",
        "params": params,
        "model": {
            "family": "standalone",
            "loader": "birefnet_refinement" if workflow_mode == "refine_mask" else (("sam_impact_shared" if normalized.get("resolved_engine") == "comfy_sam" else "sam_onnx") if workflow_mode == "interactive_sam" else ("commercial_api" if normalized.get("resolved_engine") == "commercial_api" else ("rembg_onnx" if normalized.get("resolved_engine") == "native_rembg" else "birefnet"))),
            "model": normalized.get("resolved_model") or normalized.get("model") or normalized.get("native_model") or normalized.get("commercial_profile_id") or "",
            "vae": "",
        },
        "extensions": {
            "used": [build_background_removal_extension_usage(params=normalized, route=route, node_status=metadata.get("node_status") or {})],
            "payloads": {EXTENSION_ID: payload_block},
            "workflow_patches": [],
            "validation": [{
                "extension_id": EXTENSION_ID,
                "level": "info",
                "message": (
                    "Reviewed-mask refinement graph queued without BiRefNet segmentation."
                    if workflow_mode == "refine_mask"
                    else (
                        "Interactive SAM selection completed with stored multi-subject groups and shared/native SAM routing."
                        if workflow_mode == "interactive_sam"
                        else ("Commercial background-removal API completed after explicit per-run consent." if normalized.get("resolved_engine") == "commercial_api" else ("Native rembg fallback completed." if normalized.get("resolved_engine") == "native_rembg" else "Standalone BiRefNet background-removal graph queued."))
                    )
                ),
            }],
            "replay_payloads": {EXTENSION_ID: metadata.get("replay_payload") or {}},
            "assistant_summaries": {EXTENSION_ID: metadata.get("assistant_summary") or ""},
            "memory_events": {EXTENSION_ID: metadata},
        },
    }


def _call_workflow_builder(
    workflow_builder: WorkflowBuilder,
    source_name: str,
    settings: dict[str, Any],
    mask_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if str(settings.get("workflow_mode") or "segment") == "refine_mask":
        return workflow_builder(source_name, settings, mask_name)
    return workflow_builder(source_name, settings)


def create_background_removal_api_router(
    root_dir: Path,
    *,
    profile_provider_resolver: ProfileProviderResolver,
    model_catalog_provider_resolver: ProfileProviderResolver | None = None,
    detector_backend_resolver: DetectorBackendResolver | None = None,
    context_recorder: ContextRecorder | None = None,
    workflow_builder: WorkflowBuilder | None = None,
    native_runner: NativeRunner | None = None,
    sam_runner: NativeRunner | None = None,
    native_result_persister: NativeResultPersister | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/extensions/background-removal", tags=["background-removal"])
    catalog_resolver = model_catalog_provider_resolver or profile_provider_resolver
    workflow_builder = workflow_builder or build_background_removal_workflow
    native_runner = native_runner or run_native_rembg
    sam_runner = sam_runner or run_native_sam_selection

    @router.get("/models")
    async def background_removal_models(profile_id: str = "") -> dict[str, Any]:
        try:
            if profile_id:
                provider, profile = catalog_resolver(profile_id)
                detector_backend = detector_backend_resolver(profile_id) if detector_backend_resolver is not None else None
                payload = _build_model_catalog(root_dir, provider, profile, detector_backend)
            else:
                payload = {
                    "models": [], "sources": [], "object_info_available": False, "available_nodes": [],
                    "required_nodes": sorted(SEGMENT_REQUIRED_NODES), "refinement_required_nodes": sorted(REFINEMENT_REQUIRED_NODES),
                    "optional_nodes": sorted(OPTIONAL_NODES), "missing_nodes": sorted(SEGMENT_REQUIRED_NODES),
                    "missing_refinement_nodes": sorted(REFINEMENT_REQUIRED_NODES), "nodes_ready": False,
                    "refinement_nodes_ready": False, "model_folder": "ComfyUI/models/BiRefNet/",
                    "presets": {key: list(value) for key, value in PRESET_MODEL_CANDIDATES.items()},
                    "native": native_rembg_status(), "native_models": list(NATIVE_MODEL_IDS),
                    "native_preset_models": dict(NATIVE_PRESET_MODELS), "engines": ["smart", "comfy_birefnet", "native_rembg", "native_sam", "commercial_api"],
                    "sam_variants": list(SAM_MODEL_VARIANTS), "sam_refinement_models": list(SAM_REFINEMENT_MODEL_IDS),
                    "shared_sam": build_shared_sam_catalog(available_nodes=[]),
                }
            payload.update({"ok": True, "extension_id": EXTENSION_ID, "profile_id": profile_id, "endpoint": MODELS_ENDPOINT})
            return public_model_catalog(payload)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Background Removal model scan failed: {exc}") from exc

    @router.post("/detect-subjects")
    async def detect_background_removal_subjects(
        file: UploadFile = File(...),
        settings_json: str = Form("{}"),
        profile_id: str = Form(""),
    ) -> dict[str, Any]:
        raw = await file.read()
        payload = _parse_settings(settings_json)
        detector_type = str(payload.get("sam_detector_type") or "bbox").strip().casefold()
        if detector_type not in {"bbox", "segm"}:
            detector_type = "bbox"
        try:
            resolved_backend = detector_backend_resolver(profile_id or None) if detector_backend_resolver is not None else {"profile_id": profile_id}
            detector_backend = configured_detailer_backend_details(resolved_backend)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Detector profile resolution failed: {type(exc).__name__}.",
            ) from exc
        object_info = detector_backend.get("object_info") if isinstance(detector_backend.get("object_info"), dict) else {}
        local_catalog = list_detailer_models(object_info=object_info, backend_details=detector_backend)
        detector_models = portable_model_identifiers(
            list(local_catalog.get("segm_models") or []) if detector_type == "segm" else list(local_catalog.get("bbox_models") or []),
            detector_type,
        )
        requested_detector = str(payload.get("sam_detector_model") or "").strip()
        resolved_detector = resolve_person_detector_choice(requested_detector, detector_models)
        detector_label = "Segmentation" if detector_type == "segm" else "BBox"
        if not detector_models:
            raise HTTPException(status_code=409, detail=f"No {detector_label} detector models were found for the selected Comfy profile. Refresh the model scan after checking Admin Models.")
        if not resolved_detector:
            raise HTTPException(status_code=409, detail=f"No person-capable {detector_label} detector could be resolved. Choose a person, human, generic YOLO/COCO, or explicit custom one-class model.")
        resolved_model_file = resolve_detailer_model_file(
            resolved_detector,
            detector_type,
            backend_details=detector_backend,
        )
        if resolved_model_file is None:
            raise HTTPException(
                status_code=409,
                detail=f"Detector {Path(resolved_detector).name} is listed by Comfy but is not readable by Neo's local detector runtime. Check the selected profile's Admin Models root or use a manual subject box.",
            )
        detection_settings = {
            "provider": "ultralytics",
            "mode": "person",
            "detector_type": detector_type,
            "detector_model": resolved_detector,
            "strict_detector": True,
            "confidence": payload.get("sam_detection_confidence") if payload.get("sam_detection_confidence") is not None else 0.35,
            "bbox_grow": payload.get("sam_gate_expand") or 0,
            "priority_preset": "crowd_scan",
            "count": 0,
            "top_k": 0,
            "order_mode": "left_to_right",
        }
        try:
            preview = preview_detailer_detections(
                raw,
                detection_settings,
                resolved_model_path=resolved_model_file,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        width = max(1, int(preview.get("image_width") or 1))
        height = max(1, int(preview.get("image_height") or 1))
        rows = [row for row in (preview.get("detections") or []) if isinstance(row, dict) and row.get("selected", True)]
        person_labels = {"person", "people", "human", "humans"}
        recognized_people = [row for row in rows if str(row.get("label") or "").strip().casefold() in person_labels]
        warnings = list(preview.get("warnings") or [])
        if recognized_people:
            rows = recognized_people
        elif rows and any(str(row.get("label") or "").strip().casefold() not in {"", "target"} for row in rows):
            warnings.append("The selected detector returned no recognized person labels; all detector candidates were kept so custom one-class models still work.")
        subjects: list[dict[str, Any]] = []
        for index, row in enumerate(rows[:MAX_SAM_SUBJECTS], start=1):
            x1 = max(0.0, min(1.0, float(row.get("x") or 0) / width))
            y1 = max(0.0, min(1.0, float(row.get("y") or 0) / height))
            x2 = max(x1, min(1.0, float((row.get("x") or 0) + (row.get("w") or 0)) / width))
            y2 = max(y1, min(1.0, float((row.get("y") or 0) + (row.get("h") or 0)) / height))
            if x2 - x1 < 0.002 or y2 - y1 < 0.002:
                continue
            subjects.append({
                "id": f"person_{index}",
                "label": f"Person {index}",
                "selected": True,
                "source": str(row.get("source") or preview.get("preview_mode") or "person_detector"),
                "confidence": float(row.get("confidence") or 0.0),
                "bbox": {"x1": round(x1, 6), "y1": round(y1, 6), "x2": round(x2, 6), "y2": round(y2, 6)},
                "keep_points": [],
                "remove_points": [],
            })
        public_resolved_detector = portable_model_identifier(resolved_detector, detector_type)
        return {
            "ok": True,
            "endpoint": DETECT_SUBJECTS_ENDPOINT,
            "subjects": subjects,
            "count": len(subjects),
            "image_width": width,
            "image_height": height,
            "preview_mode": preview.get("preview_mode") or "",
            "resolved_detector_type": detector_type,
            "resolved_detector_model": public_resolved_detector,
            "detector_execution": {
                "schema_id": "neo.image.background_removal.detect_subjects_execution.v1",
                "status": "executed",
                "profile_id": str(detector_backend.get("profile_id") or profile_id or ""),
                "detector_type": detector_type,
                "detector_model": public_resolved_detector,
                "preview_mode": preview.get("preview_mode") or "",
                "fallback_used": False,
                "path_policy": "absolute_paths_server_side_only",
            },
            "message": preview.get("message") or f"Detected {len(subjects)} people.",
            "warnings": warnings,
        }

    @router.get("/source-file/{filename}")
    async def background_removal_source_file(filename: str) -> FileResponse:
        safe = Path(filename).name
        if not safe or safe != filename:
            raise HTTPException(status_code=400, detail="Invalid Background Removal source filename.")
        roots = [
            root_dir / "neo_data" / "inputs" / "background_removal",
            root_dir / "neo_data" / "inputs" / "background_removal" / "masks",
        ]
        for folder in roots:
            candidate = (folder / safe).resolve()
            try:
                candidate.relative_to(folder.resolve())
            except ValueError:
                continue
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="Background Removal source file was not found.")

    @router.post("/queue")
    async def queue_background_removal(
        profile_id: str = Form(""),
        settings_json: str = Form("{}"),
        image_files: list[UploadFile] = File(default=[]),
        image_file: UploadFile | None = File(default=None),
        mask_file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        if image_file is not None:
            image_files = [*image_files, image_file]
        try:
            raw_settings = _parse_settings(settings_json)
            settings = normalize_settings(raw_settings)
            validation = validate_payload_settings(
                settings,
                require_source=True,
                source_images=[f.filename for f in image_files],
                mask_images=[mask_file.filename] if mask_file else [],
            )
        except PayloadContractError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not image_files:
            raise HTTPException(status_code=400, detail="Pick a source image for Background Removal.")
        if settings.get("workflow_mode") == "refine_mask" and len(image_files) != 1:
            raise HTTPException(status_code=400, detail="Mask Review refinement accepts one source image and one reviewed mask per run.")
        if settings.get("workflow_mode") == "interactive_sam" and len(image_files) != 1:
            raise HTTPException(status_code=400, detail="Interactive SAM selection accepts one source image per run.")
        if not validation.get("ok"):
            raise HTTPException(status_code=400, detail="; ".join(validation.get("errors") or ["Background Removal validation failed."]))

        if settings.get("engine") == "commercial_api":
            requested_commercial_profile = str(settings.get("commercial_profile_id") or profile_id or "").strip()
            if profile_id and requested_commercial_profile and profile_id != requested_commercial_profile:
                raise HTTPException(status_code=400, detail="Commercial provider profile mismatch. Re-select the provider and confirm upload consent again.")
            profile_id = requested_commercial_profile

        provider: Any | None = None
        profile: dict[str, Any] = {}
        profile_error = ""
        if profile_id:
            try:
                provider, profile = profile_provider_resolver(profile_id)
            except Exception as exc:
                profile_error = str(exc)
        elif settings.get("engine") == "commercial_api":
            raise HTTPException(status_code=400, detail="Choose a commercial background-removal provider profile.")
        elif settings.get("workflow_mode") == "refine_mask" or (settings.get("workflow_mode") != "interactive_sam" and settings.get("engine") == "comfy_birefnet"):
            raise HTTPException(status_code=400, detail="Choose a ComfyUI profile for this Background Removal route.")

        profile_provider_id = _provider_id(provider, profile) if provider is not None else ""
        if settings.get("engine") == "commercial_api":
            if provider is None:
                raise HTTPException(status_code=400, detail=profile_error or "Commercial background-removal provider is unavailable.")
            if profile_provider_id not in COMMERCIAL_PROVIDER_IDS:
                raise HTTPException(status_code=400, detail="The selected profile is not a supported commercial background-removal provider.")
            if str(profile.get("profile_role") or "") != "image_background_removal_backend":
                raise HTTPException(status_code=400, detail="The selected provider profile is not scoped to Image Background Removal.")

        if provider is not None and profile_provider_id in SUPPORTED_COMFY_BACKENDS:
            catalog = _build_model_catalog(root_dir, provider, profile)
        else:
            catalog = {
                "models": [], "sources": [], "object_info_available": False, "available_nodes": [],
                "missing_nodes": sorted(SEGMENT_REQUIRED_NODES), "missing_refinement_nodes": sorted(REFINEMENT_REQUIRED_NODES),
                "nodes_ready": False, "refinement_nodes_ready": False,
                "native": native_rembg_status(), "native_models": list(NATIVE_MODEL_IDS),
                "native_preset_models": dict(NATIVE_PRESET_MODELS),
            }

        workflow_mode = str(settings.get("workflow_mode") or "segment")
        if settings.get("engine") == "commercial_api":
            if workflow_mode != "segment":
                raise HTTPException(status_code=400, detail="Commercial providers support the standard Remove Background run only.")
            settings = normalize_settings({
                **settings,
                "commercial_profile_id": profile_id,
                "fallback_policy": "never",
                "resolved_engine": "commercial_api",
                "resolved_model": profile_provider_id,
                "fallback_used": False,
                "fallback_reason": "",
            })
            resolution = EngineResolution("commercial_api", "commercial_api", settings.get("preset") or "smart_auto", profile_provider_id, False, "", True, ())
        elif workflow_mode == "refine_mask":
            if provider is None:
                raise HTTPException(status_code=400, detail=profile_error or "Mask Review refinement requires a connected ComfyUI profile.")
            settings = normalize_settings({**settings, "engine": "comfy_birefnet", "resolved_engine": "comfy_birefnet"})
            resolution = EngineResolution("comfy_birefnet", "comfy_birefnet", settings.get("preset") or "smart_auto", settings.get("model") or "", False, "", True, ())
        elif workflow_mode == "interactive_sam":
            native_status = catalog.get("native") or {}
            sam_status = native_status.get("interactive_sam") if isinstance(native_status.get("interactive_sam"), dict) else {}
            shared_catalog = catalog.get("shared_sam") if isinstance(catalog.get("shared_sam"), dict) else build_shared_sam_catalog(available_nodes=catalog.get("available_nodes") or [])
            shared_resolution = resolve_shared_sam(settings, shared_catalog)
            comfy_compatible, comfy_reason = subjects_support_comfy(list(settings.get("sam_subjects") or []))
            requested_execution = str(settings.get("sam_execution") or "auto")
            comfy_profile_ready = provider is not None and profile_provider_id in SUPPORTED_COMFY_BACKENDS
            native_ready = bool(native_status.get("available")) and sam_status.get("available") is not False
            if requested_execution == "comfy_impact":
                if not comfy_profile_ready:
                    raise HTTPException(status_code=400, detail=profile_error or "Shared Impact Pack SAM requires a connected ComfyUI profile.")
                if not shared_resolution.ready:
                    raise HTTPException(status_code=400, detail=shared_resolution.reason)
                if not comfy_compatible:
                    raise HTTPException(status_code=400, detail=comfy_reason)
                resolved_engine = "comfy_sam"
            elif requested_execution == "native_onnx":
                if not native_ready:
                    raise HTTPException(status_code=400, detail=str(native_status.get("reason") or sam_status.get("reason") or "Neo Native ONNX SAM is unavailable."))
                resolved_engine = "native_sam"
            elif comfy_profile_ready and shared_resolution.ready and comfy_compatible:
                resolved_engine = "comfy_sam"
            elif native_ready:
                resolved_engine = "native_sam"
            else:
                detail = shared_resolution.reason if comfy_profile_ready else (profile_error or "No connected ComfyUI profile for Comfy SAM.")
                raise HTTPException(status_code=400, detail=f"No Interactive SAM route is ready. Comfy SAM: {detail}; Native SAM: {native_status.get('reason') or sam_status.get('reason') or 'unavailable'}")
            settings = normalize_settings({
                **settings,
                "preset": "interactive_select",
                "resolved_engine": resolved_engine,
                "resolved_model": shared_resolution.model if resolved_engine == "comfy_sam" else (settings.get("sam_model_variant") or "sam_vit_b_01ec64"),
                "sam_comfy_model": shared_resolution.model if resolved_engine == "comfy_sam" else settings.get("sam_comfy_model"),
                "sam_node_map": shared_resolution.node_map if resolved_engine == "comfy_sam" else {},
                "model": shared_resolution.refinement_model if resolved_engine == "comfy_sam" and shared_resolution.refinement_model else settings.get("model"),
                "sam_shared_refine_enabled": bool(resolved_engine == "comfy_sam" and shared_resolution.refinement_ready and settings.get("sam_refine_mode") == "birefnet_gate"),
                "sam_refine_fallback_used": bool(resolved_engine == "comfy_sam" and shared_resolution.refinement_fallback),
                "sam_refine_fallback_reason": shared_resolution.reason if resolved_engine == "comfy_sam" and shared_resolution.refinement_fallback else "",
                "fallback_used": requested_execution == "auto" and resolved_engine == "native_sam" and comfy_profile_ready,
                "fallback_reason": comfy_reason if requested_execution == "auto" and resolved_engine == "native_sam" and comfy_profile_ready else "",
                "manual_mask": True,
                "mask_source": "interactive_sam",
            })
            resolution = EngineResolution(requested_execution, resolved_engine, "interactive_select", settings.get("resolved_model") or "sam", bool(settings.get("fallback_used")), settings.get("fallback_reason") or "", True, ())
        else:
            resolution = resolve_engine(settings, comfy_catalog=catalog, native_status=catalog.get("native") or {})
            if not resolution.ready:
                detail = "; ".join(resolution.errors) or profile_error or "No Background Removal engine is ready."
                raise HTTPException(status_code=400, detail=detail)
            settings = normalize_settings({
                **settings,
                "resolved_engine": resolution.resolved_engine,
                "resolved_model": resolution.model,
                "fallback_used": resolution.fallback_used,
                "fallback_reason": resolution.fallback_reason,
                "model": resolution.model if resolution.resolved_engine == "comfy_birefnet" else settings.get("model"),
                "native_model": resolution.model if resolution.resolved_engine == "native_rembg" else settings.get("native_model"),
            })

        support: dict[str, Any]
        if settings.get("resolved_engine") == "commercial_api":
            support = {
                "state": "available",
                "route": _route_for_profile(provider, profile, settings),
                "node_status": {
                    "ready": True,
                    "workflow_mode": workflow_mode,
                    "required_nodes": [],
                    "missing_nodes": [],
                    "commercial_provider_id": profile_provider_id,
                    "external_upload": True,
                    "consent_recorded": bool(settings.get("commercial_upload_consent")),
                },
            }
        elif settings.get("resolved_engine") in {"native_rembg", "native_sam"}:
            support = {
                "state": "available",
                "route": _route_for_profile(None, {}, settings),
                "node_status": {
                    "ready": True,
                    "workflow_mode": workflow_mode,
                    "required_nodes": [],
                    "missing_nodes": [],
                    "native_runtime": catalog.get("native") or {},
                },
            }
        elif settings.get("resolved_engine") == "comfy_sam":
            shared_catalog = catalog.get("shared_sam") or {}
            support = {
                "state": "available",
                "route": _route_for_profile(provider, profile, settings),
                "node_status": {
                    "ready": True,
                    "workflow_mode": workflow_mode,
                    "required_nodes": shared_catalog.get("required_nodes") or [],
                    "missing_nodes": shared_catalog.get("missing_nodes") or [],
                    "available_nodes": catalog.get("available_nodes") or [],
                    "shared_sam": shared_catalog,
                },
            }
        else:
            if provider is None:
                raise HTTPException(status_code=400, detail=profile_error or "Comfy BiRefNet requires a connected ComfyUI profile.")
            try:
                if workflow_mode != "refine_mask":
                    settings = normalize_settings(_resolve_model_for_preset(settings, catalog))
                    settings = normalize_settings({**settings, "resolved_model": settings.get("model") or resolution.model})
                support = _assert_route_ready(provider, profile, settings, catalog)
            except HTTPException as exc:
                allow_queue_fallback = (
                    workflow_mode != "refine_mask"
                    and settings.get("engine") == "smart"
                    and settings.get("fallback_policy") in {"on_unavailable", "on_unavailable_or_queue_failure"}
                    and bool((catalog.get("native") or {}).get("available"))
                )
                if not allow_queue_fallback:
                    raise
                fallback_model = NATIVE_PRESET_MODELS.get(str(settings.get("preset") or "smart_auto"), NATIVE_PRESET_MODELS["smart_auto"])
                settings = normalize_settings({
                    **settings,
                    "resolved_engine": "native_rembg",
                    "resolved_model": fallback_model,
                    "native_model": fallback_model,
                    "fallback_used": True,
                    "fallback_reason": f"Comfy route was not ready: {exc.detail}",
                })
                support = {
                    "state": "available",
                    "route": _route_for_profile(None, {}, settings),
                    "node_status": {"ready": True, "workflow_mode": workflow_mode, "required_nodes": [], "missing_nodes": [], "native_runtime": catalog.get("native") or {}},
                }

        route = {**support.get("route", {}), "route_state": support.get("state"), "provider_id": support.get("route", {}).get("provider_id") or (_provider_id(provider, profile) if provider is not None else "neo_native")}

        review_mask_asset: dict[str, Any] | None = None
        review_mask_comfy_name = ""
        if workflow_mode == "refine_mask" and mask_file is not None:
            review_mask_asset = await _save_mask_upload(mask_file, root_dir)
            review_mask_comfy_name = _upload_to_comfy(provider, Path(review_mask_asset["path"]))

        queued: list[dict[str, Any]] = []
        completed_outputs: list[dict[str, Any]] = []
        completed_results: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for index, upload in enumerate(image_files, start=1):
            asset: dict[str, Any] | None = None
            try:
                asset = await _save_upload(upload, root_dir, index)
                per_source = normalize_settings({
                    **settings,
                    "source_width": asset.get("width") or 0,
                    "source_height": asset.get("height") or 0,
                    "manual_mask": bool(review_mask_asset) or settings.get("manual_mask"),
                    "mask_source": "manual_review" if review_mask_asset else settings.get("mask_source"),
                })
                per_source.update({
                    "source_width": asset.get("width") or 0,
                    "source_height": asset.get("height") or 0,
                })
                payload_block = build_payload_block(
                    per_source,
                    enabled=True,
                    route=route,
                    source_images=[asset],
                    mask_images=[review_mask_asset] if review_mask_asset else [],
                )

                if per_source.get("resolved_engine") == "commercial_api":
                    if native_result_persister is None:
                        raise RuntimeError("Commercial Background Removal result persistence is not configured.")
                    if provider is None or not hasattr(provider, "run_background_removal"):
                        raise RuntimeError("Selected commercial provider cannot run background removal.")
                    job_id = f"background-removal-commercial-{uuid4().hex[:12]}"
                    commercial_output_root = root_dir / "neo_data" / "runtime" / "background_removal_commercial" / job_id
                    per_source = normalize_settings({**per_source, "native_output_root": str(commercial_output_root), "commercial_profile_id": profile_id})
                    payload_block = build_payload_block(per_source, enabled=True, route=route, source_images=[asset], mask_images=[])
                    commercial_result = provider.run_background_removal(Path(asset["path"]), output_root=commercial_output_root, settings=per_source)
                    notes = list(commercial_result.get("notes") or [])
                    metadata = build_background_removal_metadata(
                        route=route,
                        params=per_source,
                        assets={"source_images": [asset], "mask_images": [], "commercial_runtime": commercial_result.get("runtime") or {}},
                        payload_block=payload_block,
                        node_status=support.get("node_status") or {},
                        compile_notes=notes,
                    )
                    context = _context_for_job(
                        prompt_id=job_id,
                        profile_id=profile_id,
                        profile=profile,
                        route=route,
                        payload_block=payload_block,
                        metadata=metadata,
                        normalized=per_source,
                        notes=notes,
                        asset=asset,
                    )
                    persisted = native_result_persister(list(commercial_result.get("outputs") or []), context)
                    outputs = list(persisted.get("outputs") or persisted.get("files") or [])
                    completed_outputs.extend(outputs)
                    completed_results.append({
                        "job_id": job_id,
                        "result_id": persisted.get("result_id") or "",
                        "outputs": outputs,
                        "record": persisted.get("record") or {},
                        "source": asset.get("filename"),
                        "engine": "commercial_api",
                        "provider_id": profile_provider_id,
                        "profile_id": profile_id,
                        "model": profile_provider_id,
                        "fallback_used": False,
                        "fallback_reason": "",
                        "credits": commercial_result.get("credits") or {},
                        "metadata": metadata,
                    })
                    continue

                if per_source.get("resolved_engine") in {"native_rembg", "native_sam"}:
                    if native_result_persister is None:
                        raise RuntimeError("Native Background Removal result persistence is not configured.")
                    is_sam = per_source.get("resolved_engine") == "native_sam"
                    job_id = f"background-removal-{'sam' if is_sam else 'native'}-{uuid4().hex[:12]}"
                    native_output_root = root_dir / "neo_data" / "runtime" / "background_removal_native" / job_id
                    per_source = normalize_settings({**per_source, "native_output_root": str(native_output_root)})
                    payload_block = build_payload_block(per_source, enabled=True, route=route, source_images=[asset], mask_images=[])
                    selected_runner = sam_runner if is_sam else native_runner
                    native_result = selected_runner(Path(asset["path"]), settings=per_source, output_root=native_output_root)
                    notes = list(native_result.get("notes") or [])
                    metadata = build_background_removal_metadata(
                        route=route,
                        params=per_source,
                        assets={"source_images": [asset], "mask_images": [], "native_runtime": native_result.get("runtime") or {}},
                        payload_block=payload_block,
                        node_status=support.get("node_status") or {},
                        compile_notes=notes,
                    )
                    context = _context_for_job(
                        prompt_id=job_id,
                        profile_id=profile_id or "native.background_removal",
                        profile={"provider_id": "neo_native"},
                        route=route,
                        payload_block=payload_block,
                        metadata=metadata,
                        normalized=per_source,
                        notes=notes,
                        asset=asset,
                    )
                    persisted = native_result_persister(list(native_result.get("outputs") or []), context)
                    outputs = list(persisted.get("outputs") or persisted.get("files") or [])
                    completed_outputs.extend(outputs)
                    completed_results.append({
                        "job_id": job_id,
                        "result_id": persisted.get("result_id") or "",
                        "outputs": outputs,
                        "record": persisted.get("record") or {},
                        "source": asset.get("filename"),
                        "engine": "native_sam" if is_sam else "native_rembg",
                        "model": per_source.get("sam_model_variant") if is_sam else per_source.get("native_model"),
                        "fallback_used": bool(per_source.get("fallback_used")),
                        "fallback_reason": per_source.get("fallback_reason") or "",
                        "metadata": metadata,
                    })
                    continue

                comfy_name = _upload_to_comfy(provider, Path(asset["path"]))
                workflow, normalized, notes = _call_workflow_builder(workflow_builder, comfy_name, per_source, review_mask_comfy_name)
                normalized.update({
                    "resolved_engine": per_source.get("resolved_engine") or "comfy_birefnet",
                    "resolved_model": per_source.get("resolved_model") or normalized.get("sam_comfy_model") or normalized.get("model") or "",
                    "fallback_used": bool(per_source.get("fallback_used")),
                    "fallback_reason": per_source.get("fallback_reason") or "",
                })
                _assert_standalone_workflow(workflow, normalized)
                client_id = f"neo-background-removal-{uuid4().hex[:10]}"
                response = _post_prompt(provider, workflow, client_id)
                prompt_id = str(response.get("prompt_id") or f"background-removal-{uuid4().hex[:10]}")
                assets = {
                    "source_images": [asset],
                    "mask_images": [review_mask_asset] if review_mask_asset else [],
                    "comfy_source_image_name": comfy_name,
                    "comfy_review_mask_name": review_mask_comfy_name,
                }
                metadata = build_background_removal_metadata(
                    route=route,
                    params=normalized,
                    assets=assets,
                    payload_block=payload_block,
                    node_status=support.get("node_status") or {},
                    compile_notes=notes,
                )
                if context_recorder:
                    context_recorder(prompt_id, _context_for_job(
                        prompt_id=prompt_id,
                        profile_id=profile_id,
                        profile=profile,
                        route=route,
                        payload_block=payload_block,
                        metadata=metadata,
                        normalized=normalized,
                        notes=notes,
                        asset=asset,
                        mask_asset=review_mask_asset,
                    ))
                queued.append({
                    "job_id": prompt_id,
                    "prompt_id": prompt_id,
                    "profile_id": profile_id,
                    "provider_id": route.get("provider_id"),
                    "client_id": client_id,
                    "source": asset.get("filename"),
                    "stored_source": asset.get("stored_filename"),
                    "stored_mask": review_mask_asset.get("stored_filename") if review_mask_asset else "",
                    "comfy_source_image_name": comfy_name,
                    "comfy_review_mask_name": review_mask_comfy_name,
                    "model": normalized.get("resolved_model") or normalized.get("model"),
                    "preset": normalized.get("preset"),
                    "workflow_mode": normalized.get("workflow_mode"),
                    "engine": normalized.get("resolved_engine") or "comfy_birefnet",
                    "fallback_used": bool(normalized.get("fallback_used")),
                    "fallback_reason": normalized.get("fallback_reason") or "",
                    "compile_notes": notes,
                    "metadata": metadata,
                })
            except HTTPException as exc:
                failed.append({"index": index, "name": upload.filename or f"image_{index}", "error": str(exc.detail)})
            except Exception as exc:
                can_queue_fallback = (
                    workflow_mode != "refine_mask"
                    and settings.get("engine") == "smart"
                    and settings.get("fallback_policy") == "on_unavailable_or_queue_failure"
                    and bool((catalog.get("native") or {}).get("available"))
                    and asset is not None
                    and native_result_persister is not None
                )
                if can_queue_fallback:
                    try:
                        fallback_model = NATIVE_PRESET_MODELS.get(str(settings.get("preset") or "smart_auto"), NATIVE_PRESET_MODELS["smart_auto"])
                        per_source = normalize_settings({
                            **settings,
                            "resolved_engine": "native_rembg",
                            "resolved_model": fallback_model,
                            "native_model": fallback_model,
                            "fallback_used": True,
                            "fallback_reason": f"Comfy queue failed: {exc}",
                        })
                        fallback_route = {**_route_for_profile(None, {}, per_source), "route_state": "available", "provider_id": "neo_native"}
                        job_id = f"background-removal-native-{uuid4().hex[:12]}"
                        native_output_root = root_dir / "neo_data" / "runtime" / "background_removal_native" / job_id
                        per_source = normalize_settings({**per_source, "native_output_root": str(native_output_root)})
                        payload_block = build_payload_block(per_source, enabled=True, route=fallback_route, source_images=[asset], mask_images=[])
                        native_result = native_runner(Path(asset["path"]), settings=per_source, output_root=native_output_root)
                        notes = [f"Smart fallback activated after Comfy queue failure: {exc}", *(native_result.get("notes") or [])]
                        metadata = build_background_removal_metadata(route=fallback_route, params=per_source, assets={"source_images": [asset], "mask_images": [], "native_runtime": native_result.get("runtime") or {}}, payload_block=payload_block, node_status={"ready": True, "native_runtime": catalog.get("native") or {}}, compile_notes=notes)
                        context = _context_for_job(prompt_id=job_id, profile_id=profile_id or "native.background_removal", profile={"provider_id": "neo_native"}, route=fallback_route, payload_block=payload_block, metadata=metadata, normalized=per_source, notes=notes, asset=asset)
                        persisted = native_result_persister(list(native_result.get("outputs") or []), context)
                        outputs = list(persisted.get("outputs") or persisted.get("files") or [])
                        completed_outputs.extend(outputs)
                        completed_results.append({"job_id": job_id, "result_id": persisted.get("result_id") or "", "outputs": outputs, "record": persisted.get("record") or {}, "source": asset.get("filename"), "engine": "native_rembg", "model": fallback_model, "fallback_used": True, "fallback_reason": per_source.get("fallback_reason") or "", "metadata": metadata})
                        continue
                    except Exception as fallback_exc:
                        failed.append({"index": index, "name": upload.filename or f"image_{index}", "error": f"Comfy failed ({exc}); native fallback also failed ({fallback_exc})"})
                        continue
                failed.append({"index": index, "name": upload.filename or f"image_{index}", "error": str(exc)})

        if not queued and not completed_outputs:
            detail = failed[0]["error"] if failed else "Could not run Background Removal."
            raise HTTPException(status_code=502, detail=detail)
        refinement = settings.get("workflow_mode") == "refine_mask"
        interactive_sam = settings.get("workflow_mode") == "interactive_sam"
        completed_engines = {str(item.get("engine") or "") for item in completed_results if str(item.get("engine") or "")}
        queued_engines = {str(item.get("engine") or "") for item in queued if str(item.get("engine") or "")}
        all_engines = completed_engines | queued_engines
        if len(all_engines) == 1:
            resolved_engine = next(iter(all_engines))
        elif len(all_engines) > 1:
            resolved_engine = "mixed"
        else:
            resolved_engine = str(settings.get("resolved_engine") or "comfy_birefnet")
        return {
            "ok": True,
            "extension_id": EXTENSION_ID,
            "endpoint": QUEUE_ENDPOINT,
            "source_file_endpoint": SOURCE_FILE_ENDPOINT,
            "profile_id": profile_id,
            "route": route,
            "jobs": queued,
            "completed_outputs": completed_outputs,
            "completed_results": completed_results,
            "queued_count": len(queued),
            "completed_count": len(completed_results),
            "failed": failed,
            "failed_count": len(failed),
            "workflow_mode": settings.get("workflow_mode"),
            "resolved_engine": resolved_engine,
            "fallback_used": bool(settings.get("fallback_used")) or any(bool(item.get("fallback_used")) for item in completed_results + queued),
            "message": (
                "Queued reviewed-mask refinement without rerunning BiRefNet segmentation."
                if refinement
                else (
                    "Completed Interactive SAM selection and saved a transparent child output."
                    if interactive_sam
                    else (
                    f"Completed {'commercial' if resolved_engine == 'commercial_api' else 'native'} Background Removal for {len(completed_results)} image{'s' if len(completed_results) != 1 else ''}."
                    if completed_results and not queued
                    else f"Started Background Removal for {len(queued) + len(completed_results)} image{'s' if len(queued) + len(completed_results) != 1 else ''}."
                    )
                )
            ),
        }

    return router


def register_background_removal_api_routes(
    app: FastAPI,
    root_dir: Path,
    *,
    profile_provider_resolver: ProfileProviderResolver,
    model_catalog_provider_resolver: ProfileProviderResolver | None = None,
    detector_backend_resolver: DetectorBackendResolver | None = None,
    context_recorder: ContextRecorder | None = None,
    workflow_builder: WorkflowBuilder | None = None,
    native_runner: NativeRunner | None = None,
    sam_runner: NativeRunner | None = None,
    native_result_persister: NativeResultPersister | None = None,
) -> APIRouter:
    router = create_background_removal_api_router(
        root_dir,
        profile_provider_resolver=profile_provider_resolver,
        model_catalog_provider_resolver=model_catalog_provider_resolver,
        detector_backend_resolver=detector_backend_resolver,
        context_recorder=context_recorder,
        workflow_builder=workflow_builder,
        native_runner=native_runner,
        sam_runner=sam_runner,
        native_result_persister=native_result_persister,
    )
    app.include_router(router)
    return router
