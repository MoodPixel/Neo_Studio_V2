from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib import parse, request
from uuid import uuid4

from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part

ROOT_DIR = Path(__file__).resolve().parents[2]
VIDEO_SOURCE_DIR = ROOT_DIR / "neo_data" / "outputs" / "video" / "source"
IMAGE_SOURCE_DIR = ROOT_DIR / "neo_data" / "inputs" / "image"


def _safe_name(value: str | None) -> str:
    return Path(str(value or "").split("?", 1)[0]).name


def _existing_file(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        if path.exists() and path.is_file():
            return path.resolve()
    except OSError:
        return None
    return None


def resolve_video_source_image_path(payload: dict[str, Any] | None) -> Path | None:
    """Resolve a Video source image reference into a Neo-owned local file path.

    Video uploads are stored under neo_data/outputs/video/source, while a few
    cross-surface handoffs may point at neo_data/inputs/image. Comfy LoadImage
    cannot read those Neo paths directly; the resolved file is later uploaded to
    Comfy's input folder.
    """
    data = payload if isinstance(payload, dict) else {}
    candidates: list[str] = []
    for key in (
        "source_image",
        "source_image_path",
        "image",
        "init_image",
        "first_image",
        "first_image_path",
    ):
        value = str(data.get(key) or "").strip()
        if value:
            candidates.append(value)
    for key in ("source_id", "source_image_id", "source_image_name", "source_image_comfy_name", "comfy_source_image_name", "image_name"):
        value = _safe_name(str(data.get(key) or ""))
        if value:
            candidates.append(value)
    for key in ("source_image_url", "source_url", "image_url"):
        value = str(data.get(key) or "").strip()
        if value:
            candidates.append(_safe_name(value))

    search_dirs = [
        get_video_output_paths("source", create=True).output_dir,
        VIDEO_SOURCE_DIR,
        IMAGE_SOURCE_DIR,
    ]
    seen: set[str] = set()
    for raw in candidates:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        if raw.startswith(("http://", "https://")):
            continue
        if raw.startswith("/api/video/source-file/"):
            raw = raw.rsplit("/", 1)[-1]
        if raw.startswith("/api/image/source-file/"):
            raw = raw.rsplit("/", 1)[-1]
        path = Path(raw)
        if path.is_absolute():
            found = _existing_file(path)
            if found:
                return found
        else:
            found = _existing_file((ROOT_DIR / raw).resolve())
            if found:
                return found
            safe = _safe_name(raw)
            for folder in search_dirs:
                found = _existing_file(folder / safe)
                if found:
                    return found
    return None


def _post_multipart_image(base_url: str, image_path: Path, *, timeout: float = 10.0, prefix: str = "neo_video_source") -> dict[str, Any]:
    boundary = f"----NeoStudioVideoBoundary{uuid4().hex}"
    source_name = sanitize_path_part(image_path.name, fallback="source.png")
    filename = f"{prefix}_{uuid4().hex[:8]}_{source_name}"
    content_type = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode())
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode())
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.extend(image_path.read_bytes())
    body.extend(b"\r\n")
    add_field("type", "input")
    add_field("overwrite", "true")
    body.extend(f"--{boundary}--\r\n".encode())

    url = parse.urljoin(base_url.rstrip("/") + "/", "upload/image")
    req = request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=max(timeout, 10.0)) as response:  # noqa: S310 - user-configured local Comfy URL.
        raw = response.read().decode("utf-8", errors="replace").strip()
    payload: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            payload = parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            payload = {"raw": raw}
    name = str(payload.get("name") or payload.get("filename") or filename).strip() or filename
    return {"ok": True, "name": name, "type": payload.get("type") or "input", "subfolder": payload.get("subfolder") or "", "response": payload}


def verify_comfy_input_image(base_url: str, image_name: str, *, timeout: float = 5.0) -> bool:
    safe = _safe_name(image_name)
    if not safe:
        return False
    query = parse.urlencode({"filename": safe, "type": "input"})
    url = parse.urljoin(base_url.rstrip("/") + "/", f"view?{query}")
    try:
        req = request.Request(url, headers={"User-Agent": "NeoStudioVideoComfyInputHandoff/1.0"}, method="GET")
        with request.urlopen(req, timeout=max(timeout, 5.0)) as response:  # noqa: S310 - user-configured local Comfy URL.
            return 200 <= int(getattr(response, "status", 200)) < 300
    except Exception:
        return False


def prepare_video_source_image_handoff(payload: dict[str, Any] | None, base_url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """Upload the selected Video source image to Comfy input and patch payload names.

    Returns a payload copy with source_image_name/comfy_source_image_name set to a
    real Comfy input filename. This is the critical handoff that prevents Comfy
    LoadImage validation errors like "Invalid image file: images.jpg".
    """
    data = dict(payload or {})
    existing_comfy_name = str(data.get("comfy_source_image_name") or data.get("source_image_comfy_name") or "").strip()
    if existing_comfy_name and verify_comfy_input_image(base_url, existing_comfy_name, timeout=timeout):
        data["source_image_name"] = existing_comfy_name
        data["source_image_comfy_name"] = existing_comfy_name
        data["comfy_source_image_name"] = existing_comfy_name
        return {"ok": True, "uploaded": False, "comfy_image_name": existing_comfy_name, "payload": data, "verified": True, "source_path": ""}

    source_path = resolve_video_source_image_path(data)
    if source_path is None:
        # Last chance: maybe the visible value is already a valid Comfy input filename.
        for key in ("source_image_name", "source_image", "image_name", "image"):
            candidate = _safe_name(str(data.get(key) or ""))
            if candidate and verify_comfy_input_image(base_url, candidate, timeout=timeout):
                data["source_image_name"] = candidate
                data["source_image_comfy_name"] = candidate
                data["comfy_source_image_name"] = candidate
                return {"ok": True, "uploaded": False, "comfy_image_name": candidate, "payload": data, "verified": True, "source_path": ""}
        return {
            "ok": False,
            "uploaded": False,
            "comfy_image_name": "",
            "payload": data,
            "verified": False,
            "source_path": "",
            "error": "Video source image could not be resolved to a Neo-owned file or existing Comfy input image.",
        }

    upload = _post_multipart_image(base_url, source_path, timeout=timeout)
    comfy_name = str(upload.get("name") or "").strip()
    verified = verify_comfy_input_image(base_url, comfy_name, timeout=timeout) if comfy_name else False
    if not comfy_name:
        return {"ok": False, "uploaded": False, "comfy_image_name": "", "payload": data, "verified": False, "source_path": str(source_path), "error": "Comfy /upload/image did not return an image name."}

    data["source_image_name"] = comfy_name
    data["source_image_comfy_name"] = comfy_name
    data["comfy_source_image_name"] = comfy_name
    data["source_image_uploaded_to_comfy"] = True
    return {
        "ok": True,
        "uploaded": True,
        "comfy_image_name": comfy_name,
        "payload": data,
        "verified": verified,
        "source_path": str(source_path),
        "upload_response": upload.get("response", {}),
        "warning": "Uploaded image could not be verified through /view; Comfy may still accept the returned upload name." if not verified else "",
    }
