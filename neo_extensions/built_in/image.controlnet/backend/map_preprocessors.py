from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import sleep, time
from typing import Any
from uuid import uuid4
import base64
import io
import json
import mimetypes
import struct
import urllib.parse
import urllib.request
import zlib

try:  # Pillow is optional; Neo Studio must boot even when image extras are not installed.
    from PIL import Image, ImageFilter, ImageOps, ImageDraw
    PIL_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised through import-safety tests.
    Image = ImageFilter = ImageOps = ImageDraw = None
    PIL_AVAILABLE = False

from .asset_contract import IMAGE_EXTENSIONS, _asset_ref
from .node_discovery import inspect_nodes, preprocessor_status

LOCAL_FALLBACK_MODES = {"canny", "softedge", "scribble", "lineart", "lineart_anime", "depth", "normalbae"}
NODE_REQUIRED_MODES = {"openpose", "dwpose", "tile"}
SUPPORTED_MODES = LOCAL_FALLBACK_MODES | NODE_REQUIRED_MODES | {"none"}


COMFY_PREPROCESSOR_CANDIDATES = {
    "canny": ["CannyEdgePreprocessor", "CannyPreprocessor", "Canny", "AIO Aux Preprocessor"],
    "openpose": ["DWPreprocessor", "OpenposePreprocessor", "OpenPosePreprocessor", "AIO Aux Preprocessor"],
    "dwpose": ["DWPreprocessor", "DWPose_Preprocessor", "AIO Aux Preprocessor"],
    "depth": ["DepthAnythingV2Preprocessor", "DepthAnythingPreprocessor", "MiDaSDepthMapPreprocessor", "MiDaS-DepthMapPreprocessor", "ZoeDepthMapPreprocessor", "Zoe-DepthMapPreprocessor", "AIO Aux Preprocessor"],
    "lineart": ["LineArtPreprocessor", "LineartPreprocessor", "LineartStandardPreprocessor", "AIO Aux Preprocessor"],
    "lineart_anime": ["LineartAnimePreprocessor", "LineArtAnimePreprocessor", "AnimeLineArtPreprocessor", "AIO Aux Preprocessor"],
    "softedge": ["HEDPreprocessor", "SoftEdgePreprocessor", "PiDiNetPreprocessor", "AnyLinePreprocessor", "AIO Aux Preprocessor"],
    "scribble": ["ScribblePreprocessor", "Scribble_XDoG_Preprocessor", "Scribble_PiDiNet_Preprocessor", "AIO Aux Preprocessor"],
    "normalbae": ["BAE-NormalMapPreprocessor", "NormalBaePreprocessor", "NormalMapPreprocessor", "AIO Aux Preprocessor"],
    "tile": ["TilePreprocessor", "TilePreprocessorProvider", "AIO Aux Preprocessor"],
}

COMFY_AIO_ALIASES = {
    "canny": ["CannyEdgePreprocessor", "CannyPreprocessor", "canny"],
    "openpose": ["DWPreprocessor", "OpenposePreprocessor", "openpose_full", "openpose"],
    "dwpose": ["DWPreprocessor", "DWPose_Preprocessor", "dwpose", "openpose_full"],
    "depth": ["DepthAnythingV2Preprocessor", "DepthAnythingPreprocessor", "MiDaSDepthMapPreprocessor", "depth_anything_v2", "depth"],
    "lineart": ["LineArtPreprocessor", "lineart_standard", "lineart"],
    "lineart_anime": ["LineartAnimePreprocessor", "lineart_anime"],
    "softedge": ["HEDPreprocessor", "SoftEdgePreprocessor", "softedge_hed", "softedge"],
    "scribble": ["ScribblePreprocessor", "scribble_xdog", "scribble"],
    "normalbae": ["BAE-NormalMapPreprocessor", "normalbae", "normal_bae"],
    "tile": ["TilePreprocessor", "tile"],
}


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return struct.pack("!I", len(data)) + chunk_type + data + struct.pack("!I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)


def _solid_png_bytes(width: int = 512, height: int = 512, rgb: tuple[int, int, int] = (16, 24, 39)) -> bytes:
    """Return a dependency-free RGB PNG used when Pillow is unavailable.

    This is intentionally tiny/simple: it prevents Neo Studio startup failures and
    keeps the map preview tile visible, while real edge/depth processing remains
    available when Pillow or Comfy preprocessors are installed.
    """
    width = max(1, int(width or 512))
    height = max(1, int(height or 512))
    r, g, b = [max(0, min(255, int(v))) for v in rgb]
    row = b"\x00" + bytes([r, g, b]) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )




def _comfy_base(url: str | None) -> str:
    url = str(url or "http://127.0.0.1:8188").strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


def _comfy_json(base_url: str, endpoint: str, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "NeoStudio-ControlNet-MapGen/1.0"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_comfy_base(base_url) + endpoint, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw or "{}")


def _comfy_upload_image(base_url: str, raw: bytes, filename: str, mime_type: str = "image/png", timeout: float = 30.0) -> str:
    boundary = "----NeoControlNetMapGen" + uuid4().hex
    safe_filename = Path(filename).name or f"controlnet_source_{uuid4().hex[:8]}.png"
    fields = {
        "overwrite": "true",
        "subfolder": "neo_studio_controlnet",
        "type": "input",
    }
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode("utf-8"))
    parts.append((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{safe_filename}\"\r\n"
        f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n"
    ).encode("utf-8"))
    parts.append(raw)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        _comfy_base(base_url) + "/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    name = result.get("name") or result.get("filename") or safe_filename
    subfolder = result.get("subfolder") or fields["subfolder"]
    return f"{subfolder}/{name}" if subfolder else str(name)


def _default_for_input(spec: Any) -> Any:
    if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]:
        return spec[1]["default"]
    if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list) and spec[0]:
        return spec[0][0]
    return None


def _fill_preprocessor_inputs(class_type: str, object_info: dict[str, Any], kind: str, request: dict[str, Any], image_resolution: int) -> dict[str, Any]:
    meta = object_info.get(class_type) or {}
    inputs_meta: dict[str, Any] = {}
    for group in ("required", "optional"):
        group_inputs = ((meta.get("input") or {}).get(group) or {})
        if isinstance(group_inputs, dict):
            inputs_meta.update(group_inputs)
    settings = request.get("settings") if isinstance(request.get("settings"), dict) else {}
    inputs: dict[str, Any] = {}
    linked = False
    for name, spec in inputs_meta.items():
        low = str(name).lower()
        if low in ("image", "input_image") or (not linked and "image" in low and "resolution" not in low):
            inputs[name] = ["1", 0]
            linked = True
        elif low in ("detect_resolution", "resolution"):
            inputs[name] = int(settings.get("detect_resolution") or image_resolution or 512)
        elif low in ("image_resolution", "output_resolution"):
            inputs[name] = int(image_resolution or settings.get("detect_resolution") or 512)
        elif low in ("low_threshold", "lowth", "threshold_low"):
            inputs[name] = int(settings.get("canny_low") or 100)
        elif low in ("high_threshold", "highth", "threshold_high"):
            inputs[name] = int(settings.get("canny_high") or 200)
        elif low in ("include_body", "body"):
            inputs[name] = True
        elif low in ("include_hand", "include_hands", "hand", "hands"):
            inputs[name] = bool(request.get("openpose_hand") or False)
        elif low in ("include_face", "face"):
            inputs[name] = bool(request.get("openpose_face") or False)
        elif low in ("preprocessor", "preprocessor_name", "aux_preprocessor", "processor"):
            choices = COMFY_AIO_ALIASES.get(kind, [kind])
            default = _default_for_input(spec)
            if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list):
                available = spec[0]
                pick = next((c for c in choices if c in available), None) or default or (available[0] if available else kind)
                inputs[name] = pick
            else:
                inputs[name] = choices[0]
        else:
            default = _default_for_input(spec)
            if default is not None:
                inputs[name] = default
    if not linked:
        inputs["image"] = ["1", 0]
    return inputs
def _data_url_from_bytes(raw: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64," + base64.b64encode(raw).decode("ascii")


def _decode_data_image(source_preview: str) -> tuple[bytes, str, str] | None:
    text = str(source_preview or "").strip()
    if not text.startswith("data:image/") or "," not in text:
        return None
    header, encoded = text.split(",", 1)
    mime = header[5:].split(";", 1)[0] or "image/png"
    ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
    }.get(mime, "png")
    try:
        return base64.b64decode(encoded), mime, ext
    except Exception:
        return None


def canonical_map_mode(mode: str | None) -> str:
    text = str(mode or "none").strip().lower().replace("-", "_")
    aliases = {
        "open_pose": "openpose",
        "dw_pose": "dwpose",
        "normal": "normalbae",
        "normal_bae": "normalbae",
        "anime_lineart": "lineart_anime",
        "lineart_anime": "lineart_anime",
        "hed": "softedge",
        "pidinet": "softedge",
        "midas": "depth",
        "zoe": "depth",
        "depth_anything": "depth",
    }
    return aliases.get(text, text)


def map_mode_status(mode: str, available_preprocessors: dict[str, list[str]] | None = None) -> dict[str, object]:
    mode = canonical_map_mode(mode)
    available_preprocessors = available_preprocessors or {}
    if mode == "none":
        return {"mode": mode, "state": "available", "backend": "identity", "node": None, "reason": "Using the supplied control image directly as a map."}
    if mode not in SUPPORTED_MODES:
        return {"mode": mode, "state": "unsupported", "reason": "Unknown ControlNet map mode."}
    lookup = "openpose" if mode == "dwpose" else mode
    nodes = available_preprocessors.get(lookup) or available_preprocessors.get(mode) or []
    if nodes:
        return {"mode": mode, "state": "available", "backend": "comfy_preprocessor", "node": nodes[0]}
    if mode in LOCAL_FALLBACK_MODES:
        return {"mode": mode, "state": "experimental_available", "backend": "local_fallback", "node": None, "reason": "Comfy preprocessor node was not detected; V1-compatible local fallback contract is available."}
    return {"mode": mode, "state": "provider_gated", "reason": "This ControlNet map mode requires an installed Comfy/custom preprocessor node."}


def list_preprocessor_options(
    object_info: dict[str, Any] | None = None,
    *,
    backend_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node_status = inspect_nodes(object_info, backend_details=backend_details)
    options = []
    for mode in ["none", "canny", "depth", "openpose", "dwpose", "lineart", "lineart_anime", "softedge", "scribble", "normalbae", "tile"]:
        status = map_mode_status(mode, node_status.get("preprocessors") or {})
        options.append({
            "id": mode,
            "label": {
                "none": "None / use image as map",
                "canny": "Canny",
                "depth": "Depth",
                "openpose": "OpenPose",
                "dwpose": "DWPose",
                "lineart": "Lineart",
                "lineart_anime": "Anime Lineart",
                "softedge": "SoftEdge",
                "scribble": "Scribble",
                "normalbae": "NormalBae",
                "tile": "Tile",
            }.get(mode, mode),
            **status,
        })
    return {"ok": True, "schema_version": "neo.image.controlnet.preprocessors.v1", "node_status": node_status, "options": options}


def build_map_request(payload: dict[str, Any] | None, *, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    unit = payload.get("unit") if isinstance(payload.get("unit"), dict) else payload
    uid = str(unit.get("uid") or payload.get("uid") or "unit_1")
    mode = canonical_map_mode(unit.get("preprocessor") or unit.get("unit") or payload.get("mode") or "none")
    node_status = inspect_nodes(object_info or payload.get("object_info") or {})
    mode_state = map_mode_status(mode, node_status.get("preprocessors") or {})
    source = unit.get("control_image") or payload.get("control_image") or payload.get("source") or payload.get("source_image") or ""
    source_preview = unit.get("control_image_preview") or payload.get("control_image_preview") or unit.get("source_preview") or payload.get("source_preview") or ""
    return {
        "schema_version": "neo.image.controlnet.map_request.v1",
        "uid": uid,
        "mode": mode,
        "source": _asset_ref(source) if source else {},
        "source_preview": str(source_preview or ""),
        "openpose_hand": bool(unit.get("openpose_hand") or payload.get("openpose_hand") or False),
        "openpose_face": bool(unit.get("openpose_face") or payload.get("openpose_face") or False),
        "settings": {
            "detect_resolution": int(unit.get("detect_resolution") or payload.get("detect_resolution") or 512),
            "fit_mode": unit.get("fit_mode") or payload.get("fit_mode") or "contain",
            "canny_low": int(unit.get("canny_low") or payload.get("canny_low") or 100),
            "canny_high": int(unit.get("canny_high") or payload.get("canny_high") or 200),
            "safe_mode": unit.get("safe_mode", payload.get("safe_mode", True)) is not False,
            "invert_map": bool(unit.get("invert_map") or payload.get("invert_map") or False),
        },
        "mode_status": mode_state,
        "node_status": node_status,
    }

def _decode_preview_image(source_preview: str) -> Image.Image | None:
    if not PIL_AVAILABLE:
        return None
    decoded = _decode_data_image(source_preview)
    if decoded is None:
        return None
    raw, _mime, _ext = decoded
    try:
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None


def _load_source_image(request: dict[str, Any]) -> Image.Image:
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed; image decoding is unavailable.")
    source_preview = str(request.get("source_preview") or "")
    preview_img = _decode_preview_image(source_preview)
    if preview_img is not None:
        return preview_img
    source = request.get("source") or {}
    ref = str(source.get("path") or source.get("ref") or source.get("url") or "").strip()
    if ref:
        candidate = Path(ref)
        if candidate.exists() and candidate.is_file():
            try:
                return Image.open(candidate).convert("RGB")
            except Exception:
                pass
    # Safe fallback: create a visible neutral map tile instead of a broken JSON ref.
    img = Image.new("RGB", (512, 512), (16, 24, 39))
    draw = ImageDraw.Draw(img)
    draw.rectangle((32, 32, 480, 480), outline=(80, 130, 170), width=3)
    draw.text((72, 226), "ControlNet map", fill=(180, 210, 235))
    draw.text((72, 252), str(request.get("mode") or "map"), fill=(130, 170, 200))
    return img


def _resize_for_detect(img: Image.Image, request: dict[str, Any]) -> Image.Image:
    settings = request.get("settings") if isinstance(request.get("settings"), dict) else {}
    detect = int(settings.get("detect_resolution") or 512)
    detect = max(64, min(4096, detect))
    img.thumbnail((detect, detect))
    return img.copy()


def _build_local_map_image(request: dict[str, Any]) -> Image.Image:
    mode = str(request.get("mode") or "none")
    img = _resize_for_detect(_load_source_image(request), request)
    if mode == "none":
        result = img
    elif mode == "canny":
        # Lightweight V1-style local fallback approximation: grayscale edge map.
        result = ImageOps.grayscale(img).filter(ImageFilter.FIND_EDGES).convert("RGB")
    elif mode in {"softedge", "scribble", "lineart", "lineart_anime"}:
        result = ImageOps.grayscale(img).filter(ImageFilter.FIND_EDGES)
        result = ImageOps.autocontrast(result).convert("RGB")
    elif mode == "depth":
        result = ImageOps.grayscale(img).convert("RGB")
    elif mode == "normalbae":
        result = ImageOps.grayscale(img).filter(ImageFilter.EMBOSS).convert("RGB")
    else:
        result = img
    settings = request.get("settings") if isinstance(request.get("settings"), dict) else {}
    if settings.get("invert_map"):
        result = ImageOps.invert(result.convert("RGB"))
    return result.convert("RGB")


def _encode_png_data_url(img: Image.Image) -> tuple[bytes, str]:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    return raw, _data_url_from_bytes(raw, "image/png")




def _source_image_bytes(request: dict[str, Any]) -> tuple[bytes, str, str] | None:
    decoded = _decode_data_image(str(request.get("source_preview") or ""))
    if decoded is not None:
        raw, mime_type, ext = decoded
        return raw, mime_type, ext
    source = request.get("source") if isinstance(request.get("source"), dict) else {}
    ref = str(source.get("path") or source.get("ref") or source.get("url") or "").strip()
    if not ref:
        return None
    if ref.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(ref, timeout=20.0) as resp:
                raw = resp.read()
            mime_type = mimetypes.guess_type(ref)[0] or "image/png"
            ext = mimetypes.guess_extension(mime_type) or Path(urllib.parse.urlparse(ref).path).suffix or ".png"
            return raw, mime_type, ext.lstrip(".")
        except Exception:
            return None
    candidate = Path(ref)
    if candidate.exists() and candidate.is_file():
        raw = candidate.read_bytes()
        mime_type = mimetypes.guess_type(str(candidate))[0] or "image/png"
        return raw, mime_type, candidate.suffix.lstrip(".") or "png"
    return None


def _pick_comfy_preprocessor_node(object_info: dict[str, Any], kind: str, preferred: str | None = None) -> str:
    if preferred and preferred in object_info:
        return preferred
    nodes = set(object_info.keys())
    for name in COMFY_PREPROCESSOR_CANDIDATES.get(kind, []):
        if name in nodes:
            return name
    low_kind = kind.replace("_", "")
    for name in sorted(nodes):
        low = name.lower().replace("_", "")
        if "preprocessor" in low and low_kind in low:
            return name
    return ""


def _download_comfy_image(base_url: str, image_info: dict[str, Any], timeout: float = 30.0) -> tuple[bytes, str]:
    qs = urllib.parse.urlencode({
        "filename": image_info.get("filename") or "",
        "subfolder": image_info.get("subfolder") or "",
        "type": image_info.get("type") or "output",
    })
    with urllib.request.urlopen(_comfy_base(base_url) + "/view?" + qs, timeout=timeout) as resp:
        raw = resp.read()
    return raw, "image/png"


def _run_comfy_preprocessor(request: dict[str, Any], object_info: dict[str, Any], runtime: dict[str, Any] | None = None) -> tuple[bytes, str, str, str, str] | None:
    runtime = runtime or {}
    base_url = runtime.get("base_url") or runtime.get("comfy_url") or "http://127.0.0.1:8188"
    timeout = float(runtime.get("timeout_seconds") or runtime.get("timeout") or 30)
    kind = canonical_map_mode(request.get("mode"))
    class_type = _pick_comfy_preprocessor_node(object_info, kind, (request.get("mode_status") or {}).get("node"))
    if not class_type:
        return None
    source = _source_image_bytes(request)
    if source is None:
        return None
    raw_source, source_mime, source_ext = source
    source_name = f"neo_controlnet_{request.get('uid') or 'unit'}_{uuid4().hex[:8]}.{source_ext or 'png'}"
    image_name = _comfy_upload_image(str(base_url), raw_source, source_name, source_mime, timeout=timeout)
    detect = int((request.get("settings") or {}).get("detect_resolution") or 512)
    prefix = f"neo_studio_controlnet/{kind}_{uuid4().hex[:10]}"
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": class_type, "inputs": _fill_preprocessor_inputs(class_type, object_info, kind, request, detect)},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": prefix}},
    }
    prompt = _comfy_json(str(base_url), "/prompt", {"prompt": workflow, "client_id": "neo_studio_controlnet_mapgen_" + uuid4().hex}, timeout=timeout)
    prompt_id = prompt.get("prompt_id")
    if not prompt_id:
        return None
    item: dict[str, Any] = {}
    for _ in range(120):
        sleep(0.5)
        history = _comfy_json(str(base_url), f"/history/{prompt_id}", timeout=10.0)
        if prompt_id in history:
            item = history.get(prompt_id) or {}
            break
    outputs = item.get("outputs") or {}
    images: list[dict[str, Any]] = []
    for out in outputs.values():
        images.extend(out.get("images") or [])
    if not images:
        return None
    raw, mime_type = _download_comfy_image(str(base_url), images[-1], timeout=timeout)
    return raw, _data_url_from_bytes(raw, mime_type), "png", mime_type, f"comfy_preprocessor:{class_type}"
def _build_map_output_bytes(request: dict[str, Any], *, object_info: dict[str, Any] | None = None, runtime: dict[str, Any] | None = None) -> tuple[bytes, str, str, str, str]:
    status = request.get("mode_status") if isinstance(request.get("mode_status"), dict) else {}
    if status.get("backend") == "comfy_preprocessor" and object_info:
        try:
            comfy_result = _run_comfy_preprocessor(request, object_info, runtime)
            if comfy_result is not None:
                return comfy_result
        except Exception:
            # Fall through to local fallback instead of breaking the tool.
            pass

    if PIL_AVAILABLE:
        raw, preview_data_url = _encode_png_data_url(_build_local_map_image(request))
        return raw, preview_data_url, "png", "image/png", "pillow_local_fallback"

    # Without Pillow, do not pretend the original image is a processed map for node-backed preprocessors.
    # Return a visible diagnostic PNG so the user sees the map build is not a real processed edge/depth map yet.
    raw = _solid_png_bytes(rgb=(20, 38, 54))
    return raw, _data_url_from_bytes(raw, "image/png"), "png", "image/png", "placeholder_no_pillow_or_comfy"

def _fake_output_ref(root: str | Path, request: dict[str, Any], *, object_info: dict[str, Any] | None = None, runtime: dict[str, Any] | None = None) -> dict[str, str]:
    """Create a real previewable PNG map ref.

    Earlier Phase F emitted a placeholder JSON contract. V1 returned a real image
    preview/output URL, and Phase G expects the generated map to become the active
    ControlNet source. This preserves that V1 behavior while still keeping the
    implementation local/safe when Comfy preprocessing is not available.
    """
    output_dir = Path(root) / "neo_data" / "controlnet_maps"
    output_dir.mkdir(parents=True, exist_ok=True)
    uid = str(request.get("uid") or "unit")
    mode = str(request.get("mode") or "map")
    raw, preview_data_url, ext, mime_type, generation_backend = _build_map_output_bytes(request, object_info=object_info, runtime=runtime)
    filename = f"controlnet_{uid}_{mode}_{uuid4().hex[:10]}.{ext}"
    path = output_dir / filename
    path.write_bytes(raw)

    # V1 parity: the preview URL is for Neo's UI, but the workflow patch must
    # feed Comfy's LoadImage with a Comfy input filename. A Neo API URL/local
    # neo_data path is previewable in the browser but does not act as a valid
    # Comfy LoadImage source. Upload the final map PNG to Comfy input when a
    # backend runtime/base_url is available, and return that as image_name.
    comfy_image_name = ""
    runtime = runtime or {}
    base_url = runtime.get("base_url") or runtime.get("comfy_url") or ""
    if base_url:
        try:
            comfy_image_name = _comfy_upload_image(str(base_url), raw, filename, mime_type or "image/png", timeout=float(runtime.get("timeout_seconds") or runtime.get("timeout") or 30))
        except Exception:
            comfy_image_name = ""

    return {
        "map_id": filename,
        "filename": filename,
        "comfy_image_name": comfy_image_name,
        "image_name": comfy_image_name or filename,
        "path": str(path),
        "url": f"/api/extensions/controlnet/maps/file/{filename}",
        "preview_url": f"/api/extensions/controlnet/maps/file/{filename}",
        "preview_data_url": preview_data_url,
        "mime_type": mime_type,
        "generation_backend": generation_backend,
        "workflow_source": comfy_image_name or filename,
    }

def preview_map_payload(root: str | Path, payload: dict[str, Any] | None, *, object_info: dict[str, Any] | None = None, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time()
    request = build_map_request(payload, object_info=object_info)
    status = request["mode_status"]
    if status.get("state") in {"unsupported", "provider_gated"}:
        return {"ok": False, "state": status.get("state"), "reason": status.get("reason"), "request": request}
    source = request.get("source") or {}
    if request.get("mode") != "none" and not source.get("ref"):
        return {"ok": False, "state": "validation_failed", "reason": "A control image/source is required to build a ControlNet map.", "request": request}
    output = _fake_output_ref(root, request, object_info=object_info, runtime=runtime)
    return {
        "ok": True,
        "schema_version": "neo.image.controlnet.map_preview.v1",
        "state": status.get("state"),
        "backend": status.get("backend"),
        "node": status.get("node"),
        "fallback": status.get("backend") == "local_fallback",
        "request": request,
        "output": output,
        "output_url": output.get("url"),
        "preview_data_url": output.get("preview_data_url"),
        "generated_map": {request["uid"]: output},
        "elapsed": round(time() - started, 4),
        "note": "Build Map returns a previewable image map ref and stores it as the active generated map. Comfy preprocessors are used when available; otherwise Neo falls back locally when possible or shows a diagnostic placeholder instead of silently reusing the source image.",
    }


def batch_preview_payload(root: str | Path, payload: dict[str, Any] | None, *, object_info: dict[str, Any] | None = None, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    units = payload.get("units") if isinstance(payload.get("units"), list) else []
    results = [preview_map_payload(root, {"unit": deepcopy(unit)}, object_info=object_info or payload.get("object_info") or {}, runtime=runtime or payload.get("runtime") or {}) for unit in units]
    return {
        "ok": all(item.get("ok") for item in results),
        "schema_version": "neo.image.controlnet.batch_map_preview.v1",
        "batch_id": f"controlnet_batch_{uuid4().hex[:10]}",
        "count": len(results),
        "completed": sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok")),
        "results": results,
        "manifest": {"generated_maps": {k: v for item in results if item.get("ok") for k, v in (item.get("generated_map") or {}).items()}},
    }
