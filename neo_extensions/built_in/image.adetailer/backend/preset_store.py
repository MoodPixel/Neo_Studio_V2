from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import EXTENSION_ID, EXTENSION_TYPE, WORKSPACE_APP, VERSION
from .payload_schema import normalize_block, default_payload_block

_PRESET_FILE = Path('neo_data/user/adetailer_presets.json')
_DRAFT_FILE = Path('neo_data/user/adetailer_draft.json')

_BUILT_IN_PRESETS: dict[str, dict[str, Any]] = {
    'face_clean': {
        'preset_id': 'face_clean', 'name': 'Face Clean', 'type': 'built_in',
        'params': {'enabled': True, 'confidence': 0.35, 'denoise': 0.12, 'steps': 12, 'bbox_grow': 12, 'mask_blur': 4,
                   'detailer_passes': [{'id': 'primary', 'label': 'Primary pass', 'enabled': True, 'mode': 'face', 'detector_type': 'bbox', 'detector_model': 'face_yolov8m.pt', 'positive_prompt': 'clean natural face, detailed eyes, natural skin texture', 'negative_prompt': 'deformed face, bad eyes, distorted mouth'}]},
    },
    'face_rebuild': {
        'preset_id': 'face_rebuild', 'name': 'Face Rebuild', 'type': 'built_in',
        'params': {'enabled': True, 'confidence': 0.30, 'denoise': 0.35, 'steps': 20, 'bbox_grow': 28, 'mask_blur': 8,
                   'detailer_passes': [{'id': 'primary', 'label': 'Primary pass', 'enabled': True, 'mode': 'face', 'detector_type': 'bbox', 'detector_model': 'face_yolov8m.pt', 'positive_prompt': 'rebuilt face, symmetrical eyes, clean facial details', 'negative_prompt': 'melted face, asymmetry, bad anatomy'}]},
    },
    'hands_fix': {
        'preset_id': 'hands_fix', 'name': 'Hands Fix', 'type': 'built_in',
        'params': {'enabled': True, 'confidence': 0.25, 'denoise': 0.32, 'steps': 18, 'bbox_grow': 24, 'mask_blur': 6,
                   'detailer_passes': [{'id': 'primary', 'label': 'Primary pass', 'enabled': True, 'mode': 'hands', 'detector_type': 'bbox', 'detector_model': 'hand_yolov8s.pt', 'positive_prompt': 'natural hands, correct fingers, clean hand anatomy', 'negative_prompt': 'extra fingers, fused fingers, broken hands'}]},
    },
    'eyes_polish': {
        'preset_id': 'eyes_polish', 'name': 'Eyes Polish', 'type': 'built_in',
        'params': {'enabled': True, 'confidence': 0.40, 'denoise': 0.16, 'steps': 14, 'bbox_grow': 10, 'mask_blur': 3,
                   'detailer_passes': [{'id': 'primary', 'label': 'Primary pass', 'enabled': True, 'mode': 'face', 'detector_type': 'bbox', 'detector_model': 'face_yolov8m.pt', 'positive_prompt': 'sharp expressive eyes, clean eyelashes, natural catchlights', 'negative_prompt': 'cross-eyed, blurry eyes, dead eyes'}]},
    },
    'anime_face': {
        'preset_id': 'anime_face', 'name': 'Anime Face', 'type': 'built_in',
        'params': {'enabled': True, 'confidence': 0.35, 'denoise': 0.22, 'steps': 16, 'bbox_grow': 18, 'mask_blur': 5,
                   'detailer_passes': [{'id': 'primary', 'label': 'Primary pass', 'enabled': True, 'mode': 'face', 'detector_type': 'bbox', 'detector_model': 'face_yolov8m.pt', 'positive_prompt': 'clean anime face, crisp eyes, polished linework', 'negative_prompt': 'messy face, warped anime eyes, bad lineart'}]},
    },
    'product_logo': {
        'preset_id': 'product_logo', 'name': 'Product / Logo Repair', 'type': 'built_in',
        'params': {'enabled': True, 'confidence': 0.25, 'denoise': 0.18, 'steps': 14, 'bbox_grow': 8, 'mask_blur': 2, 'custom_classes': 'product, logo, label, text',
                   'detailer_passes': [{'id': 'primary', 'label': 'Primary pass', 'enabled': True, 'mode': 'custom', 'detector_type': 'bbox', 'detector_model': '', 'positive_prompt': 'clean product detail, crisp logo, readable label', 'negative_prompt': 'warped logo, broken text, smeared label'}]},
    },
    'clothing_detail': {
        'preset_id': 'clothing_detail', 'name': 'Clothing Detail', 'type': 'built_in',
        'params': {'enabled': True, 'confidence': 0.30, 'denoise': 0.20, 'steps': 14, 'bbox_grow': 12, 'mask_blur': 5, 'custom_classes': 'clothing, fabric, outfit',
                   'detailer_passes': [{'id': 'primary', 'label': 'Primary pass', 'enabled': True, 'mode': 'custom', 'detector_type': 'segm', 'detector_model': 'person_yolov8m-seg.pt', 'positive_prompt': 'clean clothing detail, natural fabric texture', 'negative_prompt': 'warped fabric, broken seams, messy clothing'}]},
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    base = re.sub(r'[^a-zA-Z0-9_.-]+', '-', text.strip().lower()).strip('-')
    return base[:80] or 'adetailer-preset'


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return deepcopy(fallback)
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if data is not None else deepcopy(fallback)
    except Exception:
        return deepcopy(fallback)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def _normalize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    block = normalize_block({'extensions': {EXTENSION_ID: payload or default_payload_block()}} if payload and 'enabled' in payload else (payload or {}))
    block.setdefault('metadata', {})
    block['metadata'].update({
        'extension_id': EXTENSION_ID,
        'extension_type': EXTENSION_TYPE,
        'preset_safe': True,
        'draft_replay_safe': True,
    })
    return block


def built_in_presets() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for preset in _BUILT_IN_PRESETS.values():
        block = _normalize_payload({'enabled': True, 'version': VERSION, 'inputs': {}, 'params': preset['params'], 'assets': {}, 'metadata': {'preset_id': preset['preset_id'], 'preset_type': 'built_in'}})
        items.append({**preset, 'payload': block, 'params': block.get('params', {})})
    return items


def list_user_presets() -> dict[str, Any]:
    data = _read_json(_PRESET_FILE, {'presets': [], 'default_preset_id': ''})
    user_presets = data.get('presets') if isinstance(data, dict) and isinstance(data.get('presets'), list) else []
    return {
        'extension_id': EXTENSION_ID,
        'built_in_presets': built_in_presets(),
        'presets': user_presets,
        'default_preset_id': data.get('default_preset_id', '') if isinstance(data, dict) else '',
    }


def save_user_preset(name: str, payload: dict[str, Any], *, preset_id: str | None = None, make_default: bool = False) -> dict[str, Any]:
    name = (name or '').strip()
    if not name:
        raise ValueError('Preset name is required.')
    data = _read_json(_PRESET_FILE, {'presets': [], 'default_preset_id': ''})
    presets = data.get('presets') if isinstance(data, dict) and isinstance(data.get('presets'), list) else []
    pid = preset_id or _slug(name)
    block = _normalize_payload(payload)
    record = {
        'preset_id': pid,
        'name': name,
        'type': 'user',
        'extension_id': EXTENSION_ID,
        'payload': block,
        'params': block.get('params', {}),
        'created_at': _now(),
        'updated_at': _now(),
    }
    kept = []
    for item in presets:
        if item.get('preset_id') == pid:
            record['created_at'] = item.get('created_at') or record['created_at']
        else:
            kept.append(item)
    kept.append(record)
    data = {'presets': kept, 'default_preset_id': pid if make_default else data.get('default_preset_id', '')}
    _write_json(_PRESET_FILE, data)
    return record


def delete_user_preset(preset_id: str) -> dict[str, Any]:
    data = _read_json(_PRESET_FILE, {'presets': [], 'default_preset_id': ''})
    presets = data.get('presets') if isinstance(data, dict) and isinstance(data.get('presets'), list) else []
    kept = [item for item in presets if item.get('preset_id') != preset_id]
    data['presets'] = kept
    if data.get('default_preset_id') == preset_id:
        data['default_preset_id'] = ''
    _write_json(_PRESET_FILE, data)
    return {'ok': True, 'deleted': len(kept) != len(presets), 'preset_id': preset_id}


def set_default_preset(preset_id: str) -> dict[str, Any]:
    data = _read_json(_PRESET_FILE, {'presets': [], 'default_preset_id': ''})
    data['default_preset_id'] = preset_id or ''
    _write_json(_PRESET_FILE, data)
    return {'ok': True, 'default_preset_id': data['default_preset_id']}


def save_draft(payload: dict[str, Any]) -> dict[str, Any]:
    block = _normalize_payload(payload)
    record = {'extension_id': EXTENSION_ID, 'updated_at': _now(), 'payload': block}
    _write_json(_DRAFT_FILE, record)
    return record


def load_draft() -> dict[str, Any]:
    return _read_json(_DRAFT_FILE, {'extension_id': EXTENSION_ID, 'payload': _normalize_payload(default_payload_block())})
