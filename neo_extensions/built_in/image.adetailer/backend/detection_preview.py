from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model_catalog import default_detailer_roots

try:  # optional preview dependencies
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None
    np = None

_ORDER_MODES = {'auto','left_to_right','right_to_left','top_to_bottom','bottom_to_top','largest_first','smallest_first','center_first'}
_PRIORITY_PRESETS = {'respect_pass','primary_subject','primary_plus_secondary','balanced','crowd_scan'}
_FOREGROUND_BIAS_LABELS = {'off':'Off','center_bias':'Center bias','foreground_subjects':'Foreground boost','pinned_subjects':'Pinned subjects'}
_PRIORITY_PRESET_LABELS = {'respect_pass':'Respect pass settings','primary_subject':'Main subject only','primary_plus_secondary':'Main + secondary','balanced':'Balanced','crowd_scan':'Crowd scan'}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1','true','yes','on','enabled'}


def _clamp_int(value: Any, fallback: int, min_v: int, max_v: int) -> int:
    try:
        return max(min_v, min(max_v, int(float(value))))
    except Exception:
        return fallback


def _clamp_float(value: Any, fallback: float, min_v: float, max_v: float) -> float:
    try:
        return max(min_v, min(max_v, float(value)))
    except Exception:
        return fallback


def _decode_image(raw: bytes):
    if not raw:
        raise ValueError('Upload an image first.')
    if cv2 is None or np is None:
        raise RuntimeError('Detection preview needs opencv-python and numpy installed.')
    arr = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('The uploaded image could not be decoded.')
    height, width = image.shape[:2]
    return image, int(width), int(height)


def resolve_detailer_model_path(detector_model: str, detector_type: str = 'bbox', detector_root: str = '') -> Path | None:
    model = str(detector_model or '').strip()
    if not model:
        return None
    roots = default_detailer_roots()
    candidates: list[Path] = []
    normalized = model.replace('\\', '/')
    model_path = Path(normalized)
    basename = model_path.name
    if detector_root:
        root = Path(detector_root).expanduser()
        candidates.append(root / normalized)
        candidates.append(root / basename)
        candidates.append(root / ('segm' if 'segm' in str(detector_type or '').lower() else 'bbox') / basename)
        candidates.append(root / 'bbox' / basename)
        candidates.append(root / 'segm' / basename)
    key = 'segm_dir' if 'segm' in str(detector_type or '').lower() else 'bbox_dir'
    if roots.get(key):
        candidates.append(Path(roots[key]) / normalized)
        candidates.append(Path(roots[key]) / basename)
    if roots.get('ultralytics_dir'):
        candidates.append(Path(roots['ultralytics_dir']) / normalized)
        candidates.append(Path(roots['ultralytics_dir']) / basename)
    for extra_key in ('bbox_dir', 'segm_dir'):
        if roots.get(extra_key):
            candidates.append(Path(roots[extra_key]) / basename)
    for item in candidates:
        try:
            if item.exists() and item.is_file():
                return item
        except Exception:
            continue
    return candidates[0] if candidates else None


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = int(a.get('x') or 0), int(a.get('y') or 0)
    ax2, ay2 = ax1 + int(a.get('w') or 0), ay1 + int(a.get('h') or 0)
    bx1, by1 = int(b.get('x') or 0), int(b.get('y') or 0)
    bx2, by2 = bx1 + int(b.get('w') or 0), by1 + int(b.get('h') or 0)
    ix1, iy1, ix2, iy2 = max(ax1,bx1), max(ay1,by1), min(ax2,bx2), min(ay2,by2)
    iw, ih = max(0, ix2-ix1), max(0, iy2-iy1)
    inter = iw * ih
    area = max(1, int(a.get('area') or (ax2-ax1)*(ay2-ay1))) + max(1, int(b.get('area') or (bx2-bx1)*(by2-by1))) - inter
    return inter / area if area > 0 else 0.0


def _nms(rows: list[dict[str, Any]], threshold: float = 0.35) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda x: (-float(x.get('confidence') or 0), -int(x.get('area') or 0))):
        if all(_iou(row, kept) < threshold for kept in out):
            out.append(row)
    return out


def _run_face_fallback(image, confidence: float, bbox_grow: int, width: int, height: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        cascade_path = getattr(cv2.data, 'haarcascades', '') + 'haarcascade_frontalface_default.xml'
        detector = cv2.CascadeClassifier(cascade_path)
        faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(24,24))
    except Exception:
        faces = []
    for i, (x, y, w, h) in enumerate(faces, start=1):
        gx = max(0, int(x) - bbox_grow)
        gy = max(0, int(y) - bbox_grow)
        gr = min(width, int(x + w) + bbox_grow)
        gb = min(height, int(y + h) + bbox_grow)
        rows.append({'id': i, 'x': gx, 'y': gy, 'w': max(1, gr-gx), 'h': max(1, gb-gy), 'area': max(1, gr-gx)*max(1, gb-gy), 'confidence': round(max(confidence, 0.5), 4), 'label': 'face', 'source': 'auto', 'selected': True})
    return _nms(rows)



def _run_ultralytics_preview(model_path: Path | None, image, confidence: float, bbox_grow: int, width: int, height: int) -> tuple[list[dict[str, Any]], str | None]:
    """Run the same YOLO-style detector family V1/Impact Pack uses when available."""
    if model_path is None:
        return [], 'No detector model path was resolved for Ultralytics preview.'
    try:
        if not model_path.exists():
            return [], f'Detector model file was not found: {model_path.name}'
    except Exception:
        return [], f'Detector model file was not accessible: {model_path.name}'
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception:
        return [], 'Install ultralytics to enable YOLO detector preview parity with V1.'
    try:
        model = YOLO(str(model_path))
        results = model.predict(source=image, conf=confidence, verbose=False)
    except Exception as exc:
        return [], f'Ultralytics could not execute detector {model_path.name}: {type(exc).__name__}.'
    rows: list[dict[str, Any]] = []
    for result in results or []:
        boxes = getattr(result, 'boxes', None)
        if boxes is None:
            continue
        xyxy = getattr(boxes, 'xyxy', None)
        confs = getattr(boxes, 'conf', None)
        clss = getattr(boxes, 'cls', None)
        names = getattr(result, 'names', {}) or {}
        try:
            xyxy_list = xyxy.cpu().numpy().tolist() if hasattr(xyxy, 'cpu') else xyxy.tolist()
        except Exception:
            xyxy_list = []
        try:
            conf_list = confs.cpu().numpy().tolist() if hasattr(confs, 'cpu') else (confs.tolist() if confs is not None else [])
        except Exception:
            conf_list = []
        try:
            cls_list = clss.cpu().numpy().tolist() if hasattr(clss, 'cpu') else (clss.tolist() if clss is not None else [])
        except Exception:
            cls_list = []
        for i, coords in enumerate(xyxy_list, start=1):
            if len(coords) < 4:
                continue
            x1, y1, x2, y2 = [float(v) for v in coords[:4]]
            gx = max(0, int(round(x1)) - bbox_grow)
            gy = max(0, int(round(y1)) - bbox_grow)
            gr = min(width, int(round(x2)) + bbox_grow)
            gb = min(height, int(round(y2)) + bbox_grow)
            w, h = max(1, gr - gx), max(1, gb - gy)
            cls_id = int(cls_list[i-1]) if i-1 < len(cls_list) else 0
            label = str(names.get(cls_id, 'target')) if isinstance(names, dict) else 'target'
            rows.append({
                'id': i,
                'x': gx,
                'y': gy,
                'w': w,
                'h': h,
                'area': w * h,
                'confidence': round(float(conf_list[i-1]) if i-1 < len(conf_list) else confidence, 4),
                'label': label,
                'source': 'ultralytics',
                'selected': True,
            })
    return _nms(rows), None

def _run_people_fallback(image, bbox_grow: int, width: int, height: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        rects, weights = hog.detectMultiScale(image, winStride=(8,8), padding=(16,16), scale=1.05)
    except Exception:
        rects, weights = [], []
    for i, rect in enumerate(rects, start=1):
        x, y, w, h = [int(v) for v in rect]
        gx = max(0, x - bbox_grow); gy = max(0, y - bbox_grow); gr = min(width, x+w+bbox_grow); gb = min(height, y+h+bbox_grow)
        conf = 0.55
        try:
            conf = max(0.01, min(0.99, float(weights[i-1])))
        except Exception:
            pass
        rows.append({'id': i, 'x': gx, 'y': gy, 'w': max(1, gr-gx), 'h': max(1, gb-gy), 'area': max(1, gr-gx)*max(1, gb-gy), 'confidence': round(conf,4), 'label':'person', 'source':'auto', 'selected':True})
    return _nms(rows)


def _sort_for_order(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == 'left_to_right': return sorted(rows, key=lambda r:(r['x'], r['y']))
    if mode == 'right_to_left': return sorted(rows, key=lambda r:(-r['x'], r['y']))
    if mode == 'top_to_bottom': return sorted(rows, key=lambda r:(r['y'], r['x']))
    if mode == 'bottom_to_top': return sorted(rows, key=lambda r:(-r['y'], r['x']))
    if mode == 'largest_first': return sorted(rows, key=lambda r:-int(r.get('area') or 0))
    if mode == 'smallest_first': return sorted(rows, key=lambda r:int(r.get('area') or 0))
    return sorted(rows, key=lambda r:(-float(r.get('confidence') or 0), -int(r.get('area') or 0), r['y'], r['x']))


def _parse_boxes(raw_value: Any) -> list[dict[str, Any]]:
    if isinstance(raw_value, list):
        data = raw_value
    else:
        try:
            data = json.loads(str(raw_value or '[]'))
        except Exception:
            data = []
    out = []
    for i, item in enumerate(data if isinstance(data, list) else []):
        if not isinstance(item, dict): continue
        x = _clamp_int(item.get('x'), 0, 0, 999999); y = _clamp_int(item.get('y'), 0, 0, 999999)
        w = _clamp_int(item.get('w'), 0, 1, 999999); h = _clamp_int(item.get('h'), 0, 1, 999999)
        if w > 0 and h > 0:
            out.append({'x': x, 'y': y, 'w': w, 'h': h, 'area': w*h, 'track_id': str(item.get('track_id') or f'subject-h{i+1}'), 'pinned': bool(item.get('pinned')), 'locked': bool(item.get('locked'))})
    return out


def _apply_priority(rows: list[dict[str, Any]], *, priority_preset: str, order_mode: str, start_index: int, count: int, top_k: int, min_area: int, max_area: int, mode: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    notes: list[str] = []
    effective = {'order_mode': order_mode, 'start_index': start_index, 'count': count, 'top_k': top_k, 'min_area': min_area, 'max_area': max_area}
    if priority_preset == 'primary_subject':
        effective.update({'order_mode': 'largest_first' if order_mode == 'auto' else order_mode, 'start_index': 1, 'count': 1, 'top_k': max(1, top_k)})
        notes.append('Priority preset focused preview on the strongest main subject candidate.')
    elif priority_preset == 'primary_plus_secondary':
        effective.update({'order_mode': 'largest_first' if order_mode == 'auto' else order_mode, 'start_index': 1, 'count': 2, 'top_k': max(2, top_k)})
        notes.append('Priority preset focused preview on the main subject plus one secondary candidate.')
    elif priority_preset == 'balanced' and count <= 0 and top_k <= 0:
        effective['count'] = 3
        notes.append('Balanced preset selected the strongest few candidates.')
    elif priority_preset == 'crowd_scan':
        effective.update({'start_index': 1, 'count': 0, 'top_k': 0, 'order_mode': 'left_to_right' if mode in {'face','person'} and order_mode == 'auto' else order_mode})
        notes.append('Crowd scan kept the broader candidate set visible.')
    eligible = [dict(r) for r in rows]
    if effective['min_area'] > 0: eligible = [r for r in eligible if int(r.get('area') or 0) >= effective['min_area']]
    if effective['max_area'] > 0: eligible = [r for r in eligible if int(r.get('area') or 0) <= effective['max_area']]
    if effective['top_k'] > 0: eligible = _sort_for_order(eligible, effective['order_mode'])[:effective['top_k']]
    eligible = _sort_for_order(eligible, effective['order_mode'])
    if effective['start_index'] > 1: eligible = eligible[effective['start_index'] - 1:]
    if effective['count'] > 0: eligible = eligible[:effective['count']]
    selected_ids = {id(r) for r in eligible}
    # Select by coordinate signature to survive copies.
    selected_sig = {(r.get('x'), r.get('y'), r.get('w'), r.get('h')) for r in eligible}
    ordered = _sort_for_order(rows, effective['order_mode'])
    n = 0
    for idx, row in enumerate(ordered, start=1):
        row['ordered_index'] = idx
        sig = (row.get('x'), row.get('y'), row.get('w'), row.get('h'))
        selected = sig in selected_sig
        row['selected'] = selected
        if selected:
            n += 1; row['target_index'] = n; row['prompt_index'] = n; row['number_label'] = f'#{n}'
            row['group_key'] = 'primary' if n == 1 else ('secondary' if n <= 3 else 'active')
            row['group_label'] = 'Primary subject' if n == 1 else ('Secondary targets' if n <= 3 else 'Additional active targets')
        else:
            row['target_index'] = 0; row['prompt_index'] = 0; row['number_label'] = ''; row['group_key'] = 'skipped'; row['group_label'] = 'Skipped by current filters'
    return ordered, effective, notes


def preview_detailer_detections(
    raw_image: bytes,
    settings: dict[str, Any] | None = None,
    *,
    resolved_model_path: Path | None = None,
) -> dict[str, Any]:
    payload = dict(settings or {})
    image, width, height = _decode_image(raw_image)
    provider = str(payload.get('provider') or 'ultralytics').lower()
    mode = str(payload.get('mode') or payload.get('target') or 'face').lower()
    detector_type = str(payload.get('detector_type') or 'bbox').lower()
    detector_model = str(payload.get('detector_model') or '').strip()
    detector_root = str(payload.get('custom_detector_root') or payload.get('detector_root') or '').strip()
    confidence = _clamp_float(payload.get('confidence'), 0.35, 0.01, 0.99)
    bbox_grow = _clamp_int(payload.get('bbox_grow'), 0, -128, 512)
    top_k = _clamp_int(payload.get('top_k'), 0, 0, 999)
    order_mode = str(payload.get('order_mode') or payload.get('target_order') or 'auto').lower()
    if order_mode not in _ORDER_MODES: order_mode = 'auto'
    priority_preset = str(payload.get('priority_preset') or 'respect_pass').lower()
    if priority_preset not in _PRIORITY_PRESETS: priority_preset = 'respect_pass'
    start_index = _clamp_int(payload.get('start_index'), 1, 1, 999)
    count = _clamp_int(payload.get('count'), 0, 0, 999)
    min_area = _clamp_int(payload.get('min_area'), 0, 0, 999999999)
    max_area = _clamp_int(payload.get('max_area'), 0, 0, 999999999)
    resolved_model = resolved_model_path or resolve_detailer_model_path(detector_model, detector_type, detector_root)
    strict_detector = _as_bool(payload.get('strict_detector'), False)
    warnings: list[str] = []
    strategy = 'opencv-face'
    detections: list[dict[str, Any]] = []
    # V1 parity path: prefer the real YOLO/Ultralytics detector model used by Impact Pack/ADetailer.
    if provider == 'ultralytics' and detector_model:
        detections, ultra_warning = _run_ultralytics_preview(resolved_model, image, confidence=confidence, bbox_grow=bbox_grow, width=width, height=height)
        if ultra_warning is None:
            strategy = 'ultralytics-yolo'
        else:
            warnings.append(ultra_warning)
            if strict_detector:
                raise RuntimeError(f'Selected detector could not run. {ultra_warning}')
    # Safe fallback only when the real detector bridge is unavailable. Haar/HOG is weaker and can miss side/profile faces.
    if not detections and not (strict_detector and provider == 'ultralytics' and detector_model):
        if mode == 'person':
            detections = _run_people_fallback(image, bbox_grow=bbox_grow, width=width, height=height)
            strategy = 'opencv-hog-person'
        else:
            if mode == 'hands':
                warnings.append('Hands preview uses face fallback unless an external detector preview bridge is installed.')
            detections = _run_face_fallback(image, confidence=confidence, bbox_grow=bbox_grow, width=width, height=height)
            strategy = 'opencv-face-fallback' if mode == 'hands' else 'opencv-face'
    history = _parse_boxes(payload.get('history_boxes'))
    for row in detections:
        row['reacquired'] = any(_iou(row, old) >= 0.12 for old in history)
    detections, effective_filters, preset_notes = _apply_priority(detections, priority_preset=priority_preset, order_mode=order_mode, start_index=start_index, count=count, top_k=top_k, min_area=min_area, max_area=max_area, mode=mode)
    warnings.extend(preset_notes)
    selected_count = sum(1 for r in detections if r.get('selected'))
    message = f'Detector preview found {len(detections)} target(s); {selected_count} currently selected for manual-box handoff.' if detections else 'Detector preview did not find any usable targets on this image.'
    if not detections:
        warnings.append('Try lowering confidence, using a YOLO face/person model, or using manual boxes for exact control.')
    return {
        'ok': True,
        'image_width': width,
        'image_height': height,
        'preview_mode': strategy,
        'provider': provider,
        'resolved_model_path': str(resolved_model) if resolved_model else '',
        'detections': detections,
        'selected_count': selected_count,
        'suppressed_count': 0,
        'merged_cluster_count': 0,
        'reacquired_pinned_count': sum(1 for r in detections if r.get('reacquired')),
        'priority_preset': priority_preset,
        'priority_preset_label': _PRIORITY_PRESET_LABELS.get(priority_preset, 'Respect pass settings'),
        'foreground_bias': str(payload.get('foreground_bias') or 'off'),
        'foreground_bias_label': _FOREGROUND_BIAS_LABELS.get(str(payload.get('foreground_bias') or 'off'), 'Off'),
        'effective_filters': effective_filters,
        'target_order': effective_filters.get('order_mode') or order_mode,
        'suppression_settings': {'auto_suppress_tiny_faces': _as_bool(payload.get('auto_suppress_tiny_faces'), True), 'cluster_merge': _as_bool(payload.get('cluster_merge'), True)},
        'message': message,
        'warnings': warnings,
        'tuning_hints': ['Use manual boxes if automatic preview misses a target.'] if not detections else [],
    }
