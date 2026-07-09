from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any
from urllib import error, request
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.providers.base import BaseProvider
import os
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderFeatureCapabilities, ProviderManifest, ProviderRunResult

# In-memory job handoff for sync image APIs. The Image surface polls after queueing,
# so keep completed results available even when the provider instance is rebuilt.
_XAI_GROK_IMAGE_JOBS: dict[str, ProviderRunResult] = {}

_ALLOWED_ASPECT_RATIOS = {"auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "19.5:9", "9:19.5", "20:9", "9:20"}
_ALLOWED_RESOLUTIONS = {"1k", "2k"}
_IMAGE_MODES = {"txt2img", "generate", "img2img", "image_to_image", "multi_image_edit"}


class XaiGrokProvider(BaseProvider):
    """xAI Grok Imagine provider adapter for Neo Image cloud API profiles.

    Phase K implements image generation plus image edit / multi-image edit. Video/text xAI
    provider surfaces remain manifest templates until their own runtime phases.
    """

    def __init__(self, manifest: ProviderManifest, profile: dict[str, Any] | None = None) -> None:
        super().__init__(manifest)
        self.profile = profile or {}

    def feature_capabilities(self) -> ProviderFeatureCapabilities:
        return ProviderFeatureCapabilities(
            progress=False,
            live_preview=False,
            cancel=False,
            pause=False,
            resume=False,
            clip_skip=False,
            prompt_conditioning=False,
            node_manager=False,
            output_handoff="neo_owned_cloud_output",
            progress_source="sync_complete_poll",
            live_preview_source="none",
        )

    def discover_models(self) -> list[dict[str, Any]]:
        model_block = self.profile.get("model") if isinstance(self.profile.get("model"), dict) else {}
        models = model_block.get("available_models") or ["grok-imagine-image", "grok-imagine-image-quality"]
        return [
            {"id": str(model), "name": str(model), "kind": "image_model", "provider_id": "xai_grok"}
            for model in models
            if str(model or "").strip()
        ]

    def compile_job(self, job: NeoJob) -> CompiledJob:
        try:
            payload = self._build_xai_payload(job)
            endpoint = self._endpoint_for_mode(job.mode)
            return CompiledJob(
                provider_id=self.manifest.provider_id,
                compile_status="compiled",
                backend_payload={
                    "endpoint": endpoint,
                    "payload": _redact_large_images(payload),
                    "mode": self._normalize_mode(job.mode),
                    "profile_id": self.profile.get("profile_id") or job.params.get("backend_profile_id") or job.params.get("profile_id") or "",
                },
            )
        except Exception as exc:  # noqa: BLE001 - compile is diagnostic.
            return CompiledJob(
                provider_id=self.manifest.provider_id,
                compile_status="mock_compiled",
                backend_payload={"error": str(exc), "mode": job.mode},
            )

    def run_job(self, job: NeoJob) -> ProviderRunResult:
        job_id = job.job_id or f"xai-grok-{uuid4().hex[:10]}"
        try:
            self._validate_runtime_profile(job)
            endpoint = self._endpoint_for_mode(job.mode)
            xai_payload = self._build_xai_payload(job)
            raw_response = self._post_json(endpoint, xai_payload)
            outputs = self._normalize_outputs(raw_response, job_id=job_id)
            actual_params = self._actual_params(job, xai_payload)
            result = ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="completed",
                message=f"Grok Imagine {self._normalize_mode(job.mode)} completed.",
                outputs=outputs,
                runtime={
                    "provider_id": "xai_grok",
                    "mode": self._normalize_mode(job.mode),
                    "endpoint": endpoint,
                    "actual_params": actual_params,
                    "route_snapshot": {
                        "provider_id": "xai_grok",
                        "backend": "cloud_api",
                        "family": "grok_imagine",
                        "loader": "api_model",
                        "mode": self._normalize_mode(job.mode),
                        "parameter_profile": "grok_imagine_image_api",
                    },
                    "extensions": self._extension_runtime_snapshot(job),
                    "source_images": actual_params.get("source_images") or [],
                    "respect_moderation": _extract_first(raw_response, "respect_moderation"),
                    "raw_response_keys": sorted(raw_response.keys()) if isinstance(raw_response, dict) else [],
                    "phase": "L-output-replay-metadata",
                    "sync_completion": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 - provider result should be normalized.
            result = ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=str(exc),
                outputs=[],
                runtime={
                    "provider_id": "xai_grok",
                    "mode": self._normalize_mode(job.mode),
                    "error_type": exc.__class__.__name__,
                },
            )
        _XAI_GROK_IMAGE_JOBS[job_id] = result
        return result

    def poll_job(self, job_id: str) -> ProviderRunResult:
        return _XAI_GROK_IMAGE_JOBS.get(job_id) or ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="failed",
            message="Grok Imagine job result is no longer available in memory. Re-run the request.",
            outputs=[],
        )

    def fetch_outputs(self, job_id: str) -> list[dict[str, Any]]:
        result = _XAI_GROK_IMAGE_JOBS.get(job_id)
        return model_to_dict(result).get("outputs", []) if result else []

    def cancel_job(self, job_id: str) -> ProviderRunResult:
        result = _XAI_GROK_IMAGE_JOBS.get(job_id)
        if result and result.status == "completed":
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="completed", message="Grok Imagine requests complete synchronously; completed jobs cannot be cancelled.", outputs=result.outputs, runtime=result.runtime)
        return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="cancelled", message="No active Grok Imagine job to cancel.")

    def _validate_runtime_profile(self, job: NeoJob) -> None:
        if job.surface != "image":
            raise ValueError("Phase K xAI adapter supports Image jobs only.")
        if self.profile.get("enabled") is False:
            raise ValueError("Grok backend profile is disabled in Admin > Backends.")
        if self._normalize_mode(job.mode) not in {"txt2img", "img2img", "multi_image_edit"}:
            raise ValueError(f"Grok Imagine Image does not support mode '{job.mode}' in Phase K.")
        if not self.profile:
            raise ValueError("Grok backend profile is required. Create/select an Image → Grok Imagine profile in Admin > Backends.")
        if str(self.profile.get("connection_type") or "").strip().lower() != "cloud_api":
            raise ValueError("Grok backend profile must use connection_type=cloud_api.")
        key_status = _resolve_api_key(self.profile)
        if not key_status.get("api_key_value"):
            source = key_status.get("api_key_source") or "api key"
            env = key_status.get("api_key_env") or (self.profile.get("connection") or {}).get("api_key_env") or "XAI_API_KEY"
            if source == "env":
                raise ValueError(f"Grok API key is missing. Set {env} or configure a manual key in Admin > Backends.")
            raise ValueError("Grok API key is missing. Configure it in Admin > Backends.")

    def _endpoint_for_mode(self, mode: str) -> str:
        defaults = self._defaults()
        normalized = self._normalize_mode(mode)
        if normalized == "txt2img":
            path = str(defaults.get("generation_path") or "/images/generations")
        else:
            path = str(defaults.get("edit_path") or "/images/edits")
        base_url = str((self.profile.get("connection") or {}).get("base_url") or "https://api.x.ai/v1").rstrip("/")
        return f"{base_url}/{path.lstrip('/')}"

    def _build_xai_payload(self, job: NeoJob) -> dict[str, Any]:
        params = job.params if isinstance(job.params, dict) else {}
        defaults = self._defaults()
        model_block = self.profile.get("model") if isinstance(self.profile.get("model"), dict) else {}
        available_models = [str(item).strip() for item in (model_block.get("available_models") or []) if str(item or "").strip()]
        model = str(job.model or params.get("model") or model_block.get("default_model") or "grok-imagine-image").strip()
        if available_models and model not in available_models:
            # Avoid forwarding stale UI placeholder values such as provider_default.
            model = str(model_block.get("default_model") or available_models[0] or "grok-imagine-image").strip()
        prompt = str(job.prompt or params.get("prompt") or params.get("positive_prompt") or "").strip()
        if not prompt:
            raise ValueError("Prompt is required for Grok Imagine image requests.")
        payload: dict[str, Any] = {"model": model, "prompt": prompt}
        n = _safe_int(params.get("n", params.get("image_count", defaults.get("n", 1))), 1)
        payload["n"] = max(1, min(10, n))
        aspect_ratio = str(params.get("aspect_ratio") or defaults.get("aspect_ratio") or "").strip()
        if aspect_ratio and aspect_ratio in _ALLOWED_ASPECT_RATIOS:
            payload["aspect_ratio"] = aspect_ratio
        resolution = str(params.get("resolution") or defaults.get("resolution") or "").strip().lower()
        if resolution and resolution in _ALLOWED_RESOLUTIONS:
            payload["resolution"] = resolution
        response_format = str(params.get("response_format") or defaults.get("response_format") or "b64_json").strip()
        if response_format in {"b64_json", "url"}:
            payload["response_format"] = response_format

        normalized_mode = self._normalize_mode(job.mode)
        if normalized_mode != "txt2img":
            image_records = self._image_input_records(params)
            image_inputs = [record["payload"] for record in image_records]
            if not image_inputs:
                raise ValueError("Grok image edit requires source_image/source_image_1 before generation.")
            # xAI's single-image edit examples use `image`; multi-image edit uses
            # `images` with up to 3 source image_url objects.
            if len(image_inputs) == 1:
                payload["image"] = image_inputs[0]
            else:
                payload["images"] = image_inputs[:3]
            payload["_neo_source_images"] = [record["metadata"] for record in image_records]
        return payload

    def _image_input_records(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = [
            {"lane": 1, "key": "source_image", "value": params.get("source_image") or params.get("source_image_path") or params.get("init_image"), "name": params.get("source_image_name"), "role": params.get("source_image_1_role") or "main_subject"},
            {"lane": 2, "key": "source_image_2", "value": params.get("source_image_2") or params.get("source_image_2_path") or params.get("reference_image_2"), "name": params.get("source_image_2_name") or params.get("reference_image_2_name"), "role": params.get("source_image_2_role") or "secondary_subject"},
            {"lane": 3, "key": "source_image_3", "value": params.get("source_image_3") or params.get("source_image_3_path") or params.get("composition_image") or params.get("reference_image_3"), "name": params.get("source_image_3_name") or params.get("composition_image_name"), "role": params.get("source_image_3_role") or "composition_guide"},
        ]
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            value = str(candidate.get("value") or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            url = self._image_ref_to_url(value)
            kind = "data_uri" if url.startswith("data:image/") else "url" if url.startswith(("http://", "https://")) else "file_id"
            payload = {"url": url, "type": "image_url"}
            records.append({
                "payload": payload,
                "metadata": {
                    "lane": candidate.get("lane"),
                    "key": candidate.get("key"),
                    "name": str(candidate.get("name") or Path(value).name or f"source_image_{candidate.get('lane')}").strip(),
                    "role": str(candidate.get("role") or "reference").strip(),
                    "input_kind": kind,
                    "ref": value if not url.startswith("data:image/") else "local_file_data_uri",
                },
            })
        return records[:3]

    def _image_input_payloads(self, params: dict[str, Any]) -> list[dict[str, str]]:
        return [record["payload"] for record in self._image_input_records(params)]

    def _image_ref_to_url(self, value: str) -> str:
        if value.startswith("http://") or value.startswith("https://") or value.startswith("data:image/"):
            return value
        path = Path(value).expanduser()
        if not path.exists() or not path.is_file():
            raise ValueError(f"Source image not found: {value}")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        connection = self.profile.get("connection") if isinstance(self.profile.get("connection"), dict) else {}
        timeout = float(connection.get("timeout_seconds") or 120)
        api_key = _resolve_api_key(self.profile).get("api_key_value")
        payload = {k: v for k, v in payload.items() if not str(k).startswith("_neo_")}
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Neo-Studio/phase-l-output-replay-metadata",
            },
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - configured cloud API endpoint.
                raw = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise ValueError(f"Grok API authentication failed ({exc.code}). Check the API key in Admin > Backends.") from exc
            raise ValueError(f"Grok API request failed ({exc.code}): {_compact_error_body(detail)}") from exc
        except error.URLError as exc:
            raise ValueError(f"Grok API is unreachable: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise ValueError("Grok API returned a non-JSON response.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Grok API returned an unexpected response shape.")
        if parsed.get("error"):
            raise ValueError(f"Grok API error: {_compact_error_body(parsed.get('error'))}")
        return parsed

    def _normalize_outputs(self, raw: dict[str, Any], *, job_id: str) -> list[dict[str, Any]]:
        items = raw.get("data") if isinstance(raw.get("data"), list) else []
        if not items and (raw.get("url") or raw.get("b64_json") or raw.get("image")):
            items = [raw]
        outputs: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            b64 = str(item.get("b64_json") or item.get("base64") or "").strip()
            url = str(item.get("url") or "").strip()
            output: dict[str, Any] = {
                "kind": "image",
                "provider_id": "xai_grok",
                "filename": f"grok_imagine_{job_id}_{index}.png",
                "metadata": {k: v for k, v in item.items() if k not in {"b64_json", "base64"}},
            }
            if b64:
                output["local_path"] = str(_write_temp_image(job_id, index, b64))
            elif url:
                output["url"] = url
            else:
                continue
            outputs.append(output)
        if not outputs:
            raise ValueError("Grok API response did not include image output data.")
        return outputs

    def _actual_params(self, job: NeoJob, xai_payload: dict[str, Any]) -> dict[str, Any]:
        params = dict(job.params or {})
        source_images = xai_payload.get("_neo_source_images") if isinstance(xai_payload.get("_neo_source_images"), list) else []
        params.update({
            "provider_id": "xai_grok",
            "api_endpoint": self._endpoint_for_mode(job.mode),
            "backend": "cloud_api",
            "model": xai_payload.get("model"),
            "aspect_ratio": xai_payload.get("aspect_ratio", params.get("aspect_ratio", "")),
            "resolution": xai_payload.get("resolution", params.get("resolution", "")),
            "n": xai_payload.get("n", 1),
            "response_format": xai_payload.get("response_format", ""),
            "source_image_count": len(source_images),
            "source_images": source_images,
            "edit_endpoint_mode": "multi_image_edit" if len(source_images) > 1 else "image_edit" if len(source_images) == 1 else "txt2img",
            "_neo_replay_provider_contract": {
                "schema_version": "neo.xai_grok.replay_contract.v1",
                "provider_id": "xai_grok",
                "connection_type": "cloud_api",
                "requires_backend_profile": True,
                "requires_api_key": True,
                "supports_exact_seed_replay": False,
                "supports_source_image_replay": bool(source_images),
                "source_image_count": len(source_images),
                "inline_extensions_blocked": ["adetailer", "highres_lab", "controlnet", "ip_adapter", "lora_stack"],
                "post_output_bridge_required_for_blocked_extensions": True,
            },
        })
        # SD/Comfy-only controls are recorded as unsupported, not forwarded.
        params["unsupported_inline_controls"] = [
            key for key in ("steps", "cfg", "sampler", "scheduler", "seed", "denoise", "lora", "controlnet", "adetailer", "highres")
            if key in params
        ]
        return params

    def _extension_runtime_snapshot(self, job: NeoJob) -> dict[str, Any]:
        extensions = job.extensions if isinstance(job.extensions, dict) else {}
        return {
            "provider_neutral_allowed": ["wildcards", "style_stack", "prompt_extensions"],
            "inline_blocked_for_cloud_api": ["adetailer", "highres_lab", "controlnet", "ip_adapter", "lora_stack", "regional_conditioning"],
            "request_extensions": extensions,
        }

    def _defaults(self) -> dict[str, Any]:
        defaults = self.profile.get("defaults") or self.profile.get("generation_defaults") or {}
        return defaults if isinstance(defaults, dict) else {}

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        clean = str(mode or "txt2img").strip().lower()
        if clean in {"generate", "txt2img"}:
            return "txt2img"
        if clean in {"img2img", "image_to_image", "edit", "image_edit"}:
            return "img2img"
        if clean in {"multi_image_edit", "multi-image-edit", "reference_edit"}:
            return "multi_image_edit"
        return clean


def _write_temp_image(job_id: str, index: int, b64: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "neo_data" / "runtime" / "xai_grok" / "images"
    root.mkdir(parents=True, exist_ok=True)
    # Strip accidental data-uri prefix if the API ever returns it.
    if "," in b64 and b64.startswith("data:image/"):
        b64 = b64.split(",", 1)[1]
    data = base64.b64decode(b64)
    path = root / f"grok_imagine_{job_id}_{index}_{int(time.time())}.png"
    path.write_bytes(data)
    return path


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _extract_first(raw: dict[str, Any], key: str) -> Any:
    if key in raw:
        return raw.get(key)
    data = raw.get("data") if isinstance(raw.get("data"), list) else []
    if data and isinstance(data[0], dict):
        return data[0].get(key)
    return None


def _compact_error_body(value: Any, limit: int = 500) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value or "")
    text = " ".join(text.split())
    return text[:limit]


def _redact_large_images(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload))

    def scrub_image(item: Any) -> Any:
        if isinstance(item, dict) and str(item.get("url") or "").startswith("data:image/"):
            return {**item, "url": "data:image/*;base64,<redacted>"}
        return item

    if isinstance(redacted.get("image"), dict):
        redacted["image"] = scrub_image(redacted["image"])
    if isinstance(redacted.get("images"), list):
        redacted["images"] = [scrub_image(item) for item in redacted["images"]]
    return redacted


def _resolve_api_key(profile: dict[str, Any]) -> dict[str, Any]:
    # Pass T: provider runtime must load manual keys from the local secret store,
    # not from backend_profiles.json. Keep a small fallback so direct/unit imports
    # remain resilient during partial provider bootstrap.
    try:
        from neo_app.providers.profiles import resolve_backend_profile_api_key

        return resolve_backend_profile_api_key(profile or {})
    except Exception:  # noqa: BLE001 - provider auth fallback must not crash imports.
        connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
        auth_mode = str(connection.get("auth_mode") or connection.get("api_key_mode") or "none").strip().lower()
        if auth_mode not in {"env", "manual", "none"}:
            auth_mode = "env"
        if auth_mode == "none":
            return {"auth_mode": "none", "api_key_is_configured": True, "api_key_source": "none", "api_key_value": ""}
        if auth_mode == "env":
            env_name = str(connection.get("api_key_env") or "XAI_API_KEY").strip() or "XAI_API_KEY"
            value = str(os.getenv(env_name) or "").strip()
            return {"auth_mode": "env", "api_key_is_configured": bool(value), "api_key_source": "env", "api_key_env": env_name, "api_key_value": value}
        value = str(connection.get("api_key_value") or "").strip()
        return {"auth_mode": "manual", "api_key_is_configured": bool(value), "api_key_source": "manual", "api_key_value": value}
