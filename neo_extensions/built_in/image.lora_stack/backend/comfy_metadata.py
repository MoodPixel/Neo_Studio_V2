from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any
from urllib import parse, request


def _decode_response(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"ok": False, "metadata": {}, "error": "Comfy metadata response was not JSON."}
    if isinstance(payload, dict):
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
        return {"ok": True, "metadata": metadata, "raw": payload}
    return {"ok": False, "metadata": {}, "raw": payload, "error": "Comfy metadata response was not an object."}


def fetch_comfy_lora_metadata(base_url: str, lora_name: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch safetensors metadata from a running ComfyUI server.

    Comfy exposes metadata through /view_metadata/{folder_name}. Different builds accept
    either the full relative filename or filename+subfolder, so try both safely.
    """
    base = str(base_url or "").rstrip("/")
    name = str(lora_name or "").replace("\\", "/").strip()
    if not base or not name:
        return {"ok": False, "metadata": {}, "error": "Missing Comfy base URL or LoRA name."}

    posix = PurePosixPath(name)
    filename = posix.name
    subfolder = "" if str(posix.parent) == "." else str(posix.parent)
    attempts: list[str] = []
    for folder in ("loras", "lora", "Loras", "LoRAs"):
        attempts.append(f"/view_metadata/{parse.quote(folder)}?{parse.urlencode({'filename': name})}")
        if subfolder:
            attempts.append(f"/view_metadata/{parse.quote(folder)}?{parse.urlencode({'filename': filename, 'subfolder': subfolder})}")

    errors: list[str] = []
    for path in attempts:
        url = f"{base}{path}"
        try:
            with request.urlopen(url, timeout=timeout) as response:
                result = _decode_response(response.read())
                if result.get("ok") and result.get("metadata"):
                    return {**result, "source": "comfy:view_metadata", "url": url}
                errors.append(str(result.get("error") or "empty metadata"))
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    return {"ok": False, "metadata": {}, "source": "comfy:view_metadata", "attempted": attempts, "errors": errors[-4:]}
