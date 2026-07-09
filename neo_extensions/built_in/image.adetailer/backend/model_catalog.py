from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any
from urllib.request import urlretrieve

try:
    from neo_app.admin.image_node_manager import load_node_manager_settings
except Exception:  # pragma: no cover
    def load_node_manager_settings() -> dict:
        return {}

MODEL_EXTS = {'.pt', '.pth', '.onnx', '.safetensors'}
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
    settings = load_node_manager_settings()
    comfy_root = str(settings.get('comfy_root_path') or '').strip()
    if comfy_root:
        return _safe_path(comfy_root)
    custom_nodes = str(settings.get('custom_nodes_path') or '').strip()
    path = _safe_path(custom_nodes)
    if path and path.name.lower() == 'custom_nodes':
        return path.parent
    return path


def default_detailer_roots() -> dict[str, str]:
    comfy_root = comfy_root_from_node_manager()
    models_root = comfy_root / 'models' if comfy_root else None
    bbox_dir = models_root / 'ultralytics' / 'bbox' if models_root else None
    segm_dir = models_root / 'ultralytics' / 'segm' if models_root else None
    sam_dir = models_root / 'sams' if models_root else None
    return {
        'comfy_root': str(comfy_root) if comfy_root else '',
        'bbox_dir': str(bbox_dir) if bbox_dir else '',
        'segm_dir': str(segm_dir) if segm_dir else '',
        'sam_dir': str(sam_dir) if sam_dir else '',
    }


def _scan_model_dir(path: Path | None) -> list[str]:
    if not path or not path.exists() or not path.is_dir():
        return []
    rows: list[str] = []
    for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        if child.is_file() and child.suffix.lower() in MODEL_EXTS:
            rows.append(child.name)
    return rows


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


def _classify_custom_detector_files(path: Path | None) -> tuple[list[str], list[str], list[str]]:
    if not path or not path.exists() or not path.is_dir():
        return [], [], []
    bbox: list[str] = []
    segm: list[str] = []
    onnx: list[str] = []
    for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_file() or child.suffix.lower() not in MODEL_EXTS:
            continue
        name = child.name.lower()
        if child.suffix.lower() == '.onnx':
            onnx.append(child.name)
        elif 'seg' in name or 'mask' in name:
            segm.append(child.name)
        else:
            bbox.append(child.name)
    return bbox, segm, onnx


def list_detailer_models(detector_root: str = '', sam_root: str = '') -> dict[str, Any]:
    roots = default_detailer_roots()
    bbox_models = _scan_model_dir(_safe_path(roots.get('bbox_dir')))
    segm_models = _scan_model_dir(_safe_path(roots.get('segm_dir')))
    sam_models = _scan_model_dir(_safe_path(roots.get('sam_dir')))
    onnx_models: list[str] = []

    custom_detector_root = str(detector_root or '').strip()
    custom_sam_root = str(sam_root or '').strip()
    if custom_detector_root:
        custom_bbox, custom_segm, custom_onnx = _classify_custom_detector_files(_safe_path(custom_detector_root))
        bbox_models = _dedupe(bbox_models + custom_bbox)
        segm_models = _dedupe(segm_models + custom_segm)
        onnx_models = _dedupe(onnx_models + custom_onnx)
    if custom_sam_root:
        sam_models = _dedupe(sam_models + _scan_model_dir(_safe_path(custom_sam_root)))

    default_detector = bbox_models[0] if bbox_models else (segm_models[0] if segm_models else '')
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
        'model_exts': sorted(MODEL_EXTS),
        'sam_presets': [{'key': key, **value} for key, value in SAM_PRESETS.items()],
        'counts': {'bbox': len(bbox_models), 'segm': len(segm_models), 'onnx': len(onnx_models), 'sam': len(sam_models)},
    }


def _copy_if_missing(source: Path, target_dir: Path) -> str:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if not target.exists() or target.stat().st_size == 0:
        shutil.copy2(str(source), str(target))
    return target.name


def stage_detailer_assets_for_payload(payload: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if not isinstance(payload, dict):
        return notes
    roots = default_detailer_roots()
    bbox_dir = _safe_path(roots.get('bbox_dir'))
    segm_dir = _safe_path(roots.get('segm_dir'))
    sam_dir = _safe_path(roots.get('sam_dir'))
    custom_detector_root = _safe_path(payload.get('custom_detector_root') or payload.get('detailer_custom_detector_root'))
    custom_sam_root = _safe_path(payload.get('custom_sam_root') or payload.get('detailer_custom_sam_root'))
    passes = payload.get('detailer_passes') if isinstance(payload.get('detailer_passes'), list) else []
    if not passes and isinstance(payload.get('detailer'), dict):
        passes = [payload['detailer']]
    for unit in passes:
        if not isinstance(unit, dict) or not bool(unit.get('enabled', True)):
            continue
        detector_name = str(unit.get('detector_model') or unit.get('model') or '').strip()
        detector_type = str(unit.get('detector_type') or 'bbox').lower()
        if detector_name and custom_detector_root:
            target_dir = segm_dir if 'segm' in detector_type else bbox_dir
            source = custom_detector_root / detector_name
            if target_dir and source.exists() and not (target_dir / detector_name).exists():
                unit['detector_model'] = _copy_if_missing(source, target_dir)
                notes.append(f'Staged ADetailer detector into ComfyUI models: {detector_name}')
        sam_name = str(unit.get('sam_model') or payload.get('sam_model') or '').strip()
        if sam_name and custom_sam_root and sam_dir:
            source = custom_sam_root / sam_name
            if source.exists() and not (sam_dir / sam_name).exists():
                _copy_if_missing(source, sam_dir)
                notes.append(f'Staged ADetailer SAM model into ComfyUI models: {sam_name}')
    return notes


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
