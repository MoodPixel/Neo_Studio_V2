from __future__ import annotations

from pathlib import Path, PurePosixPath
import os
import shutil
from typing import Any
from urllib.request import urlretrieve
from uuid import uuid4

from neo_app.providers.comfy_model_paths import (
    resolve_comfy_extra_model_folders,
    resolve_comfy_model_paths,
)

try:
    from neo_app.admin.image_node_manager import load_node_manager_settings
except Exception:  # pragma: no cover
    def load_node_manager_settings() -> dict:
        return {}

try:
    from neo_app.admin.models.model_paths import load_model_paths
except Exception:  # pragma: no cover
    def load_model_paths(*, create: bool = False) -> dict:
        return {}

MODEL_EXTS = {'.pt', '.pth', '.onnx', '.safetensors'}
EXTRA_GENERIC_DETECTOR_KEYS = {
    'ultralytics',
    'ultralytics_models',
    'yolo',
    'yolo_models',
    'detectors',
    'detector_models',
    'adetailer',
    'adetailer_models',
    'detailer',
    'detailer_models',
}
EXTRA_BBOX_DETECTOR_KEYS = {
    'ultralytics_bbox',
    'yolo_bbox',
    'bbox_detectors',
    'adetailer_bbox',
    'detailer_bbox',
}
EXTRA_SEGM_DETECTOR_KEYS = {
    'ultralytics_segm',
    'yolo_segm',
    'segm_detectors',
    'segmentation_detectors',
    'adetailer_segm',
    'adetailer_segmentation',
    'detailer_segm',
}
SAM_PRESETS: dict[str, dict[str, str]] = {
    'vit_b': {'filename': 'sam_vit_b_01ec64.pth', 'url': 'https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth', 'label': 'SAM ViT-B'},
    'vit_l': {'filename': 'sam_vit_l_0b3195.pth', 'url': 'https://dl.fbaipublicfiles.com/segment_anything/sam_vit_l_0b3195.pth', 'label': 'SAM ViT-L'},
    'vit_h': {'filename': 'sam_vit_h_4b8939.pth', 'url': 'https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth', 'label': 'SAM ViT-H'},
}


def _safe_path(value: str | None) -> Path | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return Path(text).expanduser()
    except Exception:
        return None


def comfy_root_from_node_manager() -> Path | None:
    """Resolve the actual ComfyUI application root from Node Manager settings.

    Portable installations commonly store ``comfy_root_path`` at the wrapper
    directory (for example ``ComfyUI_windows_portable``) while
    ``custom_nodes_path`` points at the real application root beneath
    ``ComfyUI/custom_nodes``.  The custom-nodes parent is therefore the most
    authoritative standard root and must win over the portable wrapper.
    """

    settings = load_node_manager_settings()
    custom_nodes = _safe_path(settings.get('custom_nodes_path'))
    if custom_nodes and custom_nodes.name.casefold() == 'custom_nodes':
        return custom_nodes.parent

    configured_root = _safe_path(settings.get('comfy_root_path'))
    if not configured_root:
        return custom_nodes

    nested_root = configured_root / 'ComfyUI'
    if nested_root.exists() and nested_root.is_dir():
        return nested_root
    if (configured_root / 'models').exists() or (configured_root / 'custom_nodes').exists():
        return configured_root
    return configured_root


def _detailer_model_root_map(models_root: Path | None, comfy_root: Path | None = None) -> dict[str, str]:
    ultralytics_dir = models_root / 'ultralytics' if models_root else None
    bbox_dir = ultralytics_dir / 'bbox' if ultralytics_dir else None
    segm_dir = ultralytics_dir / 'segm' if ultralytics_dir else None
    adetailer_dir = models_root / 'adetailer' if models_root else None
    onnx_dir = models_root / 'onnx' if models_root else None
    sam_dir = models_root / 'sams' if models_root else None
    return {
        'comfy_root': str(comfy_root) if comfy_root else '',
        'models_root': str(models_root) if models_root else '',
        'ultralytics_dir': str(ultralytics_dir) if ultralytics_dir else '',
        'bbox_dir': str(bbox_dir) if bbox_dir else '',
        'segm_dir': str(segm_dir) if segm_dir else '',
        'adetailer_dir': str(adetailer_dir) if adetailer_dir else '',
        'onnx_dir': str(onnx_dir) if onnx_dir else '',
        'sam_dir': str(sam_dir) if sam_dir else '',
    }


def _detailer_root_map(comfy_root: Path | None) -> dict[str, str]:
    return _detailer_model_root_map(comfy_root / 'models' if comfy_root else None, comfy_root)


def default_detailer_roots() -> dict[str, str]:
    return _detailer_root_map(comfy_root_from_node_manager())


def _detailer_roots_for_backend(backend_details: dict[str, Any] | None = None) -> dict[str, str]:
    """Prefer Admin's explicit models root, then active profile and Node Manager."""

    details = backend_details if isinstance(backend_details, dict) else {}
    connection = details.get('connection') if isinstance(details.get('connection'), dict) else {}
    runtime = details.get('runtime') if isinstance(details.get('runtime'), dict) else {}
    configured_models_root = _safe_path(str(
        details.get('configured_models_root')
        or details.get('models_root')
        or connection.get('models_root')
        or runtime.get('models_root')
        or ''
    ))
    configured_comfy_root = _safe_path(str(
        details.get('configured_comfy_root')
        or details.get('comfy_root')
        or connection.get('comfy_root')
        or runtime.get('comfy_root')
        or ''
    ))
    if configured_models_root and configured_models_root.exists() and configured_models_root.is_dir():
        return _detailer_model_root_map(configured_models_root, configured_comfy_root)

    raw_roots = [
        details.get('comfy_root'),
        details.get('comfy_root_path'),
        details.get('comfyui_path'),
        details.get('portable_path'),
        connection.get('comfy_root'),
        connection.get('comfy_root_path'),
        connection.get('comfyui_path'),
        connection.get('portable_path'),
        runtime.get('comfy_root'),
        runtime.get('comfy_root_path'),
        runtime.get('comfyui_path'),
        runtime.get('portable_path'),
    ]
    candidates: list[Path] = []
    for raw in raw_roots:
        root = _safe_path(str(raw or ''))
        if not root:
            continue
        if root.name.casefold() == 'models':
            candidates.append(root.parent)
        else:
            candidates.extend([root, root / 'ComfyUI'])
    fallback_root = _safe_path(default_detailer_roots().get('comfy_root'))
    if fallback_root:
        candidates.append(fallback_root)
    seen: set[str] = set()
    deduped: list[Path] = []
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    for candidate in deduped:
        if (candidate / 'models').is_dir() or (candidate / 'custom_nodes').is_dir():
            return _detailer_root_map(candidate)
    if configured_models_root:
        return _detailer_model_root_map(configured_models_root, configured_comfy_root)
    return _detailer_root_map(deduped[0]) if deduped else default_detailer_roots()


def _scan_model_dir(path: Path | None, *, recursive: bool = False) -> list[str]:
    if not path or not path.exists() or not path.is_dir():
        return []
    rows: list[str] = []
    iterator = path.rglob('*') if recursive else path.iterdir()
    for child in sorted(iterator, key=lambda p: str(p).casefold()):
        if not child.is_file() or child.suffix.lower() not in MODEL_EXTS:
            continue
        try:
            rows.append(child.relative_to(path).as_posix() if recursive else child.name)
        except Exception:
            rows.append(child.name)
    return rows


def _detector_pool_for_identifier(identifier: str, suffix: str = '') -> str:
    """Return the typed ADetailer pool for one portable model identifier.

    Explicit folder scopes win over filename heuristics. This keeps nested custom
    layouts such as ``adetailer/segm/body.pt`` typed correctly while generic
    face/person/hand/custom checkpoints remain BBox models.
    """

    portable = str(identifier or '').strip().replace('\\', '/')
    folded = portable.casefold().strip('/')
    extension = str(suffix or Path(portable).suffix).casefold()
    if extension == '.onnx':
        return 'onnx'
    parts = [part for part in folded.split('/') if part]
    explicit_segm = {'segm', 'seg', 'segment', 'segmentation', 'mask', 'masks'}
    explicit_bbox = {'bbox', 'box', 'boxes', 'detection', 'detections'}
    if any(part in explicit_segm for part in parts[:-1]):
        return 'segm'
    if any(part in explicit_bbox for part in parts[:-1]):
        return 'bbox'
    stem = Path(portable).stem.casefold()
    return 'segm' if ('seg' in stem or 'mask' in stem) else 'bbox'


def _classify_standard_ultralytics(path: Path | None) -> tuple[list[str], list[str], list[str]]:
    """Discover detector assets even when they are not placed directly in bbox/segm.

    Impact Pack installations and model packs sometimes retain a nested layout or
    place generic YOLO checkpoints directly under ``models/ultralytics``.  Keep
    explicit ``bbox``/``segm`` folders authoritative, then classify the remaining
    files conservatively by path/name.
    """

    if not path or not path.exists() or not path.is_dir():
        return [], [], []
    bbox: list[str] = []
    segm: list[str] = []
    onnx: list[str] = []
    for child in sorted(path.rglob('*'), key=lambda p: str(p).casefold()):
        if not child.is_file() or child.suffix.lower() not in MODEL_EXTS:
            continue
        rel = child.relative_to(path).as_posix()
        folded = rel.casefold()
        # Dedicated bbox/segm roots are scanned recursively above.  Skip them
        # here so nested files are not exposed twice under different names.
        if folded.startswith('bbox/') or folded.startswith('segm/'):
            continue
        pool = _detector_pool_for_identifier(rel, child.suffix)
        if pool == 'onnx':
            onnx.append(rel)
        elif pool == 'segm':
            segm.append(rel)
        else:
            bbox.append(rel)
    return bbox, segm, onnx


def _dedupe(rows: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in rows:
        key = str(item or '').strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(item))
    return out



def _scan_extra_ultralytics(backend_details: dict[str, Any] | None = None) -> dict[str, Any]:
    details = backend_details if isinstance(backend_details, dict) else {}
    bbox: list[str] = []
    segm: list[str] = []
    onnx: list[str] = []
    bbox_resolution = resolve_comfy_extra_model_folders(details, categories=EXTRA_BBOX_DETECTOR_KEYS)
    segm_resolution = resolve_comfy_extra_model_folders(details, categories=EXTRA_SEGM_DETECTOR_KEYS)
    generic_resolution = resolve_comfy_extra_model_folders(details, categories=EXTRA_GENERIC_DETECTOR_KEYS)

    for folder in bbox_resolution.get('folders', []):
        for name in _scan_model_dir(folder, recursive=True):
            if Path(name).suffix.casefold() == '.onnx':
                onnx.append(name)
            else:
                bbox.append(f'bbox/{name}')
    for folder in segm_resolution.get('folders', []):
        for name in _scan_model_dir(folder, recursive=True):
            if Path(name).suffix.casefold() == '.onnx':
                onnx.append(name)
            else:
                segm.append(f'segm/{name}')
    for folder in generic_resolution.get('folders', []):
        for name in _scan_model_dir(folder, recursive=True):
            folded = name.casefold().replace('\\', '/')
            pool = _detector_pool_for_identifier(name)
            if pool == 'onnx':
                onnx.append(name)
            elif pool == 'segm':
                segm.append(name if folded.startswith('segm/') else f'segm/{name}')
            else:
                bbox.append(name if folded.startswith('bbox/') else f'bbox/{name}')

    resolutions = (bbox_resolution, segm_resolution, generic_resolution)
    folder_keys = {
        str(folder).replace('\\', '/').casefold()
        for resolution in resolutions
        for folder in resolution.get('folders', [])
    }
    diagnostics = [
        resolution.get('diagnostics', {})
        for resolution in resolutions
        if isinstance(resolution.get('diagnostics'), dict)
    ]
    return {
        'bbox_models': _dedupe(bbox),
        'segm_models': _dedupe(segm),
        'onnx_models': _dedupe(onnx),
        'diagnostics': {
            'schema_id': 'neo.image.adetailer.extra_model_paths.v2',
            'config_candidates': max((int(item.get('config_candidates') or 0) for item in diagnostics), default=0),
            'config_files_found': max((int(item.get('config_files_found') or 0) for item in diagnostics), default=0),
            'configured_detector_folders': len(folder_keys),
            'existing_detector_folders': len({
                str(folder).replace('\\', '/').casefold()
                for resolution in resolutions
                for folder in resolution.get('folders', [])
                if isinstance(folder, Path) and folder.exists() and folder.is_dir()
            }),
            'path_policy': 'absolute_paths_server_side_only',
        },
    }

def _preferred_detector(models: list[str], *, avoid_face: bool = True) -> str:
    rows = _dedupe(models)
    if not rows:
        return ''
    def score(name: str) -> tuple[int, int, str]:
        folded = Path(name).stem.casefold()
        face_like = any(token in folded for token in ('face', 'eye', 'hand', 'mouth', 'lip', 'head'))
        person_like = any(token in folded for token in ('person', 'human', 'people', 'body'))
        generic = any(token in folded for token in ('yolo', 'coco'))
        return (
            0 if person_like else 1 if generic and not face_like else 2 if not face_like else 4,
            len(name),
            name.casefold(),
        )
    ranked = sorted(rows, key=score)
    if avoid_face:
        non_face = [item for item in ranked if not any(token in Path(item).stem.casefold() for token in ('face', 'eye', 'hand', 'mouth', 'lip', 'head'))]
        if non_face:
            return non_face[0]
    return ranked[0]


def _extract_choice_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        rows: list[str] = []
        for key in ('choices', 'options', 'values', 'models', 'items'):
            rows.extend(_extract_choice_values(value.get(key)))
        return _dedupe(rows)
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        values = first
    elif all(isinstance(item, str) for item in value):
        values = value
    else:
        return []
    return [str(item).strip().replace("\\", "/") for item in values if str(item).strip()]


def _node_choice_values(object_info: dict[str, Any], node_name: str, *input_names: str) -> list[str]:
    node = object_info.get(node_name) if isinstance(object_info.get(node_name), dict) else {}
    input_block = node.get("input") if isinstance(node.get("input"), dict) else {}
    required = input_block.get("required") if isinstance(input_block.get("required"), dict) else {}
    optional = input_block.get("optional") if isinstance(input_block.get("optional"), dict) else {}
    rows: list[str] = []
    for input_name in input_names:
        rows.extend(_extract_choice_values(required.get(input_name)))
        rows.extend(_extract_choice_values(optional.get(input_name)))
    return _dedupe(rows)


def _merge_live_authoritative(filesystem_rows: list[str], live_rows: list[str]) -> list[str]:
    """Prefer Comfy's executable combo value while retaining offline-only files."""

    live = _dedupe(live_rows)
    live_basenames = {Path(item).name.casefold() for item in live}
    offline_only = [item for item in _dedupe(filesystem_rows) if Path(item).name.casefold() not in live_basenames]
    return _dedupe(live + offline_only)


def live_detailer_model_choices(object_info: dict[str, Any] | None) -> dict[str, list[str]]:
    """Read the authoritative detector/SAM dropdowns exposed by Comfy nodes.

    Filesystem discovery remains useful while Comfy is offline, but Impact Pack's
    live combo values are the source of truth for custom/nested detector models.
    Keep ADetailer's catalog broad; person-only filtering belongs to Background
    Removal and must never narrow the normal ADetailer model picker.
    """

    info = object_info if isinstance(object_info, dict) else {}
    bbox: list[str] = []
    segm: list[str] = []
    onnx: list[str] = []
    sam: list[str] = []
    for node_name in info:
        folded_node = str(node_name).casefold()
        if "ultralyticsdetectorprovider" in folded_node:
            for item in _node_choice_values(info, str(node_name), "model_name", "model", "detector_model"):
                pool = _detector_pool_for_identifier(item)
                if pool == "segm":
                    segm.append(item)
                elif pool == "onnx":
                    onnx.append(item)
                else:
                    bbox.append(item)
        elif "onnxdetectorprovider" in folded_node:
            onnx.extend(_node_choice_values(info, str(node_name), "model_name", "model", "detector_model"))
        elif "samloader" in folded_node:
            sam.extend(_node_choice_values(info, str(node_name), "model_name", "model", "sam_model", "sam_model_name"))
    return {
        "bbox_models": _dedupe(bbox),
        "segm_models": _dedupe(segm),
        "onnx_models": _dedupe(onnx),
        "sam_models": _dedupe(sam),
    }


def registered_detailer_model_choices(backend_details: dict[str, Any] | None) -> dict[str, list[str]]:
    """Classify names returned by Comfy's registered model-folder endpoints."""

    details = backend_details if isinstance(backend_details, dict) else {}
    folders = details.get('comfy_model_folders') if isinstance(details.get('comfy_model_folders'), dict) else {}
    bbox: list[str] = []
    segm: list[str] = []
    onnx: list[str] = []
    sam: list[str] = []

    for raw_key, raw_names in folders.items():
        key = str(raw_key or '').strip().casefold().replace('-', '_')
        names = [str(item or '').strip().replace('\\', '/') for item in raw_names] if isinstance(raw_names, list) else []
        names = [name for name in names if name]
        if key in {'ultralytics_bbox', 'bbox', 'bbox_detectors'}:
            bbox.extend(names)
        elif key in {'ultralytics_segm', 'segm', 'segmentation_detectors'}:
            segm.extend(names)
        elif key in {'onnx', 'onnx_models', 'onnx_detectors'}:
            onnx.extend(names)
        elif key in {'sams', 'sam', 'sam_models'}:
            sam.extend(names)
        elif key in {'ultralytics', 'adetailer', 'adetailer_models', 'detectors'}:
            for name in names:
                pool = _detector_pool_for_identifier(name)
                if pool == 'onnx':
                    onnx.append(name)
                elif pool == 'segm':
                    segm.append(name)
                else:
                    bbox.append(name)
    return {
        'bbox_models': _dedupe(bbox),
        'segm_models': _dedupe(segm),
        'onnx_models': _dedupe(onnx),
        'sam_models': _dedupe(sam),
    }


def _classify_custom_detector_files(path: Path | None) -> tuple[list[str], list[str], list[str]]:
    if not path or not path.exists() or not path.is_dir():
        return [], [], []
    bbox: list[str] = []
    segm: list[str] = []
    onnx: list[str] = []
    for child in sorted(path.rglob('*'), key=lambda p: str(p).casefold()):
        if not child.is_file() or child.suffix.lower() not in MODEL_EXTS:
            continue
        try:
            display_name = child.relative_to(path).as_posix()
        except Exception:
            display_name = child.name
        pool = _detector_pool_for_identifier(display_name, child.suffix)
        if pool == 'onnx':
            onnx.append(display_name)
        elif pool == 'segm':
            segm.append(display_name)
        else:
            bbox.append(display_name)
    return bbox, segm, onnx


def list_detailer_models(detector_root: str = '', sam_root: str = '', object_info: dict[str, Any] | None = None, backend_details: dict[str, Any] | None = None) -> dict[str, Any]:
    backend_details = backend_details if isinstance(backend_details, dict) else {}
    roots = _detailer_roots_for_backend(backend_details)
    standard_bbox_models = _scan_model_dir(_safe_path(roots.get('bbox_dir')), recursive=True)
    standard_segm_models = _scan_model_dir(_safe_path(roots.get('segm_dir')), recursive=True)
    standard_onnx_models = _scan_model_dir(_safe_path(roots.get('onnx_dir')), recursive=True)
    standard_sam_models = _scan_model_dir(_safe_path(roots.get('sam_dir')), recursive=True)
    extra_bbox, extra_segm, ultralytics_onnx_models = _classify_standard_ultralytics(_safe_path(roots.get('ultralytics_dir')))
    legacy_bbox, legacy_segm, legacy_onnx = _classify_custom_detector_files(_safe_path(roots.get('adetailer_dir')))
    live = live_detailer_model_choices(object_info)
    registered = registered_detailer_model_choices(backend_details)
    extra_paths = _scan_extra_ultralytics(backend_details)
    disk_bbox_models = _dedupe(standard_bbox_models + extra_bbox + legacy_bbox + extra_paths['bbox_models'])
    disk_segm_models = _dedupe(standard_segm_models + extra_segm + legacy_segm + extra_paths['segm_models'])
    disk_onnx_models = _dedupe(standard_onnx_models + ultralytics_onnx_models + legacy_onnx + extra_paths['onnx_models'])
    disk_sam_models = _dedupe(standard_sam_models)
    bbox_models = _merge_live_authoritative(_merge_live_authoritative(disk_bbox_models, registered['bbox_models']), live["bbox_models"])
    segm_models = _merge_live_authoritative(_merge_live_authoritative(disk_segm_models, registered['segm_models']), live["segm_models"])
    onnx_models = _merge_live_authoritative(_merge_live_authoritative(disk_onnx_models, registered['onnx_models']), live["onnx_models"])
    sam_models = _merge_live_authoritative(_merge_live_authoritative(disk_sam_models, registered['sam_models']), live["sam_models"])

    custom_detector_root = str(detector_root or '').strip()
    custom_sam_root = str(sam_root or '').strip()
    if custom_detector_root:
        custom_bbox, custom_segm, custom_onnx = _classify_custom_detector_files(_safe_path(custom_detector_root))
        bbox_models = _dedupe(bbox_models + custom_bbox)
        segm_models = _dedupe(segm_models + custom_segm)
        onnx_models = _dedupe(onnx_models + custom_onnx)
    if custom_sam_root:
        sam_models = _dedupe(sam_models + _scan_model_dir(_safe_path(custom_sam_root), recursive=True))

    default_bbox = _preferred_detector(bbox_models)
    default_segm = _preferred_detector(segm_models)
    default_onnx = _preferred_detector(onnx_models, avoid_face=False)
    default_detector = default_bbox or default_segm or default_onnx
    info = object_info if isinstance(object_info, dict) else {}
    ultralytics_node_count = sum(1 for name in info if 'ultralyticsdetectorprovider' in str(name).casefold())
    onnx_node_count = sum(1 for name in info if 'onnxdetectorprovider' in str(name).casefold())
    sam_node_count = sum(1 for name in info if 'samloader' in str(name).casefold())
    object_info_error_code = str(backend_details.get('object_info_error_code') or '').strip()
    warnings: list[str] = []
    if object_info_error_code:
        warnings.append(f'comfy_object_info_unavailable:{object_info_error_code}')
    if info and not ultralytics_node_count and not onnx_node_count:
        warnings.append('detector_provider_nodes_not_found')
    if not bbox_models and not segm_models and not onnx_models:
        warnings.append('no_detector_models_discovered')
    if extra_paths['diagnostics']['config_files_found'] and not extra_paths['diagnostics']['configured_detector_folders']:
        warnings.append('extra_model_paths_has_no_supported_detector_keys')
    return {
        'ok': True,
        **roots,
        'custom_detector_root': custom_detector_root,
        'custom_sam_root': custom_sam_root,
        'bbox_models': bbox_models,
        'segm_models': segm_models,
        'onnx_models': onnx_models,
        'sam_models': sam_models,
        'default_detector_model': default_detector,
        'default_bbox_model': default_bbox,
        'default_segm_model': default_segm,
        'default_onnx_model': default_onnx,
        'model_exts': sorted(MODEL_EXTS),
        'sam_presets': [{'key': key, **value} for key, value in SAM_PRESETS.items()],
        'sources': [name for name, present in (
            ('filesystem', bool(disk_bbox_models or disk_segm_models or disk_onnx_models or disk_sam_models)),
            ('admin_models_root', bool(backend_details.get('configured_models_root') and (disk_bbox_models or disk_segm_models or disk_onnx_models or disk_sam_models))),
            ('comfy_model_folders', any(registered.values())),
            ('comfy_object_info', any(live.values())),
            ('extra_model_paths_yaml', bool(extra_paths['bbox_models'] or extra_paths['segm_models'] or extra_paths['onnx_models'])),
            ('comfy_adetailer_folder', bool(legacy_bbox or legacy_segm or legacy_onnx)),
        ) if present],
        'counts': {'bbox': len(bbox_models), 'segm': len(segm_models), 'onnx': len(onnx_models), 'sam': len(sam_models)},
        'diagnostics': {
            'schema_id': 'neo.image.adetailer.catalog_diagnostics.v1',
            'profile_id': str(backend_details.get('profile_id') or ''),
            'provider_id': str(backend_details.get('provider_id') or ''),
            'object_info': {
                'available': bool(info),
                'error_code': object_info_error_code,
                'timeout_seconds': backend_details.get('object_info_timeout_seconds'),
                'ultralytics_provider_nodes': ultralytics_node_count,
                'onnx_provider_nodes': onnx_node_count,
                'sam_loader_nodes': sam_node_count,
                'bbox_choices': len(live['bbox_models']),
                'segm_choices': len(live['segm_models']),
                'onnx_choices': len(live['onnx_models']),
                'sam_choices': len(live['sam_models']),
            },
            'standard_filesystem': {
                'configured': bool(roots.get('models_root')),
                'models_root_source': str(backend_details.get('models_root_source') or ('profile_or_node_manager' if roots.get('models_root') else '')),
                'bbox_models': len(standard_bbox_models) + len(extra_bbox),
                'segm_models': len(standard_segm_models) + len(extra_segm),
                'onnx_models': len(standard_onnx_models) + len(ultralytics_onnx_models),
                'sam_models': len(standard_sam_models),
                'legacy_adetailer_bbox_models': len(legacy_bbox),
                'legacy_adetailer_segm_models': len(legacy_segm),
                'legacy_adetailer_onnx_models': len(legacy_onnx),
            },
            'comfy_model_folders': {
                'available': any(registered.values()),
                'bbox_choices': len(registered['bbox_models']),
                'segm_choices': len(registered['segm_models']),
                'onnx_choices': len(registered['onnx_models']),
                'sam_choices': len(registered['sam_models']),
                **(
                    backend_details.get('comfy_model_folder_diagnostics')
                    if isinstance(backend_details.get('comfy_model_folder_diagnostics'), dict)
                    else {}
                ),
            },
            'extra_model_paths': dict(extra_paths['diagnostics']),
            'warnings': warnings,
            'path_policy': 'absolute_paths_server_side_only',
        },
    }


def _configured_detailer_backend_details(
    backend_details: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve server-only filesystem details for execution-time staging.

    Catalog API requests receive their active profile details from ``main.py``.
    Workflow compilation does not, so the execution bridge also resolves the
    profile id carried by the compile route and the local Admin Models setting.
    The returned values never cross the ADetailer public API boundary.
    """

    details = dict(backend_details or {})
    route_data = route if isinstance(route, dict) else {}
    actual_params = route_data.get('actual_params') if isinstance(route_data.get('actual_params'), dict) else {}
    route_params = route_data.get('params') if isinstance(route_data.get('params'), dict) else {}
    route_base_url = str(route_data.get('comfy_base_url') or route_data.get('base_url') or '').strip()
    if route_base_url:
        details.setdefault('base_url', route_base_url)
    profile_id = str(
        details.get('profile_id')
        or route_data.get('backend_profile_id')
        or route_data.get('profile_id')
        or actual_params.get('backend_profile_id')
        or actual_params.get('profile_id')
        or route_params.get('backend_profile_id')
        or route_params.get('profile_id')
        or ''
    ).strip()
    if profile_id:
        details.setdefault('profile_id', profile_id)
        try:
            from neo_app.providers.profiles import get_backend_profile

            profile = get_backend_profile(profile_id) or {}
        except Exception:  # pragma: no cover - profile lookup is an optional runtime aid.
            profile = {}
        if isinstance(profile, dict) and profile:
            connection = profile.get('connection') if isinstance(profile.get('connection'), dict) else {}
            runtime = profile.get('runtime') if isinstance(profile.get('runtime'), dict) else {}
            details.setdefault('provider_id', profile.get('provider_id'))
            details.setdefault('base_url', connection.get('base_url') or runtime.get('base_url') or '')
            details.setdefault('portable_path', connection.get('portable_path') or runtime.get('portable_path') or '')
            details.setdefault('comfy_root', connection.get('comfy_root') or runtime.get('comfy_root') or '')
            details.setdefault('models_root', connection.get('models_root') or runtime.get('models_root') or '')

    try:
        model_paths = load_model_paths(create=False)
    except Exception:  # pragma: no cover - Node Manager/profile fallback remains available.
        model_paths = {}
    try:
        node_manager_settings = load_node_manager_settings()
    except Exception:  # pragma: no cover - profile/Admin Models remain usable.
        node_manager_settings = {}
    return resolve_comfy_model_paths(
        details,
        model_paths=model_paths,
        node_manager_settings=node_manager_settings,
    )


def configured_detailer_backend_details(
    backend_details: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Public server-side resolver shared by detector-consuming extensions.

    ADetailer, Background Removal, and any later detector consumer must derive
    their filesystem snapshot from the same active profile/Admin Models inputs.
    Callers must still redact this server-only mapping at their API boundary.
    """

    return _configured_detailer_backend_details(backend_details, route=route)


def _has_local_or_shared_filesystem_access(details: dict[str, Any]) -> bool:
    """Return false for an explicitly remote, URL-only Comfy profile."""

    if details.get('filesystem_access') is False:
        return False
    if details.get('filesystem_access') is True:
        return True
    profile_paths = (
        details.get('portable_path'),
        details.get('comfy_root'),
        details.get('models_root'),
    )
    if any(str(value or '').strip() for value in profile_paths):
        return True
    base_url = str(details.get('base_url') or '').strip().lower()
    if not base_url:
        return bool(
            str(details.get('configured_models_root') or '').strip()
            or str(details.get('configured_comfy_root') or '').strip()
            or str(default_detailer_roots().get('models_root') or '').strip()
        )
    return any(marker in base_url for marker in ('://127.0.0.1', '://localhost', '://[::1]'))


def _detector_relative_name(model_name: str, detector_type: str) -> tuple[str, str, str]:
    """Return ``(kind, relative_path, Comfy load value)`` for a detector.

    Only relative model identifiers are accepted.  This prevents a saved pass
    from using the bridge as an arbitrary filesystem copy primitive.
    """

    raw = str(model_name or '').strip().replace('\\', '/')
    if not raw or raw.startswith('/') or raw.startswith('//') or (len(raw) > 1 and raw[1] == ':'):
        raise ValueError('invalid_model_path')
    parts = list(PurePosixPath(raw).parts)
    if not parts or any(part in {'', '.', '..'} for part in parts):
        raise ValueError('invalid_model_path')
    requested_type = str(detector_type or 'bbox').strip().lower()
    kind = 'onnx' if requested_type.startswith('onnx') else ('segm' if requested_type.startswith('segm') else 'bbox')
    if parts[0].lower() in {'bbox', 'segm', 'onnx'}:
        prefix = parts.pop(0).lower()
        if kind != 'onnx' and prefix in {'bbox', 'segm'}:
            kind = prefix
    if not parts or any(part in {'', '.', '..'} for part in parts):
        raise ValueError('invalid_model_path')
    relative = PurePosixPath(*parts).as_posix()
    suffix = PurePosixPath(relative).suffix.lower()
    if suffix not in MODEL_EXTS or (kind == 'onnx' and suffix != '.onnx'):
        raise ValueError('unsupported_model_extension')
    load_value = relative if kind == 'onnx' else f'{kind}/{relative}'
    return kind, relative, load_value


def _resolved_child(root: Path, relative: str) -> Path:
    root_resolved = root.expanduser().resolve()
    child = (root_resolved / Path(*PurePosixPath(relative).parts)).resolve()
    if not child.is_relative_to(root_resolved):
        raise ValueError('model_path_outside_root')
    return child


def _find_flat_source(source_root: Path, relative: str) -> Path | None:
    """Resolve one selected model from ``models/adetailer`` without guessing."""

    if not source_root.exists() or not source_root.is_dir():
        return None
    direct = _resolved_child(source_root, relative)
    if direct.is_file() and direct.suffix.lower() in MODEL_EXTS:
        return direct
    basename = PurePosixPath(relative).name
    root_candidate = _resolved_child(source_root, basename)
    if root_candidate.is_file() and root_candidate.suffix.lower() in MODEL_EXTS:
        return root_candidate
    matches: list[Path] = []
    root_resolved = source_root.expanduser().resolve()
    for candidate in source_root.rglob(basename):
        if not candidate.is_file() or candidate.suffix.lower() not in MODEL_EXTS:
            continue
        resolved = candidate.resolve()
        if resolved.is_relative_to(root_resolved):
            matches.append(resolved)
            if len(matches) > 1:
                return None
    return matches[0] if len(matches) == 1 else None


def resolve_detailer_model_file(
    model_name: str,
    detector_type: str = 'bbox',
    *,
    backend_details: dict[str, Any] | None = None,
) -> Path | None:
    """Resolve one catalog model to a readable local file without exposing roots.

    The catalog value stays relative and portable. Resolution uses the same
    active-profile/Admin Models snapshot as discovery, including native Impact
    folders, the flat compatibility ``models/adetailer`` folder, top-level
    Ultralytics files, and supported ``extra_model_paths.yaml`` folders.
    """

    details = _configured_detailer_backend_details(backend_details)
    try:
        kind, relative, _load_value = _detector_relative_name(model_name, detector_type)
    except ValueError:
        return None
    roots = _detailer_roots_for_backend(details)
    candidates: list[Path] = []

    def add_candidate(root_value: str | Path | None, child: str) -> None:
        root = _safe_path(str(root_value or ''))
        if not root:
            return
        try:
            candidates.append(_resolved_child(root, child))
        except ValueError:
            return

    target_key = {'bbox': 'bbox_dir', 'segm': 'segm_dir', 'onnx': 'onnx_dir'}.get(kind, 'bbox_dir')
    add_candidate(roots.get(target_key), relative)

    flat_root = _safe_path(roots.get('adetailer_dir'))
    if flat_root:
        try:
            flat_source = _find_flat_source(flat_root, relative)
        except (OSError, ValueError):
            flat_source = None
        if flat_source:
            candidates.append(flat_source)

    # Generic YOLO checkpoints may be directly beneath ``models/ultralytics``.
    add_candidate(roots.get('ultralytics_dir'), relative)
    add_candidate(roots.get('ultralytics_dir'), PurePosixPath(relative).name)

    typed_categories = (
        EXTRA_SEGM_DETECTOR_KEYS
        if kind == 'segm'
        else EXTRA_BBOX_DETECTOR_KEYS
        if kind == 'bbox'
        else set()
    )
    extra_resolution = resolve_comfy_extra_model_folders(
        details,
        categories=set(typed_categories) | EXTRA_GENERIC_DETECTOR_KEYS,
    )
    for folder in extra_resolution.get('folders', []):
        add_candidate(folder, relative)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file() and candidate.suffix.casefold() in MODEL_EXTS:
                return candidate
        except OSError:
            continue
    return None


def _atomic_copy_if_missing(source: Path, target: Path) -> str:
    """Copy one model without changing the source or exposing partial targets."""

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0:
        return 'already_ready'
    temporary = target.with_name(f'.{target.name}.neo-stage-{uuid4().hex}.tmp')
    try:
        shutil.copy2(str(source), str(temporary))
        if temporary.stat().st_size <= 0:
            raise OSError('empty_staged_model')
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return 'staged'


def _selected_detector_units(payload: dict[str, Any]) -> list[dict[str, Any]]:
    passes = payload.get('detailer_passes') if isinstance(payload.get('detailer_passes'), list) else []
    if not passes and isinstance(payload.get('detailer'), dict):
        passes = [payload['detailer']]
    if not passes and (payload.get('detector_model') or payload.get('model')):
        passes = [payload]
    return [unit for unit in passes if isinstance(unit, dict) and bool(unit.get('enabled', True))]


def prepare_detailer_assets_for_execution(
    payload: dict[str, Any],
    *,
    backend_details: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage selected flat-folder detectors into Comfy's native load folders.

    This is intentionally an execution-time bridge, not catalog maintenance:
    only enabled selected models are considered, source files remain untouched,
    and absolute paths remain server-side.  Models that are already registered
    through Comfy or extra paths do not need a local source and remain runnable.
    """

    details = _configured_detailer_backend_details(backend_details, route=route)
    raw_root_source = str(details.get('models_root_source') or '').strip().casefold()
    safe_root_source = raw_root_source if raw_root_source in {
        'admin_models_paths',
        'backend_profile',
        'node_manager',
        'node_manager_custom_nodes',
        'node_manager_comfy_root',
        'profile_or_node_manager',
    } else 'configured_runtime'
    result: dict[str, Any] = {
        'schema_id': 'neo.image.adetailer.execution_bridge.v1',
        'status': 'not_needed',
        'models_root_source': safe_root_source,
        'attempted_count': 0,
        'staged_count': 0,
        'already_ready_count': 0,
        'not_local_count': 0,
        'blocked_count': 0,
        'records': [],
        'path_policy': 'absolute_paths_server_side_only',
    }
    if not isinstance(payload, dict):
        return result
    selected = _selected_detector_units(payload)
    if not selected:
        return result
    if not _has_local_or_shared_filesystem_access(details):
        result['status'] = 'remote_url_only'
        result['not_local_count'] = len(selected)
        return result

    roots = _detailer_roots_for_backend(details)
    models_root = _safe_path(roots.get('models_root'))
    source_root = _safe_path(roots.get('adetailer_dir'))
    if not models_root or not models_root.exists() or not models_root.is_dir():
        result['status'] = 'no_local_models_root'
        result['not_local_count'] = len(selected)
        return result

    target_roots = {
        'bbox': _safe_path(roots.get('bbox_dir')),
        'segm': _safe_path(roots.get('segm_dir')),
        'onnx': _safe_path(roots.get('onnx_dir')),
    }
    seen: set[tuple[str, str]] = set()
    for unit in selected:
        model_name = str(unit.get('detector_model') or unit.get('model') or '').strip()
        if not model_name:
            continue
        detector_type = str(unit.get('detector_type') or 'bbox').strip().lower()
        result['attempted_count'] += 1
        try:
            kind, relative, load_value = _detector_relative_name(model_name, detector_type)
        except ValueError as exc:
            result['blocked_count'] += 1
            result['records'].append({
                'model': PurePosixPath(model_name.replace('\\', '/')).name,
                'detector_type': detector_type,
                'status': 'blocked',
                'error_code': str(exc),
            })
            continue
        dedupe_key = (kind, relative.casefold())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        record = {'model': relative, 'detector_type': kind, 'load_value': load_value}
        target_root = target_roots.get(kind)
        if not target_root:
            result['blocked_count'] += 1
            result['records'].append({**record, 'status': 'blocked', 'error_code': 'target_root_unavailable'})
            continue
        try:
            target = _resolved_child(target_root, relative)
        except ValueError:
            result['blocked_count'] += 1
            result['records'].append({**record, 'status': 'blocked', 'error_code': 'target_outside_models_root'})
            continue
        if target.is_file() and target.stat().st_size > 0:
            result['already_ready_count'] += 1
            result['records'].append({**record, 'status': 'already_ready'})
            continue
        try:
            source = resolve_detailer_model_file(
                model_name,
                kind,
                backend_details=details,
            )
            if not source and source_root:
                source = _find_flat_source(source_root, relative)
        except (OSError, ValueError):
            source = None
        if not source:
            result['not_local_count'] += 1
            result['records'].append({**record, 'status': 'not_local_source'})
            continue
        try:
            copy_status = _atomic_copy_if_missing(source, target)
        except OSError:
            result['blocked_count'] += 1
            result['records'].append({**record, 'status': 'blocked', 'error_code': 'copy_failed'})
            continue
        result[f'{copy_status}_count'] += 1
        result['records'].append({**record, 'status': copy_status})

    if result['blocked_count']:
        result['status'] = 'blocked'
    elif result['staged_count']:
        result['status'] = 'staged'
    elif result['already_ready_count']:
        result['status'] = 'ready'
    elif result['attempted_count']:
        result['status'] = 'not_local_source'
    return result


def stage_detailer_assets_for_payload(payload: dict[str, Any]) -> list[str]:
    """Compatibility wrapper retained for older internal callers."""

    bridge = prepare_detailer_assets_for_execution(payload)
    if bridge.get('staged_count'):
        return [f"Staged {bridge['staged_count']} selected ADetailer detector model(s) for Comfy execution."]
    return []


def download_sam_model(model_key: str, target_root: str = '') -> dict[str, Any]:
    key = str(model_key or '').strip()
    preset = SAM_PRESETS.get(key)
    if not preset:
        raise ValueError('Pick a SAM preset first.')
    roots = default_detailer_roots()
    root_text = str(target_root or '').strip() or roots.get('sam_dir') or ''
    if not root_text:
        raise ValueError('No SAM target path is configured yet.')
    target_dir = Path(root_text).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / preset['filename']
    if not target_path.exists() or target_path.stat().st_size == 0:
        urlretrieve(preset['url'], str(target_path))
    return {'ok': True, 'key': key, 'label': preset['label'], 'filename': preset['filename'], 'path': str(target_path), 'target_root': str(target_dir)}
