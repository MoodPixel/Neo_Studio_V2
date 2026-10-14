from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
from pathlib import Path
from typing import Any, BinaryIO
from urllib import parse, request
from uuid import uuid4

from neo_app.providers.profiles import get_backend_profile_for_live_task, get_backend_profile_for_runtime


COMFY_ASSET_TRANSPORT_SCHEMA_VERSION = "neo.comfy_asset_transport.v1"
_COMFY_PROVIDER_IDS = {"comfyui", "comfyui_portable"}


class ComfyAssetTransportError(RuntimeError):
    """Raised when Neo cannot safely hand an asset to/from one selected Comfy runtime."""


def _profile_base_url(profile: dict[str, Any]) -> str:
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    return str(connection.get("base_url") or runtime.get("base_url") or "http://127.0.0.1:8188").strip().rstrip("/")


def resolve_comfy_asset_profile(profile_id: str, *, timeout: float | None = None) -> dict[str, Any]:
    """Resolve exactly one selected Comfy profile for asset transport.

    This never scans or falls back to a different profile. The returned base URL is
    backend-only data and must not be forwarded to the browser as an asset record.
    """

    clean_profile_id = str(profile_id or "").strip()
    if not clean_profile_id:
        raise ComfyAssetTransportError("A Comfy backend profile id is required for asset transport.")
    profile = get_backend_profile_for_live_task(clean_profile_id) or get_backend_profile_for_runtime(clean_profile_id)
    if not profile:
        raise ComfyAssetTransportError(f"Unknown backend profile: {clean_profile_id}")
    provider_id = str(profile.get("provider_id") or "").strip()
    if provider_id not in _COMFY_PROVIDER_IDS:
        raise ComfyAssetTransportError(f"Backend profile '{clean_profile_id}' is not a ComfyUI profile.")
    if profile.get("enabled") is False:
        raise ComfyAssetTransportError(f"Backend profile '{clean_profile_id}' is disabled.")

    base_url = _profile_base_url(profile)
    parsed = parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ComfyAssetTransportError("ComfyUI profile base URL must use http or https.")
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    request_timeout = float(timeout if timeout is not None else connection.get("timeout_seconds") or 10.0)
    request_timeout = max(0.5, min(request_timeout, 120.0))
    return {
        "profile_id": clean_profile_id,
        "provider_id": provider_id,
        "base_url": base_url,
        "timeout": request_timeout,
    }


def _http_connection(base_url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parsed = parse.urlsplit(base_url)
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
    elif parsed.scheme == "http":
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    else:  # pragma: no cover - guarded by profile resolver
        raise ComfyAssetTransportError("Unsupported Comfy transport URL scheme.")
    prefix = (parsed.path or "").rstrip("/")
    return connection, prefix


def _send_multipart_file(
    *,
    base_url: str,
    timeout: float,
    endpoint: str,
    field_name: str,
    file_path: Path,
    filename: str,
    fields: dict[str, str],
) -> dict[str, Any]:
    """Stream one multipart file with stdlib HTTP so large video/audio files are not buffered in RAM."""

    boundary = f"----NeoComfySpaceAsset{uuid4().hex}"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    preamble = bytearray()
    for name, value in fields.items():
        preamble.extend(f"--{boundary}\r\n".encode())
        preamble.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        preamble.extend(str(value).encode("utf-8"))
        preamble.extend(b"\r\n")
    preamble.extend(f"--{boundary}\r\n".encode())
    preamble.extend(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode())
    preamble.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    epilogue = f"\r\n--{boundary}--\r\n".encode()
    file_size = file_path.stat().st_size
    content_length = len(preamble) + file_size + len(epilogue)

    connection, prefix = _http_connection(base_url, timeout)
    route = f"{prefix}{endpoint}" or endpoint
    try:
        connection.putrequest("POST", route)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        connection.putheader("Accept", "application/json")
        connection.endheaders()
        connection.send(bytes(preamble))
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                connection.send(chunk)
        connection.send(epilogue)
        response = connection.getresponse()
        raw = response.read(2 * 1024 * 1024)
        if response.status < 200 or response.status >= 300:
            detail = raw.decode("utf-8", errors="replace")[:500]
            raise ComfyAssetTransportError(f"Comfy asset upload failed with HTTP {response.status}: {detail}")
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            raise ComfyAssetTransportError("Comfy asset upload returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ComfyAssetTransportError("Comfy asset upload returned an invalid payload.")
        return payload
    except (OSError, http.client.HTTPException) as exc:
        raise ComfyAssetTransportError(f"Unable to upload asset to ComfyUI: {exc}") from exc
    finally:
        connection.close()


def _safe_comfy_name(filename: str, *, kind: str, sha256: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if not suffix or len(suffix) > 16:
        suffix = ".bin"
    clean_kind = str(kind or "file").strip().lower()
    if clean_kind not in {"image", "mask", "video", "audio", "file"}:
        clean_kind = "file"
    digest = str(sha256 or "").lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ComfyAssetTransportError("Asset SHA-256 is invalid for Comfy handoff.")
    return f"neo_cs_{clean_kind}_{digest}{suffix}"


def verify_comfy_input_asset(
    profile_id: str,
    *,
    filename: str,
    subfolder: str = "",
    timeout: float | None = None,
) -> bool:
    resolved = resolve_comfy_asset_profile(profile_id, timeout=timeout)
    query = parse.urlencode({"filename": filename, "subfolder": subfolder, "type": "input"})
    url = f"{resolved['base_url']}/view?{query}"
    try:
        with request.urlopen(url, timeout=float(resolved["timeout"])) as response:  # noqa: S310 - selected local/admin backend profile.
            response.read(1)
            return 200 <= int(getattr(response, "status", 200) or 200) < 300
    except Exception:
        return False


def upload_comfy_input_asset(
    profile_id: str,
    *,
    file_path: Path | str,
    original_filename: str,
    kind: str,
    sha256: str,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Upload a Neo-owned asset into one selected Comfy input directory.

    ComfyUI's long-standing ``/upload/image`` route is used as the compatibility
    transport because its server implementation writes the posted file bytes into
    the requested input directory and returns a generic name/subfolder/type record.
    Neo does not depend on the route name to infer media kind.
    """

    source = Path(file_path).resolve()
    if not source.is_file():
        raise ComfyAssetTransportError("Neo asset content is missing before Comfy handoff.")
    resolved = resolve_comfy_asset_profile(profile_id, timeout=timeout)
    upload_name = _safe_comfy_name(original_filename, kind=kind, sha256=sha256)

    # Cached content-addressed handoffs are reusable only after re-verification.
    if verify_comfy_input_asset(profile_id, filename=upload_name, timeout=float(resolved["timeout"])):
        return {
            "schema_version": COMFY_ASSET_TRANSPORT_SCHEMA_VERSION,
            "ok": True,
            "profile_id": resolved["profile_id"],
            "provider_id": resolved["provider_id"],
            "method": "comfy_upload_image_compat",
            "name": upload_name,
            "subfolder": "",
            "type": "input",
            "comfy_value": upload_name,
            "verified": True,
            "reused": True,
            "base_url_exposed": False,
        }

    payload = _send_multipart_file(
        base_url=str(resolved["base_url"]),
        timeout=float(resolved["timeout"]),
        endpoint="/upload/image",
        field_name="image",
        file_path=source,
        filename=upload_name,
        fields={"type": "input", "subfolder": "", "overwrite": "true"},
    )
    returned_name = str(payload.get("name") or upload_name).strip()
    returned_subfolder = str(payload.get("subfolder") or "").strip().replace("\\", "/").strip("/")
    returned_type = str(payload.get("type") or "input").strip() or "input"
    if returned_type != "input":
        raise ComfyAssetTransportError("Comfy asset upload did not return an input-folder asset.")
    if not returned_name or Path(returned_name).name != returned_name or ".." in returned_name:
        raise ComfyAssetTransportError("Comfy asset upload returned an unsafe filename.")
    if returned_subfolder and any(part in {"", ".", ".."} for part in returned_subfolder.split("/")):
        raise ComfyAssetTransportError("Comfy asset upload returned an unsafe subfolder.")
    if not verify_comfy_input_asset(
        profile_id,
        filename=returned_name,
        subfolder=returned_subfolder,
        timeout=float(resolved["timeout"]),
    ):
        raise ComfyAssetTransportError("Comfy accepted the asset upload but Neo could not verify the input asset.")
    comfy_value = "/".join(part for part in (returned_subfolder, returned_name) if part)
    return {
        "schema_version": COMFY_ASSET_TRANSPORT_SCHEMA_VERSION,
        "ok": True,
        "profile_id": resolved["profile_id"],
        "provider_id": resolved["provider_id"],
        "method": "comfy_upload_image_compat",
        "name": returned_name,
        "subfolder": returned_subfolder,
        "type": returned_type,
        "comfy_value": comfy_value,
        "verified": True,
        "reused": False,
        "base_url_exposed": False,
    }


def _validate_comfy_artifact_coordinates(filename: str, subfolder: str, artifact_type: str) -> tuple[str, str, str]:
    safe_filename = str(filename or "").strip()
    if not safe_filename or Path(safe_filename).name != safe_filename or safe_filename in {".", ".."}:
        raise ComfyAssetTransportError("Comfy artifact filename is unsafe.")
    safe_subfolder = str(subfolder or "").strip().replace("\\", "/").strip("/")
    if safe_subfolder and any(part in {"", ".", ".."} for part in safe_subfolder.split("/")):
        raise ComfyAssetTransportError("Comfy artifact subfolder is unsafe.")
    safe_type = str(artifact_type or "output").strip().lower() or "output"
    if safe_type not in {"input", "output", "temp"}:
        raise ComfyAssetTransportError("Comfy artifact type must be input, output, or temp.")
    return safe_filename, safe_subfolder, safe_type


def download_comfy_artifact(
    profile_id: str,
    *,
    filename: str,
    subfolder: str = "",
    artifact_type: str = "output",
    destination: Path | str,
    max_bytes: int,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Stream one already-produced Comfy artifact into a Neo-owned temporary file."""

    safe_filename, safe_subfolder, safe_type = _validate_comfy_artifact_coordinates(filename, subfolder, artifact_type)
    resolved = resolve_comfy_asset_profile(profile_id, timeout=timeout)
    query = parse.urlencode({"filename": safe_filename, "subfolder": safe_subfolder, "type": safe_type})
    url = f"{resolved['base_url']}/view?{query}"
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    digest = hashlib.sha256()
    try:
        with request.urlopen(url, timeout=float(resolved["timeout"])) as response:  # noqa: S310 - selected local/admin backend profile.
            content_type = str(response.headers.get_content_type() or "application/octet-stream")
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > int(max_bytes):
                        raise ComfyAssetTransportError(f"Comfy artifact exceeds the {int(max_bytes)} byte Neo asset limit.")
                    digest.update(chunk)
                    handle.write(chunk)
    except ComfyAssetTransportError:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise ComfyAssetTransportError(f"Unable to collect Comfy artifact: {exc}") from exc
    if size <= 0:
        target.unlink(missing_ok=True)
        raise ComfyAssetTransportError("Comfy artifact is empty.")
    return {
        "schema_version": COMFY_ASSET_TRANSPORT_SCHEMA_VERSION,
        "ok": True,
        "profile_id": resolved["profile_id"],
        "provider_id": resolved["provider_id"],
        "method": "comfy_view_stream",
        "filename": safe_filename,
        "subfolder": safe_subfolder,
        "type": safe_type,
        "content_type": content_type,
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "base_url_exposed": False,
    }


def comfy_asset_transport_capabilities() -> dict[str, Any]:
    return {
        "schema_version": COMFY_ASSET_TRANSPORT_SCHEMA_VERSION,
        "status": "implemented",
        "selected_profile_only": True,
        "fallback_profile_used": False,
        "streaming_upload": True,
        "streaming_download": True,
        "generic_input_transport": "comfy_upload_image_compat",
        "artifact_collection_transport": "comfy_view_stream",
        "content_addressed_handoff_names": True,
        "input_verification": True,
        "workflow_execution": False,
        "prompt_submission": False,
        "base_url_exposed_to_browser": False,
    }
