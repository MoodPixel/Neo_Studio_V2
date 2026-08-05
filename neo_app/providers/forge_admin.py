from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request
from urllib.parse import urlparse

from neo_app.providers.forge_neo_bridge import bridge_snapshot_payload, forge_bridge_headers, forge_bridge_mode
from neo_app.providers.forge_neo_extension_bridge import build_forge_generic_extension_bridge, forge_generic_extension_bridge_contract_payload
from neo_app.providers.forge_neo_loader_translation import forge_loader_translation_contract_payload
from neo_app.providers.forge_neo_workflow_compilers import forge_workflow_compiler_contract_payload
from neo_app.providers.forge_neo_model_classification import (
    build_forge_live_model_classification,
    build_forge_live_route_intersection,
    ensure_forge_live_discovery,
)
from neo_app.runtime_data import provider_capability_cache_path
from neo_app.providers.forge_neo_ip_adapter import build_forge_ip_adapter_capability
from neo_app.providers.forge_neo_shared_models import build_forge_shared_model_paths_capability, forge_cmd_flags_probe_policy


FORGE_ADMIN_SCHEMA_ID = "neo.provider.forge_admin.v1"
FORGE_SETTINGS_SCHEMA_ID = "neo.provider.forge_settings_catalog.v1"
FORGE_CACHE_FILENAME = "forge_capabilities.json"

FORGE_CORE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("models", "/sdapi/v1/sd-models"),
    ("samplers", "/sdapi/v1/samplers"),
    ("schedulers", "/sdapi/v1/schedulers"),
)
FORGE_OPTIONAL_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("modules", "/sdapi/v1/sd-modules"),
    ("upscalers", "/sdapi/v1/upscalers"),
    ("face_restorers", "/sdapi/v1/face-restorers"),
    ("scripts", "/sdapi/v1/scripts"),
    ("script_info", "/sdapi/v1/script-info"),
    ("extensions", "/sdapi/v1/extensions"),
    ("embeddings", "/sdapi/v1/embeddings"),
    ("loras", "/sdapi/v1/loras"),
    ("memory", "/sdapi/v1/memory"),
    ("cmd_flags", "/sdapi/v1/cmd-flags"),
    ("openapi", "/openapi.json"),
)

GUIDED_SETTING_KEYS: frozenset[str] = frozenset(
    {
        "sd_model_checkpoint",
        "forge_additional_modules",
        "CLIP_stop_at_last_layers",
        "samples_save",
        "samples_format",
        "jpeg_quality",
        "webp_lossless",
        "show_progress_every_n_steps",
        "live_previews_enable",
        "sd_checkpoint_cache",
        "sd_vae_checkpoint_cache",
        "persistent_cond_cache",
        "always_discard_next_to_last_sigma",
        "enable_quantization",
        "emphasis",
        "randn_source",
        "eta_noise_seed_delta",
        "img2img_color_correction",
        "img2img_fix_steps",
        "img2img_background_color",
        "inpainting_mask_weight",
        "mask_blur_x",
        "mask_blur_y",
        "upscaling_max_images_in_cache",
    }
)

_SENSITIVE_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "auth",
    "cookie",
)
_PATH_TOKENS = (
    "path",
    "folder",
    "directory",
    "_dir",
    "home",
    "root",
    "filename",
    "file_name",
    "tls_key",
    "tls_cert",
)
_NETWORK_TOKENS = (
    "listen",
    "server_name",
    "port",
    "tls",
    "cors",
    "allowed_path",
    "api_server_stop",
)
_RESTART_HINT_TOKENS = (
    "precision",
    "attention",
    "cuda",
    "xformers",
    "flash",
    "sage",
    "compile",
    "memory",
    "offload",
    "quant",
    "dtype",
)
_ABSOLUTE_PATH_RE = re.compile(r"(?:^[A-Za-z]:[\\/]|^/|^~[/\\]|^\\\\)")
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ForgeAdminResponse:
    status_code: int
    data: Any = None
    text: str = ""
    headers: dict[str, str] | None = None


class ForgeAdminRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, kind: str = "request_failed", detail: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind
        self.detail = detail


class ForgeAdminHttpClient:
    """Small stdlib HTTP client for Forge Admin probes.

    The client intentionally has no dependency on the future Forge execution
    provider. Phase 2 can therefore discover and manage Admin state while image
    generation remains route-gated.
    """

    def request_json(self, method: str, base_url: str, path: str, *, timeout: float = 10.0, payload: Any = None, headers: dict[str, str] | None = None) -> ForgeAdminResponse:
        url = f"{base_url.rstrip('/')}/{str(path or '').lstrip('/')}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, headers=request_headers, method=method.upper())
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - user-configured local backend URL
                raw = response.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw.strip() else {}
                return ForgeAdminResponse(status_code=int(getattr(response, "status", 200)), data=data, text=raw, headers=dict(response.headers.items()))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise ForgeAdminRequestError(
                f"Forge endpoint returned HTTP {exc.code}.",
                status_code=int(exc.code),
                kind="http_error",
                detail=detail,
            ) from exc
        except error.URLError as exc:
            raise ForgeAdminRequestError(f"Could not reach Forge: {exc.reason}", kind="offline", detail=str(exc.reason)) from exc
        except json.JSONDecodeError as exc:
            raise ForgeAdminRequestError("Forge endpoint returned non-JSON content.", kind="invalid_json", detail=str(exc)) from exc
        except TimeoutError as exc:
            raise ForgeAdminRequestError("Forge request timed out.", kind="timeout", detail=str(exc)) from exc

    def request_text(self, method: str, base_url: str, path: str, *, timeout: float = 10.0) -> ForgeAdminResponse:
        url = f"{base_url.rstrip('/')}/{str(path or '').lstrip('/')}"
        req = request.Request(url, headers={"Accept": "text/html,application/json"}, method=method.upper())
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - user-configured local backend URL
                raw = response.read().decode("utf-8", errors="replace")
                return ForgeAdminResponse(status_code=int(getattr(response, "status", 200)), text=raw, headers=dict(response.headers.items()))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise ForgeAdminRequestError(
                f"Forge endpoint returned HTTP {exc.code}.",
                status_code=int(exc.code),
                kind="http_error",
                detail=detail,
            ) from exc
        except error.URLError as exc:
            raise ForgeAdminRequestError(f"Could not reach Forge: {exc.reason}", kind="offline", detail=str(exc.reason)) from exc
        except TimeoutError as exc:
            raise ForgeAdminRequestError("Forge request timed out.", kind="timeout", detail=str(exc)) from exc


def _valid_base_url(base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
    )


def _safe_profile_id(profile_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in str(profile_id or "forge_profile").strip()).strip("_") or "forge_profile"


def forge_admin_cache_path(profile_id: str, *, root_dir: Path | str | None = None) -> Path:
    return provider_capability_cache_path(_safe_profile_id(profile_id), FORGE_CACHE_FILENAME, root_dir)


def forge_admin_cache_metadata(profile_id: str) -> dict[str, Any]:
    safe_id = _safe_profile_id(profile_id)
    return {
        "runtime_store": True,
        "repo_template": False,
        "gitignored": True,
        "relative_path": f"neo_data/provider_cache/{safe_id}/{FORGE_CACHE_FILENAME}",
        "contains_backend_paths": False,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def load_forge_admin_cache(profile_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any] | None:
    path = forge_admin_cache_path(profile_id, root_dir=root_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _endpoint_record(path: str, *, ok: bool, status_code: int | None = None, message: str = "", required: bool = False, error_kind: str = "") -> dict[str, Any]:
    return {
        "path": path,
        "available": bool(ok),
        "status": "available" if ok else "unavailable",
        "http_status": status_code,
        "required": required,
        "message": message,
        "error_kind": error_kind,
    }


def _probe_json_endpoint(client: ForgeAdminHttpClient, base_url: str, path: str, *, timeout: float, required: bool = False) -> tuple[dict[str, Any], Any]:
    try:
        response = client.request_json("GET", base_url, path, timeout=timeout)
        return _endpoint_record(path, ok=True, status_code=response.status_code, required=required), response.data
    except ForgeAdminRequestError as exc:
        return _endpoint_record(path, ok=False, status_code=exc.status_code, message=str(exc), required=required, error_kind=exc.kind), None


def _basename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1] if text else ""


def _portable_name(value: Any) -> str:
    text = str(value or "").strip()
    return _basename(text) if _looks_like_path(text) else text


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key or "").casefold()
    return any(token in lowered for token in _SENSITIVE_TOKENS)


def _is_path_key(key: str) -> bool:
    lowered = str(key or "").casefold()
    return any(token in lowered for token in _PATH_TOKENS)


def _is_network_key(key: str) -> bool:
    lowered = str(key or "").casefold()
    return any(token in lowered for token in _NETWORK_TOKENS)


def _looks_like_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    clean = value.strip()
    return bool(clean and (_ABSOLUTE_PATH_RE.search(clean) or _URI_RE.search(clean) or "\\" in clean or clean.startswith("./") or clean.startswith("../")))


def _safe_value(key: str, value: Any) -> Any:
    has_value = value is not None and value != "" and value is not False
    if _is_sensitive_key(key):
        return "<redacted>" if has_value else ""
    if _is_path_key(key) or _looks_like_path(value):
        return "<configured-path>" if has_value else ""
    if isinstance(value, dict):
        return {str(child_key): _safe_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_safe_value(key, item) for item in value]
    return value


def _sanitize_model_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = _portable_name(item.get("title") or item.get("model_name") or item.get("name"))
        if not title:
            continue
        records.append(
            {
                "title": title,
                "model_name": _portable_name(item.get("model_name") or title),
                "hash": item.get("hash"),
                "sha256": item.get("sha256"),
                "filename": _basename(item.get("filename")),
                "config": _basename(item.get("config")),
            }
        )
    return records


def _sanitize_module_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = _portable_name(item.get("model_name") or item.get("name"))
        if name:
            records.append({"model_name": name, "filename": _basename(item.get("filename"))})
    return records


def _sanitize_named_records(payload: Any, *, fields: Iterable[str] = ("name", "label")) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        clean: dict[str, Any] = {}
        for field in fields:
            if field in item and item.get(field) is not None:
                clean[field] = _safe_value(field, item.get(field))
        if clean:
            records.append(clean)
    return records


def _sanitize_upscalers(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = _portable_name(item.get("name") or item.get("model_name"))
        if name:
            records.append(
                {
                    "name": name,
                    "model_name": str(item.get("model_name") or ""),
                    "model_file": _basename(item.get("model_path")),
                    "scale": item.get("scale"),
                }
            )
    return records


def _sanitize_scripts(payload: Any) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        return {"txt2img": [], "img2img": []}
    return {
        "txt2img": [str(item) for item in payload.get("txt2img") or [] if str(item or "").strip()],
        "img2img": [str(item) for item in payload.get("img2img") or [] if str(item or "").strip()],
    }


def _safe_script_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _ABSOLUTE_PATH_RE.search(text) or _URI_RE.search(text):
            return _basename(text)
        return text[:500]
    if isinstance(value, list):
        return [_safe_script_scalar(item) for item in value[:500] if isinstance(item, (str, bool, int, float, dict)) or item is None]
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in list(value.items())[:120]:
            safe = _safe_script_scalar(item)
            if safe is not None:
                clean[str(key)[:120]] = safe
        return clean
    return None


def _sanitize_script_info(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        if not name:
            continue
        raw_args = item.get("args") if isinstance(item.get("args"), list) else []
        args: list[dict[str, Any]] = []
        for index, arg in enumerate(raw_args):
            if not isinstance(arg, dict):
                continue
            record: dict[str, Any] = {
                "index": index,
                "label": str(arg.get("label") or "").strip()[:240],
            }
            for key in ("value", "minimum", "maximum", "step", "choices"):
                value = _safe_script_scalar(arg.get(key))
                if value not in (None, "", []):
                    record[key] = value
            args.append(record)
        records.append({
            "name": name,
            "alwayson": bool(item.get("is_alwayson", item.get("alwayson", False))),
            "is_img2img": bool(item.get("is_img2img", False)),
            "argument_count": len(raw_args),
            "args": args,
        })
    return records


def _normalized_script_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _script_records(script_info: list[dict[str, Any]], token: str) -> list[dict[str, Any]]:
    target = _normalized_script_name(token)
    return [item for item in script_info if target in _normalized_script_name(item.get("name"))]


def _build_extension_capabilities(
    *,
    script_info: list[dict[str, Any]],
    endpoint_records: dict[str, dict[str, Any]],
    payloads: dict[str, Any],
    openapi_paths: list[str],
    upscalers: list[dict[str, Any]],
    face_restorers: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    shared_model_paths: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    control_scripts = [item for item in _script_records(script_info, "controlnet") if item.get("alwayson")]
    control_modes = sorted({"img2img" if item.get("is_img2img") else "txt2img" for item in control_scripts if int(item.get("argument_count") or 0) > 0})
    control_slots_by_mode = {
        mode: max(int(item.get("argument_count") or 0) for item in control_scripts if ("img2img" if item.get("is_img2img") else "txt2img") == mode)
        for mode in control_modes
    }
    control_models = []
    control_modules = []
    if isinstance(payloads.get("controlnet_models"), dict):
        control_models = [str(item) for item in payloads["controlnet_models"].get("model_list") or [] if str(item or "").strip()]
    if isinstance(payloads.get("controlnet_modules"), dict):
        control_modules = [str(item) for item in payloads["controlnet_modules"].get("module_list") or [] if str(item or "").strip()]
    control_ready = bool(
        control_modes
        and endpoint_records.get("controlnet_models", {}).get("available")
        and endpoint_records.get("controlnet_modules", {}).get("available")
        and control_models
        and control_modules
    )

    ad_scripts = [item for item in _script_records(script_info, "adetailer") if item.get("alwayson")]

    def adetailer_contract(record: dict[str, Any]) -> bool:
        labels = {str(arg.get("label") or "").casefold() for arg in record.get("args") or [] if isinstance(arg, dict)}
        known = bool(
            record
            and record.get("argument_count", 0) >= 3
            and any("enable adetailer" in label for label in labels)
            and any("adetailer" in label and ("arg" in label or "parameter" in label) for label in labels)
        )
        if known:
            return True
        args = record.get("args") or []
        values = [arg.get("value") for arg in args if isinstance(arg, dict)]
        return bool(len(values) >= 3 and isinstance(values[0], bool) and isinstance(values[1], bool) and any(isinstance(value, dict) for value in values[2:]))

    verified_ad_scripts = [item for item in ad_scripts if adetailer_contract(item)]
    ad_modes = sorted({"img2img" if item.get("is_img2img") else "txt2img" for item in verified_ad_scripts})
    ad_slots_by_mode = {
        mode: max(max(0, int(item.get("argument_count") or 0) - 2) for item in verified_ad_scripts if ("img2img" if item.get("is_img2img") else "txt2img") == mode)
        for mode in ad_modes
    }
    ad_known_contract = bool(verified_ad_scripts)
    ad_script = verified_ad_scripts[0] if verified_ad_scripts else (ad_scripts[0] if ad_scripts else {})
    ad_max_passes = max(ad_slots_by_mode.values(), default=0)

    stitch_scripts = [item for item in _script_records(script_info, "imagestitch") if item.get("alwayson")]

    def image_stitch_contract(record: dict[str, Any]) -> bool:
        args = [item for item in record.get("args") or [] if isinstance(item, dict)]
        if len(args) != 3:
            return False
        labels = [str(item.get("label") or "").strip().casefold() for item in args]
        enable_value = args[0].get("value")
        max_value = args[2].get("value")
        return bool(
            isinstance(enable_value, bool)
            and isinstance(max_value, (int, float))
            and any("reference" in label and "image" in label for label in labels)
            and any("maximum" in label and ("side" in label or "length" in label) for label in labels)
        )

    verified_stitch_scripts = [item for item in stitch_scripts if image_stitch_contract(item)]
    stitch_modes = sorted({"img2img" if item.get("is_img2img") else "txt2img" for item in verified_stitch_scripts})
    stitch_script = verified_stitch_scripts[0] if verified_stitch_scripts else (stitch_scripts[0] if stitch_scripts else {})
    stitch_ready = bool(verified_stitch_scripts)

    def _arg_labels(record: dict[str, Any]) -> list[str]:
        return [str(item.get("label") or "").strip().casefold() for item in record.get("args") or [] if isinstance(item, dict)]

    def _script_modes(records: list[dict[str, Any]]) -> list[str]:
        return sorted({"img2img" if item.get("is_img2img") else "txt2img" for item in records})

    def _arg_choices(record: dict[str, Any], index: int) -> list[str]:
        args = [item for item in record.get("args") or [] if isinstance(item, dict)]
        if not 0 <= index < len(args):
            return []
        return [str(item) for item in (args[index].get("choices") or []) if str(item or "").strip()]

    pid_scripts = [item for item in _script_records(script_info, "pidintegrated") if item.get("alwayson")]
    def pid_contract(record: dict[str, Any]) -> bool:
        args = [item for item in record.get("args") or [] if isinstance(item, dict)]
        labels = _arg_labels(record)
        if len(args) != 7:
            return False
        return bool(
            isinstance(args[0].get("value"), bool)
            and any(label == "prompt" for label in labels)
            and any(label == "pid" for label in labels)
            and any(label == "vae" for label in labels)
            and any("gemma2" in label or "elm" in label for label in labels)
            and any("degrade" in label and "sigma" in label for label in labels)
            and any("color" in label and "correction" in label for label in labels)
        )
    verified_pid_scripts = [item for item in pid_scripts if pid_contract(item)]
    pid_script = verified_pid_scripts[0] if verified_pid_scripts else (pid_scripts[0] if pid_scripts else {})

    spectrum_scripts = [item for item in _script_records(script_info, "spectrumintegrated") if item.get("alwayson")]
    def spectrum_contract(record: dict[str, Any]) -> bool:
        args = [item for item in record.get("args") or [] if isinstance(item, dict)]
        labels = _arg_labels(record)
        required = ("prediction weighting", "polynomial degree", "regularization", "cache window", "window growth", "warmup steps", "stop caching")
        return bool(len(args) == 8 and isinstance(args[0].get("value"), bool) and all(any(token in label for label in labels) for token in required))
    verified_spectrum_scripts = [item for item in spectrum_scripts if spectrum_contract(item)]
    spectrum_script = verified_spectrum_scripts[0] if verified_spectrum_scripts else (spectrum_scripts[0] if spectrum_scripts else {})
    options = options if isinstance(options, dict) else {}
    try:
        spectrum_skip_early = float(options.get("skip_early_cond") or 0.0)
    except (TypeError, ValueError):
        spectrum_skip_early = 0.0
    try:
        spectrum_s_min_uncond = float(options.get("s_min_uncond") or 0.0)
    except (TypeError, ValueError):
        spectrum_s_min_uncond = 0.0
    spectrum_conflict = spectrum_skip_early > 0.0 or spectrum_s_min_uncond > 0.0

    multidiff_scripts = [item for item in _script_records(script_info, "multidiffusionintegrated") if item.get("alwayson") and item.get("is_img2img")]
    def multidiff_contract(record: dict[str, Any]) -> bool:
        args = [item for item in record.get("args") or [] if isinstance(item, dict)]
        labels = _arg_labels(record)
        if len(args) != 6 or not isinstance(args[0].get("value"), bool):
            return False
        required = ("method", "tile width", "tile height", "tile overlap", "tile batch size")
        method_choices = set(_arg_choices(record, 1))
        return bool(all(any(token in label for label in labels) for token in required) and {"MultiDiffusion", "Mixture of Diffusers"}.issubset(method_choices))
    verified_multidiff_scripts = [item for item in multidiff_scripts if multidiff_contract(item)]
    multidiff_script = verified_multidiff_scripts[0] if verified_multidiff_scripts else (multidiff_scripts[0] if multidiff_scripts else {})

    forge_couple_scripts = [item for item in _script_records(script_info, "forgecouple") if item.get("alwayson")]

    def forge_couple_contract(record: dict[str, Any]) -> bool:
        args = [item for item in record.get("args") or [] if isinstance(item, dict)]
        if len(args) != 17:
            return False
        mode_choices = set(_arg_choices(record, 2))
        direction_choices = set(_arg_choices(record, 4))
        background_choices = set(_arg_choices(record, 5))
        common_choices = set(_arg_choices(record, 8))
        return bool(
            isinstance(args[0].get("value"), bool)
            and isinstance(args[1].get("value"), bool)
            and {"Basic", "Advanced", "Mask"}.issubset(mode_choices)
            and {"Horizontal", "Vertical"}.issubset(direction_choices)
            and {"None", "First Line", "Last Line"}.issubset(background_choices)
            and {"off", "{ }", "< >"}.issubset(common_choices)
            and isinstance(args[9].get("value"), bool)
            and isinstance(args[10].get("value"), bool)
        )

    verified_forge_couple_scripts = [item for item in forge_couple_scripts if forge_couple_contract(item)]
    forge_couple_script = verified_forge_couple_scripts[0] if verified_forge_couple_scripts else (forge_couple_scripts[0] if forge_couple_scripts else {})

    sd_upscale_scripts = [
        item for item in _script_records(script_info, "sdupscale")
        if not item.get("alwayson") and item.get("is_img2img")
    ]

    def sd_upscale_contract(record: dict[str, Any]) -> bool:
        args = [item for item in record.get("args") or [] if isinstance(item, dict)]
        labels = _arg_labels(record)
        if len(args) != 4:
            return False
        required = ("overlap", "upscaler", "scale factor", "save to extras")
        return bool(
            all(any(token in label for label in labels) for token in required)
            and isinstance(args[3].get("value"), bool)
        )

    verified_sd_upscale_scripts = [item for item in sd_upscale_scripts if sd_upscale_contract(item)]
    sd_upscale_script = verified_sd_upscale_scripts[0] if verified_sd_upscale_scripts else (sd_upscale_scripts[0] if sd_upscale_scripts else {})
    sd_upscale_choices = _arg_choices(sd_upscale_script, 1)
    if not sd_upscale_choices:
        sd_upscale_choices = [
            str(item.get("name") or item.get("model_name") or "").strip()
            for item in upscalers
            if isinstance(item, dict) and str(item.get("name") or item.get("model_name") or "").strip()
        ]

    embeddings_payload = payloads.get("embeddings") if isinstance(payloads.get("embeddings"), dict) else {}
    embedding_names = sorted({str(name) for bucket in ("loaded", "skipped") for name in (embeddings_payload.get(bucket) or {}).keys()})
    lora_names: list[str] = []
    lora_seen: set[str] = set()
    raw_loras = payloads.get("loras") if isinstance(payloads.get("loras"), list) else []
    for item in raw_loras:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("alias") or item.get("title") or "").replace("\\", "/").strip()
        else:
            name = str(item or "").replace("\\", "/").strip()
        key = name.casefold()
        if name and key not in lora_seen:
            lora_seen.add(key)
            lora_names.append(name)
    shared_model_paths = shared_model_paths if isinstance(shared_model_paths, dict) else {}
    shared_loras = shared_model_paths.get("loras") if isinstance(shared_model_paths.get("loras"), dict) else {}
    for name in shared_loras.get("shared_model_names") or []:
        clean = str(name or "").replace("\\", "/").strip()
        key = clean.casefold()
        if clean and key not in lora_seen:
            lora_seen.add(key)
            lora_names.append(clean)
    lora_endpoint_available = bool(endpoint_records.get("loras", {}).get("available"))
    lora_shared_available = bool(shared_loras.get("available"))
    path_set = set(openapi_paths)
    face_restorers = face_restorers if isinstance(face_restorers, list) else []
    face_restorer_names = [
        str(item.get("name") or "").strip()
        for item in face_restorers
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    face_restorer_keys = {name.casefold() for name in face_restorer_names}
    supports_codeformer = any("codeformer" in name for name in face_restorer_keys)
    supports_gfpgan = any("gfpgan" in name for name in face_restorer_keys)
    extras_ready = bool(upscalers) and ("/sdapi/v1/extra-single-image" in path_set)
    return {
        "lora_stack": {
            "available": True,
            "mode": "prompt_extra_network",
            "contract": "forge.extra_network.lora.v2",
            "catalog_contract": "neo.lora_stack.provider_catalog.v1",
            "catalog_available": bool(lora_names),
            "catalog_source": "forge_sdapi_loras" if lora_endpoint_available else "forge_verified_shared_model_paths" if lora_shared_available else "forge_prompt_only",
            "loras": lora_names,
            "lora_count": len(lora_names),
            "endpoint_available": lora_endpoint_available,
            "shared_catalog_available": lora_shared_available,
            "selected_profile_only": True,
            "automatic_provider_fallback": False,
            "reason": (
                "Forge parses LoRA extra-network tags from the positive prompt and exposes a selected-profile LoRA catalog."
                if lora_names
                else "Forge parses LoRA extra-network tags from the positive prompt, but no selected-profile LoRA catalog was discovered."
            ),
        },
        "embeddings_ti": {
            "available": bool(endpoint_records.get("embeddings", {}).get("available")),
            "mode": "prompt_embedding_token",
            "contract": "forge.embedding.token.v2",
            "catalog_contract": "neo.embeddings_ti.provider_catalog.v1",
            "catalog_available": bool(embedding_names),
            "catalog_source": "forge_sdapi_embeddings",
            "embeddings": embedding_names,
            "embedding_count": len(embedding_names),
            "serialization": "plain_trigger_compile_time",
            "visible_prompt_mutation": False,
            "selected_profile_only": True,
            "automatic_provider_fallback": False,
            "supported_targets": ["positive_prompt", "negative_prompt", "both"],
            "reason": "Forge embeddings endpoint and plain textual-inversion trigger contract are available." if endpoint_records.get("embeddings", {}).get("available") else "Forge embeddings endpoint was not available.",
        },
        "high_res_lab": {
            "available": "/sdapi/v1/txt2img" in path_set or bool(endpoint_records.get("openapi", {}).get("available")),
            "mode": "txt2img_hires_fix",
            "contract": "forge.txt2img.hires.v1",
            "reason": "Forge txt2img high-resolution fields are available.",
        },
        "image_upscale": {
            "available": extras_ready,
            "backend_available": extras_ready,
            "mode": "standalone_extras",
            "contract": "forge.extras.single_image.v2",
            "endpoint": "/sdapi/v1/extra-single-image",
            "upscalers": [str(item.get("name") or "") for item in upscalers if str(item.get("name") or "").strip()],
            "face_restorers": face_restorer_names,
            "supports_codeformer": supports_codeformer,
            "supports_gfpgan": supports_gfpgan,
            "supports_face_restoration": bool(face_restorer_names),
            "supports_exact_dimensions": True,
            "supports_secondary_upscaler": True,
            "supports_upscale_first": bool(face_restorer_names),
            "supports_crop_to_fit": True,
            "supports_seedvr2": False,
            "selected_profile_only": True,
            "automatic_provider_fallback": False,
            "reason": "Forge Extras and selected-profile upscaler discovery are available through Neo's standalone Image Upscale bridge." if extras_ready else "Forge Extras or selected-profile upscaler discovery is unavailable.",
        },
        "image_stitch": {
            "available": stitch_ready,
            "mode": "alwayson_script",
            "contract": "forge.image_stitch.integrated.v1" if stitch_ready else "unverified",
            "script_name": str(stitch_script.get("name") or "ImageStitch Integrated") if stitch_script else "ImageStitch Integrated",
            "available_modes": stitch_modes,
            "argument_count": int(stitch_script.get("argument_count") or 0) if stitch_script else 0,
            "default_max_side": 1024,
            "reason": "Forge ImageStitch Integrated always-on API shape was verified." if stitch_ready else "ImageStitch Integrated is missing or its three-argument API shape is not recognized.",
        },
        "pid_integrated": {
            "available": bool(verified_pid_scripts),
            "mode": "alwayson_script",
            "contract": "forge.pid.integrated.v1" if verified_pid_scripts else "unverified",
            "script_name": str(pid_script.get("name") or "PiD Integrated") if pid_script else "PiD Integrated",
            "available_modes": _script_modes(verified_pid_scripts),
            "argument_count": int(pid_script.get("argument_count") or 0) if pid_script else 0,
            "pid_models": _arg_choices(pid_script, 2),
            "vaes": _arg_choices(pid_script, 3),
            "text_encoders": _arg_choices(pid_script, 4),
            "conflicts": ["high_res_lab"],
            "reason": "Forge PiD Integrated seven-argument API shape was verified." if verified_pid_scripts else "PiD Integrated is missing or its seven-argument API shape is not recognized.",
        },
        "spectrum": {
            "available": bool(verified_spectrum_scripts) and not spectrum_conflict,
            "detected": bool(verified_spectrum_scripts),
            "mode": "alwayson_script",
            "contract": "forge.spectrum.integrated.v1" if verified_spectrum_scripts else "unverified",
            "script_name": str(spectrum_script.get("name") or "Spectrum Integrated") if spectrum_script else "Spectrum Integrated",
            "available_modes": _script_modes(verified_spectrum_scripts),
            "argument_count": int(spectrum_script.get("argument_count") or 0) if spectrum_script else 0,
            "blocked_by_negative_prompt_optimization": spectrum_conflict,
            "reason": ("Spectrum is detected but Forge Ignore/Skip Negative Prompt optimization is enabled." if spectrum_conflict else "Forge Spectrum Integrated eight-argument API shape was verified.") if verified_spectrum_scripts else "Spectrum Integrated is missing or its eight-argument API shape is not recognized.",
        },
        "multidiffusion": {
            "available": bool(verified_multidiff_scripts),
            "mode": "alwayson_script",
            "contract": "forge.multidiffusion.integrated.v1" if verified_multidiff_scripts else "unverified",
            "script_name": str(multidiff_script.get("name") or "MultiDiffusion Integrated") if multidiff_script else "MultiDiffusion Integrated",
            "available_modes": _script_modes(verified_multidiff_scripts),
            "argument_count": int(multidiff_script.get("argument_count") or 0) if multidiff_script else 0,
            "methods": _arg_choices(multidiff_script, 1),
            "reason": "Forge MultiDiffusion Integrated six-argument img2img API shape was verified." if verified_multidiff_scripts else "MultiDiffusion Integrated is missing or its six-argument img2img API shape is not recognized.",
        },
        "forge_couple": {
            "available": bool(verified_forge_couple_scripts),
            "detected": bool(forge_couple_scripts),
            "mode": "alwayson_script",
            "contract": "haoming02.forge_couple.basic_advanced_mask_tile.api.v1" if verified_forge_couple_scripts else "unverified",
            "script_name": str(forge_couple_script.get("name") or "Forge Couple") if forge_couple_script else "Forge Couple",
            "available_modes": _script_modes(verified_forge_couple_scripts),
            "argument_count": int(forge_couple_script.get("argument_count") or 0) if forge_couple_script else 0,
            "supported_region_modes": ["Basic", "Advanced", "Mask"],
            "native_supported_region_modes": ["Basic", "Advanced", "Mask"],
            "supports_common_prompts": True,
            "supports_hires_compatibility": True,
            "supports_tile_mode": True,
            "tile_runtime_available": bool(verified_sd_upscale_scripts),
            "tile_contract": "forge.sd_upscale.selectable.v1" if verified_sd_upscale_scripts else "unverified",
            "tile_script_name": str(sd_upscale_script.get("name") or "SD Upscale") if sd_upscale_script else "SD Upscale",
            "tile_argument_count": int(sd_upscale_script.get("argument_count") or 0) if sd_upscale_script else 0,
            "tile_upscalers": sorted(dict.fromkeys(sd_upscale_choices)),
            "tile_supported_region_modes": ["Basic", "Advanced"],
            "tile_reason": "Forge selectable SD Upscale four-argument API shape was verified." if verified_sd_upscale_scripts else ("SD Upscale was detected, but its selectable four-argument API shape is not recognized." if sd_upscale_scripts else "Forge selectable SD Upscale is unavailable for Img2Img."),
            "reason": "ForgeCouple 17-argument always-on API shape was verified; Neo Phase 3 enables Basic, Advanced, Mask, and native Tile arguments." if verified_forge_couple_scripts else ("ForgeCouple was detected, but its 17-argument API shape is not recognized." if forge_couple_scripts else "ForgeCouple is not installed or enabled in Forge."),
        },
        "controlnet": {
            "available": control_ready,
            "mode": "alwayson_script",
            "contract": "forge.controlnet.unit.v1",
            "script_name": str(control_scripts[0].get("name") or "ControlNet") if control_scripts else "ControlNet",
            "available_modes": control_modes,
            "unit_slots_by_mode": control_slots_by_mode,
            "max_units": max(control_slots_by_mode.values(), default=0),
            "models": control_models,
            "modules": control_modules,
            "reason": "Forge ControlNet script and custom catalogs were verified." if control_ready else "Forge ControlNet script/custom API catalogs were not fully verified.",
        },
        "adetailer": {
            "available": ad_known_contract,
            "mode": "alwayson_script",
            "contract": "bing-su.adetailer.api.v1" if ad_known_contract else "unverified",
            "script_name": str(ad_script.get("name") or "ADetailer") if ad_script else "ADetailer",
            "available_modes": ad_modes,
            "pass_slots_by_mode": ad_slots_by_mode,
            "max_passes": ad_max_passes,
            "reason": "Official ADetailer always-on API argument shape was verified." if ad_known_contract else "ADetailer is missing or its script argument schema is not recognized.",
        },
        "ip_adapter": build_forge_ip_adapter_capability(
            controlnet_available=control_ready,
            controlnet_script_name=str(control_scripts[0].get("name") or "ControlNet") if control_scripts else "ControlNet",
            controlnet_modes=control_modes,
            controlnet_slots_by_mode=control_slots_by_mode,
            control_models=control_models,
            control_modules=control_modules,
            shared_models=list((((shared_model_paths or {}).get("ip_adapter") or {}).get("shared_model_names") or [])),
            shared_encoders=list((((shared_model_paths or {}).get("ip_adapter") or {}).get("shared_encoder_names") or [])),
            shared_path_reference_ready=bool((((shared_model_paths or {}).get("ip_adapter") or {}).get("shared_path_reference_ready"))),
        ),
    }


def _sanitize_extensions(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            records.append(
                {
                    "name": name,
                    "branch": str(item.get("branch") or ""),
                    "commit_hash": str(item.get("commit_hash") or "")[:12],
                    "version": str(item.get("version") or ""),
                    "enabled": bool(item.get("enabled", False)),
                }
            )
    return records


def _sanitize_memory(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    def numbers_only(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): numbers_only(child) for key, child in value.items() if isinstance(child, (dict, int, float, bool))}
        return value if isinstance(value, (int, float, bool)) else None

    return numbers_only(payload) or {}


def _sanitize_cmd_flags(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        if _is_path_key(key_text) or _looks_like_path(value):
            safe[f"{key_text}_configured"] = bool(value)
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key_text] = value
    return safe


def _openapi_paths(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        return []
    return sorted(str(path) for path in payload["paths"].keys())


def _openapi_feature_keys(payload: Any) -> list[str]:
    """Retain only path-safe feature tokens needed by Forge live classification.

    The OpenAPI document can contain large schemas and machine-specific defaults.
    Phase 2 stores only strings that explicitly describe ImageStitch or the
    Flux.2-Klein regular-img2img capability.
    """

    found: set[str] = set()

    def add_feature(value: Any) -> None:
        text = str(value or "").strip()
        if not text or _ABSOLUTE_PATH_RE.search(text) or _URI_RE.search(text):
            return
        found.add(text[:240])

    def visit(value: Any, *, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, child in list(value.items())[:2000]:
                key_text = str(key or "").strip()
                normalized = re.sub(r"[^a-z0-9]+", "_", key_text.casefold())
                if "imagestitch" in normalized or ("image" in normalized and "stitch" in normalized):
                    add_feature(key_text)
                if "flux" in normalized and "klein" in normalized and "img2img" in normalized:
                    add_feature(key_text)
                visit(child, depth=depth + 1)
        elif isinstance(value, list):
            for child in value[:2000]:
                visit(child, depth=depth + 1)
        elif isinstance(value, str):
            normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold())
            if "imagestitch" in normalized or ("image" in normalized and "stitch" in normalized):
                add_feature(value)
            if "flux" in normalized and "klein" in normalized and "img2img" in normalized:
                add_feature(value)

    visit(payload)
    return sorted(item for item in found if item)


def _openapi_option_descriptions(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    schemas = ((payload.get("components") or {}).get("schemas") or {}) if isinstance(payload.get("components"), dict) else {}
    options = schemas.get("Options") if isinstance(schemas, dict) else None
    properties = options.get("properties") if isinstance(options, dict) else None
    return properties if isinstance(properties, dict) else {}


def _setting_category(key: str) -> str:
    lowered = key.casefold()
    if any(token in lowered for token in ("model", "checkpoint", "vae", "clip", "lora", "module")):
        return "Models & Modules"
    if any(token in lowered for token in ("sample", "scheduler", "sampler", "cfg", "sigma", "eta", "seed", "prompt")):
        return "Generation"
    if any(token in lowered for token in ("img2img", "inpaint", "mask", "outpaint")):
        return "Image Edit"
    if any(token in lowered for token in ("upscale", "esrgan", "swin", "realesr")):
        return "Upscaling"
    if any(token in lowered for token in ("preview", "progress", "grid", "jpeg", "webp", "png", "save")):
        return "Output & Preview"
    if any(token in lowered for token in _RESTART_HINT_TOKENS):
        return "Performance & Memory"
    return "Other"


def _setting_value_type(value: Any, schema: dict[str, Any]) -> str:
    schema_type = str((schema or {}).get("type") or "").strip()
    if schema_type:
        return schema_type
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"


def build_forge_settings_catalog(options: Any, openapi: Any = None) -> dict[str, Any]:
    option_values = options if isinstance(options, dict) else {}
    schema = _openapi_option_descriptions(openapi)
    settings: list[dict[str, Any]] = []
    for key in sorted(option_values.keys(), key=str.casefold):
        key_text = str(key)
        value = option_values.get(key)
        metadata = schema.get(key_text) if isinstance(schema.get(key_text), dict) else {}
        blocked_reason = ""
        if _is_sensitive_key(key_text):
            blocked_reason = "Sensitive credential/auth option is read-only in Neo Admin."
        elif _is_path_key(key_text) or _looks_like_path(value):
            blocked_reason = "Filesystem/path option is read-only to avoid persisting machine-specific paths in Neo records."
        elif _is_network_key(key_text):
            blocked_reason = "Server/network startup option must be changed in the Forge launch configuration."
        editable_type = isinstance(value, (str, int, float, bool, list)) or value is None
        editable = bool(editable_type and not blocked_reason)
        level = "guided" if key_text in GUIDED_SETTING_KEYS else "advanced" if editable else "expert_readonly"
        settings.append(
            {
                "key": key_text,
                "label": str(metadata.get("title") or key_text.replace("_", " ").strip().title()),
                "description": str(metadata.get("description") or ""),
                "category": _setting_category(key_text),
                "level": level,
                "type": _setting_value_type(value, metadata),
                "current_value": _safe_value(key_text, value),
                "editable": editable,
                "blocked_reason": blocked_reason,
                "requires_restart": any(token in key_text.casefold() for token in _RESTART_HINT_TOKENS),
                "enum": list(metadata.get("enum") or []) if isinstance(metadata.get("enum"), list) else [],
            }
        )
    categories: dict[str, int] = {}
    for setting in settings:
        categories[setting["category"]] = categories.get(setting["category"], 0) + 1
    return {
        "schema_id": FORGE_SETTINGS_SCHEMA_ID,
        "settings": settings,
        "summary": {
            "total": len(settings),
            "guided": sum(1 for item in settings if item["level"] == "guided"),
            "advanced": sum(1 for item in settings if item["level"] == "advanced"),
            "readonly": sum(1 for item in settings if not item["editable"]),
            "categories": categories,
        },
        "write_policy": {
            "guided_without_confirmation": True,
            "advanced_requires_expert_confirmation": True,
            "paths_credentials_and_network_startup_are_readonly": True,
        },
    }


def _forge_identity(options: Any, cmd_flags: Any, modules_available: bool, openapi: Any) -> dict[str, Any]:
    option_values = options if isinstance(options, dict) else {}
    flag_values = cmd_flags if isinstance(cmd_flags, dict) else {}
    signals: list[str] = []
    if "forge_additional_modules" in option_values:
        signals.append("forge_additional_modules_option")
    if modules_available:
        signals.append("sd_modules_endpoint")
    if any(str(key).startswith("forge_") for key in flag_values):
        signals.append("forge_command_flags")
    if any(str(key) in {"model_ref", "cuda_stream", "pin_shared_memory", "expandable_segments"} for key in flag_values):
        signals.append("forge_neo_command_flags")
    info = (openapi or {}).get("info") if isinstance(openapi, dict) else {}
    title = str((info or {}).get("title") or "")
    version = str((info or {}).get("version") or "")
    variant = "forge_neo" if len(signals) >= 2 else "forge_or_a1111_compatible"
    confidence = "high" if len(signals) >= 3 else "medium" if signals else "low"
    return {
        "variant": variant,
        "confidence": confidence,
        "signals": signals,
        "api_title": title,
        "api_version": version,
    }


def _capability_flags(endpoint_records: dict[str, dict[str, Any]], openapi_paths: list[str]) -> dict[str, Any]:
    def available(name: str) -> bool:
        return bool((endpoint_records.get(name) or {}).get("available"))

    paths = set(openapi_paths)
    return {
        "api_enabled": available("options"),
        "settings_read": available("options"),
        "settings_write": available("options"),
        "model_discovery": available("models"),
        "module_discovery": available("modules"),
        "sampler_discovery": available("samplers"),
        "scheduler_discovery": available("schedulers"),
        "upscaler_discovery": available("upscalers"),
        "script_discovery": available("scripts"),
        "script_metadata": available("script_info"),
        "extension_discovery": available("extensions"),
        "memory_diagnostics": available("memory"),
        "command_flag_diagnostics": available("cmd_flags"),
        "txt2img_api": "/sdapi/v1/txt2img" in paths if paths else True,
        "img2img_api": "/sdapi/v1/img2img" in paths if paths else True,
        "progress_api": "/sdapi/v1/progress" in paths if paths else True,
        "interrupt_api": "/sdapi/v1/interrupt" in paths if paths else True,
        "extras_api": "/sdapi/v1/extra-single-image" in paths if paths else available("upscalers"),
        "neo_execution_adapter": bool(
            available("options")
            and ("/sdapi/v1/txt2img" in paths if paths else True)
            and ("/sdapi/v1/img2img" in paths if paths else True)
            and ("/sdapi/v1/progress" in paths if paths else True)
            and ("/sdapi/v1/interrupt" in paths if paths else True)
        ),
    }


def _state_payload(
    profile: dict[str, Any],
    *,
    status: str,
    message: str,
    reachable: bool,
    api_enabled: bool,
    base_url: str,
    endpoint_records: dict[str, dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    profile_id = str(profile.get("profile_id") or "forge_profile")
    empty_classification = build_forge_live_model_classification()
    return {
        "schema_id": FORGE_ADMIN_SCHEMA_ID,
        "profile_id": profile_id,
        "provider_id": "forge",
        "surface": str(profile.get("surface") or "image"),
        "status": status,
        "reachable": bool(reachable),
        "api_enabled": bool(api_enabled),
        "message": message,
        "base_url": base_url,
        "checked_at": _now_iso(),
        "endpoint_status": endpoint_records or {},
        "warnings": warnings or [],
        "errors": errors or [],
        "identity": {"variant": "unknown", "confidence": "low", "signals": []},
        "capabilities": {"neo_execution_adapter": False},
        "models": [],
        "modules": [],
        "samplers": [],
        "schedulers": [],
        "upscalers": [],
        "scripts": {"txt2img": [], "img2img": []},
        "script_info": [],
        "extensions": [],
        "memory": {},
        "command_flags": {},
        "shared_model_paths": {
            "schema_id": "neo.provider.forge_shared_model_paths.v1",
            "status": "not_checked",
            "available": False,
            "path_policy": "absolute_paths_server_side_only",
        },
        "settings_catalog": build_forge_settings_catalog({}),
        "openapi_feature_keys": [],
        "model_classification": empty_classification,
        "live_route_intersection": build_forge_live_route_intersection(empty_classification),
        "loader_translation_contract": forge_loader_translation_contract_payload(),
        "workflow_compiler_contract": forge_workflow_compiler_contract_payload(),
        "cache": forge_admin_cache_metadata(profile_id),
        "execution_gate": {
            "status": "provider_gated",
            "message": "Forge Image execution is unavailable until the profile passes API and lifecycle capability checks.",
        },
    }



def _probe_forge_bridge(
    client: ForgeAdminHttpClient,
    profile: dict[str, Any],
    base_url: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    mode = forge_bridge_mode(profile)
    if mode == "standard":
        return bridge_snapshot_payload(
            profile,
            status="disabled_by_profile",
            message="Forge Bridge is disabled for this profile; standard SDAPI lifecycle is selected.",
        )
    headers = forge_bridge_headers(profile)
    try:
        handshake_response = (
            client.request_json("GET", base_url, "/neo-api/v1/handshake", timeout=timeout, headers=headers)
            if headers
            else client.request_json("GET", base_url, "/neo-api/v1/handshake", timeout=timeout)
        )
        handshake = handshake_response.data if isinstance(handshake_response.data, dict) else {}
        capability_response = (
            client.request_json("GET", base_url, "/neo-api/v1/capabilities", timeout=timeout, headers=headers)
            if headers
            else client.request_json("GET", base_url, "/neo-api/v1/capabilities", timeout=timeout)
        )
        capabilities = capability_response.data if isinstance(capability_response.data, dict) else {}
        try:
            schema_response = (
                client.request_json("GET", base_url, "/neo-api/v1/settings-schema", timeout=timeout, headers=headers)
                if headers
                else client.request_json("GET", base_url, "/neo-api/v1/settings-schema", timeout=timeout)
            )
            settings_schema = schema_response.data if isinstance(schema_response.data, dict) else {}
        except ForgeAdminRequestError:
            settings_schema = {}
        protocol = str(handshake.get("protocol_version") or "")
        compatible = bool(handshake.get("ok") and protocol.split(".", 1)[0] == "1")
        return bridge_snapshot_payload(
            profile,
            handshake=handshake,
            capabilities=capabilities,
            settings_schema=settings_schema,
            status="connected" if compatible else "protocol_incompatible",
            message=(
                "Optional Forge Neo Bridge detected and ready."
                if compatible
                else "Forge Bridge responded, but its protocol is incompatible with this Neo build."
            ),
        )
    except ForgeAdminRequestError as exc:
        if exc.status_code in {401, 403}:
            return bridge_snapshot_payload(
                profile,
                status="authentication_required",
                message="Forge Bridge requires a token. Configure bridge_token_env for this profile.",
                error=str(exc),
            )
        if exc.status_code in {404, 405}:
            return bridge_snapshot_payload(
                profile,
                status="not_installed",
                message="Optional Forge Bridge was not detected; standard SDAPI fallback remains available.",
            )
        return bridge_snapshot_payload(
            profile,
            status="unavailable",
            message="Forge Bridge probe failed; standard SDAPI fallback remains available when permitted.",
            error=str(exc),
        )


def probe_forge_admin_profile(
    profile: dict[str, Any],
    *,
    client: ForgeAdminHttpClient | None = None,
    persist: bool = True,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    profile = profile or {}
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str((connection or {}).get("base_url") or "").strip().rstrip("/")
    timeout = max(1.0, float((connection or {}).get("timeout_seconds") or 10.0))
    client = client or ForgeAdminHttpClient()

    if str(profile.get("provider_id") or "") != "forge":
        return _state_payload(profile, status="wrong_provider", message="This Admin probe only supports Forge profiles.", reachable=False, api_enabled=False, base_url=base_url, errors=["provider_id must be forge"])
    if profile.get("enabled") is False:
        return _state_payload(profile, status="disabled", message="Forge profile is disabled. Enable and save it before probing.", reachable=False, api_enabled=False, base_url=base_url)
    if not _valid_base_url(base_url):
        return _state_payload(profile, status="missing_config", message="Forge base URL is missing or invalid.", reachable=False, api_enabled=False, base_url=base_url)

    endpoint_records: dict[str, dict[str, Any]] = {}
    options_record, options = _probe_json_endpoint(client, base_url, "/sdapi/v1/options", timeout=timeout, required=True)
    endpoint_records["options"] = options_record
    if not options_record["available"]:
        status_code = options_record.get("http_status")
        if status_code in {401, 403}:
            snapshot = _state_payload(
                profile,
                status="authentication_required",
                message="Forge is reachable, but its API requires authentication.",
                reachable=True,
                api_enabled=True,
                base_url=base_url,
                endpoint_records=endpoint_records,
                warnings=["Neo Phase 2 detects Forge API authentication but does not persist Forge Basic Auth credentials."],
            )
        elif status_code in {404, 405}:
            root_reachable = False
            try:
                client.request_text("GET", base_url, "/", timeout=timeout)
                root_reachable = True
            except ForgeAdminRequestError:
                root_reachable = False
            snapshot = _state_payload(
                profile,
                status="api_disabled" if root_reachable else "disconnected",
                message="Forge is reachable, but the Stable Diffusion API is unavailable. Launch Forge with --api." if root_reachable else "Forge could not be reached.",
                reachable=root_reachable,
                api_enabled=False,
                base_url=base_url,
                endpoint_records=endpoint_records,
            )
        else:
            kind = ""
            message = str(options_record.get("message") or "")
            if str(options_record.get("error_kind") or "") in {"offline", "timeout"} or "Could not reach" in message or "timed out" in message or message.strip().casefold() == "offline":
                kind = "disconnected"
            snapshot = _state_payload(
                profile,
                status=kind or "version_incompatible",
                message="Forge could not be reached." if kind else "The endpoint responded, but it is not exposing a compatible Forge/A1111 API.",
                reachable=False,
                api_enabled=False,
                base_url=base_url,
                endpoint_records=endpoint_records,
                errors=[message] if message else [],
            )
        if persist:
            _atomic_write_json(forge_admin_cache_path(str(profile.get("profile_id") or "forge_profile"), root_dir=root_dir), snapshot)
        return snapshot

    payloads: dict[str, Any] = {"options": options}
    cmd_flags_policy = forge_cmd_flags_probe_policy()
    for name, path in (*FORGE_CORE_ENDPOINTS, *FORGE_OPTIONAL_ENDPOINTS):
        if name == "cmd_flags" and not bool(cmd_flags_policy.get("probe")):
            record = _endpoint_record(
                path,
                ok=False,
                required=False,
                message=str(cmd_flags_policy.get("reason") or "Forge command-flag diagnostics were skipped."),
                error_kind="provider_path_serialization_guard",
            )
            record["status"] = "skipped"
            endpoint_records[name] = record
            payloads[name] = None
            continue
        record, payload = _probe_json_endpoint(client, base_url, path, timeout=timeout, required=(name in {item[0] for item in FORGE_CORE_ENDPOINTS}))
        endpoint_records[name] = record
        payloads[name] = payload

    bridge = _probe_forge_bridge(client, profile, base_url, timeout=timeout)

    raw_script_info = payloads.get("script_info") if isinstance(payloads.get("script_info"), list) else []
    has_controlnet = any("controlnet" in _normalized_script_name(item.get("name")) for item in raw_script_info if isinstance(item, dict))
    if has_controlnet:
        for name, path in (
            ("controlnet_models", "/controlnet/model_list"),
            ("controlnet_modules", "/controlnet/module_list"),
            ("controlnet_types", "/controlnet/control_types"),
        ):
            record, payload = _probe_json_endpoint(client, base_url, path, timeout=timeout, required=False)
            endpoint_records[name] = record
            payloads[name] = payload

    core_missing = [name for name, _path in FORGE_CORE_ENDPOINTS if not endpoint_records[name]["available"]]
    soft_optional_names = {"embeddings", "loras", "cmd_flags", "face_restorers"}
    optional_missing = [
        name
        for name, _path in FORGE_OPTIONAL_ENDPOINTS
        if name not in soft_optional_names and not endpoint_records[name]["available"]
    ]
    openapi_paths = _openapi_paths(payloads.get("openapi"))
    openapi_feature_keys = _openapi_feature_keys(payloads.get("openapi"))
    identity = _forge_identity(options, payloads.get("cmd_flags"), endpoint_records.get("modules", {}).get("available", False), payloads.get("openapi"))
    warnings: list[str] = []
    errors: list[str] = []
    if core_missing:
        errors.append(f"Missing required API discovery endpoints: {', '.join(core_missing)}.")
    if optional_missing:
        warnings.append(f"Optional Forge Admin endpoints unavailable: {', '.join(optional_missing)}.")
    if identity["variant"] != "forge_neo":
        warnings.append("A1111-compatible API detected, but Forge Neo identity could not be confirmed with high confidence.")
    if not openapi_paths:
        warnings.append("OpenAPI schema was unavailable; generation endpoint capability flags use conservative standard-API assumptions.")
    bridge_mode = forge_bridge_mode(profile)
    if bridge_mode == "required" and not bridge.get("available"):
        errors.append("Forge Bridge is required by this profile but is not available.")
    elif bridge.get("status") in {"authentication_required", "protocol_incompatible", "unavailable"}:
        warnings.append(str(bridge.get("message") or "Optional Forge Bridge is unavailable."))

    status = "version_incompatible" if core_missing or (bridge_mode == "required" and not bridge.get("available")) else "connected_with_warnings" if warnings else "connected"
    reachable = status in {"connected", "connected_with_warnings"}
    models = _sanitize_model_records(payloads.get("models"))
    modules = _sanitize_module_records(payloads.get("modules"))
    samplers = _sanitize_named_records(payloads.get("samplers"), fields=("name",))
    schedulers = _sanitize_named_records(payloads.get("schedulers"), fields=("name", "label"))
    upscalers = _sanitize_upscalers(payloads.get("upscalers"))
    face_restorers = _sanitize_named_records(payloads.get("face_restorers"), fields=("name",))
    scripts = _sanitize_scripts(payloads.get("scripts"))
    script_info = _sanitize_script_info(payloads.get("script_info"))
    extensions = _sanitize_extensions(payloads.get("extensions"))
    memory = _sanitize_memory(payloads.get("memory"))
    cmd_flags = _sanitize_cmd_flags(payloads.get("cmd_flags"))
    settings_catalog = build_forge_settings_catalog(options, payloads.get("openapi"))
    capabilities = _capability_flags(endpoint_records, openapi_paths)
    cmd_flags_record = endpoint_records.get("cmd_flags") if isinstance(endpoint_records.get("cmd_flags"), dict) else {}
    shared_model_paths = build_forge_shared_model_paths_capability(
        command_flags=payloads.get("cmd_flags") if isinstance(payloads.get("cmd_flags"), dict) else {},
        options=options,
        allow_local_reference_fallback=bool(
            cmd_flags_policy.get("local_reference_fallback")
            and not cmd_flags_record.get("available")
        ),
        command_flags_status=str(cmd_flags_record.get("status") or cmd_flags_policy.get("status") or "unknown"),
    )
    extension_capabilities = _build_extension_capabilities(
        script_info=script_info,
        endpoint_records=endpoint_records,
        payloads=payloads,
        openapi_paths=openapi_paths,
        upscalers=upscalers,
        face_restorers=face_restorers,
        options=options,
        shared_model_paths=shared_model_paths,
    )
    shared_adetailer = shared_model_paths.get("adetailer") if isinstance(shared_model_paths.get("adetailer"), dict) else {}
    if isinstance(extension_capabilities.get("adetailer"), dict):
        extension_capabilities["adetailer"].update({
            "shared_model_names": list(shared_adetailer.get("shared_model_names") or []),
            "shared_model_count": int(shared_adetailer.get("shared_model_count") or 0),
            "shared_covered_model_names": list(shared_adetailer.get("covered_model_names") or []),
            "shared_covered_model_count": int(shared_adetailer.get("covered_model_count") or 0),
            "shared_uncovered_model_names": list(shared_adetailer.get("uncovered_model_names") or []),
            "shared_uncovered_model_count": int(shared_adetailer.get("uncovered_model_count") or 0),
            "shared_model_coverage_known": True,
            "shared_extra_model_dirs_ready": bool(shared_adetailer.get("extra_model_dirs_ready")),
            "shared_model_path_status": str(shared_adetailer.get("status") or "not_discovered"),
            "shared_model_path_reason": str(shared_adetailer.get("reason") or ""),
        })
    if isinstance(extension_capabilities.get("ip_adapter"), dict):
        shared_ip = shared_model_paths.get("ip_adapter") if isinstance(shared_model_paths.get("ip_adapter"), dict) else {}
        extension_capabilities["ip_adapter"].update({
            "shared_path_reference_ready": bool(shared_ip.get("shared_path_reference_ready")),
            "shared_path_reference_status": str(shared_model_paths.get("status") or "not_configured"),
            "shared_model_count": int(shared_ip.get("shared_model_count") or 0),
            "shared_encoder_count": int(shared_ip.get("shared_encoder_count") or 0),
        })
    generic_extension_bridge = build_forge_generic_extension_bridge(
        extensions=extensions,
        scripts=scripts,
        script_info=script_info,
    )
    extension_capabilities["forge_script_bridge"] = dict(generic_extension_bridge.get("capability") or {})
    bridge_caps = bridge.get("capabilities") if isinstance(bridge.get("capabilities"), dict) else {}
    capabilities.update({
        "lora": bool(extension_capabilities["lora_stack"]["available"]),
        "embeddings": bool(extension_capabilities["embeddings_ti"]["available"]),
        "highres_inline": bool(extension_capabilities["high_res_lab"]["available"]),
        "image_upscale": bool(extension_capabilities["image_upscale"]["available"]),
        "controlnet": bool(extension_capabilities["controlnet"]["available"]),
        "ip_adapter": bool(extension_capabilities["ip_adapter"]["available"]),
        "face_id": bool(extension_capabilities["ip_adapter"].get("faceid_available")),
        "faceid": bool(extension_capabilities["ip_adapter"].get("faceid_available")),
        "instantid": bool(extension_capabilities["ip_adapter"].get("instantid_available")),
        "adetailer_inline": bool(extension_capabilities["adetailer"]["available"]),
        "shared_comfy_model_paths": bool(shared_model_paths.get("available")),
        "pid_integrated": bool(extension_capabilities["pid_integrated"]["available"]),
        "spectrum": bool(extension_capabilities["spectrum"]["available"]),
        "multidiffusion": bool(extension_capabilities["multidiffusion"]["available"]),
        "forge_couple": bool(extension_capabilities["forge_couple"]["available"]),
        "generic_extension_bridge": bool(extension_capabilities["forge_script_bridge"].get("available")),
        "bridge_available": bool(bridge.get("available")),
        "bridge_selected": bool(bridge.get("selected")),
        "bridge_durable_jobs": bool(bridge_caps.get("durable_jobs")),
        "bridge_job_specific_progress": bool(bridge_caps.get("job_specific_progress")),
        "bridge_history": bool(bridge_caps.get("history")),
        "bridge_settings_schema": bool(bridge_caps.get("settings_schema")),
        "bridge_native_post_hires": bool(
            bridge.get("selected")
            and bridge_caps.get("native_post_hires")
            and "native_txt2img_upscale" in set(bridge_caps.get("native_operations") or [])
            and bridge_caps.get("native_post_hires_size_contract")
        ),
        "bridge_native_post_hires_size_contract": bool(
            bridge.get("selected") and bridge_caps.get("native_post_hires_size_contract")
        ),
        "bridge_native_operations": list(bridge_caps.get("native_operations") or []),
    })
    model_classification = build_forge_live_model_classification(
        models=models,
        modules=modules,
        settings_catalog=settings_catalog,
        scripts=scripts,
        script_info=script_info,
        extensions=extensions,
        identity=identity,
        capabilities=capabilities,
        bridge=bridge,
        openapi_feature_keys=openapi_feature_keys,
    )
    profile_route_flags = {
        **(profile.get("capability_flags") if isinstance(profile.get("capability_flags"), dict) else {}),
        **(profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else {}),
    }
    has_explicit_mode_flags = any(mode in profile_route_flags for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit"))
    enabled_modes = (
        {mode for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit") if bool(profile_route_flags.get(mode))}
        if has_explicit_mode_flags
        else {"txt2img", "img2img", "inpaint", "outpaint", "edit"}
    )
    live_route_intersection = build_forge_live_route_intersection(model_classification, enabled_modes=enabled_modes)
    capabilities.update({
        "live_model_classification": True,
        "live_route_intersection": True,
        "loader_translation": True,
        "workflow_compilers": True,
        "classified_model_count": int(model_classification.get("summary", {}).get("classified_models") or 0),
        "route_eligible_model_count": int(model_classification.get("summary", {}).get("route_eligible_models") or 0),
        "live_selectable_route_count": len(live_route_intersection.get("selectable_summary", {}).get("routes") or []),
    })
    execution_ready = bool(
        capabilities.get("neo_execution_adapter")
        and not core_missing
        and not (bridge_mode == "required" and not bridge.get("available"))
    )
    execution_gate = {
        "status": "available" if execution_ready else "provider_gated",
        "message": (
            (
                "Forge Image execution is available through the optional durable Bridge lifecycle."
                if bridge.get("selected")
                else "Forge Image execution lifecycle is available for live route-authority-backed classic and modern workflows."
            )
            if execution_ready
            else "Forge API discovery succeeded, but one or more generation lifecycle endpoints are unavailable."
        ),
    }

    snapshot = {
        **_state_payload(
            profile,
            status=status,
            message=(
                "Connected to Forge Neo Admin API." if status == "connected" else
                "Connected to Forge Admin API with capability warnings." if status == "connected_with_warnings" else
                "Forge API is reachable, but required discovery endpoints are incompatible."
            ),
            reachable=reachable,
            api_enabled=True,
            base_url=base_url,
            endpoint_records=endpoint_records,
            warnings=warnings,
            errors=errors,
        ),
        "identity": identity,
        "bridge": bridge,
        "capabilities": capabilities,
        "extension_capabilities": extension_capabilities,
        "shared_model_paths": shared_model_paths,
        "execution_gate": execution_gate,
        "models": models,
        "modules": modules,
        "samplers": samplers,
        "schedulers": schedulers,
        "upscalers": upscalers,
        "face_restorers": face_restorers,
        "scripts": scripts,
        "script_info": script_info,
        "extensions": extensions,
        "generic_extension_bridge": generic_extension_bridge,
        "generic_extension_bridge_contract": forge_generic_extension_bridge_contract_payload(),
        "memory": memory,
        "command_flags": cmd_flags,
        "openapi_paths": openapi_paths,
        "openapi_feature_keys": openapi_feature_keys,
        "settings_catalog": settings_catalog,
        "model_classification": model_classification,
        "live_route_intersection": live_route_intersection,
        "loader_translation_contract": forge_loader_translation_contract_payload(),
        "workflow_compiler_contract": forge_workflow_compiler_contract_payload(),
        "summary": {
            "models": len(models),
            "modules": len(modules),
            "classified_models": int(model_classification.get("summary", {}).get("classified_models") or 0),
            "ambiguous_models": int(model_classification.get("summary", {}).get("ambiguous_models") or 0),
            "unclassified_models": int(model_classification.get("summary", {}).get("unclassified_models") or 0),
            "live_selectable_routes": len(live_route_intersection.get("selectable_summary", {}).get("routes") or []),
            "loader_translation_contract_version": "1.1.0",
            "workflow_compiler_contract_version": "1.0.0",
            "samplers": len(samplers),
            "schedulers": len(schedulers),
            "upscalers": len(upscalers),
            "face_restorers": len(face_restorers),
            "scripts": len(set(scripts.get("txt2img", []) + scripts.get("img2img", []))),
            "extensions": len(extensions),
            "compatible_extension_mappings": sum(1 for item in extension_capabilities.values() if item.get("available")),
            "generic_bridge_ready_scripts": int((generic_extension_bridge.get("summary") or {}).get("generic_bridge_ready") or 0),
            "generic_adapter_required_scripts": int((generic_extension_bridge.get("summary") or {}).get("adapter_required") or 0),
            "bridge_available": bool(bridge.get("available")),
            "bridge_selected": bool(bridge.get("selected")),
            "available_endpoints": sum(1 for record in endpoint_records.values() if record.get("available")),
            "total_endpoints": len(endpoint_records),
        },
    }
    if persist:
        _atomic_write_json(forge_admin_cache_path(str(profile.get("profile_id") or "forge_profile"), root_dir=root_dir), snapshot)
    return snapshot


def forge_models_for_backend_profile(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    classification, _intersection = ensure_forge_live_discovery(snapshot)
    buckets: dict[str, list[dict[str, Any]]] = {
        "models": [],
        "diffusion_models": [],
        "text_encoders": [],
        "qwen_text_encoders": [],
        "vaes": [],
        "samplers": [],
        "schedulers": [],
        "gguf_models": [],
        "gguf_text_encoders": [],
        "gguf_text_encoder_primary": [],
        "gguf_text_encoder_secondary": [],
        "gguf_vaes": [],
        "mmproj": [],
        "loras": [],
        "embeddings": [],
        "ip_adapter_models": [],
        "clip_vision_models": [],
        "ip_adapter_faceid_models": [],
        "upscalers": [],
        "text_models": [],
        "vision_models": [],
    }

    def append_unique(bucket: str, record: dict[str, Any]) -> None:
        name = str(record.get("name") or "").strip()
        if not name:
            return
        if any(str(item.get("name") or "") == name for item in buckets[bucket]):
            return
        buckets[bucket].append(record)

    for item in classification.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or item.get("model_name") or "").strip()
        if not name:
            continue
        metadata = {
            "family": str(item.get("family") or ""),
            "family_candidates": list(item.get("family_candidates") or []),
            "classification_status": str(item.get("classification_status") or "unclassified"),
            "format": str(item.get("format") or "unknown"),
            "packaging": str(item.get("packaging") or "unknown"),
            "loader_candidates": list(item.get("loader_candidates") or []),
            "route_eligible": bool(item.get("route_eligible")),
            "confidence": str(item.get("confidence") or "none"),
            "variant": str(item.get("variant") or ""),
        }
        base_record = {"kind": "checkpoint", "name": name, "source": "forge_sd_models", **metadata}
        append_unique("models", base_record)
        loaders = set(metadata["loader_candidates"])
        if "diffusion_model" in loaders:
            append_unique("diffusion_models", {**base_record, "kind": "diffusion_model"})
        if "gguf" in loaders:
            append_unique("gguf_models", {**base_record, "kind": "gguf_model"})

    for item in classification.get("modules") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        roles = {str(role) for role in item.get("roles") or []}
        module_format = str(item.get("format") or "unknown")
        base_record = {
            "name": name,
            "source": "forge_sd_modules",
            "roles": sorted(roles),
            "format": module_format,
            "module_kind": str(item.get("module_kind") or "module"),
        }
        if item.get("module_kind") == "vae" or roles & {"vae", "vae_or_ae", "ae_or_vae", "qwen_image_vae"}:
            append_unique("vaes", {**base_record, "kind": "vae"})
            if module_format == "gguf":
                append_unique("gguf_vaes", {**base_record, "kind": "gguf_vae"})
        if item.get("module_kind") == "text_encoder" or any(role.endswith("text_encoder") for role in roles):
            append_unique("text_encoders", {**base_record, "kind": "text_encoder"})
            if roles & {"qwen_text_encoder", "qwen3_text_encoder", "qwen3vl_4b_text_encoder"}:
                append_unique("qwen_text_encoders", {**base_record, "kind": "qwen_text_encoder"})
            if module_format == "gguf":
                append_unique("gguf_text_encoders", {**base_record, "kind": "gguf_text_encoder"})
                if "text_encoder_primary" in roles or roles & {"qwen_text_encoder", "qwen3_text_encoder", "qwen3vl_4b_text_encoder"}:
                    append_unique("gguf_text_encoder_primary", {**base_record, "kind": "gguf_text_encoder_primary"})
                if "text_encoder_secondary" in roles:
                    append_unique("gguf_text_encoder_secondary", {**base_record, "kind": "gguf_text_encoder_secondary"})
        if "mmproj" in roles:
            append_unique("mmproj", {**base_record, "kind": "mmproj"})
        if not roles and item.get("module_kind") == "module":
            append_unique("diffusion_models", {**base_record, "kind": "module"})

    for item in snapshot.get("samplers") or []:
        name = str(item.get("name") or "").strip()
        if name:
            append_unique("samplers", {"kind": "sampler", "name": name, "source": "forge_samplers"})
    for item in snapshot.get("schedulers") or []:
        name = str(item.get("label") or item.get("name") or "").strip()
        if name:
            append_unique("schedulers", {"kind": "scheduler", "name": name, "source": "forge_schedulers"})
    for item in snapshot.get("upscalers") or []:
        name = str(item.get("name") or "").strip()
        if name:
            append_unique("upscalers", {"kind": "upscaler", "name": name, "source": "forge_upscalers"})
    extension_capabilities = snapshot.get("extension_capabilities") if isinstance(snapshot.get("extension_capabilities"), dict) else {}
    lora_capability = extension_capabilities.get("lora_stack") if isinstance(extension_capabilities.get("lora_stack"), dict) else {}
    for name in lora_capability.get("loras") or []:
        clean = str(name or "").strip()
        if clean:
            append_unique("loras", {"kind": "lora", "name": clean, "source": str(lora_capability.get("catalog_source") or "forge_lora_catalog")})
    embedding_capability = extension_capabilities.get("embeddings_ti") if isinstance(extension_capabilities.get("embeddings_ti"), dict) else {}
    for name in embedding_capability.get("embeddings") or []:
        clean = str(name or "").strip()
        if clean:
            append_unique("embeddings", {"kind": "embedding", "name": clean, "source": "forge_embeddings"})
    ip_capability = extension_capabilities.get("ip_adapter") if isinstance(extension_capabilities.get("ip_adapter"), dict) else {}
    for item in ip_capability.get("models") or []:
        if not isinstance(item, dict):
            continue
        clean = str(item.get("catalog_name") or item.get("name") or "").strip()
        if clean:
            append_unique("ip_adapter_models", {
                "kind": "ip_adapter",
                "name": clean,
                "source": "forge_controlnet_catalog",
                "family": str(item.get("family") or ""),
                "variant": str(item.get("variant") or ""),
                "required_preprocessor": str(item.get("required_module") or ""),
            })
    faceid_available = bool(ip_capability.get("faceid_available"))
    for item in ip_capability.get("faceid_records") or []:
        if not isinstance(item, dict):
            continue
        clean = str(item.get("catalog_name") or item.get("name") or "").strip()
        if clean:
            append_unique("ip_adapter_faceid_models", {
                "kind": "ip_adapter_faceid",
                "name": clean,
                "source": "forge_controlnet_catalog",
                "available": faceid_available and bool(item.get("supported")),
                "family": str(item.get("family") or ""),
                "variant": str(item.get("variant") or "faceid"),
                "required_preprocessor": str(item.get("required_module") or ""),
            })
    return buckets


def _coerce_setting_value(current: Any, incoming: Any) -> Any:
    if isinstance(current, bool):
        if isinstance(incoming, bool):
            return incoming
        return str(incoming).strip().casefold() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(incoming)
    if isinstance(current, float):
        return float(incoming)
    if isinstance(current, str):
        return str(incoming)
    if isinstance(current, list):
        if isinstance(incoming, list):
            return incoming
        if isinstance(incoming, str):
            text = incoming.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                return [item.strip() for item in text.split(",") if item.strip()]
        raise ValueError("Expected a list value.")
    if current is None:
        if isinstance(incoming, (str, int, float, bool, list, dict)) or incoming is None:
            return incoming
    raise ValueError(f"Unsupported setting type: {type(current).__name__}")


def update_forge_settings(
    profile: dict[str, Any],
    changes: dict[str, Any],
    *,
    expert_confirmed: bool = False,
    client: ForgeAdminHttpClient | None = None,
) -> dict[str, Any]:
    profile = profile or {}
    if str(profile.get("provider_id") or "") != "forge":
        return {"ok": False, "status": "wrong_provider", "errors": ["Forge settings can only be changed on a Forge backend profile."]}
    if profile.get("enabled") is False:
        return {"ok": False, "status": "disabled", "errors": ["Forge profile is disabled."]}
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str((connection or {}).get("base_url") or "").strip().rstrip("/")
    timeout = max(1.0, float((connection or {}).get("timeout_seconds") or 10.0))
    if not _valid_base_url(base_url):
        return {"ok": False, "status": "missing_config", "errors": ["Forge base URL is missing or invalid."]}
    if not isinstance(changes, dict) or not changes:
        return {"ok": False, "status": "no_changes", "errors": ["No Forge settings were supplied."]}
    client = client or ForgeAdminHttpClient()
    try:
        current_response = client.request_json("GET", base_url, "/sdapi/v1/options", timeout=timeout)
    except ForgeAdminRequestError as exc:
        return {"ok": False, "status": "request_failed", "errors": [str(exc)], "http_status": exc.status_code}
    current = current_response.data if isinstance(current_response.data, dict) else {}
    accepted: dict[str, Any] = {}
    rejected: list[dict[str, str]] = []
    for raw_key, incoming in changes.items():
        key = str(raw_key or "").strip()
        if not key or key not in current:
            rejected.append({"key": key, "reason": "Unknown Forge setting key."})
            continue
        if _is_sensitive_key(key) or _is_path_key(key) or _is_network_key(key) or _looks_like_path(current.get(key)):
            rejected.append({"key": key, "reason": "Credential, path, or server startup settings are read-only in Neo Admin."})
            continue
        if key not in GUIDED_SETTING_KEYS and not expert_confirmed:
            rejected.append({"key": key, "reason": "Advanced Forge settings require Expert mode confirmation."})
            continue
        try:
            accepted[key] = _coerce_setting_value(current.get(key), incoming)
        except (TypeError, ValueError) as exc:
            rejected.append({"key": key, "reason": str(exc)})
    if not accepted:
        return {"ok": False, "status": "rejected", "accepted": {}, "rejected": rejected, "errors": ["No Forge settings passed the write policy."]}
    try:
        client.request_json("POST", base_url, "/sdapi/v1/options", timeout=timeout, payload=accepted)
    except ForgeAdminRequestError as exc:
        return {"ok": False, "status": "request_failed", "accepted": accepted, "rejected": rejected, "errors": [str(exc)], "http_status": exc.status_code}
    return {
        "ok": True,
        "status": "updated",
        "accepted": accepted,
        "updated_keys": sorted(accepted),
        "rejected": rejected,
        "message": f"Updated {len(accepted)} Forge setting(s).",
    }


def refresh_forge_model_catalog(profile: dict[str, Any], *, client: ForgeAdminHttpClient | None = None) -> dict[str, Any]:
    profile = profile or {}
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str((connection or {}).get("base_url") or "").strip().rstrip("/")
    timeout = max(1.0, float((connection or {}).get("timeout_seconds") or 10.0))
    if str(profile.get("provider_id") or "") != "forge":
        return {"ok": False, "status": "wrong_provider", "errors": ["Forge model refresh can only run on a Forge profile."]}
    if profile.get("enabled") is False:
        return {"ok": False, "status": "disabled", "errors": ["Forge profile is disabled."]}
    if not _valid_base_url(base_url):
        return {"ok": False, "status": "missing_config", "errors": ["Forge base URL is missing or invalid."]}
    client = client or ForgeAdminHttpClient()
    results: dict[str, Any] = {}
    errors_out: list[str] = []
    for name, path in (("checkpoints", "/sdapi/v1/refresh-checkpoints"), ("modules", "/sdapi/v1/refresh-vae")):
        try:
            response = client.request_json("POST", base_url, path, timeout=timeout, payload={})
            results[name] = {"ok": True, "http_status": response.status_code}
        except ForgeAdminRequestError as exc:
            results[name] = {"ok": False, "http_status": exc.status_code, "message": str(exc)}
            errors_out.append(f"{name}: {exc}")
    return {
        "ok": not errors_out,
        "status": "refreshed" if not errors_out else "partial",
        "results": results,
        "errors": errors_out,
        "message": "Forge model catalogs refreshed." if not errors_out else "Forge model refresh completed with warnings.",
    }
