from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import mimetypes
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request, error
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.extensions.workflow_hooks import (
    apply_comfy_workflow_extension_patches,
    has_comfy_workflow_extension_requests,
    lora_stack_execution_requested,
)
from neo_app.providers.base import BaseProvider
from neo_app.providers.comfy_artifact_paths import (
    ComfyArtifactPathError,
    ComfyArtifactReference,
    normalize_comfy_artifact_reference,
    parse_comfy_artifact_name,
)
from neo_app.providers.capability_discovery import discover_comfy_backend_capabilities, discovery_result_to_dict
from neo_app.providers.compile_router import select_comfy_compile_route
from neo_app.providers.comfy_workflows.checkpoint_sd import resolve_sd_checkpoint_defaults
from neo_app.providers.comfy_workflows.flux_native import compile_flux_native_txt2img, compile_flux_klein_txt2img, compile_flux_fill_workflow, compile_flux_krea_workflow
from neo_app.providers.comfy_workflows.flux_gguf import compile_flux_gguf_txt2img
from neo_app.providers.comfy_workflows.krea2 import compile_krea2_workflow
from neo_app.providers.comfy_workflows.lanpaint import (
    LANPAINT_BASE_OBJECT_INFO_NODE_CLASSES,
    LANPAINT_OBJECT_INFO_NODE_CLASSES,
    PHASE5_GRAPH_STATE,
)
from neo_app.providers.comfy_workflows.lanpaint_family import compile_lanpaint_family_inpaint
from neo_app.providers.comfy_workflows.masked_edit_engine import patch_masked_edit_workflow
from neo_app.providers.comfy_workflows.multi_ksampler import MultiKSamplerError, patch_multi_ksampler_workflow, verify_multi_ksampler_workflow
from neo_app.providers.comfy_workflows.res4lyf_sampler import (
    CLOWNSHARK_NODE_CANDIDATES,
    Res4lyfSamplerError,
    inspect_res4lyf_sampler,
    patch_res4lyf_sampler_backend,
    res4lyf_sampler_requested,
)
from neo_app.providers.comfy_workflows.qwen_gguf import compile_qwen_gguf_txt2img
from neo_app.providers.comfy_workflows.qwen_native import compile_qwen_native_txt2img
from neo_app.providers.comfy_workflows.qwen_aio import compile_qwen_native_edit, compile_qwen_rapid_aio_checkpoint
from neo_app.providers.comfy_workflows.qwen_stitch_handoff import record_qwen_stitch_comfy_handoff
from neo_app.image.qwen_stitch_contract import extract_qwen_stitch_payload
from neo_app.image.source_visibility_mask import (
    SourceVisibilityMaskError,
    apply_source_visibility_mask,
    normalize_source_visibility_mask,
)
from neo_app.image.flux2_klein_contract import (
    check_flux2_klein_compatibility,
    reconcile_flux2_klein_encoder_params,
    resolve_flux2_klein_variant,
)
from neo_app.image.flux1_krea_contract import (
    check_flux1_krea_compatibility,
    is_flux1_krea_route,
    is_flux1_krea_variant,
    resolve_flux1_variant,
)
from neo_app.image.krea2_contract import check_krea2_compatibility, resolve_krea2_variant
from neo_app.image.parameter_integrity import (
    concrete_mismatches,
    finalize_comfy_parameter_integrity,
    mismatch_message,
)
from neo_app.image.family_compatibility import (
    build_image_family_compatibility_matrix,
    validate_image_feature_request,
)
from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_capability_discovery import (
    build_lanpaint_capability_snapshot_metadata,
    build_lanpaint_discovery_contract,
)
from neo_app.image.lanpaint_family_expansion import lanpaint_family_expansion_registry, lanpaint_family_expansion_summary
from neo_app.image.lanpaint_family_adapter import lanpaint_family_adapter_registry
from neo_app.providers.comfy_workflows.image_stitch_route import (
    extract_image_stitch_payload,
    image_stitch_has_ready_group,
    image_stitch_is_qwen_lane_route,
    patch_source_loadimage_with_stitch,
)
from neo_app.providers.comfy_workflows.z_image import compile_z_image_txt2img
from neo_app.providers.comfy_workflows.hidream import compile_hidream_txt2img
from neo_app.providers.comfy_workflows.phase22_families import compile_anima_image, compile_ideogram4_txt2img
from neo_extensions.built_in.ip_adapter.backend.node_discovery import split_ip_adapter_model_names
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.image.state_boundary import sanitize_image_params_for_state_boundary
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.image.output_records import build_route_metadata, build_route_snapshot, normalize_latent_capture_request
from neo_app.image.lanpaint_replay import refresh_lanpaint_replay_contract
from neo_extensions.built_in.adetailer.backend.route_contract import build_adetailer_route_contract
from neo_extensions.built_in.adetailer.backend.support_matrix import support_for_route as adetailer_support_for_route
from neo_extensions.built_in.high_res_lab.backend.support_matrix import is_route_active as high_res_lab_route_active
from neo_extensions.built_in.scene_director.backend.support_matrix import get_scene_director_support
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile
from neo_extensions.built_in.background_removal.backend.context_latent import (
    build_context_latent_catalog,
    normalize_context_latent,
    resolve_context_latent,
)
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderFeatureCapabilities, ProviderRunResult, ProviderValidationResult
from neo_app.runtime.job_registry import GenerationJobRegistry, get_generation_job_registry
from neo_app.services.comfy_gpu_lifecycle import ComfyGpuBusyError, get_comfy_gpu_lifecycle_manager
from neo_app.services.comfy_model_residency import get_comfy_model_residency_guard
from neo_app.services.comfy_runtime_recovery import classify_comfy_runtime_error
from neo_app.services.runtime_debug_logs import (
    log_image_event,
    record_compiled_workflow,
    record_generation_error,
    record_poll_payload,
    record_queue_payload,
)


def _image_timing_iso(timestamp: float | None = None) -> str:
    try:
        value = float(timestamp if timestamp is not None else time.time())
    except (TypeError, ValueError):
        value = time.time()
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _image_elapsed_label(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0))))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remaining = total % 60
    if hours:
        return f"{hours}h {minutes:02d}m {remaining:02d}s"
    if minutes:
        return f"{minutes}m {remaining:02d}s"
    return f"{remaining}s"


def _image_run_timing(runtime: dict[str, Any] | None, *, completed: bool = False) -> dict[str, Any]:
    runtime = runtime if isinstance(runtime, dict) else {}
    started = float(runtime.get("started_at") or time.time())
    completed_ts = float(runtime.get("completed_at") or time.time()) if completed else time.time()
    elapsed = max(0.0, completed_ts - started)
    return {
        "schema_version": "neo.image.run_timing.v1",
        "surface": "image",
        "timing_source": "comfy_provider_wall_clock",
        "started_at": runtime.get("started_at_iso") or _image_timing_iso(started),
        "queued_at": runtime.get("queued_at_iso") or runtime.get("started_at_iso") or _image_timing_iso(started),
        "completed_at": _image_timing_iso(completed_ts) if completed else "",
        "elapsed_seconds": round(elapsed, 3),
        "elapsed_label": _image_elapsed_label(elapsed),
        "state": "completed" if completed else "running",
        "replay_used": False,
        "notes": ["Measured from successful Comfy /prompt queue handoff until completed history poll."],
    }


_COMFY_HISTORY_FAILURE_STATES = {"error", "failed", "execution_error", "execution_failed"}


def _comfy_history_failure_details(history_item: dict[str, Any] | None) -> dict[str, Any]:
    """Extract a concise Comfy execution error from a history record.

    Comfy writes failed executions into ``/history`` too. A history record with
    no image outputs is therefore not automatically a successful completion.
    This helper keeps backend execution failure separate from Neo output-import
    recovery so the original node/exception message reaches the user.
    """

    item = history_item if isinstance(history_item, dict) else {}
    status_payload = item.get("status") if isinstance(item.get("status"), dict) else {}
    status_str = str(status_payload.get("status_str") or item.get("status_str") or "").strip().lower()
    messages = status_payload.get("messages") if isinstance(status_payload.get("messages"), list) else item.get("messages")
    messages = messages if isinstance(messages, list) else []

    event_name = ""
    event_payload: dict[str, Any] = {}
    for raw_event in reversed(messages):
        if not isinstance(raw_event, (list, tuple)) or len(raw_event) < 2:
            continue
        name = str(raw_event[0] or "").strip()
        payload = raw_event[1] if isinstance(raw_event[1], dict) else {}
        if name in {"execution_error", "execution_failed", "execution_interrupted"}:
            event_name = name
            event_payload = payload
            break

    node_errors = item.get("node_errors") if isinstance(item.get("node_errors"), dict) else {}
    explicit_error = item.get("error")
    failed = status_str in _COMFY_HISTORY_FAILURE_STATES or bool(event_name) or bool(node_errors) or bool(explicit_error)
    if not failed:
        return {}

    node_id = str(event_payload.get("node_id") or "").strip()
    node_type = str(event_payload.get("node_type") or event_payload.get("class_type") or "").strip()
    exception_type = str(event_payload.get("exception_type") or "").strip()
    exception_message = str(
        event_payload.get("exception_message")
        or event_payload.get("message")
        or explicit_error
        or ""
    ).strip()

    if not exception_message and node_errors:
        first_node_id, first_node_error = next(iter(node_errors.items()))
        node_id = node_id or str(first_node_id or "").strip()
        if isinstance(first_node_error, dict):
            node_type = node_type or str(first_node_error.get("class_type") or first_node_error.get("node_type") or "").strip()
            raw_errors = first_node_error.get("errors")
            if isinstance(raw_errors, list) and raw_errors:
                first_error = raw_errors[0]
                if isinstance(first_error, dict):
                    exception_message = str(first_error.get("message") or first_error.get("details") or first_error.get("type") or "").strip()
                else:
                    exception_message = str(first_error or "").strip()
            exception_message = exception_message or str(first_node_error.get("message") or first_node_error.get("error") or "").strip()
        else:
            exception_message = str(first_node_error or "").strip()

    location = ""
    if node_id and node_type:
        location = f" at node {node_id} ({node_type})"
    elif node_id:
        location = f" at node {node_id}"
    elif node_type:
        location = f" in {node_type}"
    reason = exception_message or exception_type or status_str or "Unknown ComfyUI execution error"
    summary = f"ComfyUI execution failed{location}: {reason}"

    return {
        "status_str": status_str or "error",
        "event": event_name or "history_error",
        "node_id": node_id,
        "node_type": node_type,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "summary": summary,
    }


def normalize_comfy_clip_skip(value: Any) -> dict[str, Any]:
    """Map Neo Clip Skip to Comfy's CLIPSetLastLayer semantics.

    Neo UI follows the common SD WebUI convention: 1 = disabled/no skip,
    2 = skip one layer, etc. Comfy's CLIPSetLastLayer expects negative
    stop_at_clip_layer values where -1 is the default/no skip and -2 maps
    to Clip Skip 2.
    """
    try:
        clip_skip = int(value)
    except (TypeError, ValueError):
        clip_skip = 1
    clip_skip = max(1, min(12, clip_skip))
    return {
        "clip_skip": clip_skip,
        "enabled": clip_skip > 1,
        "stop_at_clip_layer": -clip_skip,
        "backend_node": "CLIPSetLastLayer" if clip_skip > 1 else "checkpoint_clip",
        "backend_mode": "comfy_clip_set_last_layer" if clip_skip > 1 else "disabled",
    }


class ComfyProvider(BaseProvider):
    """First real provider adapter for ComfyUI / ComfyUI Portable.

    Phase 11 keeps the adapter deliberately small and safe:
    - probe status without crashing when ComfyUI is offline
    - discover checkpoint/VAE names from `/object_info` when available
    - compile a basic checkpoint txt2img graph
    - queue/poll/fetch through ComfyUI HTTP endpoints

    Advanced families/loaders/extensions are intentionally left to later phases.
    """

    def __init__(self, manifest, base_url: str | None = None, timeout: float | None = None, job_registry: GenerationJobRegistry | None = None) -> None:
        super().__init__(manifest)
        config = manifest.config_schema or {}
        self.base_url = (base_url or config.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
        self.timeout = float(timeout if timeout is not None else config.get("timeout_seconds", 3))
        self._queued_jobs: dict[str, dict[str, Any]] = {}
        self.job_registry = job_registry or get_generation_job_registry()

    def feature_capabilities(self) -> ProviderFeatureCapabilities:
        return ProviderFeatureCapabilities(
            progress=True,
            live_preview=True,
            cancel=True,
            pause=False,
            resume=False,
            clip_skip=True,
            prompt_conditioning=True,
            node_manager=True,
            output_handoff="preview_image_temp_then_neo_data",
            progress_source="comfyui.websocket_and_history",
            live_preview_source="comfyui.websocket_preview",
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _registry_summary(self, job_id: str) -> dict[str, Any]:
        try:
            return self.job_registry.summary(job_id)
        except Exception as exc:  # noqa: BLE001
            return {"schema_id": "neo.runtime.generation_job_registry.v25_2", "ok": False, "job_id": job_id, "error": str(exc)}

    def _load_registered_runtime(self, job_id: str) -> dict[str, Any]:
        runtime = self._queued_jobs.get(job_id) if isinstance(getattr(self, "_queued_jobs", None), dict) else None
        if isinstance(runtime, dict) and runtime:
            return runtime
        try:
            record = self.job_registry.get(job_id) or {}
        except Exception:  # noqa: BLE001
            record = {}
        registered_runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
        if registered_runtime:
            self._queued_jobs[job_id] = dict(registered_runtime)
            return self._queued_jobs[job_id]
        return {}

    def _registered_cancel_requested(self, job_id: str, runtime: dict[str, Any] | None = None) -> bool:
        runtime = runtime if isinstance(runtime, dict) else {}
        if runtime.get("cancel_requested"):
            return True
        try:
            record = self.job_registry.get(job_id) or {}
        except Exception:  # noqa: BLE001
            return False
        control = record.get("control") if isinstance(record.get("control"), dict) else {}
        return bool(control.get("cancel_requested") or record.get("cancel_requested"))

    def _get_json(self, path: str, *, timeout: float | None = None) -> dict[str, Any]:
        with request.urlopen(self._url(path), timeout=self.timeout if timeout is None else timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _is_timeout_error(exc: BaseException) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        if isinstance(exc, error.URLError):
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return True
            return "timed out" in str(reason).lower()
        return "timed out" in str(exc).lower()

    def fetch_live_preview(self, job_id: str) -> dict[str, Any]:
        """Return the latest safe live-preview frame metadata for a queued Comfy job.

        Comfy's reliable preview stream is websocket-based in this build. The HTTP
        polling endpoint is intentionally conservative so the UI can poll without
        crashing when no preview frame has been captured yet.
        """
        job = self._queued_jobs.get(job_id) if isinstance(getattr(self, "_queued_jobs", None), dict) else None
        registry_summary = self._registry_summary(job_id)
        return {
            "ok": False,
            "provider_id": self.manifest.provider_id,
            "job_id": job_id,
            "status": (job or {}).get("status") or registry_summary.get("status") or "unavailable",
            "is_final": False,
            "preview": None,
            "job_registry": registry_summary,
            "message": "No HTTP preview exposed yet; websocket live preview is used when available.",
        }

    def _post_json(self, path: str, payload: dict[str, Any], *, allow_empty: bool = False) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(self._url(path), data=data, headers={"Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=self.timeout) as response:
            raw = response.read()
            if not raw:
                if allow_empty:
                    return {"ok": True, "status_code": getattr(response, "status", None)}
                return {}
            text = raw.decode("utf-8").strip()
            if not text:
                if allow_empty:
                    return {"ok": True, "status_code": getattr(response, "status", None)}
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if allow_empty:
                    return {"ok": True, "raw_response": text, "status_code": getattr(response, "status", None)}
                raise

    @staticmethod
    def _http_error_details(exc: BaseException) -> dict[str, Any]:
        """Return safe Comfy HTTP error context without hiding the queued prompt.

        Comfy often returns useful validation text in the body of 400 responses.
        Neo keeps this generic: any provider queue failure can record the HTTP
        status/body plus the exact prompt payload that was attempted.
        """
        if not isinstance(exc, error.HTTPError):
            return {}
        body_text = ""
        try:
            raw = exc.read()
            if isinstance(raw, bytes):
                body_text = raw.decode("utf-8", errors="replace").strip()
            elif raw is not None:
                body_text = str(raw).strip()
        except Exception:  # noqa: BLE001
            body_text = ""
        parsed_body: Any = None
        if body_text:
            try:
                parsed_body = json.loads(body_text)
            except json.JSONDecodeError:
                parsed_body = None
        return {
            "status_code": getattr(exc, "code", None),
            "reason": getattr(exc, "reason", None),
            "url": getattr(exc, "url", ""),
            "body": parsed_body if parsed_body is not None else body_text,
        }

    @staticmethod
    def _comfy_http_validation_summary(http_error: dict[str, Any] | None) -> dict[str, Any]:
        """Extract the first actionable Comfy prompt-validation failure.

        Keep the raw HTTP body in the durable error log, but expose a compact
        node/input summary to the client so a 400 does not degrade into only
        ``HTTP Error 400: Bad Request``.
        """
        if not isinstance(http_error, dict):
            return {}
        body = http_error.get("body")
        if not isinstance(body, dict):
            return {}
        error_block = body.get("error") if isinstance(body.get("error"), dict) else {}
        node_errors = body.get("node_errors") if isinstance(body.get("node_errors"), dict) else {}
        summary: dict[str, Any] = {
            "schema": "neo.comfy.prompt_validation_summary.v1",
            "status_code": http_error.get("status_code"),
            "reason": http_error.get("reason"),
            "error_type": str(error_block.get("type") or ""),
            "error_message": str(error_block.get("message") or "").strip(),
        }
        if not node_errors:
            return summary if summary.get("error_message") else {}

        node_id, node_block = next(iter(node_errors.items()))
        node_block = node_block if isinstance(node_block, dict) else {}
        errors = node_block.get("errors") if isinstance(node_block.get("errors"), list) else []
        first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
        extra = first_error.get("extra_info") if isinstance(first_error.get("extra_info"), dict) else {}
        summary.update({
            "node_id": str(node_id),
            "class_type": str(node_block.get("class_type") or ""),
            "node_error_type": str(first_error.get("type") or ""),
            "node_error_message": str(first_error.get("message") or "").strip(),
            "details": str(first_error.get("details") or "").strip(),
            "input_name": str(extra.get("input_name") or ""),
            "received_value": extra.get("received_value"),
        })
        return summary

    @staticmethod
    def _extract_comfy_choices(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or not value:
            return []
        first = value[0]
        if isinstance(first, (list, tuple)):
            return [str(item).strip() for item in first if str(item).strip()]
        if all(isinstance(item, str) for item in value):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @classmethod
    def _node_required_choices(cls, object_info: dict[str, Any], node_name: str, *input_names: str) -> list[str]:
        if not node_name:
            return []
        required = (((object_info.get(node_name) or {}).get("input") or {}).get("required") or {})
        optional = (((object_info.get(node_name) or {}).get("input") or {}).get("optional") or {})
        merged: list[str] = []
        seen: set[str] = set()
        for input_name in input_names:
            for item in cls._extract_comfy_choices(required.get(input_name)) + cls._extract_comfy_choices(optional.get(input_name)):
                key = item.casefold()
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
        return merged

    @classmethod
    def _merged_node_choices(cls, object_info: dict[str, Any], aliases: list[str], *input_names: str) -> list[str]:
        """Merge model choices from every compatible Comfy loader alias.

        Custom/portable Comfy builds may register an empty UNETLoader while a
        DiffusionModelLoader alias contains the actual catalog. Selecting only
        the first existing node hides valid component models.
        """

        merged: list[str] = []
        seen: set[str] = set()
        for node_name in aliases:
            if node_name not in object_info:
                continue
            for item in cls._node_required_choices(object_info, node_name, *input_names):
                key = item.casefold()
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
        return merged

    @staticmethod
    def _first_existing_node(object_info: dict[str, Any], aliases: list[str]) -> str:
        return next((alias for alias in aliases if alias in object_info), "")

    @staticmethod
    def _extract_model_names_from_endpoint_payload(payload: Any) -> list[str]:
        candidates: list[Any] = []
        if isinstance(payload, dict):
            for key in (
                "models", "files", "items", "checkpoints", "checkpoint",
                "diffusion_models", "diffusion_model", "unets", "unet",
                "text_encoders", "text_encoder", "clip", "clips",
                "vaes", "vae", "loras", "lora",
                "upscalers", "upscale_models", "upscale_model",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
        elif isinstance(payload, list):
            candidates.extend(payload)
        names: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            raw = item.get("name") or item.get("filename") or item.get("file") or item.get("path") if isinstance(item, dict) else item
            name = str(raw or "").strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
        return names

    def _discover_model_folder_names(self, folder_names: list[str]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for folder in folder_names:
            try:
                payload = self._get_json(f"/models/{folder}")
            except Exception:
                continue
            for name in self._extract_model_names_from_endpoint_payload(payload):
                key = name.casefold()
                if key not in seen:
                    seen.add(key)
                    names.append(name)
        return names

    @staticmethod
    def _is_mmproj_asset(value: str) -> bool:
        lowered = str(value or "").casefold()
        return bool(lowered and ("mmproj" in lowered or "mm_proj" in lowered or "mm-proj" in lowered or ("vision" in lowered and ("qwen" in lowered or "image" in lowered)) or ("projector" in lowered and ("qwen" in lowered or "image" in lowered))))

    @staticmethod
    def _is_qwen_text_encoder_asset(value: str) -> bool:
        lowered = str(value or "").casefold()
        return bool(("qwen" in lowered or "qw" in lowered) and not ComfyProvider._is_mmproj_asset(value))

    @staticmethod
    def _append_model_records(models: list[dict[str, Any]], kind: str, names: list[str]) -> None:
        seen = {(str(item.get("kind") or ""), str(item.get("name") or "").casefold()) for item in models}
        for name in names:
            key = (kind, str(name or "").casefold())
            if not key[1] or key in seen:
                continue
            seen.add(key)
            models.append({"kind": kind, "name": str(name), "provider_id": "comfyui", "source": "comfy_object_info"})

    def status(self) -> dict:
        payload = super().status()
        payload.update({
            "base_url": self.base_url,
            "real_adapter": True,
            "adapter_phase": "phase-11-first-provider",
        })
        try:
            stats = self._get_json("/system_stats")
            payload.update({
                "status": "available",
                "reachable": True,
                "system_stats": stats,
            })
        except Exception as exc:  # noqa: BLE001 - status should never crash the UI.
            payload.update({
                "status": "missing_config",
                "reachable": False,
                "message": f"ComfyUI not reachable at {self.base_url}: {exc}",
            })
        return payload

    @staticmethod
    def _node_input_names(object_info: dict[str, Any], node_name: str) -> dict[str, list[str]]:
        node = object_info.get(node_name) if isinstance(object_info, dict) else None
        inputs = (node or {}).get("input") if isinstance(node, dict) else {}
        required = ((inputs or {}).get("required") or {}) if isinstance(inputs, dict) else {}
        optional = ((inputs or {}).get("optional") or {}) if isinstance(inputs, dict) else {}
        return {
            "required": sorted(str(key) for key in required.keys()),
            "optional": sorted(str(key) for key in optional.keys()),
            "all": sorted({str(key) for key in required.keys()} | {str(key) for key in optional.keys()}),
        }

    def discover_backend_capabilities(
        self,
        *,
        object_info: dict[str, Any] | None = None,
        discovery_error: str = "",
    ) -> dict[str, Any]:
        adapter_registry = lanpaint_family_adapter_registry(self.manifest.provider_id)
        active_adapters = [
            item for item in adapter_registry.get("adapters", [])
            if isinstance(item, dict) and (item.get("binding") or {}).get("selectable")
        ]
        active_routes = [
            (
                str((item.get("identity") or {}).get("family") or ""),
                str((item.get("identity") or {}).get("loader") or ""),
                str((item.get("identity") or {}).get("mode") or "inpaint"),
            )
            for item in active_adapters
        ]
        adapters_by_route = {
            (
                str((item.get("identity") or {}).get("family") or ""),
                str((item.get("identity") or {}).get("loader") or ""),
                str((item.get("identity") or {}).get("mode") or "inpaint"),
            ): item
            for item in active_adapters
        }
        expansion_registry = lanpaint_family_expansion_registry()
        expansion_summary = lanpaint_family_expansion_summary(expansion_registry)
        discovery_contract = build_lanpaint_discovery_contract(
            adapter_registry,
            base_node_classes=LANPAINT_BASE_OBJECT_INFO_NODE_CLASSES,
        )
        object_info_scope = tuple(discovery_contract["required_node_classes"])
        vae_utils_decode_nodes = ("VAEUtils_CustomVAELoader", "VAEUtils_VAEDecodeTiled")

        def evaluate_route(capabilities: dict[str, Any], family: str, loader: str, mode: str = "inpaint") -> dict[str, Any]:
            return evaluate_lanpaint_route_capabilities(
                capabilities,
                provider_id=self.manifest.provider_id,
                family=family,
                loader=loader,
                mode=mode,
                engine="lanpaint",
                family_adapter=adapters_by_route.get((family, loader, mode)),
                adapter_registry=adapter_registry,
                expansion_registry=expansion_registry,
            )

        try:
            if discovery_error:
                raise RuntimeError(discovery_error)
            info = object_info if object_info is not None else self._get_json("/object_info")
        except Exception as exc:  # noqa: BLE001 - discovery must not crash provider payloads.
            error_message = str(exc) if discovery_error else f"ComfyUI object_info discovery failed: {exc}"
            result = discover_comfy_backend_capabilities(
                {},
                provider_id=self.manifest.provider_id,
                reachable=False,
                error=error_message,
            )
            payload = discovery_result_to_dict(result)
            payload["object_info_node_inputs"] = {}
            payload["lanpaint_family_expansion"] = deepcopy(expansion_summary)
            payload["lanpaint_family_adapters"] = deepcopy(adapter_registry)
            payload["lanpaint_capability_discovery_contract"] = deepcopy(discovery_contract)
            payload["lanpaint_capability_snapshot"] = build_lanpaint_capability_snapshot_metadata(
                discovery_contract, discovered_node_classes=(), object_info_available=False
            )
            payload["lanpaint_route_capabilities"] = evaluate_route(payload, "krea2_turbo", "gguf")
            payload["lanpaint_route_capability_matrix"] = {
                f"{family}:{loader}:{mode}:lanpaint": evaluate_route(payload, family, loader, mode)
                for family, loader, mode in active_routes
            }
            payload["masked_edit_node_diagnostics"] = {
                "available_nodes": [],
                "crop_stitch_available": False,
                "required_crop_stitch_pack": "ComfyUI-Inpaint-CropAndStitch",
                "required_crop_stitch_nodes": ["InpaintCropImproved", "InpaintStitchImproved"],
                "native_core_nodes": ["InpaintModelConditioning", "DifferentialDiffusion", "ImagePadForOutpaint"],
            }
            payload["res4lyf_sampler_diagnostics"] = inspect_res4lyf_sampler({})
            payload["vae_utils_decode_diagnostics"] = {
                "available": False,
                "available_nodes": [],
                "missing_nodes": list(vae_utils_decode_nodes),
                "required_nodes": list(vae_utils_decode_nodes),
                "node_repo": "spacepxl/ComfyUI-VAE-Utils",
                "reason": "ComfyUI /object_info unavailable.",
            }
            payload["scene_director_runtime_diagnostics"] = {
                "classic_node_available": False,
                "regional_lora_node_available": False,
                "krea2_regional_available": False,
                "available_nodes": [],
                "missing_nodes": ["NeoSceneDirectorV054", "NeoRegionalLoRADelta", "Krea2RegionalBuilder", "Krea2ApplyRegional"],
                "required_for_krea2_regional": ["Krea2RegionalBuilder", "Krea2ApplyRegional"],
                "required_for_other_modern_regional_lora": "NeoRegionalLoRADelta",
                "krea2_regional_node_repo": "januspluto/ComfyUI-Krea2-Regional",
                "bundled_node_folder": "neo_scene_director",
                "reason": "ComfyUI /object_info unavailable; Scene Director runtime readiness cannot be verified.",
            }
            payload["image_family_compatibility"] = build_image_family_compatibility_matrix(
                provider_id=self.manifest.provider_id,
                object_info={},
                backend_reachable=False,
            )
            payload["lanpaint_node_diagnostics"] = {
                "compiler_id": "comfy.lanpaint.family_aware.v1",
                "available_nodes": [],
                "missing_nodes": list(object_info_scope),
                "graph_compile_enabled": False,
                "graph_state": PHASE5_GRAPH_STATE,
                "route_status": payload["lanpaint_route_capabilities"]["status"],
                "selectable": False,
                "blockers": deepcopy(payload["lanpaint_route_capabilities"]["blockers"]),
                "remediation": list(payload["lanpaint_route_capabilities"]["remediation"]),
                "note": "ComfyUI is offline or /object_info failed; LanPaint remains fail-closed.",
            }
            return payload

        result = discover_comfy_backend_capabilities(info, provider_id=self.manifest.provider_id, reachable=True)
        payload = discovery_result_to_dict(result)
        # Phase M.2: expose a tiny safe object_info slice for provider compilers.
        # Qwen Image Edit node variants differ across Comfy core/Rapid custom nodes:
        # some expose only prompt/images, while Rapid patches expose target_size.
        # The compiler must only send optional sizing inputs when the live backend
        # declares them, otherwise older/newer Comfy installs can reject the prompt.
        qwen_edit_nodes = [
            "TextEncodeQwenImageEditPlus",
            "TextEncodeQwenImageEditPlus_lrzjason",
            "TextEncodeQwenImageEditPlusAdvance_lrzjason",
            "TextEncodeQwenImageEditPlusPro_lrzjason",
        ]
        # Qwen native parity nodes are intentionally included in the safe
        # object_info slice. Provider compilers use this slice to decide whether
        # the installed Comfy backend can reproduce Neo's Qwen-native graph.
        # Omitting these classes previously made live nodes look unavailable and
        # caused CFGNorm / FluxKontext parity stages to be silently skipped.
        qwen_native_parity_nodes = [
            "CFGNorm",
            "FluxKontextImageScale",
            "FluxKontextMultiReferenceLatentMethod",
            "ModelSamplingAuraFlow",
        ]
        # ImageStitch is not a single universal Comfy class name.  Core or
        # other custom-node packs may expose ImageStitch, while the
        # ComfyUI-RMBG pack exposes AILab_ImageStitch.  Preserve both names
        # in the safe object_info slice so provider routes can select the
        # class that is actually installed instead of guessing.
        stitch_nodes = ["ImageStitch", "AILab_ImageStitch"]
        context_latent_nodes = [
            "AILab_ReferenceLatentMask",
            "ReferenceLatentMask",
            "KontextReferenceLatentMask",
        ]
        masked_edit_optional_nodes = [
            "InpaintCropImproved",
            "InpaintStitchImproved",
            "InpaintModelConditioning",
            "DifferentialDiffusion",
            "ImagePadForOutpaint",
        ]
        res4lyf_sampler_nodes = list(CLOWNSHARK_NODE_CANDIDATES)
        scene_director_runtime_nodes = [
            "NeoSceneDirectorV054", "NeoRegionalLoRADelta",
            "Krea2RegionalBuilder", "Krea2ApplyRegional",
        ]
        payload["object_info_node_inputs"] = {
            node_name: self._node_input_names(info, node_name)
            for node_name in [
                *qwen_edit_nodes,
                *qwen_native_parity_nodes,
                *stitch_nodes,
                *context_latent_nodes,
                *masked_edit_optional_nodes,
                *res4lyf_sampler_nodes,
                *vae_utils_decode_nodes,
                *scene_director_runtime_nodes,
                *object_info_scope,
            ]
            if isinstance(info.get(node_name), dict)
        }
        payload["vae_utils_decode_diagnostics"] = {
            "available": all(node_name in payload["object_info_node_inputs"] for node_name in vae_utils_decode_nodes),
            "available_nodes": [node_name for node_name in vae_utils_decode_nodes if node_name in payload["object_info_node_inputs"]],
            "missing_nodes": [node_name for node_name in vae_utils_decode_nodes if node_name not in payload["object_info_node_inputs"]],
            "required_nodes": list(vae_utils_decode_nodes),
            "node_repo": "spacepxl/ComfyUI-VAE-Utils",
            "reason": "Ready" if all(node_name in payload["object_info_node_inputs"] for node_name in vae_utils_decode_nodes) else "Install/update ComfyUI-VAE-Utils and restart ComfyUI.",
        }
        krea2_regional_required = ["Krea2RegionalBuilder", "Krea2ApplyRegional"]
        krea2_regional_ready = all(node_name in payload["object_info_node_inputs"] for node_name in krea2_regional_required)
        payload["scene_director_runtime_diagnostics"] = {
            "classic_node_available": "NeoSceneDirectorV054" in payload["object_info_node_inputs"],
            "regional_lora_node_available": "NeoRegionalLoRADelta" in payload["object_info_node_inputs"],
            "krea2_regional_available": krea2_regional_ready,
            "available_nodes": [node_name for node_name in scene_director_runtime_nodes if node_name in payload["object_info_node_inputs"]],
            "missing_nodes": [node_name for node_name in scene_director_runtime_nodes if node_name not in payload["object_info_node_inputs"]],
            "required_for_krea2_regional": list(krea2_regional_required),
            "required_for_other_modern_regional_lora": "NeoRegionalLoRADelta",
            "krea2_regional_node_repo": "januspluto/ComfyUI-Krea2-Regional",
            "bundled_node_folder": "neo_scene_director",
            "reason": (
                "Krea2 Regional ready" if krea2_regional_ready
                else "Install/update januspluto/ComfyUI-Krea2-Regional and restart ComfyUI for Krea 2 Scene Director regional LoRA isolation."
            ),
        }
        payload["lanpaint_family_expansion"] = deepcopy(expansion_summary)
        payload["lanpaint_family_adapters"] = deepcopy(adapter_registry)
        payload["lanpaint_capability_discovery_contract"] = deepcopy(discovery_contract)
        payload["lanpaint_capability_snapshot"] = build_lanpaint_capability_snapshot_metadata(
            discovery_contract,
            discovered_node_classes=payload["object_info_node_inputs"].keys(),
            object_info_available=True,
        )
        payload["lanpaint_node_diagnostics"] = {
            "compiler_id": "comfy.lanpaint.family_aware.v1",
            "available_nodes": [
                node_name
                for node_name in object_info_scope
                if node_name in payload["object_info_node_inputs"]
            ],
            "missing_nodes": [
                node_name
                for node_name in object_info_scope
                if node_name not in payload["object_info_node_inputs"]
            ],
            "graph_compile_enabled": True,
            "graph_state": PHASE5_GRAPH_STATE,
            "enabled_routes": [
                {
                    "family": str((item.get("identity") or {}).get("family") or ""),
                    "loader": str((item.get("identity") or {}).get("loader") or ""),
                    "mode": str((item.get("identity") or {}).get("mode") or "inpaint"),
                    "engine": "lanpaint",
                    "adapter_id": str((item.get("identity") or {}).get("adapter_id") or ""),
                    "adapter_fingerprint": str(item.get("adapter_fingerprint") or ""),
                }
                for item in active_adapters
            ],
            "note": "Phase 22.1 derives object_info discovery from every selectable family adapter; missing classes now reflect the exact selected route rather than a static Krea-era whitelist.",
        }
        payload["lanpaint_route_capabilities"] = evaluate_route(payload, "krea2_turbo", "gguf")
        payload["lanpaint_route_capability_matrix"] = {
            f"{family}:{loader}:{mode}:lanpaint": evaluate_route(payload, family, loader, mode)
            for family, loader, mode in active_routes
        }
        payload["lanpaint_node_diagnostics"]["route_status"] = payload["lanpaint_route_capabilities"]["status"]
        payload["lanpaint_node_diagnostics"]["selectable"] = payload["lanpaint_route_capabilities"]["selectable"]
        payload["lanpaint_node_diagnostics"]["blockers"] = deepcopy(payload["lanpaint_route_capabilities"]["blockers"])
        payload["lanpaint_node_diagnostics"]["remediation"] = list(payload["lanpaint_route_capabilities"]["remediation"])
        payload["lanpaint_node_diagnostics"]["note"] = "Phase 8 publishes one fail-closed route capability report; the UI must not infer readiness from family/loader identity alone."
        masked_available = [node for node in masked_edit_optional_nodes if node in payload["object_info_node_inputs"]]
        payload["masked_edit_node_diagnostics"] = {
            "available_nodes": masked_available,
            "crop_stitch_available": all(node in payload["object_info_node_inputs"] for node in ("InpaintCropImproved", "InpaintStitchImproved")),
            "required_crop_stitch_pack": "ComfyUI-Inpaint-CropAndStitch",
            "required_crop_stitch_nodes": ["InpaintCropImproved", "InpaintStitchImproved"],
            "native_core_nodes": ["InpaintModelConditioning", "DifferentialDiffusion", "ImagePadForOutpaint"],
        }
        payload["res4lyf_sampler_diagnostics"] = inspect_res4lyf_sampler(info)
        payload["image_family_compatibility"] = build_image_family_compatibility_matrix(
            provider_id=self.manifest.provider_id,
            object_info=info,
            backend_reachable=True,
        )
        payload["context_latent"] = build_context_latent_catalog(info)
        payload["qwen_edit_node_diagnostics"] = {
            "available_nodes": list(payload["object_info_node_inputs"].keys()),
            "builtin_declares_target_size": "target_size" in payload["object_info_node_inputs"].get("TextEncodeQwenImageEditPlus", {}).get("all", []),
            "builtin_declares_size": "size" in payload["object_info_node_inputs"].get("TextEncodeQwenImageEditPlus", {}).get("all", []),
            "native_parity_nodes": {
                node_name: node_name in payload["object_info_node_inputs"]
                for node_name in qwen_native_parity_nodes
            },
        }
        return payload

    def discover_models(self) -> list[dict]:
        try:
            info = self._get_json("/object_info")
        except Exception:
            return []

        models: list[dict] = []
        checkpoint_inputs = (((info.get("CheckpointLoaderSimple") or {}).get("input") or {}).get("required") or {})
        checkpoint_names = checkpoint_inputs.get("ckpt_name", [[]])[0] if checkpoint_inputs.get("ckpt_name") else []
        for name in checkpoint_names:
            models.append({"kind": "checkpoint", "name": name, "provider_id": self.manifest.provider_id})

        diffusion_model_names = self._merged_node_choices(
            info,
            ["UNETLoader", "DiffusionModelLoader", "LoadDiffusionModel"],
            "unet_name", "model_name", "diffusion_model_name",
        )
        if not diffusion_model_names:
            diffusion_model_names = self._discover_model_folder_names(["diffusion_models", "unet", "unets"])
        self._append_model_records(models, "diffusion_model", diffusion_model_names)

        text_encoder_names = self._merged_node_choices(
            info,
            ["CLIPLoader", "DualCLIPLoader", "TextEncoderLoader", "LoadCLIP"],
            "clip_name", "clip_name1", "clip_name2", "text_encoder_name", "text_encoder_name1", "text_encoder_name2",
        )
        if not text_encoder_names:
            text_encoder_names = self._discover_model_folder_names(["text_encoders", "clip", "clips"])
        self._append_model_records(models, "text_encoder", text_encoder_names)
        self._append_model_records(models, "qwen_text_encoder", [item for item in text_encoder_names if self._is_qwen_text_encoder_asset(item)])

        vae_names = self._merged_node_choices(info, ["VAELoader", "LoadVAE"], "vae_name", "model_name")
        if not vae_names:
            vae_names = self._discover_model_folder_names(["vae", "vaes"])
        self._append_model_records(models, "vae", vae_names)

        lora_node = self._first_existing_node(info, ["LoraLoader", "LoraLoaderModelOnly"])
        lora_names = self._node_required_choices(info, lora_node, "lora_name")
        self._append_model_records(models, "lora", lora_names)

        # Built-in IP Adapter dropdown catalogs. Keep these as dedicated model
        # kinds so UI selectors never mix checkpoints/LoRAs/GGUF assets into the
        # IP Adapter model or CLIP Vision dropdowns.
        clip_vision_node = self._first_existing_node(info, ["CLIPVisionLoader", "CLIPVisionLoaderModelOnly"])
        clip_vision_names = self._node_required_choices(info, clip_vision_node, "clip_name", "clip_vision_name", "model_name")
        self._append_model_records(models, "clip_vision", clip_vision_names)

        ip_adapter_node = self._first_existing_node(info, ["IPAdapterModelLoader", "IPAdapterUnifiedLoader", "IPAdapterLoader"])
        ip_adapter_names = self._node_required_choices(info, ip_adapter_node, "ipadapter_file", "ipadapter_name", "model", "model_name", "name")
        ip_adapter_split = split_ip_adapter_model_names(ip_adapter_names)
        self._append_model_records(models, "ip_adapter", ip_adapter_split["ip_adapter"])

        faceid_node = self._first_existing_node(info, ["IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceIDModelLoader"])
        faceid_names = self._node_required_choices(info, faceid_node, "model", "model_name", "ipadapter_file", "faceid_model")
        self._append_model_records(models, "ip_adapter_faceid", faceid_names + ip_adapter_split["faceid"])

        # V1-compatible upscaler catalog extraction. V1 exposed
        # `catalog.upscale_models || catalog.upscalers` in the Upscale Lab
        # dropdown. In Comfy this is the UpscaleModelLoader model_name list.
        upscale_node = self._first_existing_node(info, ["UpscaleModelLoader", "UpscaleModelLoaderProvider"])
        upscale_names = self._node_required_choices(info, upscale_node, "model_name", "upscale_model_name", "upscaler_name", "name")
        # Hotfix parity with V1 Upscale Lab: when object_info choices are empty,
        # query Comfy's model-folder endpoints too. Some portable/custom Comfy
        # installs list upscale models there but not inside UpscaleModelLoader.
        upscale_names.extend(self._discover_model_folder_names(["upscale_models", "upscalers", "ESRGAN", "esrgan"]))
        self._append_model_records(models, "upscaler", upscale_names)

        # Phase 12.10D: V1-compatible GGUF catalog extraction. These records stay
        # in dedicated kinds so UI dropdowns never mix normal checkpoints into GGUF model selectors.
        gguf_unet_node = self._first_existing_node(info, ["UnetLoaderGGUF", "LoaderGGUF"])
        gguf_single_clip_node = self._first_existing_node(info, ["CLIPLoaderGGUF", "ClipLoaderGGUF"])
        gguf_dual_clip_node = self._first_existing_node(info, ["DualCLIPLoaderGGUF"])
        gguf_vae_node = self._first_existing_node(info, ["VaeGGUF", "VAELoaderGGUF"])
        gguf_unet_choices = self._node_required_choices(info, gguf_unet_node, "unet_name", "model_name", "gguf_name")
        gguf_single_clip_choices = self._node_required_choices(info, gguf_single_clip_node, "clip_name", "clip_name1", "text_encoder_name")
        gguf_dual_a_choices = self._node_required_choices(info, gguf_dual_clip_node, "clip_name1", "text_encoder_name", "text_encoder_name1")
        gguf_dual_b_choices = self._node_required_choices(info, gguf_dual_clip_node, "clip_name2", "text_encoder_name2")
        gguf_vae_choices = self._node_required_choices(info, gguf_vae_node, "vae_name", "gguf_name")
        self._append_model_records(models, "gguf_model", gguf_unet_choices)
        self._append_model_records(models, "gguf_text_encoder_primary", gguf_dual_a_choices or gguf_single_clip_choices)
        self._append_model_records(models, "gguf_text_encoder_secondary", gguf_dual_b_choices)
        self._append_model_records(models, "gguf_text_encoder", gguf_single_clip_choices + gguf_dual_a_choices + gguf_dual_b_choices)
        self._append_model_records(models, "gguf_vae", gguf_vae_choices)
        mmproj_choices: list[str] = []
        for node_name in [gguf_single_clip_node, gguf_dual_clip_node]:
            if not node_name:
                continue
            required = (((info.get(node_name) or {}).get("input") or {}).get("required") or {})
            optional = (((info.get(node_name) or {}).get("input") or {}).get("optional") or {})
            for input_name, raw in {**required, **optional}.items():
                if "mmproj" in str(input_name).casefold() or "projector" in str(input_name).casefold():
                    mmproj_choices.extend(self._extract_comfy_choices(raw))
        mmproj_choices.extend([item for item in gguf_single_clip_choices + gguf_dual_a_choices + gguf_dual_b_choices if self._is_mmproj_asset(item)])
        self._append_model_records(models, "mmproj", mmproj_choices)

        ksampler_inputs = (((info.get("KSampler") or {}).get("input") or {}).get("required") or {})
        sampler_names = ksampler_inputs.get("sampler_name", [[]])[0] if ksampler_inputs.get("sampler_name") else []
        scheduler_names = ksampler_inputs.get("scheduler", [[]])[0] if ksampler_inputs.get("scheduler") else []
        for name in sampler_names:
            models.append({"kind": "sampler", "name": name, "provider_id": self.manifest.provider_id})
        for name in scheduler_names:
            models.append({"kind": "scheduler", "name": name, "provider_id": self.manifest.provider_id})
        return models

    def _runtime_job(self, job: NeoJob) -> NeoJob:
        # UI subtab "generate" maps to provider runtime mode "txt2img".
        runtime_mode = "txt2img" if job.mode == "generate" else job.mode
        clean_params, _boundary = sanitize_image_params_for_state_boundary(job.params or {}, runtime_mode)
        if job.family == "flux2_klein":
            clean_params = dict(clean_params)
            selected_model = job.model or clean_params.get("gguf_unet") or clean_params.get("gguf_model") or clean_params.get("diffusion_model") or clean_params.get("model") or ""
            clean_params, encoder_reconciliation = reconcile_flux2_klein_encoder_params(
                clean_params,
                selected_model,
                default_if_missing=False,
            )
            canonical_variant = resolve_flux2_klein_variant(clean_params.get("flux_variant") or clean_params.get("variant") or "flux2_klein", selected_model)
            clean_params["flux_variant"] = canonical_variant or "flux2_klein"
            clean_params.setdefault("gguf_clip_type", "flux2_klein")
            clean_params.setdefault("gguf_clip_mode", "single")
            clean_params.setdefault("clip_type", "flux2")
            clean_params["_neo_flux2_klein_encoder_reconciliation"] = encoder_reconciliation
        if job.family == "flux":
            clean_params = dict(clean_params)
            selected_model = job.model or clean_params.get("gguf_unet") or clean_params.get("gguf_model") or clean_params.get("diffusion_model") or clean_params.get("model") or clean_params.get("unet") or ""
            resolved_flux1_variant = resolve_flux1_variant(clean_params.get("flux_variant") or clean_params.get("variant") or "dev", selected_model)
            if is_flux1_krea_route(resolved_flux1_variant, selected_model):
                clean_params["flux_variant"] = "krea_dev"
                clean_params.setdefault("clip_type", "flux")
                if job.loader == "gguf":
                    clean_params.setdefault("gguf_clip_type", "flux")
                    clean_params.setdefault("gguf_clip_mode", "dual")
                for foreign_key in ("qwen_text_encoder", "qwen3_text_encoder", "qwen_mmproj", "mmproj", "mmproj_name"):
                    clean_params.pop(foreign_key, None)
                clean_params["_neo_flux1_krea_runtime_reconciled"] = True
            else:
                model_text = str(selected_model or "").strip().lower()
                submitted_variant = clean_params.get("flux_variant") or clean_params.get("variant") or ""
                if model_text and model_text not in {"provider_default", "automatic", "auto", "none"} and is_flux1_krea_variant(submitted_variant):
                    clean_params["flux_variant"] = "dev"
                    clean_params["_neo_flux1_krea_stale_variant_cleared"] = True
        if job.family in {"krea2", "krea2_turbo"}:
            clean_params = dict(clean_params)
            selected_model = job.model or clean_params.get("gguf_unet") or clean_params.get("gguf_model") or clean_params.get("diffusion_model") or clean_params.get("model") or clean_params.get("unet") or ""
            variant = resolve_krea2_variant(job.family, selected_model)
            clean_params["krea2_variant"] = variant
            clean_params["clip_type"] = "krea2"
            # One canonical Krea2 encoder lane. M16 intentionally keeps the
            # Qwen3-VL-4B encoder native even when the transformer is GGUF.
            encoder = (
                clean_params.get("qwen3vl_text_encoder")
                or clean_params.get("text_encoder_1")
                or clean_params.get("text_encoder_primary")
                or clean_params.get("clip_name")
                or ""
            )
            if encoder:
                clean_params["qwen3vl_text_encoder"] = encoder
                clean_params["text_encoder_1"] = encoder
                clean_params["text_encoder_primary"] = encoder
            clean_params["text_encoder_2"] = ""
            clean_params.pop("gguf_text_encoder_1", None)
            clean_params.pop("gguf_text_encoder_primary", None)
            clean_params.pop("gguf_text_encoder_2", None)
            clean_params.pop("gguf_text_encoder_secondary", None)
            clean_params.pop("flux_variant", None)
            clean_params.pop("flux_guidance", None)
            clean_params.pop("qwen_text_encoder", None)
            clean_params.pop("qwen3_text_encoder", None)
            clean_params.pop("qwen_mmproj", None)
            clean_params.pop("mmproj", None)
            clean_params.pop("mmproj_name", None)
            if job.loader == "gguf":
                clean_params["gguf_clip_mode"] = "single"
                clean_params["gguf_clip_type"] = "krea2"
            clean_params["_neo_krea2_runtime_reconciled"] = True
        if job.family in {"z_image", "z_image_turbo"}:
            clean_params = dict(clean_params)
            clean_params["gguf_clip_type"] = "z_image"
            clean_params["gguf_clip_mode"] = "single"
            clean_params["clip_type"] = "lumina2"
            clean_params.pop("qwen_text_encoder", None)
            clean_params.pop("qwen_mmproj", None)
        if job.family == "anima":
            clean_params = dict(clean_params)
            clean_params["clip_type"] = "stable_diffusion"
            encoder = clean_params.get("anima_text_encoder") or clean_params.get("qwen3_06b_text_encoder") or clean_params.get("text_encoder_1") or clean_params.get("clip_name") or ""
            vae = clean_params.get("anima_vae") or clean_params.get("qwen_image_vae") or clean_params.get("vae") or ""
            if encoder:
                clean_params["anima_text_encoder"] = encoder
                clean_params["text_encoder_1"] = encoder
            if vae:
                clean_params["anima_vae"] = vae
                clean_params["vae"] = vae
            if job.loader == "gguf":
                clean_params["gguf_clip_mode"] = "single"
                clean_params["gguf_clip_type"] = "anima"
            clean_params["_neo_anima_runtime_reconciled"] = True
        if job.family == "ideogram4":
            clean_params = dict(clean_params)
            clean_params["clip_type"] = "ideogram4"
            encoder = clean_params.get("ideogram4_text_encoder") or clean_params.get("qwen3_vl_text_encoder") or clean_params.get("text_encoder_1") or clean_params.get("clip_name") or ""
            vae = clean_params.get("ideogram4_vae") or clean_params.get("flux2_vae") or clean_params.get("vae") or ""
            if encoder:
                clean_params["ideogram4_text_encoder"] = encoder
                clean_params["text_encoder_1"] = encoder
            if vae:
                clean_params["ideogram4_vae"] = vae
                clean_params["vae"] = vae
            if job.loader == "gguf":
                clean_params["gguf_clip_mode"] = "single"
                clean_params["gguf_clip_type"] = "ideogram4"
            clean_params["_neo_ideogram4_dual_model_required"] = True
            clean_params["_neo_ideogram4_runtime_reconciled"] = True
        if runtime_mode != job.mode or clean_params != (job.params or {}):
            return job.copy(update={"mode": runtime_mode, "params": clean_params})
        return job

    def _extensions_with_legacy_controlnet_params(self, extensions: object, params: dict[str, Any] | None) -> object:
        """Mirror V1 queue-time ControlNet params into the V2 extension payload.

        V1 submitted ControlNet as top-level generation fields such as
        ``controlnet_units`` and ``controlnet_stack_enabled``.  The V2
        extension runtime consumes ``extensions.image.controlnet``.  During the
        migration both contracts may exist in the same request; the V1 fields
        must win when they describe an active unit, otherwise the workflow hook
        sees a disabled extension even though the queue payload contains a
        runnable ControlNet stack.
        """
        raw_params = params if isinstance(params, dict) else {}
        legacy_keys = {
            "controlnet_units",
            "controlnet_stack_enabled",
            "controlnet_stack_count",
            "controlnet_name",
            "controlnet_preprocessor",
            "controlnet_strength",
            "control_image_name",
        }
        if not any(key in raw_params for key in legacy_keys):
            return extensions

        units = raw_params.get("controlnet_units")
        has_unit = isinstance(units, list) and any(isinstance(unit, dict) and unit.get("enabled", True) for unit in units)
        stack_enabled = raw_params.get("controlnet_stack_enabled")
        should_enable = bool(has_unit or stack_enabled or raw_params.get("controlnet_name") or raw_params.get("control_image_name"))
        if not should_enable:
            return extensions

        try:
            from neo_extensions.built_in.controlnet.backend.payload_schema import EXTENSION_ID, normalize_block
        except Exception:  # noqa: BLE001 - keep provider compile resilient.
            return extensions

        legacy_raw = {key: deepcopy(raw_params.get(key)) for key in legacy_keys if key in raw_params}
        # V1's queue bridge uses control_image_name as the workflow-ready map
        # source.  Prefer the generated map/top-level handoff over the original
        # reference image, because Comfy LoadImage needs an image present in its
        # input tree.
        block, _notes = normalize_block(legacy_raw, route=None, enforce_route_state=False)
        if not isinstance(block, dict) or not block.get("enabled"):
            return extensions

        merged: dict[str, Any] = deepcopy(extensions) if isinstance(extensions, dict) else {}
        payloads = merged.get("payloads")
        if isinstance(payloads, dict):
            payloads[EXTENSION_ID] = block
            merged["payloads"] = payloads
            return merged
        nested = merged.get("extensions")
        if isinstance(nested, dict):
            nested[EXTENSION_ID] = block
            merged["extensions"] = nested
            return merged
        merged[EXTENSION_ID] = block
        return merged


    def _legacy_controlnet_params_active(self, params: dict[str, Any] | None) -> bool:
        """Return True when V1 queue fields describe a runnable ControlNet unit.

        This is intentionally independent from ``job.extensions`` because V1
        submitted ControlNet through top-level generation params.  During V2
        migration those fields are the source of truth if the extension state is
        stale/disabled.
        """
        raw = params if isinstance(params, dict) else {}
        units = raw.get("controlnet_units")
        if isinstance(units, list):
            for unit in units:
                if not isinstance(unit, dict) or unit.get("enabled", True) is False:
                    continue
                model = str(unit.get("model") or raw.get("controlnet_name") or "").strip()
                source = str(unit.get("generated_map") or unit.get("control_image") or raw.get("control_image_name") or "").strip()
                if model and source:
                    return True
        return bool(raw.get("controlnet_stack_enabled") and raw.get("controlnet_name") and raw.get("control_image_name"))

    def _controlnet_patch_applied(self, extension_metadata: dict[str, Any] | None) -> bool:
        meta = extension_metadata if isinstance(extension_metadata, dict) else {}
        for patch in meta.get("workflow_patches") or []:
            if isinstance(patch, dict) and patch.get("extension_id") == "image.controlnet" and patch.get("applied"):
                return True
        return False

    def _merge_extension_metadata(self, base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
        merged = deepcopy(base or {"used": [], "payloads": {}, "workflow_patches": [], "validation": [], "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}})
        other = extra or {}
        for key in ("used", "workflow_patches", "validation"):
            current = merged.get(key) if isinstance(merged.get(key), list) else []
            current.extend(other.get(key) or [])
            merged[key] = current
        for key in ("payloads", "replay_payloads", "assistant_summaries", "memory_events"):
            current = merged.get(key) if isinstance(merged.get(key), dict) else {}
            current.update(other.get(key) or {})
            merged[key] = current
        return merged

    def _enforce_explicit_lora_execution(
        self,
        *,
        extensions: object,
        route_payload: dict[str, Any],
        extension_metadata: dict[str, Any] | None,
        validation_payload: dict[str, Any],
        actual_params: dict[str, Any],
        compile_status: str,
    ) -> str:
        """Fail closed unless an explicit LoRA request has verifiable graph proof.

        This gate is workflow-engine neutral. Native generation, Img2Img, Native
        Inpaint, LanPaint, and Outpaint all use the same rule: an explicit base/global
        request either produces an applied loader patch or the job never reaches
        Comfy ``/prompt``.
        """

        metadata = extension_metadata if isinstance(extension_metadata, dict) else {}
        workflow_patches = metadata.get("workflow_patches") if isinstance(metadata.get("workflow_patches"), list) else []
        lora_patch = next((item for item in workflow_patches if isinstance(item, dict) and item.get("extension_id") == "lora_stack"), None)
        requested = lora_stack_execution_requested(extensions, route_payload)
        applied = bool(isinstance(lora_patch, dict) and lora_patch.get("applied"))
        engine = str(route_payload.get("workflow_engine") or route_payload.get("engine") or "native")

        if not requested:
            actual_params["_neo_lora_execution"] = "inactive"
            actual_params.pop("_neo_lora_blocker", None)
            actual_params.pop("_neo_lora_execution_proof", None)
            if engine == "lanpaint":
                actual_params.setdefault("_neo_lanpaint_lora_execution", "inactive")
            return compile_status

        if applied:
            proof = {
                "schema_version": "neo.image.lora_stack.execution_proof.v1",
                "execution_state": str(lora_patch.get("execution_state") or "applied"),
                "provider_id": str(route_payload.get("provider_id") or route_payload.get("backend") or self.manifest.provider_id),
                "family": str(route_payload.get("family") or ""),
                "loader": str(route_payload.get("loader") or ""),
                "workflow_mode": str(route_payload.get("workflow_mode") or route_payload.get("mode") or "generate"),
                "workflow_engine": engine,
                "loader_node_class": str(lora_patch.get("node_class") or lora_patch.get("node") or ""),
                "loader_node_ids": list(lora_patch.get("node_ids") or []),
                "portable_lora_names": list(lora_patch.get("portable_lora_names") or lora_patch.get("lora_names") or []),
                "submitted_lora_names": list(lora_patch.get("submitted_lora_names") or []),
                "catalog_bindings": deepcopy(lora_patch.get("catalog_bindings") or []),
                "provider_catalog_verified": bool(lora_patch.get("provider_catalog_verified")),
                "previous_model_ref": deepcopy(lora_patch.get("previous_model_ref") or []),
                "patched_model_ref": deepcopy(lora_patch.get("patched_model_ref") or []),
                "previous_clip_ref": deepcopy(lora_patch.get("previous_clip_ref") or []),
                "patched_clip_ref": deepcopy(lora_patch.get("patched_clip_ref") or []),
                "patched_model_consumer_nodes": list(lora_patch.get("patched_model_consumer_nodes") or []),
                "patched_clip_encode_nodes": list(lora_patch.get("patched_clip_encode_nodes") or []),
                "route": deepcopy(lora_patch.get("route") or route_payload),
            }
            actual_params["_neo_lora_execution"] = "applied"
            actual_params["_neo_lora_execution_proof"] = proof
            actual_params.pop("_neo_lora_blocker", None)
            if engine == "lanpaint":
                actual_params["_neo_lanpaint_lora_execution"] = "applied"
                actual_params.pop("_neo_lanpaint_lora_blocker", None)
            return compile_status

        execution_state = str((lora_patch or {}).get("execution_state") or "")
        if execution_state in {"", "inactive", "inactive_deferred_only"}:
            execution_state = "blocked_unapplied_explicit_request"
        reason = str((lora_patch or {}).get("reason") or "An explicitly enabled LoRA Stack could not be applied to the selected family route.")
        errors = list(validation_payload.get("errors") or [])
        if reason not in errors:
            errors.append(reason)
        validation_payload["errors"] = errors
        validation_payload["ok"] = False
        actual_params["_neo_lora_execution"] = execution_state
        actual_params["_neo_lora_blocker"] = reason
        actual_params["_neo_lora_execution_proof"] = {
            "schema_version": "neo.image.lora_stack.execution_proof.v1",
            "execution_state": execution_state,
            "provider_id": str(route_payload.get("provider_id") or route_payload.get("backend") or self.manifest.provider_id),
            "family": str(route_payload.get("family") or ""),
            "loader": str(route_payload.get("loader") or ""),
            "workflow_mode": str(route_payload.get("workflow_mode") or route_payload.get("mode") or "generate"),
            "workflow_engine": engine,
            "loader_node_class": str((lora_patch or {}).get("node_class") or (lora_patch or {}).get("node") or ""),
            "portable_lora_names": list((lora_patch or {}).get("portable_lora_names") or []),
            "submitted_lora_names": list((lora_patch or {}).get("submitted_lora_names") or []),
            "catalog_bindings": deepcopy((lora_patch or {}).get("catalog_bindings") or []),
            "provider_catalog_verified": False,
            "reason": reason,
            "route": deepcopy((lora_patch or {}).get("route") or route_payload),
        }
        if engine == "lanpaint":
            actual_params["_neo_lanpaint_lora_execution"] = "blocked_before_queue"
            actual_params["_neo_lanpaint_lora_blocker"] = reason
        return "mock_compiled"

    def _object_info_for_extensions(self, extensions: object, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return live Comfy node schemas after staging selected ADetailer assets.

        The call is intentionally lazy so normal generation routes do not pay an
        object_info request just because the provider compiled a base workflow.
        ADetailer staging runs first so Impact Pack's dynamic model choices see
        newly bridged files before Neo validates the final provider input.
        """
        if not has_comfy_workflow_extension_requests(extensions):
            return {}
        try:
            block = None
            if isinstance(extensions, dict):
                payloads = extensions.get("payloads") if isinstance(extensions.get("payloads"), dict) else {}
                nested = extensions.get("extensions") if isinstance(extensions.get("extensions"), dict) else {}
                block = extensions.get("image.adetailer") or payloads.get("image.adetailer") or nested.get("image.adetailer")
            if isinstance(block, dict) and block.get("enabled"):
                from neo_extensions.built_in.adetailer.backend.model_catalog import prepare_detailer_assets_for_execution

                params = block.get("params") if isinstance(block.get("params"), dict) else block.get("inputs")
                prepare_detailer_assets_for_execution(params or {}, route=route)
        except Exception:  # noqa: BLE001 - workflow validation reports bridge failures with safe diagnostics.
            pass
        try:
            info = self._get_json("/object_info")
        except Exception:  # noqa: BLE001 - extension validation converts this to provider_gated.
            return {}
        return info if isinstance(info, dict) else {}


    def _comfy_node_names_for_latent_capture(self) -> set[str]:
        """Return Comfy node names for the provider-owned latent save hook.

        This is intentionally separate from extension node discovery. Latent
        capture is a provider/runtime feature, so it may make a lazy object_info
        request only when the user requested capture.
        """
        try:
            info = self._get_json("/object_info")
        except Exception:  # noqa: BLE001 - compile should gate cleanly when offline.
            return set()
        return {str(key) for key in info.keys()} if isinstance(info, dict) else set()


    @staticmethod
    def _find_primary_ksampler_node_id(workflow: dict[str, Any]) -> str | None:
        """Return the sampler node extension patches should mutate.

        Family compilers do not all use checkpoint node id ``5``. Prefer the
        highest-numbered KSampler with positive/negative conditioning inputs,
        which matches the final active sampler in Flux/Qwen provider graphs.
        """
        candidates: list[tuple[int, str]] = []
        for raw_id, node in (workflow or {}).items():
            if not isinstance(node, dict) or str(node.get("class_type") or "") != "KSampler":
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if "positive" not in inputs or "negative" not in inputs:
                continue
            try:
                score = int(str(raw_id))
            except Exception:
                score = len(candidates)
            candidates.append((score, str(raw_id)))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    @staticmethod
    def _extension_block_from_job_extensions(extensions: object, extension_id: str) -> dict[str, Any]:
        if not isinstance(extensions, dict):
            return {}
        if isinstance(extensions.get(extension_id), dict):
            return deepcopy(extensions.get(extension_id) or {})
        payloads = extensions.get("payloads")
        if isinstance(payloads, dict) and isinstance(payloads.get(extension_id), dict):
            return deepcopy(payloads.get(extension_id) or {})
        nested = extensions.get("extensions")
        if isinstance(nested, dict) and isinstance(nested.get(extension_id), dict):
            return deepcopy(nested.get(extension_id) or {})
        return {}

    @classmethod
    def _non_checkpoint_patch_extensions(
        cls,
        extensions: object,
        allowed_extension_ids: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return only extension blocks approved for non-checkpoint graph patching.

        Flux/Qwen provider-owned graphs can now accept LoRA Stack and ControlNet
        conditioning patches. Keep other checkpoint-era mutators out of this
        path until each extension declares its own family route support.
        """
        if not isinstance(extensions, dict):
            return {}
        payloads: dict[str, Any] = {}
        for extension_id in allowed_extension_ids:
            block = cls._extension_block_from_job_extensions(extensions, extension_id)
            if block:
                payloads[extension_id] = block
        return {"payloads": payloads} if payloads else {}

    @staticmethod
    def _non_checkpoint_allowed_extension_ids(route_payload: dict[str, Any]) -> tuple[str, ...]:
        """Resolve provider-owned extension patches from exact route support.

        LoRA Stack and ControlNet remain established non-checkpoint owners.
        Scene Director is admitted only from its canonical generation/reference
        support matrix, while High-Res Lab and ADetailer keep their Finish-owned
        route checks. This prevents provider-side family allowlists from silently
        stripping a supported modern regional workflow. Unsupported routes never
        receive a submitted extension block by accident.
        """
        normalized_route = {
            **dict(route_payload or {}),
            "workspace": "image",
            "workspace_app": "finish",
            "subtab": "finish",
        }
        allowed = ["lora_stack", "image.controlnet"]
        scene_mode = str(route_payload.get("workflow_mode") or route_payload.get("mode") or "txt2img").strip().lower()
        scene_workspace_app = "reference" if scene_mode in {"img2img", "edit", "inpaint", "outpaint"} else "generations"
        scene_route = {
            **dict(route_payload or {}),
            "workspace": "image",
            "workspace_app": scene_workspace_app,
            "subtab": scene_workspace_app,
        }
        scene_support = get_scene_director_support(scene_route, require_node=False)
        if bool(scene_support.get("workflow_patch_allowed")):
            allowed.append("image.scene_director")
        if high_res_lab_route_active(normalized_route):
            allowed.append("image.high_res_lab")
        support = adetailer_support_for_route(normalized_route)
        if bool(support.get("workflow_patch_allowed")):
            allowed.append("image.adetailer")
        return tuple(allowed)

    @classmethod
    def _enforce_scene_director_execution(
        cls,
        *,
        extensions: object,
        route_payload: dict[str, Any],
        workflow: dict[str, Any],
        extension_metadata: dict[str, Any] | None,
        validation_payload: dict[str, Any],
        actual_params: dict[str, Any],
        compile_status: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Fail closed when Scene Director was explicitly submitted but did not mutate the graph.

        IMG-SD1 separates submit intent from execution truth.  The browser owns
        the editor state, but only the provider can prove that the final Comfy
        workflow contains the regional conditioning/model-delta patch.  Modern
        routes must never silently fall back to a plain generation graph or a
        global LoRA loader.
        """
        block = cls._extension_block_from_job_extensions(extensions, "image.scene_director")
        requested = bool(isinstance(block, dict) and block.get("enabled") is True)
        support = get_scene_director_support(route_payload, require_node=False)
        route_active = bool(support.get("workflow_patch_allowed"))
        executable_request = bool(requested and route_active)
        metadata = extension_metadata if isinstance(extension_metadata, dict) else {}
        workflow_patches = metadata.get("workflow_patches") if isinstance(metadata.get("workflow_patches"), list) else []
        scene_patch = next(
            (
                item for item in workflow_patches
                if isinstance(item, dict) and str(item.get("extension_id") or "") == "image.scene_director"
            ),
            None,
        )
        patch_applied = bool(isinstance(scene_patch, dict) and scene_patch.get("applied"))
        patch_for_verification = scene_patch if isinstance(scene_patch, dict) else {}
        runtime_for_verification = (
            patch_for_verification.get("scene_director_lightweight_runtime_proof")
            if isinstance(patch_for_verification.get("scene_director_lightweight_runtime_proof"), dict)
            else {}
        )
        regional_lora_route_count = int(runtime_for_verification.get("regional_lora_route_count") or 0)
        regional_lora_nodes_added = int(runtime_for_verification.get("regional_lora_nodes_added") or 0)
        regional_lora_compile_status = str(runtime_for_verification.get("regional_lora_compile_status") or "")
        verification_nodes_added = [str(item) for item in (patch_for_verification.get("nodes_added") or [])]
        verification_node_classes = [
            str((workflow.get(node_id) or {}).get("class_type") or "")
            for node_id in verification_nodes_added
            if isinstance(workflow.get(node_id), dict)
        ]
        runtime_engine = str(runtime_for_verification.get("engine") or patch_for_verification.get("scene_director_engine") or "")
        krea2_external_runtime = runtime_engine == "krea2_regional_external"
        krea2_external_graph_verified = bool(
            verification_node_classes.count("Krea2RegionalBuilder") == 1
            and verification_node_classes.count("Krea2ApplyRegional") == 1
        ) if krea2_external_runtime else False
        submit_authority = actual_params.get("_neo_scene_director_submit_authority") if isinstance(actual_params.get("_neo_scene_director_submit_authority"), dict) else {}
        expected_regional_lora_row_ids = [
            str(item) for item in (submit_authority.get("regional_lora_row_ids") or []) if str(item or "").strip()
        ]
        expected_regional_lora_count = len(expected_regional_lora_row_ids)
        regional_lora_request_preserved = bool(
            expected_regional_lora_count <= 0
            or regional_lora_route_count == expected_regional_lora_count
        )
        regional_lora_verified = bool(
            regional_lora_request_preserved
            and (
                regional_lora_route_count <= 0
                or (
                    krea2_external_runtime
                    and krea2_external_graph_verified
                    and regional_lora_compile_status == "external_runtime_armed"
                )
                or (
                    not krea2_external_runtime
                    and regional_lora_nodes_added == 1
                    and regional_lora_compile_status == "armed_not_gpu_proven"
                )
            )
        )
        applied = bool(
            patch_applied
            and runtime_for_verification.get("contract_ok", True) is not False
            and regional_lora_verified
        )

        submit_state = actual_params.get("_neo_extension_state") if isinstance(actual_params.get("_neo_extension_state"), dict) else None
        if isinstance(submit_state, dict):
            submit_extensions = submit_state.get("extensions") if isinstance(submit_state.get("extensions"), dict) else None
            scene_state = submit_extensions.get("image.scene_director") if isinstance(submit_extensions, dict) and isinstance(submit_extensions.get("image.scene_director"), dict) else None
            if isinstance(scene_state, dict):
                scene_state["workflow_requested"] = requested
                scene_state["workflow_applied"] = applied
                scene_state["workflow_status"] = "applied" if applied else ("blocked" if executable_request else "inactive")

        if not executable_request:
            actual_params["_neo_scene_director_execution"] = "inactive"
            actual_params.pop("_neo_scene_director_execution_proof", None)
            actual_params.pop("_neo_scene_director_blocker", None)
            return compile_status, None

        patch = scene_patch if isinstance(scene_patch, dict) else {}
        nodes_added = [str(item) for item in (patch.get("nodes_added") or patch.get("node_ids") or [])]
        runtime_proof = patch.get("scene_director_lightweight_runtime_proof") if isinstance(patch.get("scene_director_lightweight_runtime_proof"), dict) else {}
        lora_contract = patch.get("scene_director_regional_lora_contract") if isinstance(patch.get("scene_director_regional_lora_contract"), dict) else {}
        lora_routes = lora_contract.get("routes") if isinstance(lora_contract.get("routes"), list) else []
        conditioning_mask_nodes = [
            node_id for node_id in nodes_added
            if isinstance(workflow.get(node_id), dict)
            and str((workflow.get(node_id) or {}).get("class_type") or "") == "ConditioningSetMask"
        ]
        combine_nodes = [
            node_id for node_id in nodes_added
            if isinstance(workflow.get(node_id), dict)
            and str((workflow.get(node_id) or {}).get("class_type") or "") == "ConditioningCombine"
        ]
        regional_lora_nodes = [
            node_id for node_id in nodes_added
            if isinstance(workflow.get(node_id), dict)
            and str((workflow.get(node_id) or {}).get("class_type") or "") == "NeoRegionalLoRADelta"
        ]
        krea2_regional_builder_nodes = [
            node_id for node_id in nodes_added
            if isinstance(workflow.get(node_id), dict)
            and str((workflow.get(node_id) or {}).get("class_type") or "") == "Krea2RegionalBuilder"
        ]
        krea2_regional_apply_nodes = [
            node_id for node_id in nodes_added
            if isinstance(workflow.get(node_id), dict)
            and str((workflow.get(node_id) or {}).get("class_type") or "") == "Krea2ApplyRegional"
        ]
        proof = {
            "schema_version": "neo.image.scene_director.execution_proof.img_sd3.v5",
            "execution_state": "applied" if applied else "blocked_unapplied_explicit_request",
            "requested": True,
            "applied": applied,
            "provider_id": str(route_payload.get("provider_id") or route_payload.get("backend") or "comfyui"),
            "route": deepcopy(route_payload),
            "engine": str(patch.get("scene_director_engine") or (patch.get("scene_director_execution_strategy") or {}).get("engine") or support.get("execution_engine") or ""),
            "patch_type": str(patch.get("patch_type") or ""),
            "sampler_node_id": str(patch.get("sampler_node_id") or ""),
            "region_count": int(patch.get("regions") or 0),
            "nodes_added": nodes_added,
            "conditioning_set_mask_node_ids": conditioning_mask_nodes,
            "conditioning_combine_node_ids": combine_nodes,
            "regional_lora_node_ids": regional_lora_nodes,
            "krea2_regional_builder_node_ids": krea2_regional_builder_nodes,
            "krea2_regional_apply_node_ids": krea2_regional_apply_nodes,
            "krea2_regional_external_graph_verified": bool(krea2_external_graph_verified),
            "external_runtime_repo": str(runtime_proof.get("external_runtime_repo") or lora_contract.get("external_runtime_repo") or ""),
            "adaptive_masks": runtime_proof.get("adaptive_masks"),
            "exclusive_masks": runtime_proof.get("exclusive_masks"),
            "restrict_img_attn": runtime_proof.get("restrict_img_attn"),
            "layout_in_base": runtime_proof.get("layout_in_base"),
            "region_lock_strength": runtime_proof.get("region_lock_strength"),
            "regional_prompt_lane_count": int(runtime_proof.get("regional_prompt_lane_count") or 0),
            "subject_authority_applied": bool(runtime_proof.get("subject_authority_applied")),
            "subject_authority_merge_mode": str(runtime_proof.get("subject_authority_merge_mode") or "inactive"),
            "subject_authority_bridge_ref": deepcopy(runtime_proof.get("subject_authority_bridge_ref")),
            "subject_authority_node_ids": [str(item) for item in (runtime_proof.get("subject_authority_node_ids") or [])],
            "subject_authority_source_text_node_id": str(runtime_proof.get("subject_authority_source_text_node_id") or ""),
            "subject_authority_negative_source_text_node_id": str(runtime_proof.get("subject_authority_negative_source_text_node_id") or ""),
            "subject_authority_merge": deepcopy(runtime_proof.get("subject_authority_merge") or {}),
            "subject_authority_negative_merge": deepcopy(runtime_proof.get("subject_authority_negative_merge") or {}),
            "subject_authority": deepcopy(runtime_proof.get("subject_authority") or {}),
            "conditioning_rewire_location": str(runtime_proof.get("conditioning_rewire_location") or ""),
            "sampler_conditioning_rewired": bool(runtime_proof.get("sampler_conditioning_rewired")),
            "conditioning_wrapper_rewired": bool(runtime_proof.get("conditioning_wrapper_rewired")),
            "inpaint_conditioning_wrapper_node_id": str(runtime_proof.get("inpaint_conditioning_wrapper_node_id") or ""),
            "inpaint_conditioning_wrapper_preserved": runtime_proof.get("inpaint_conditioning_wrapper_preserved"),
            "inpaint_conditioning_anchor_positive_ref": deepcopy(runtime_proof.get("inpaint_conditioning_anchor_positive_ref")),
            "inpaint_conditioning_anchor_negative_ref": deepcopy(runtime_proof.get("inpaint_conditioning_anchor_negative_ref")),
            "prompt_conflict_count": int(runtime_proof.get("prompt_conflict_count") or 0),
            "prompt_conflicts": deepcopy((runtime_proof.get("subject_authority") or {}).get("directional_conflicts") or []),
            "regional_lora_route_count": int(runtime_proof.get("regional_lora_route_count") or lora_contract.get("route_count") or 0),
            "regional_lora_expected_row_ids": expected_regional_lora_row_ids,
            "regional_lora_expected_count": expected_regional_lora_count,
            "regional_lora_request_preserved": regional_lora_request_preserved,
            "regional_lora_compile_status": str(runtime_proof.get("regional_lora_compile_status") or ""),
            "regional_lora_graph_verified": regional_lora_verified,
            "modern_scene_director_core": deepcopy(runtime_proof.get("modern_scene_director_core") or patch.get("scene_director_modern_core") or {}),
            "regional_lora_isolation_goal": str(lora_contract.get("isolation_goal") or ""),
            "regional_lora_isolation_profile": str(lora_contract.get("isolation_profile") or ""),
            "regional_lora_overlap_diagnostics": deepcopy(lora_contract.get("isolation_overlap_diagnostics") or {}),
            "regional_lora_hard_isolation_claimed": bool(lora_contract.get("hard_region_isolation_claimed")),
            "regional_lora_clip_delta_execution": str(lora_contract.get("clip_delta_execution") or ""),
            "regional_lora_bindings": [
                {
                    "row_id": str(item.get("row_id") or item.get("lora_row_id") or ""),
                    "region_id": str(item.get("region_id") or ""),
                    "lora_name": str(item.get("lora_name") or item.get("name") or ""),
                    "strength": item.get("strength"),
                }
                for item in lora_routes if isinstance(item, dict)
            ],
            "single_sampler_preserved": runtime_proof.get("single_sampler_preserved"),
            "contract_ok": runtime_proof.get("contract_ok", True if applied else False),
            "fallback_policy": str(patch.get("fallback_policy") or "never_fallback_to_classic_v054_or_global_lora"),
            "reason": str(patch.get("reason") or ""),
        }
        actual_params["_neo_scene_director_execution_proof"] = proof

        if applied:
            actual_params["_neo_scene_director_execution"] = "applied"
            actual_params.pop("_neo_scene_director_blocker", None)
            return compile_status, proof

        patch_reason = str(patch.get("reason") or "")
        route_family = str(route_payload.get("family") or route_payload.get("model_family") or "").strip().lower().replace("-", "_")
        krea_dependency_missing = bool(
            route_family in {"krea2", "krea2_turbo", "krea_2", "krea_2_turbo"}
            and (
                regional_lora_compile_status == "missing_external_runtime"
                or "ComfyUI-Krea2-Regional" in patch_reason
                or "Krea2RegionalBuilder" in patch_reason
                or "Krea2ApplyRegional" in patch_reason
            )
        )
        reason = str(
            (
                "Krea 2 Scene Director regional LoRA isolation requires januspluto/ComfyUI-Krea2-Regional "
                "(Krea2RegionalBuilder + Krea2ApplyRegional). Install/update that custom-node pack, restart ComfyUI, "
                "then reconnect Neo. Neo will not fall back to NeoRegionalLoRADelta or global LoRA loading."
                if krea_dependency_missing
                else (
                    (
                        f"Scene Director requested {expected_regional_lora_count} regional LoRA row(s), but only "
                        f"{regional_lora_route_count} survived compatibility/routing validation. Neo blocked the job "
                        "instead of silently dropping a region-assigned LoRA."
                        if expected_regional_lora_count > 0 and not regional_lora_request_preserved
                        else (
                            f"Scene Director regional LoRA routing was requested for {regional_lora_route_count} row(s), "
                            f"but the regional runtime was not armed ({regional_lora_compile_status or 'unknown'}). "
                            + (
                                "Sync Neo's bundled neo_scene_director custom node into ComfyUI/custom_nodes and restart ComfyUI."
                                if regional_lora_compile_status == "missing_runtime_node"
                                else ""
                            )
                        ).strip()
                    )
                    if (expected_regional_lora_count > 0 or regional_lora_route_count > 0) and not regional_lora_verified
                    else ""
                )
            )
            or patch_reason
            or "Scene Director was enabled on an executable route but the provider could not attach a verified regional workflow patch before queueing."
        )
        errors = list(validation_payload.get("errors") or [])
        if reason not in errors:
            errors.append(reason)
        validation_payload["errors"] = errors
        validation_payload["ok"] = False
        actual_params["_neo_scene_director_execution"] = "blocked_before_queue"
        actual_params["_neo_scene_director_blocker"] = reason
        proof["execution_state"] = "blocked_before_queue"
        proof["reason"] = reason
        return "mock_compiled", proof

    @classmethod
    def _enforce_high_res_execution(
        cls,
        *,
        extensions: object,
        route_payload: dict[str, Any],
        workflow: dict[str, Any],
        extension_metadata: dict[str, Any] | None,
        validation_payload: dict[str, Any],
        actual_params: dict[str, Any],
        compile_status: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Fail closed when an active High-Res request has no graph mutation proof.

        The frontend submit-state snapshot represents user intent, not provider
        execution.  This provider-owned proof is created only after the final
        extension graph has been patched, so Neo cannot claim High-Res Lab ran
        merely because its toggle was enabled.
        """

        block = cls._extension_block_from_job_extensions(extensions, "image.high_res_lab")
        requested = bool(isinstance(block, dict) and block.get("enabled") is True)
        route_active = bool(high_res_lab_route_active(route_payload))
        executable_request = bool(requested and route_active)
        metadata = extension_metadata if isinstance(extension_metadata, dict) else {}
        workflow_patches = metadata.get("workflow_patches") if isinstance(metadata.get("workflow_patches"), list) else []
        high_res_patch = next(
            (
                item
                for item in workflow_patches
                if isinstance(item, dict) and str(item.get("extension_id") or "") == "image.high_res_lab"
            ),
            None,
        )
        applied = bool(isinstance(high_res_patch, dict) and high_res_patch.get("applied"))

        submit_state = actual_params.get("_neo_extension_state") if isinstance(actual_params.get("_neo_extension_state"), dict) else None
        if isinstance(submit_state, dict):
            submit_extensions = submit_state.get("extensions") if isinstance(submit_state.get("extensions"), dict) else None
            high_res_state = submit_extensions.get("image.high_res_lab") if isinstance(submit_extensions, dict) and isinstance(submit_extensions.get("image.high_res_lab"), dict) else None
            if isinstance(high_res_state, dict):
                high_res_state["workflow_requested"] = requested
                high_res_state["workflow_applied"] = applied
                high_res_state["workflow_status"] = "applied" if applied else ("blocked" if executable_request else "inactive")

        if not executable_request:
            actual_params["_neo_high_res_execution"] = "inactive"
            actual_params.pop("_neo_high_res_execution_proof", None)
            actual_params.pop("_neo_high_res_blocker", None)
            return compile_status, None

        patch = high_res_patch if isinstance(high_res_patch, dict) else {}
        node_ids = [str(item) for item in (patch.get("node_ids") or [])]

        def nodes_of_class(*class_names: str) -> list[str]:
            wanted = set(class_names)
            return [
                node_id
                for node_id in node_ids
                if isinstance(workflow.get(node_id), dict)
                and str((workflow.get(node_id) or {}).get("class_type") or "") in wanted
            ]

        profile = patch.get("route_profile") if isinstance(patch.get("route_profile"), dict) else {}
        refine_samplers = nodes_of_class("KSampler")
        upscale_nodes = nodes_of_class("ImageUpscaleWithModel", "ImageScaleBy", "ImageScale", "LatentUpscale", "UltimateSDUpscale")
        encode_nodes = nodes_of_class("VAEEncode")
        decode_nodes = nodes_of_class("VAEDecode", "VAEDecodeTiled", "VAEUtils_VAEDecodeTiled")
        proof = {
            "schema_version": "neo.image.high_res_lab.execution_proof.v1",
            "execution_state": "applied" if applied else "blocked_unapplied_explicit_request",
            "requested": True,
            "applied": applied,
            "provider_id": str(route_payload.get("provider_id") or route_payload.get("backend") or "comfyui"),
            "route": deepcopy(route_payload),
            "profile_id": str(patch.get("profile_id") or profile.get("profile_id") or ""),
            "base_sampler_node_id": str(patch.get("sampler_node_id") or ""),
            "upscale_node_ids": upscale_nodes,
            "vae_encode_node_ids": encode_nodes,
            "refine_sampler_node_id": refine_samplers[-1] if refine_samplers else "",
            "final_decode_node_id": decode_nodes[-1] if decode_nodes else "",
            "node_ids": node_ids,
            "source_width": actual_params.get("width"),
            "source_height": actual_params.get("height"),
            "target_width": patch.get("target_width"),
            "target_height": patch.get("target_height"),
            "target_size_source": patch.get("target_size_source"),
            "patched_output_ref": deepcopy(patch.get("patched_output_ref") or []),
            "reason": str(patch.get("reason") or ""),
        }
        actual_params["_neo_high_res_execution_proof"] = proof

        if applied:
            actual_params["_neo_high_res_execution"] = "applied"
            actual_params.pop("_neo_high_res_blocker", None)
            return compile_status, proof

        reason = str(
            patch.get("reason")
            or "High-Res Lab was enabled on an active route but the provider could not attach a verified High-Res graph before queueing."
        )
        errors = list(validation_payload.get("errors") or [])
        if reason not in errors:
            errors.append(reason)
        validation_payload["errors"] = errors
        validation_payload["ok"] = False
        actual_params["_neo_high_res_execution"] = "blocked_before_queue"
        actual_params["_neo_high_res_blocker"] = reason
        proof["execution_state"] = "blocked_before_queue"
        proof["reason"] = reason
        return "mock_compiled", proof

    @staticmethod
    def _find_final_latent_source(workflow: dict[str, Any]) -> list[Any] | None:
        """Find the latent output Neo should preserve for final-latent capture.

        Prefer the latent feeding the final VAE decode because extension passes
        such as High-Res Lab can replace the original sampler output. Fall back
        to the highest-numbered KSampler output when no VAEDecode samples input
        exists.
        """
        candidates: list[tuple[int, list[Any]]] = []
        for raw_id, node in (workflow or {}).items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or "")
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if class_type in {"VAEDecode", "VAEDecodeTiled"}:
                samples = inputs.get("samples")
                if isinstance(samples, list) and len(samples) >= 2:
                    try:
                        score = int(str(raw_id))
                    except Exception:
                        score = 0
                    candidates.append((score, [str(samples[0]), int(samples[1])]))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]
        sampler_candidates: list[tuple[int, str]] = []
        for raw_id, node in (workflow or {}).items():
            if isinstance(node, dict) and str(node.get("class_type") or "") == "KSampler":
                try:
                    score = int(str(raw_id))
                except Exception:
                    score = 0
                sampler_candidates.append((score, str(raw_id)))
        if sampler_candidates:
            sampler_candidates.sort(reverse=True)
            return [sampler_candidates[0][1], 0]
        return None


    @staticmethod
    def _find_before_high_res_latent_source(workflow: dict[str, Any], actual_params: dict[str, Any]) -> list[Any] | None:
        """Find the base latent immediately before High-Res Lab starts.

        High-Res Lab is applied as a second-pass extension after the base sampler.
        Its workflow patch metadata records the sampler node it consumed.  Saving
        that sampler output gives Neo a truthful pre-High-Res restore point.
        """
        patches = actual_params.get("extension_workflow_patches")
        if not isinstance(patches, list):
            return None
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            if str(patch.get("extension_id") or "") != "image.high_res_lab" or not patch.get("applied"):
                continue
            sampler_id = str(patch.get("sampler_node_id") or "5").strip() or "5"
            sampler = workflow.get(sampler_id) if isinstance(workflow, dict) else None
            if isinstance(sampler, dict) and str(sampler.get("class_type") or "") == "KSampler":
                return [sampler_id, 0]
        return None


    @staticmethod
    def _find_after_high_res_latent_source(workflow: dict[str, Any], actual_params: dict[str, Any]) -> list[Any] | None:
        """Find the refined latent immediately after High-Res Lab completes.

        High-Res Lab records the nodes it added.  The restore point after the
        High-Res phase is the latent output of the High-Res refine KSampler,
        before any downstream decode or finishing extension consumes it.
        """
        patches = actual_params.get("extension_workflow_patches")
        if not isinstance(patches, list):
            return None
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            if str(patch.get("extension_id") or "") != "image.high_res_lab" or not patch.get("applied"):
                continue
            node_ids = patch.get("node_ids") if isinstance(patch.get("node_ids"), list) else []
            candidates: list[tuple[int, str]] = []
            for raw_id in node_ids:
                node_id = str(raw_id)
                node = workflow.get(node_id) if isinstance(workflow, dict) else None
                if isinstance(node, dict) and str(node.get("class_type") or "") == "KSampler":
                    try:
                        score = int(node_id)
                    except Exception:
                        score = len(candidates)
                    candidates.append((score, node_id))
            if candidates:
                candidates.sort(reverse=True)
                return [candidates[0][1], 0]
        return None



    @staticmethod
    def _find_before_adetailer_latent_source(workflow: dict[str, Any], actual_params: dict[str, Any]) -> list[Any] | None:
        """Find the latent that produced the image consumed by ADetailer.

        ADetailer is an image-space finishing pass.  The truthful restore point
        before ADetailer is therefore the latent feeding the VAEDecode/decoded
        image that ADetailer replaced, not the final PNG and not an img2img
        fallback.  Prefer the ADetailer patch metadata because it records the
        previous image ref; fall back to the post-High-Res or final latent path.
        """
        patches = actual_params.get("extension_workflow_patches")
        if isinstance(patches, list):
            for patch in patches:
                if not isinstance(patch, dict):
                    continue
                if str(patch.get("extension_id") or "") != "image.adetailer" or not patch.get("applied"):
                    continue
                previous_image_ref = patch.get("previous_image_ref")
                if isinstance(previous_image_ref, list) and previous_image_ref:
                    image_node = workflow.get(str(previous_image_ref[0])) if isinstance(workflow, dict) else None
                    if isinstance(image_node, dict) and str(image_node.get("class_type") or "") in {"VAEDecode", "VAEDecodeTiled"}:
                        samples = (image_node.get("inputs") or {}).get("samples") if isinstance(image_node.get("inputs"), dict) else None
                        if isinstance(samples, list) and len(samples) >= 2:
                            return [str(samples[0]), int(samples[1])]
                # Multi-pass summaries also track the pre-pass image ref.
                pass_summaries = patch.get("pass_summaries") if isinstance(patch.get("pass_summaries"), list) else []
                for summary in pass_summaries:
                    if not isinstance(summary, dict):
                        continue
                    previous_image_ref = summary.get("previous_image_ref")
                    if isinstance(previous_image_ref, list) and previous_image_ref:
                        image_node = workflow.get(str(previous_image_ref[0])) if isinstance(workflow, dict) else None
                        if isinstance(image_node, dict) and str(image_node.get("class_type") or "") in {"VAEDecode", "VAEDecodeTiled"}:
                            samples = (image_node.get("inputs") or {}).get("samples") if isinstance(image_node.get("inputs"), dict) else None
                            if isinstance(samples, list) and len(samples) >= 2:
                                return [str(samples[0]), int(samples[1])]
                # ADetailer is active but metadata did not expose the decoded source.
                # Continue safely from the most recent latent stage before finishing.
                return (
                    ComfyProvider._find_after_high_res_latent_source(workflow, actual_params)
                    or ComfyProvider._find_final_latent_source(workflow)
                )
        return None

    @staticmethod
    def _replace_workflow_ref(workflow: dict[str, Any], old_ref: list[Any], new_ref: list[Any], *, skip_node_ids: set[str] | None = None) -> int:
        """Replace exact Comfy output references inside node inputs."""
        skip = {str(item) for item in (skip_node_ids or set())}
        replaced = 0
        old = [str(old_ref[0]), int(old_ref[1])] if isinstance(old_ref, list) and len(old_ref) >= 2 else old_ref
        new = [str(new_ref[0]), int(new_ref[1])] if isinstance(new_ref, list) and len(new_ref) >= 2 else new_ref
        for node_id, node in (workflow or {}).items():
            if str(node_id) in skip or not isinstance(node, dict):
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            for key, value in list(inputs.items()):
                normalized = [str(value[0]), int(value[1])] if isinstance(value, list) and len(value) >= 2 else value
                if normalized == old:
                    inputs[key] = list(new)
                    replaced += 1
        return replaced

    @staticmethod
    def _infer_restore_point_from_latent_output(latent: dict[str, Any]) -> str:
        text = " ".join(str(latent.get(key) or "") for key in ("filename", "subfolder", "name")).lower()
        for restore_point in ("before_high_res_fix", "after_high_res_fix", "before_adetailer", "final_latent"):
            if restore_point in text:
                return restore_point
        return "final_latent"

    @staticmethod
    def _next_workflow_node_id(workflow: dict[str, Any]) -> str:
        numbers: list[int] = []
        for key in (workflow or {}).keys():
            try:
                numbers.append(int(str(key)))
            except Exception:
                continue
        return str((max(numbers) if numbers else 0) + 1)

    @staticmethod
    def _latent_capture_filename_prefix(job: NeoJob, request_payload: dict[str, Any], restore_point: str) -> str:
        job_id = str(getattr(job, "job_id", "") or "neo_job").strip() or "neo_job"
        mode = str(request_payload.get("mode") or "final_latent").strip() or "final_latent"
        safe_job = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in job_id).strip("._-") or "neo_job"
        safe_restore = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in restore_point).strip("._-") or "final_latent"
        safe_mode = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in mode).strip("._-") or "latent"
        return f"NeoStudio_latent/{safe_job}/{safe_restore}_{safe_mode}"


    @staticmethod
    def _branch_restore_context(params: dict[str, Any]) -> dict[str, Any]:
        replay = params.get("_neo_replay_context") if isinstance(params.get("_neo_replay_context"), dict) else {}
        branch = replay.get("branch_restore") if isinstance(replay.get("branch_restore"), dict) else {}
        return dict(branch) if branch else {}

    @staticmethod
    def _comfy_latent_reference_from_branch(branch: dict[str, Any]) -> ComfyArtifactReference:
        """Return the provider-owned Comfy reference for a replay branch.

        ``artifact_path`` is a Neo retention copy and is never a valid fallback
        for Comfy ``LoadLatent``. Legacy flat fields and the Phase 2 nested
        ``provider_reference`` block are both accepted and normalized.
        """
        if branch.get("provider_owned") is False:
            raise ComfyArtifactPathError(
                "The selected latent checkpoint is not marked as provider-owned and cannot be resumed in ComfyUI."
            )
        provider_id = str(branch.get("provider_id") or "").strip()
        backend = str(branch.get("backend") or "").strip()
        if provider_id and provider_id not in {"comfyui", "comfyui_portable"}:
            raise ComfyArtifactPathError(
                "The selected latent checkpoint belongs to a different provider and cannot be loaded by ComfyUI."
            )
        if backend and backend != "comfyui":
            raise ComfyArtifactPathError(
                "The selected latent checkpoint belongs to a non-Comfy backend and cannot be loaded by ComfyUI."
            )

        provider_reference = branch.get("provider_reference") if isinstance(branch.get("provider_reference"), dict) else {}
        source_filename = (
            provider_reference.get("filename")
            or branch.get("source_filename")
            or branch.get("filename")
            or ""
        )
        source_subfolder = (
            provider_reference.get("subfolder")
            or branch.get("source_subfolder")
            or branch.get("subfolder")
            or ""
        )
        source_type = (
            provider_reference.get("type")
            or branch.get("source_type")
            or branch.get("type")
            or "output"
        )
        if source_filename:
            return normalize_comfy_artifact_reference(
                filename=source_filename,
                subfolder=source_subfolder,
                artifact_type=source_type,
            )

        load_name = (
            provider_reference.get("load_name")
            or branch.get("comfy_load_name")
            or branch.get("load_latent_name")
            or ""
        )
        if load_name:
            return normalize_comfy_artifact_reference(
                artifact_type=source_type,
                load_name=load_name,
            )

        if str(branch.get("artifact_path") or "").strip():
            raise ComfyArtifactPathError(
                "The selected latent checkpoint has only a Neo-owned storage copy and no Comfy provider reference. "
                "Start a clean generation or select another checkpoint."
            )
        raise ComfyArtifactPathError(
            "The selected latent checkpoint is missing its Comfy provider reference. "
            "Start a clean generation or select another checkpoint."
        )

    @staticmethod
    def _comfy_load_latent_name_from_branch(branch: dict[str, Any]) -> str:
        return ComfyProvider._comfy_latent_reference_from_branch(branch).load_name

    def _apply_comfy_latent_branch_restore_hook(self, compiled: CompiledJob, job: NeoJob, route: Any) -> CompiledJob:
        """Consume a saved provider-owned final latent as the sampler source.

        This enables truthful final-latent branching: Neo uses Comfy LoadLatent,
        never PNG/img2img fallback.  Phase checkpoints remain gated until Neo can
        save stable intermediate latents around High-Res / ADetailer phases.
        """
        backend_payload = dict(compiled.backend_payload or {})
        workflow = backend_payload.get("prompt")
        actual_params = dict(backend_payload.get("actual_params") if isinstance(backend_payload.get("actual_params"), dict) else (job.params or {}))
        branch = self._branch_restore_context(actual_params)
        if not branch:
            return compiled
        restore_point = str(branch.get("restore_point") or "").strip()
        actual_params["_neo_branch_restore"] = branch
        if restore_point == "base_generation_only":
            actual_params["_neo_branch_restore_provider_hook_state"] = "metadata_branch_no_provider_resume_required"
            actual_params["_neo_branch_restore_provider_hook_reason"] = "Base generation branch uses the saved recipe with enhancement extensions stripped; no LoadLatent or PNG/img2img fallback is used."
            backend_payload["actual_params"] = actual_params
            notes = list(backend_payload.get("phase_notes") or [])
            notes.append("IMG-R12 base-generation branch loaded as metadata recipe; provider latent resume hook intentionally skipped.")
            backend_payload["phase_notes"] = notes
            return compiled.model_copy(update={"backend_payload": backend_payload})
        if restore_point not in {"final_latent", "before_high_res_fix", "after_high_res_fix", "before_adetailer"}:
            actual_params["_neo_branch_restore_provider_hook_state"] = "provider_gated_restore_point_not_supported"
            actual_params["_neo_branch_restore_provider_hook_reason"] = "Only provider-owned latent restore points with Comfy LoadLatent artifacts are enabled; unsupported later phase checkpoints remain gated."
            backend_payload["actual_params"] = actual_params
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})
        if not isinstance(workflow, dict) or compiled.compile_status != "compiled":
            actual_params["_neo_branch_restore_provider_hook_state"] = "compile_gated_no_workflow"
            backend_payload["actual_params"] = actual_params
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})
        node_names = self._comfy_node_names_for_latent_capture()
        if "LoadLatent" not in node_names:
            actual_params["_neo_branch_restore_provider_hook_state"] = "provider_gated_missing_load_latent_node"
            actual_params["_neo_branch_restore_provider_hook_reason"] = "ComfyUI LoadLatent node was not detected from object_info."
            backend_payload["actual_params"] = actual_params
            notes = list(backend_payload.get("phase_notes") or [])
            notes.append("IMG-R11 final-latent branch gated because ComfyUI LoadLatent is unavailable/offline.")
            backend_payload["phase_notes"] = notes
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})
        try:
            latent_reference = self._comfy_latent_reference_from_branch(branch)
        except ComfyArtifactPathError as exc:
            message = str(exc)
            actual_params["_neo_branch_restore_provider_hook_state"] = "provider_gated_invalid_latent_name"
            actual_params["_neo_branch_restore_provider_hook_reason"] = message
            backend_payload["actual_params"] = actual_params
            backend_payload["latent_branch_resume_validation"] = {
                "schema_version": "neo.image.comfy_latent_replay_validation.v1",
                "ok": False,
                "state": "blocked_before_workflow_mutation",
                "reason_code": "invalid_or_missing_provider_reference",
                "message": message,
            }
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})
        latent_name = latent_reference.load_name
        sampler_ids: list[str] = []
        for raw_id, node in workflow.items():
            if isinstance(node, dict) and str(node.get("class_type") or "") == "KSampler":
                sampler_ids.append(str(raw_id))
        if not sampler_ids:
            actual_params["_neo_branch_restore_provider_hook_state"] = "provider_gated_no_sampler"
            actual_params["_neo_branch_restore_provider_hook_reason"] = "No KSampler was found to receive the LoadLatent restore point."
            backend_payload["actual_params"] = actual_params
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})
        
        restore_source_ref: list[Any] | None = None
        if restore_point == "before_high_res_fix" and "5" in sampler_ids:
            sampler_id = "5"
            restore_source_ref = [sampler_id, 0]
        elif restore_point == "after_high_res_fix":
            after_high_res_source = self._find_after_high_res_latent_source(workflow, actual_params)
            sampler_id = str(after_high_res_source[0]) if isinstance(after_high_res_source, list) and after_high_res_source else sorted(sampler_ids, key=lambda item: int(item) if item.isdigit() else 0)[-1]
            restore_source_ref = after_high_res_source if isinstance(after_high_res_source, list) else [sampler_id, 0]
        elif restore_point == "before_adetailer":
            before_adetailer_source = self._find_before_adetailer_latent_source(workflow, actual_params)
            sampler_id = str(before_adetailer_source[0]) if isinstance(before_adetailer_source, list) and before_adetailer_source else sorted(sampler_ids, key=lambda item: int(item) if item.isdigit() else 0)[-1]
            restore_source_ref = before_adetailer_source if isinstance(before_adetailer_source, list) else [sampler_id, 0]
        else:
            sampler_id = sorted(sampler_ids, key=lambda item: int(item) if item.isdigit() else 0)[-1]
            restore_source_ref = [sampler_id, 0]
        load_node_id = self._next_workflow_node_id(workflow)
        workflow[load_node_id] = {
            "class_type": "LoadLatent",
            "inputs": {"latent": latent_name},
            "_meta": {
                "title": "Neo Load Latent Restore Point",
                "restore_point": restore_point,
                "source_result_id": str(branch.get("source_result_id") or ""),
                "no_png_reencode": True,
            },
        }
        sampler_inputs = workflow.get(sampler_id, {}).get("inputs") if isinstance(workflow.get(sampler_id, {}).get("inputs"), dict) else {}
        previous_latent = sampler_inputs.get("latent_image")
        replaced_consumers = 0
        if restore_point in {"before_high_res_fix", "after_high_res_fix", "before_adetailer"}:
            # Continue from the saved phase checkpoint. Rewrite downstream
            # consumers of the checkpoint latent to LoadLatent, leaving the old
            # sampler path orphaned instead of rerunning the captured phase.
            source_ref = restore_source_ref if isinstance(restore_source_ref, list) and len(restore_source_ref) >= 2 else [sampler_id, 0]
            replaced_consumers = self._replace_workflow_ref(workflow, source_ref, [load_node_id, 0], skip_node_ids={sampler_id, load_node_id})
            if replaced_consumers <= 0:
                sampler_inputs["latent_image"] = [load_node_id, 0]
                workflow[sampler_id]["inputs"] = sampler_inputs
        else:
            sampler_inputs["latent_image"] = [load_node_id, 0]
            workflow[sampler_id]["inputs"] = sampler_inputs
        branch_provider = {
            "schema_version": "neo.image.comfy_latent_branch_resume.v1",
            "provider_id": self.manifest.provider_id,
            "backend": "comfyui",
            "restore_point": restore_point,
            "source_result_id": str(branch.get("source_result_id") or ""),
            "artifact_id": str(branch.get("artifact_id") or ""),
            "artifact_format": str(branch.get("artifact_format") or "comfy_latent"),
            "load_latent_node_id": load_node_id,
            "load_latent_name": latent_name,
            "provider_reference": {
                "filename": latent_reference.filename,
                "subfolder": latent_reference.subfolder,
                "type": latent_reference.artifact_type,
                "load_name": latent_name,
            },
            "preflight_state": "pending_provider_artifact_check",
            "sampler_node_id": sampler_id,
            "previous_latent_ref": previous_latent,
            "patched_latent_ref": [load_node_id, 0],
            "replaced_consumers": replaced_consumers,
            "policy": "provider_owned_load_latent_no_png_reencode" if restore_point == "final_latent" else ("provider_owned_pre_high_res_latent_continue_no_png_reencode" if restore_point == "before_high_res_fix" else ("provider_owned_post_high_res_latent_continue_no_png_reencode" if restore_point == "after_high_res_fix" else "provider_owned_pre_adetailer_latent_continue_no_png_reencode")),
        }
        branch["provider_resume_supported"] = True
        branch["requires_provider_resume"] = False
        branch["state"] = "provider_resume_enabled"
        branch["load_latent_name"] = latent_name
        branch["comfy_load_name"] = latent_name
        branch["source_filename"] = latent_reference.filename
        branch["source_subfolder"] = latent_reference.subfolder
        branch["source_type"] = latent_reference.artifact_type
        branch["provider_reference"] = {
            "filename": latent_reference.filename,
            "subfolder": latent_reference.subfolder,
            "type": latent_reference.artifact_type,
            "load_name": latent_name,
        }
        branch["provider_resume_ready"] = True
        branch["provider_reference_state"] = "normalized_pending_probe"
        actual_params["_neo_branch_restore"] = branch
        actual_params["_neo_branch_restore_provider_hook_state"] = "workflow_patched"
        actual_params["_neo_branch_restore_provider"] = branch_provider
        replay = actual_params.get("_neo_replay_context") if isinstance(actual_params.get("_neo_replay_context"), dict) else {}
        if replay:
            replay = {**replay, "branch_restore": branch}
            actual_params["_neo_replay_context"] = replay
        backend_payload["prompt"] = workflow
        backend_payload["actual_params"] = actual_params
        backend_payload["latent_branch_resume"] = branch_provider
        notes = list(backend_payload.get("phase_notes") or [])
        if restore_point == "before_high_res_fix":
            notes.append("IMG-R13 patched Comfy LoadLatent for before_high_res_fix branch resume; High-Res continues from the saved base latent with no PNG/img2img fallback.")
        elif restore_point == "after_high_res_fix":
            notes.append("IMG-R14 patched Comfy LoadLatent for after_high_res_fix branch resume; downstream finishing continues from the saved post-High-Res latent with no PNG/img2img fallback.")
        elif restore_point == "before_adetailer":
            notes.append("IMG-R15 patched Comfy LoadLatent for before_adetailer branch resume; ADetailer can rerun from the saved pre-ADetailer latent with no PNG/img2img fallback.")
        else:
            notes.append("IMG-R11 patched Comfy LoadLatent for final_latent branch resume; no PNG/img2img fallback used.")
        backend_payload["phase_notes"] = notes
        return compiled.model_copy(update={"backend_payload": backend_payload})

    def _apply_comfy_latent_capture_hook(self, compiled: CompiledJob, job: NeoJob, route: Any) -> CompiledJob:
        """Patch a Comfy workflow with provider-owned SaveLatent nodes when requested.

        R8 only saves final-latent artifacts. Milestone/phase checkpoints remain
        gated until later phases can identify stable restore points around
        High-Res Fix / ADetailer branches.
        """
        compiled = self._apply_context_latent_hook(compiled, job, route)
        backend_payload = dict(compiled.backend_payload or {})
        workflow = backend_payload.get("prompt")
        actual_params = dict(backend_payload.get("actual_params") if isinstance(backend_payload.get("actual_params"), dict) else (job.params or {}))
        request_payload = normalize_latent_capture_request(actual_params)
        if not request_payload.get("requested"):
            return compiled
        actual_params["_neo_latent_capture"] = request_payload
        if not isinstance(workflow, dict) or compiled.compile_status != "compiled":
            actual_params["_neo_latent_capture_provider_hook_state"] = "compile_gated_no_workflow"
            backend_payload["actual_params"] = actual_params
            return compiled.model_copy(update={"backend_payload": backend_payload})
        node_names = self._comfy_node_names_for_latent_capture()
        if "SaveLatent" not in node_names:
            actual_params["_neo_latent_capture_provider_hook_state"] = "provider_gated_missing_save_latent_node"
            actual_params["_neo_latent_capture_provider_hook_reason"] = "ComfyUI SaveLatent node was not detected from object_info."
            backend_payload["actual_params"] = actual_params
            notes = list(backend_payload.get("phase_notes") or [])
            notes.append("IMG-R8 latent capture requested but provider hook gated because SaveLatent is unavailable/offline.")
            backend_payload["phase_notes"] = notes
            return compiled.model_copy(update={"backend_payload": backend_payload})
        requested_points = list(request_payload.get("requested_restore_points") or [])
        if not requested_points:
            requested_points = ["final_latent"]
        capture_sources: dict[str, list[Any]] = {}
        if "before_high_res_fix" in requested_points:
            before_high_res_source = self._find_before_high_res_latent_source(workflow, actual_params)
            if before_high_res_source:
                capture_sources["before_high_res_fix"] = before_high_res_source
        if "after_high_res_fix" in requested_points:
            after_high_res_source = self._find_after_high_res_latent_source(workflow, actual_params)
            if after_high_res_source:
                capture_sources["after_high_res_fix"] = after_high_res_source
        if "before_adetailer" in requested_points:
            before_adetailer_source = self._find_before_adetailer_latent_source(workflow, actual_params)
            if before_adetailer_source:
                capture_sources["before_adetailer"] = before_adetailer_source
        if "final_latent" in requested_points:
            final_source = self._find_final_latent_source(workflow)
            if final_source:
                capture_sources["final_latent"] = final_source
        if not capture_sources:
            actual_params["_neo_latent_capture_provider_hook_state"] = "provider_gated_no_supported_latent_source"
            actual_params["_neo_latent_capture_provider_hook_reason"] = "No supported SaveLatent source was found. before_high_res_fix/after_high_res_fix require High-Res Lab to be active; before_adetailer requires ADetailer to be active; final_latent requires a final sampler/decode path."
            backend_payload["actual_params"] = actual_params
            return compiled.model_copy(update={"backend_payload": backend_payload})
        capture_nodes = []
        for restore_point, source_ref in capture_sources.items():
            node_id = self._next_workflow_node_id(workflow)
            filename_prefix = self._latent_capture_filename_prefix(job, request_payload, restore_point)
            workflow[node_id] = {
                "class_type": "SaveLatent",
                "inputs": {
                    "samples": source_ref,
                    "filename_prefix": filename_prefix,
                },
                "_meta": {
                    "title": f"Neo Save {restore_point} Restore Point",
                    "restore_point": restore_point,
                    "source_ref": source_ref,
                    "no_png_reencode": True,
                },
            }
            capture_nodes.append({
                "node_id": node_id,
                "class_type": "SaveLatent",
                "restore_point": restore_point,
                "source": source_ref,
                "filename_prefix": filename_prefix,
                "artifact_format": "comfy_latent",
            })
        supported = [item["restore_point"] for item in capture_nodes]
        gated = [item for item in ["base_generation_only", "before_high_res_fix", "after_high_res_fix", "before_adetailer", "final_latent"] if item not in supported and item != "base_generation_only"]
        actual_params["_neo_latent_capture_provider_hook_state"] = "workflow_patched"
        actual_params["_neo_latent_capture_provider"] = {
            "provider_id": self.manifest.provider_id,
            "backend": "comfyui",
            "schema_version": "neo.image.comfy_latent_capture_provider.v4" if "before_adetailer" in supported else ("neo.image.comfy_latent_capture_provider.v3" if any(item in supported for item in ("before_high_res_fix", "after_high_res_fix")) else "neo.image.comfy_latent_capture_provider.v1"),
            "nodes": capture_nodes,
            "supported_restore_points": supported,
            "gated_restore_points": gated,
            "policy": "provider_owned_save_latent_node_no_png_reencode",
        }
        backend_payload["prompt"] = workflow
        backend_payload["actual_params"] = actual_params
        backend_payload["latent_capture"] = actual_params["_neo_latent_capture_provider"]
        notes = list(backend_payload.get("phase_notes") or [])
        if "before_high_res_fix" in supported:
            notes.append("IMG-R13 provider hook patched Comfy SaveLatent before High-Res Lab; branch can resume from the pre-High-Res latent.")
        if "after_high_res_fix" in supported:
            notes.append("IMG-R14 provider hook patched Comfy SaveLatent after High-Res Lab; branch can resume from the post-High-Res latent.")
        if "before_adetailer" in supported:
            notes.append("IMG-R15 provider hook patched Comfy SaveLatent before ADetailer; branch can resume from the pre-ADetailer latent.")
        if "final_latent" in supported:
            notes.append("IMG-R11 provider hook patched Comfy SaveLatent for final_latent capture; no PNG/img2img fallback used.")
        backend_payload["phase_notes"] = notes
        return compiled.model_copy(update={"backend_payload": backend_payload})

    def validate_job(self, job: NeoJob) -> ProviderValidationResult:
        job = self._runtime_job(job)
        result = super().validate_job(job)
        route = select_comfy_compile_route(job)
        if not route.can_compile:
            result.warnings.append("Phase 12.7 compile router did not enable this route: " + "; ".join(route.blockers))
        if job.mode in {"img2img", "image_to_image", "inpaint", "outpaint", "edit"} and route.status != "provider_gated":
            params = job.params or {}
            source_image = self._source_image_value(params)
            stitch_only_ready = job.mode in {"img2img", "image_to_image", "inpaint", "outpaint", "edit"} and image_stitch_has_ready_group(params)
            if not source_image and not stitch_only_ready:
                result.errors.append(f"{job.mode} requires a source image path, filename, or reusable Neo_Data output reference.")
                result.ok = False
            if job.mode == "inpaint" and not self._mask_image_value(params):
                result.errors.append("Inpaint requires a mask image path, filename, or reusable Neo_Data mask reference.")
                result.ok = False
            if job.mode == "outpaint":
                outpaint_payload = normalize_outpaint_payload(params, default_width=int(params.get("width", 1024) or 1024), default_height=int(params.get("height", 1024) or 1024))
                if outpaint_padding_total(outpaint_payload) <= 0:
                    result.errors.append("Outpaint requires padding on at least one side.")
                    result.ok = False
        if job.loader and job.loader not in {"checkpoint", "checkpoint_aio", "diffusion_model", "gguf"}:
            result.warnings.append("This Comfy provider route does not enable this loader yet; readiness/router phases gate unsupported loaders.")
        if job.loader == "diffusion_model" and not (
            (job.family == "flux" and job.mode in {"txt2img", "img2img", "inpaint", "outpaint"})
            or (job.family == "flux1_fill" and job.mode in {"inpaint", "outpaint"})
            or (job.family == "flux2_klein" and job.mode in {"txt2img", "img2img", "edit", "inpaint", "outpaint"})
            or (job.family in {"krea2", "krea2_turbo"} and job.mode in {"txt2img", "img2img", "inpaint", "outpaint"})
            or (job.family == "qwen_image" and job.mode in {"txt2img", "img2img", "edit", "inpaint", "outpaint"})
            or (job.family in {"qwen_image_edit_2509", "qwen_image_edit_2511"} and job.mode in {"img2img", "edit", "inpaint", "outpaint"})
            or (job.family == "z_image" and job.mode in {"txt2img", "img2img", "inpaint", "outpaint"})
            or (job.family == "z_image_turbo" and job.mode in {"txt2img", "img2img", "inpaint", "outpaint"})
            or (job.family == "hidream" and job.mode == "txt2img")
        ):
            result.warnings.append("Diffusion-model loader compile is enabled for declared Flux, Krea 2 RAW/Turbo, Qwen Image, ZImage base/Turbo txt2img/img2img/inpaint/outpaint, and HiDream txt2img routes. Wan/Hunyuan remain provider-gated until confirmed image workflows exist.")
        if job.loader == "checkpoint_aio" and not (job.family == "qwen_rapid_aio" and job.mode in {"txt2img", "img2img", "inpaint", "outpaint", "edit"}):
            result.warnings.append("Checkpoint AIO loader compile is currently enabled only for Qwen Rapid AIO routes; Qwen Image Edit uses Safetensors / Components or GGUF.")
        if job.family == "flux2_klein" and job.loader in {"diffusion_model", "gguf"}:
            params = job.params or {}
            selected_model = job.model or params.get("gguf_unet") or params.get("gguf_model") or params.get("diffusion_model") or params.get("model") or ""
            params, _encoder_reconciliation = reconcile_flux2_klein_encoder_params(params, selected_model, default_if_missing=False)
            text_encoder = params.get("qwen3_text_encoder") or params.get("text_encoder_1") or params.get("gguf_text_encoder_1") or params.get("gguf_text_encoder_primary") or params.get("text_encoder_primary") or params.get("clip_name") or ""
            compatibility = check_flux2_klein_compatibility(params.get("flux_variant") or params.get("variant") or "flux2_klein", selected_model, text_encoder)
            if compatibility.compatible is False:
                result.errors.append(compatibility.message)
                result.ok = False
            elif compatibility.compatible is None and compatibility.message and str(text_encoder).strip():
                result.warnings.append(compatibility.message)
        if job.family == "flux" and job.loader in {"diffusion_model", "gguf"}:
            params = job.params or {}
            selected_model = job.model or params.get("gguf_unet") or params.get("gguf_model") or params.get("diffusion_model") or params.get("model") or params.get("unet") or ""
            resolved_flux1_variant = resolve_flux1_variant(params.get("flux_variant") or params.get("variant") or "dev", selected_model)
            if is_flux1_krea_route(resolved_flux1_variant, selected_model):
                encoder_a = params.get("text_encoder_1") or params.get("gguf_text_encoder_1") or params.get("gguf_text_encoder_primary") or params.get("text_encoder_primary") or params.get("clip_name1") or ""
                encoder_b = params.get("text_encoder_2") or params.get("gguf_text_encoder_2") or params.get("gguf_text_encoder_secondary") or params.get("text_encoder_secondary") or params.get("clip_name2") or ""
                compatibility = check_flux1_krea_compatibility(resolved_flux1_variant, selected_model, encoder_a, encoder_b)
                if compatibility.compatible is False:
                    result.errors.append(compatibility.message)
                    result.ok = False
                elif compatibility.compatible is None and compatibility.message and (str(encoder_a).strip() or str(encoder_b).strip()):
                    result.warnings.append(compatibility.message)
        if job.family in {"krea2", "krea2_turbo"} and job.loader in {"diffusion_model", "gguf"}:
            params = job.params or {}
            selected_model = job.model or params.get("gguf_unet") or params.get("gguf_model") or params.get("diffusion_model") or params.get("model") or params.get("unet") or ""
            text_encoder = params.get("qwen3vl_text_encoder") or params.get("text_encoder_1") or params.get("text_encoder_primary") or params.get("clip_name") or ""
            vae = params.get("vae") or params.get("vae_or_ae") or params.get("ae") or ""
            compatibility = check_krea2_compatibility(job.family, selected_model, text_encoder, vae, loader=job.loader)
            if compatibility.compatible is False:
                result.errors.append(compatibility.message)
                result.ok = False
            elif compatibility.compatible is None and compatibility.message:
                result.warnings.append(compatibility.message)
        if job.loader == "gguf" and not (
            (job.family == "flux" and job.mode in {"txt2img", "img2img", "inpaint", "outpaint"})
            or (job.family == "flux2_klein" and job.mode in {"txt2img", "img2img", "inpaint", "outpaint", "edit"})
            or (job.family in {"krea2", "krea2_turbo"} and job.mode in {"txt2img", "img2img", "inpaint", "outpaint"})
            or (job.family in {"qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "qwen_image_edit_2511"} and job.mode in {"txt2img", "img2img", "inpaint", "outpaint", "edit"})
            or (job.family == "z_image" and job.mode in {"txt2img", "img2img", "inpaint", "outpaint"})
            or (job.family == "z_image_turbo" and job.mode == "txt2img")
            or (job.family == "hidream" and job.mode == "txt2img")
        ):
            result.warnings.append("GGUF loader compile is enabled for Flux txt2img/img2img/inpaint/outpaint, Flux 2 Klein routes, experimental Krea 2 RAW/Turbo routes, Qwen/Qwen Rapid AIO image routes, Z-Image base image routes, Z-Image Turbo txt2img, and HiDream txt2img. ZImage Turbo GGUF image modes remain a separate workflow pass.")
        if job.family in {"qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "qwen_image_edit_2511"} and job.loader == "gguf" and job.mode in {"img2img", "inpaint", "outpaint", "edit"}:
            params = job.params or {}
            mmproj = (
                params.get("qwen_mmproj")
                or params.get("gguf_mmproj")
                or params.get("mmproj")
                or params.get("mmproj_name")
                or params.get("gguf_mmproj_name")
            )
            if not str(mmproj or "").strip():
                result.errors.append(f"Qwen GGUF {job.mode} requires a Qwen mmproj sidecar before queue.")
                result.ok = False
            if job.mode == "outpaint":
                outpaint_payload = normalize_outpaint_payload(params, default_width=int(params.get("width", 1024) or 1024), default_height=int(params.get("height", 1024) or 1024))
                if outpaint_padding_total(outpaint_payload) <= 0:
                    result.errors.append("Qwen GGUF outpaint requires padding on at least one side.")
                    result.ok = False
        return result

    @staticmethod
    def _source_image_value(params: dict[str, Any]) -> str:
        source = (
            params.get("source_image")
            or params.get("source_image_path")
            or params.get("init_image")
            or params.get("image")
            or params.get("source_image_url")
            or params.get("source_url")
            or params.get("source_id")
            or params.get("source_image_id")
        )
        if isinstance(source, dict):
            source = source.get("path") or source.get("file") or source.get("filename") or source.get("url") or source.get("source_id")
        return str(source or "").strip()



    @staticmethod
    def _extra_source_image_value(params: dict[str, Any], lane: int) -> str:
        if lane == 2:
            keys = ("source_image_2", "source_image_2_path", "source_image__2", "reference_image_2", "source_image_2_url", "source_image__2_name", "reference_image_2_name")
        elif lane == 3:
            keys = ("source_image_3", "source_image_3_path", "source_image__3", "composition_image", "source_image_3_url", "source_image__3_name", "composition_image_name", "reference_image_3_name")
        else:
            return ""
        for key in keys:
            value = params.get(key)
            if isinstance(value, dict):
                value = value.get("path") or value.get("file") or value.get("filename") or value.get("url") or value.get("name")
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _extra_qwen_source_image_value(params: dict[str, Any], lane: int) -> str:
        return ComfyProvider._extra_source_image_value(params, lane)

    @staticmethod
    def _source_visibility_mask_payload(params: dict[str, Any]) -> dict[str, Any]:
        nested = params.get("source_visibility_mask")
        if isinstance(nested, dict):
            return normalize_source_visibility_mask(nested)
        return normalize_source_visibility_mask({
            "enabled": params.get("source_visibility_mask_enabled"),
            "path": params.get("source_visibility_mask_path"),
            "url": params.get("source_visibility_mask_url"),
            "name": params.get("source_visibility_mask_name"),
            "fill_mode": params.get("source_visibility_mask_fill_mode"),
        })

    def _prepare_visibility_masked_source(
        self,
        source_ref: str,
        mask_payload: dict[str, Any] | None,
        *,
        run_id: str,
        field: str,
    ) -> tuple[str, dict[str, Any] | None]:
        normalized = normalize_source_visibility_mask(mask_payload)
        if not normalized.get("enabled"):
            return source_ref, None
        source_path = self._resolve_neo_image_ref_path(source_ref)
        mask_path = self._resolve_neo_image_ref_path(str(normalized.get("ref") or ""))
        if source_path is None:
            raise SourceVisibilityMaskError(f"{field} source image could not be resolved from Neo storage.")
        if mask_path is None:
            raise SourceVisibilityMaskError(f"{field} visibility mask could not be resolved from Neo storage.")
        result = apply_source_visibility_mask(
            source_path,
            mask_path,
            output_dir=self._neo_source_image_dir() / "masked_sources",
            fill_mode=str(normalized.get("fill_mode") or "black"),
        )
        metadata = {
            **normalized,
            **result.metadata,
            "field": field,
            "original_source_name": source_path.name,
            "masked_source_name": result.output_path.name,
        }
        log_image_event(
            "source_visibility_mask_applied",
            run_id=run_id,
            payload={
                "field": field,
                "source_name": source_path.name,
                "mask_name": mask_path.name,
                "output_name": result.output_path.name,
                "fill_mode": metadata.get("fill_mode"),
            },
        )
        return str(result.output_path), metadata

    def _prepare_image_stitch_handoff(self, params: dict[str, Any], *, run_id: str, family: str, loader: str, mode: str) -> dict[str, Any]:
        """Upload shared Stitch inputs and prepare a safe compile-time source placeholder."""

        payload = extract_image_stitch_payload(params)
        if not payload.get("enabled"):
            return params
        payload = deepcopy(payload)
        cache: dict[str, str] = {}
        ready_group = False
        for group in payload.get("groups") or []:
            if not isinstance(group, dict) or group.get("enabled", True) is False:
                continue
            inputs = group.get("inputs") if isinstance(group.get("inputs"), dict) else {}
            for side in ("image_a", "image_b"):
                raw = inputs.get(side)
                if not isinstance(raw, dict):
                    raw = {"ref": raw}
                    inputs[side] = raw
                source = str(
                    raw.get("source_id")
                    or raw.get("asset_id")
                    or raw.get("path")
                    or raw.get("ref")
                    or raw.get("filename")
                    or raw.get("file")
                    or raw.get("url")
                    or ""
                ).strip()
                if not source:
                    continue
                upload_source = source
                visibility_metadata = None
                visibility_payload = raw.get("visibility_mask") if isinstance(raw.get("visibility_mask"), dict) else None
                if visibility_payload:
                    upload_source, visibility_metadata = self._prepare_visibility_masked_source(
                        source,
                        visibility_payload,
                        run_id=run_id,
                        field=f"stitch.{group.get('id') or 'group'}.{side}",
                    )
                cache_key = f"{upload_source}|{(visibility_metadata or {}).get('mask_name', '')}"
                comfy_name = cache.get(cache_key)
                if not comfy_name:
                    comfy_name = self._upload_image_to_comfy_input(upload_source, require_verified=True)
                    cache[cache_key] = comfy_name
                if visibility_metadata:
                    raw["visibility_mask"] = visibility_metadata
                    raw["original_ref"] = source
                raw["comfy_ref"] = comfy_name
                raw["ref"] = comfy_name
                raw["stored_filename"] = comfy_name
            if inputs.get("image_a", {}).get("comfy_ref") and inputs.get("image_b", {}).get("comfy_ref"):
                ready_group = True
            group["inputs"] = inputs

        params["qwen_stitch"] = payload
        params["image_stitch"] = payload
        if ready_group and not image_stitch_is_qwen_lane_route(family=family, loader=loader, mode=mode):
            first = next(
                (
                    group["inputs"]["image_a"].get("comfy_ref")
                    for group in payload.get("groups") or []
                    if isinstance(group, dict)
                    and group.get("enabled", True) is not False
                    and isinstance(group.get("inputs"), dict)
                    and group["inputs"].get("image_a", {}).get("comfy_ref")
                ),
                "",
            )
            if first and not self._source_image_value(params):
                params["source_image"] = first
                params["source_image_path"] = first
                params["comfy_source_image_name"] = first
                params["_neo_stitch_source_placeholder"] = True
        log_image_event(
            "image_stitch_handoff",
            run_id=run_id,
            payload={"family": family, "loader": loader, "mode": mode, "uploaded_inputs": len(cache), "ready_group": ready_group},
        )
        return params

    def _patch_compiled_image_stitch(self, compiled: CompiledJob, job: NeoJob) -> CompiledJob:
        """Patch non-Qwen single-source graphs after their normal compiler runs."""

        backend_payload = dict(compiled.backend_payload or {})
        workflow = backend_payload.get("prompt")
        if not isinstance(workflow, dict) or not workflow:
            return compiled
        params = backend_payload.get("actual_params") if isinstance(backend_payload.get("actual_params"), dict) else dict(job.params or {})
        workflow, stitch_meta, warnings, errors = patch_source_loadimage_with_stitch(
            workflow,
            params,
            family=job.family,
            loader=job.loader,
            mode=job.mode,
            backend_capabilities=self.discover_backend_capabilities(),
        )
        if not params.get("qwen_stitch") and not params.get("image_stitch"):
            return compiled
        backend_payload["prompt"] = workflow
        actual_params = dict(params)
        if stitch_meta.get("patched") or stitch_meta.get("groups"):
            actual_params["image_stitch"] = stitch_meta
            actual_params["_neo_image_stitch_warnings"] = warnings
            backend_payload["actual_params"] = actual_params
        validation = dict(backend_payload.get("validation") or {})
        validation_errors = list(validation.get("errors") or [])
        validation_warnings = list(validation.get("warnings") or [])
        validation_warnings.extend(warnings)
        validation_errors.extend(errors)
        if validation_errors:
            validation["ok"] = False
            validation["errors"] = validation_errors
        validation["warnings"] = validation_warnings
        backend_payload["validation"] = validation
        notes = list(backend_payload.get("phase_notes") or [])
        notes.extend(warnings)
        if errors:
            notes.extend(f"Stitch Images: {error}" for error in errors)
        backend_payload["phase_notes"] = notes
        return compiled.model_copy(update={"backend_payload": backend_payload})

    @staticmethod
    def _mask_image_value(params: dict[str, Any]) -> str:
        mask = (
            params.get("mask_image")
            or params.get("mask_image_path")
            or params.get("inpaint_mask")
            or params.get("mask")
            or params.get("mask_image_url")
            or params.get("mask_url")
            or params.get("mask_id")
            or params.get("mask_image_id")
        )
        if isinstance(mask, dict):
            mask = mask.get("path") or mask.get("file") or mask.get("filename") or mask.get("url") or mask.get("mask_id")
        return str(mask or "").strip()

    @staticmethod
    def _comfy_input_image_name(source_image: str) -> str:
        if not source_image:
            return ""
        # Comfy LoadImage receives an input-folder filename. If Neo is handed a
        # local/Neo_Data path, run_job uploads it first and then swaps this value.
        return Path(source_image).name

    def _neo_source_image_dir(self) -> Path:
        """Return Neo's local source-image input directory.

        The UI upload endpoint stores source images under neo_data/inputs/image.
        Provider code may later receive either the absolute path, the source-file
        URL, or just the stored filename. This resolver keeps LayerDiffuse and
        img2img handoffs from treating a Neo filename as if it already existed in
        Comfy's input folder.
        """
        return Path.cwd() / "neo_data" / "inputs" / "image"

    def _neo_mask_image_dir(self) -> Path:
        """Return Neo's local mask input directory."""

        return Path.cwd() / "neo_data" / "inputs" / "image_masks"

    def _resolve_neo_output_file_url_path(self, raw: str) -> Path | None:
        """Resolve Neo output-file API URLs back to persisted output files.

        Saved Image results expose previews as /api/image/output-file?result_id=...
        URLs. Those URLs are valid for the browser, but Comfy LoadImage needs a
        local file upload. Resolve them through the output service before falling
        back to source-folder filename matching.
        """
        parsed = parse.urlparse(str(raw or "").strip())
        if parsed.path != "/api/image/output-file":
            return None
        query = parse.parse_qs(parsed.query)
        result_id = str((query.get("result_id") or [""])[0] or "").strip()
        file_id = str((query.get("file_id") or [""])[0] or "").strip()
        if not result_id or not file_id:
            return None
        try:
            from neo_app.image.output_service import resolve_output_file

            path = resolve_output_file(result_id, file_id)
            if path.exists() and path.is_file():
                return path
        except Exception:
            return None
        return None

    def _resolve_neo_image_ref_path(self, source_image: str) -> Path | None:
        """Resolve Neo-owned image refs to a concrete readable local path.

        Accepts absolute/local paths, /api/image/source-file/<name> URLs,
        /api/image/output-file?result_id=...&file_id=... URLs from saved Neo
        results, and bare stored filenames returned by /api/image/source-image.
        Returns None for external URLs and unknown Comfy-only names.
        """
        raw = str(source_image or "").strip()
        if not raw:
            return None
        if raw.startswith(("http://", "https://")):
            return None

        output_path = self._resolve_neo_output_file_url_path(raw)
        if output_path is not None:
            return output_path

        parsed = parse.urlparse(raw) if raw.startswith("/api/") else None
        parsed_path = parsed.path if parsed else raw
        name = Path(parsed_path.replace("\\", "/")).name
        candidates: list[Path] = []
        # Avoid treating API URLs with query strings as local filesystem paths.
        if not parsed:
            candidates.append(Path(raw).expanduser())
        if name:
            candidates.append(self._neo_source_image_dir() / name)
            candidates.append(self._neo_mask_image_dir() / name)
            # ControlNet Build Map persists a durable Neo-owned copy here even
            # when its eager Comfy input upload is unavailable. Pre-queue
            # materialization can therefore recover a bare generated-map name or
            # the dedicated map-file API URL without treating unrelated API
            # image URLs as ControlNet assets.
            is_controlnet_map_url = bool(parsed and parsed.path.startswith("/api/extensions/controlnet/maps/file/"))
            if not parsed or is_controlnet_map_url:
                candidates.append(Path.cwd() / "neo_data" / "controlnet_maps" / name)
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    @staticmethod
    def _layerdiffuse_round_dim_to_64(value: Any, fallback: int = 1024) -> int:
        try:
            dim = int(float(value))
        except (TypeError, ValueError):
            dim = int(fallback or 1024)
        if dim <= 0:
            dim = int(fallback or 1024)
        rounded = int(round(dim / 64.0) * 64)
        return max(64, rounded)

    def _layerdiffuse_target_dimensions(self, params: dict[str, Any], *, fallback_width: int, fallback_height: int) -> tuple[int, int]:
        """Resolve the executable LayerDiffuse source-canvas size.

        huchenlei/ComfyUI-layerdiffuse decoders assert that the decoded image
        canvas is divisible by 64. Image-conditioned LayerDiffuse templates build
        that canvas from the uploaded LoadImage -> VAEEncode source, not from a
        standalone EmptyLatentImage node. Neo therefore normalizes slot images to
        the active route size before Comfy queueing, so a 896x1296 upload does not
        crash after sampling with "Height(1296) is not multiple of 64".
        """
        width = self._layerdiffuse_round_dim_to_64(params.get("width"), fallback_width)
        height = self._layerdiffuse_round_dim_to_64(params.get("height"), fallback_height)
        return width, height

    def _prepare_layerdiffuse_upload_image(
        self,
        path: Path,
        *,
        params: dict[str, Any],
        run_id: str,
        field: str,
    ) -> tuple[Path, dict[str, Any]]:
        """Return a Comfy-uploadable LayerDiffuse source image and metadata.

        LayerDiffuse's decode node requires image dimensions to be multiples of
        64. It also effectively uses the uploaded slot image as the canvas for
        image-conditioned modes. This creates a Neo temp copy resized to the
        route width/height when needed, leaving the user's original file intact.
        """
        metadata: dict[str, Any] = {
            "field": field,
            "source_path": str(path),
            "normalized": False,
            "normalization_reason": "already_compatible",
        }
        try:
            from PIL import Image  # type: ignore
        except Exception as exc:  # noqa: BLE001
            metadata["normalization_reason"] = f"pillow_unavailable:{type(exc).__name__}"
            return path, metadata

        try:
            with Image.open(path) as image:
                original_width, original_height = image.size
                metadata["original_width"] = original_width
                metadata["original_height"] = original_height
                target_width, target_height = self._layerdiffuse_target_dimensions(
                    params,
                    fallback_width=original_width,
                    fallback_height=original_height,
                )
                metadata["target_width"] = target_width
                metadata["target_height"] = target_height
                needs_resize = (original_width, original_height) != (target_width, target_height)
                needs_multiple_fix = (original_width % 64 != 0) or (original_height % 64 != 0)
                if not needs_resize and not needs_multiple_fix:
                    return path, metadata

                resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
                normalized = image.convert("RGBA") if image.mode in {"RGBA", "LA", "P"} else image.convert("RGB")
                normalized = normalized.resize((target_width, target_height), resample)
                out_dir = Path.cwd() / "neo_data" / "temp" / "layerdiffuse_inputs"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"ld_{field}_{run_id}_{uuid4().hex[:8]}.png"
                normalized.save(out_path, format="PNG")
                metadata.update({
                    "normalized": True,
                    "normalization_reason": "route_size_or_multiple64_required",
                    "normalized_path": str(out_path),
                    "normalized_width": target_width,
                    "normalized_height": target_height,
                    "multiple_of_64": True,
                })
                log_image_event("layerdiffuse_image_normalized", run_id=run_id, payload=metadata)
                return out_path, metadata
        except Exception as exc:  # noqa: BLE001
            metadata["normalization_reason"] = f"normalization_failed:{type(exc).__name__}"
            log_image_event("layerdiffuse_image_normalization_skipped", run_id=run_id, payload=metadata)
            return path, metadata

    @staticmethod
    def _split_comfy_input_image_name(image_name: str) -> tuple[str, str]:
        """Split a Comfy LoadImage input into filename + provider subfolder.

        Comfy's upload endpoint may return names such as
        ``neo_studio_controlnet/map.png``. LoadImage keeps that relative
        subfolder, and ``/view`` expects it as a separate ``subfolder`` query
        value. Do not collapse the name to its basename or a valid nested input
        will be reported as missing before queueing.
        """
        raw = str(image_name or "").strip().replace("\\", "/")
        if not raw or raw.startswith(("http://", "https://", "/api/")):
            return "", ""
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            return "", ""
        parts = [part for part in raw.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            return "", ""
        return parts[-1], "/".join(parts[:-1])

    def _verify_comfy_input_image_name(self, image_name: str) -> bool:
        """Best-effort Comfy input validation for LoadImage filenames.

        Comfy validates LoadImage at /prompt time. Neo preflights every LoadImage
        so broken source handoffs fail cleanly before queueing. Nested Comfy
        input names must preserve their relative subfolder during this probe.
        """
        name, subfolder = self._split_comfy_input_image_name(image_name)
        if not name:
            return False
        query_values = {"filename": name, "type": "input"}
        if subfolder:
            query_values["subfolder"] = subfolder
        query = parse.urlencode(query_values)
        req = request.Request(self._url(f"/view?{query}"), method="GET")
        try:
            with request.urlopen(req, timeout=max(self.timeout, 5)) as response:
                return int(getattr(response, "status", 200)) < 400
        except Exception:  # noqa: BLE001
            return False


    def _probe_comfy_artifact_reference(self, reference: ComfyArtifactReference) -> dict[str, Any]:
        """Check one provider-relative Comfy artifact without executing a graph."""
        query = parse.urlencode({
            "filename": reference.filename,
            "subfolder": reference.subfolder,
            "type": reference.artifact_type,
        })
        req = request.Request(self._url(f"/view?{query}"), method="GET")
        try:
            with request.urlopen(req, timeout=max(self.timeout, 5)) as response:
                status_code = int(getattr(response, "status", 200))
            return {
                "ok": status_code < 400,
                "state": "available" if status_code < 400 else "unavailable",
                "status_code": status_code,
                "load_name": reference.load_name,
            }
        except error.HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            return {
                "ok": False,
                "state": "missing" if code == 404 else "rejected",
                "status_code": code,
                "load_name": reference.load_name,
                "error": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "state": "verification_unavailable",
                "status_code": 0,
                "load_name": reference.load_name,
                "error": str(exc),
            }

    def _validate_prompt_load_latents(self, prompt_graph: dict[str, Any], *, run_id: str) -> list[dict[str, Any]]:
        """Preflight every LoadLatent reference before sending ``/prompt``.

        This catches stale provider outputs and malformed legacy metadata before
        Comfy rejects the whole graph. Neo-owned copied paths are never uploaded
        or substituted automatically.
        """
        invalid: list[dict[str, Any]] = []
        if not isinstance(prompt_graph, dict):
            return invalid
        for node_id, node in prompt_graph.items():
            if not isinstance(node, dict) or node.get("class_type") != "LoadLatent":
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            raw_name = str(inputs.get("latent") or "").strip()
            try:
                reference = parse_comfy_artifact_name(raw_name, default_type="input")
            except ComfyArtifactPathError as exc:
                message = f"Latent checkpoint reference is invalid: {exc}"
                invalid.append({
                    "node_id": str(node_id),
                    "class_type": "LoadLatent",
                    "load_name": raw_name,
                    "state": "invalid_reference",
                    "message": message,
                })
                continue

            inputs["latent"] = reference.load_name
            node["inputs"] = inputs
            probe = self._probe_comfy_artifact_reference(reference)
            if probe.get("ok"):
                log_image_event(
                    "loadlatent_reference_verified",
                    run_id=run_id,
                    payload={
                        "node_id": str(node_id),
                        "load_name": reference.load_name,
                        "state": "available",
                    },
                )
                continue

            state = str(probe.get("state") or "unavailable")
            if state == "missing":
                message = (
                    f"Latent checkpoint is no longer available in ComfyUI: {reference.load_name}. "
                    "Start a clean generation or select another checkpoint."
                )
            elif state == "verification_unavailable":
                message = (
                    f"Neo could not verify the selected latent checkpoint in ComfyUI: {reference.load_name}. "
                    "Check the ComfyUI connection, then try again."
                )
            else:
                message = (
                    f"ComfyUI rejected the selected latent checkpoint reference: {reference.load_name}. "
                    "Start a clean generation or select another checkpoint."
                )
            invalid.append({
                "node_id": str(node_id),
                "class_type": "LoadLatent",
                "load_name": reference.load_name,
                "state": state,
                "status_code": int(probe.get("status_code") or 0),
                "message": message,
                "error": str(probe.get("error") or ""),
            })

        if invalid:
            record_generation_error(
                run_id=run_id,
                message="Comfy LoadLatent validation failed before queue.",
                payload={
                    "invalid_load_latents": invalid,
                    "warning": "latent_checkpoint_missing_or_unavailable",
                },
            )
        return invalid



    @staticmethod
    def _prompt_node_refs(value: Any) -> list[tuple[str, int]]:
        refs: list[tuple[str, int]] = []
        if isinstance(value, list):
            if len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                refs.append((value[0], value[1]))
            else:
                for item in value:
                    refs.extend(ComfyProvider._prompt_node_refs(item))
        elif isinstance(value, dict):
            for item in value.values():
                refs.extend(ComfyProvider._prompt_node_refs(item))
        return refs

    @staticmethod
    def _prompt_node_consumers(prompt_graph: dict[str, Any]) -> dict[str, list[str]]:
        consumers: dict[str, list[str]] = {}
        for node_id, node in prompt_graph.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            for ref_id, _slot in ComfyProvider._prompt_node_refs(inputs):
                consumers.setdefault(str(ref_id), []).append(str(node_id))
        return consumers

    @staticmethod
    def _replace_prompt_refs(prompt_graph: dict[str, Any], old_ref: tuple[str, int], new_ref: tuple[str, int] | list[Any]) -> int:
        old_id, old_slot = str(old_ref[0]), int(old_ref[1])
        replacement = [str(new_ref[0]), int(new_ref[1])]
        replaced = 0

        def walk(value: Any) -> Any:
            nonlocal replaced
            if isinstance(value, list):
                if len(value) == 2 and str(value[0]) == old_id and value[1] == old_slot:
                    replaced += 1
                    return list(replacement)
                return [walk(item) for item in value]
            if isinstance(value, dict):
                return {key: walk(item) for key, item in value.items()}
            return value

        for node in prompt_graph.values():
            if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
                node["inputs"] = walk(node["inputs"])
        return replaced

    @staticmethod
    def _prompt_ref_depends_on(prompt_graph: dict[str, Any], ref: Any, target_id: str, *, seen: set[str] | None = None) -> bool:
        if not (isinstance(ref, list) and len(ref) == 2 and isinstance(ref[0], str)):
            return False
        node_id = str(ref[0])
        if node_id == str(target_id):
            return True
        seen = seen or set()
        if node_id in seen:
            return False
        seen.add(node_id)
        node = prompt_graph.get(node_id)
        if not isinstance(node, dict):
            return False
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for child_ref in ComfyProvider._prompt_node_refs(inputs):
            if ComfyProvider._prompt_ref_depends_on(prompt_graph, [child_ref[0], child_ref[1]], target_id, seen=seen):
                return True
        return False

    @staticmethod
    def _clean_model_ref_for_optional_adapter(prompt_graph: dict[str, Any], ref: Any, target_id: str, *, seen: set[str] | None = None) -> list[Any] | None:
        if not (isinstance(ref, list) and len(ref) == 2 and isinstance(ref[0], str)):
            return None
        node_id = str(ref[0])
        seen = seen or set()
        if node_id in seen:
            return None
        seen.add(node_id)
        node = prompt_graph.get(node_id)
        if not isinstance(node, dict):
            return list(ref) if not ComfyProvider._prompt_ref_depends_on(prompt_graph, ref, target_id) else None
        cls = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if "IPAdapter" in cls or "FaceID" in cls:
            model_ref = inputs.get("model")
            if isinstance(model_ref, list) and len(model_ref) == 2:
                return ComfyProvider._clean_model_ref_for_optional_adapter(prompt_graph, model_ref, target_id, seen=seen)
            return None
        if ComfyProvider._prompt_ref_depends_on(prompt_graph, ref, target_id):
            return None
        return list(ref)

    @staticmethod
    def _is_optional_adapter_load_image(prompt_graph: dict[str, Any], load_node_id: str) -> bool:
        consumers = ComfyProvider._prompt_node_consumers(prompt_graph)
        queue = list(consumers.get(str(load_node_id), []))
        seen: set[str] = set()
        while queue:
            node_id = queue.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            node = prompt_graph.get(node_id)
            if not isinstance(node, dict):
                continue
            cls = str(node.get("class_type") or "")
            cls_l = cls.lower()
            if "ipadapter" in cls_l or "faceid" in cls_l or "controlnet" in cls_l or "clipvision" in cls_l:
                return True
            queue.extend(consumers.get(node_id, []))
        return False

    def _downgrade_optional_adapter_load_images(
        self,
        prompt_graph: dict[str, Any],
        invalid: list[dict[str, Any]],
        *,
        run_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Phase 26.9.17: remove optional adapter chains with missing LoadImage refs.

        Required source images should still fail cleanly. Optional adapter references
        from saved character/IPAdapter profiles must not block txt2img/Scene
        Director generation; stale refs are downgraded to metadata warnings and the
        graph is rewired to the pre-adapter path.
        """
        remaining: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {
            "schema": "neo.image.comfy.optional_adapter_loadimage_validation.v1",
            "phase": "SD-V054-26.9.17",
            "status": "not_applicable",
            "downgraded_count": 0,
            "uploaded_count": 0,
            "disabled_nodes": [],
            "warnings": [],
        }
        if not invalid:
            return remaining, metadata

        nodes_to_remove: set[str] = set()
        for item in invalid:
            load_id = str(item.get("node_id") or "")
            if not load_id or not self._is_optional_adapter_load_image(prompt_graph, load_id):
                remaining.append(item)
                continue

            consumers = self._prompt_node_consumers(prompt_graph)
            queue = list(consumers.get(load_id, []))
            affected: set[str] = {load_id}
            while queue:
                node_id = queue.pop(0)
                if node_id in affected:
                    continue
                node = prompt_graph.get(node_id)
                if not isinstance(node, dict):
                    continue
                affected.add(node_id)
                queue.extend(consumers.get(node_id, []))

            # Rewire identity-restore KSamplers before adapter model outputs are
            # unwrapped. Otherwise a KSampler can stop depending on the missing
            # LoadImage after its model input is repaired and remain as a silent
            # no-op img2img pass.
            affected_sorted = sorted(affected, key=lambda value: int(value) if value.isdigit() else 999999)
            for node_id in affected_sorted:
                node = prompt_graph.get(node_id)
                if not isinstance(node, dict):
                    continue
                cls = str(node.get("class_type") or "")
                inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
                if "KSampler" in cls and self._prompt_ref_depends_on(prompt_graph, inputs.get("model"), load_id):
                    latent_ref = inputs.get("latent_image")
                    if isinstance(latent_ref, list) and len(latent_ref) == 2:
                        self._replace_prompt_refs(prompt_graph, (node_id, 0), latent_ref)
                        nodes_to_remove.add(node_id)
                        metadata["disabled_nodes"].append({
                            "node_id": node_id,
                            "class_type": cls,
                            "action": "rewired_output_to_latent_input",
                            "fallback_ref": list(latent_ref),
                            "source_load_image": load_id,
                        })

            # Rewire adapter model outputs to their clean upstream model.
            for node_id in affected_sorted:
                node = prompt_graph.get(node_id)
                if not isinstance(node, dict):
                    continue
                cls = str(node.get("class_type") or "")
                if ("IPAdapter" in cls or "FaceID" in cls) and self._prompt_ref_depends_on(prompt_graph, [node_id, 0], load_id):
                    clean_model = self._clean_model_ref_for_optional_adapter(prompt_graph, [node_id, 0], load_id)
                    if isinstance(clean_model, list) and len(clean_model) == 2:
                        self._replace_prompt_refs(prompt_graph, (node_id, 0), clean_model)
                        metadata["disabled_nodes"].append({
                            "node_id": node_id,
                            "class_type": cls,
                            "action": "rewired_model_output_to_clean_upstream_model",
                            "fallback_ref": list(clean_model),
                            "source_load_image": load_id,
                        })
                    nodes_to_remove.add(node_id)
                elif "IPAdapter" in cls or "FaceID" in cls or "CLIPVision" in cls:
                    nodes_to_remove.add(node_id)

            nodes_to_remove.add(load_id)
            metadata["downgraded_count"] += 1
            metadata["warnings"].append("ipadapter_reference_image_missing_unit_disabled")
            metadata["disabled_nodes"].append({
                "node_id": load_id,
                "class_type": "LoadImage",
                "image": item.get("image"),
                "action": "removed_optional_adapter_loadimage",
                "warning": "ipadapter_reference_image_missing_unit_disabled",
            })

        # Remove only nodes that no longer have live consumers. Multiple passes keep
        # the graph conservative if an optional helper is unexpectedly shared.
        changed = True
        while changed:
            changed = False
            consumers = self._prompt_node_consumers(prompt_graph)
            for node_id in list(nodes_to_remove):
                if node_id not in prompt_graph:
                    continue
                live_consumers = [consumer for consumer in consumers.get(node_id, []) if consumer in prompt_graph and consumer not in nodes_to_remove]
                if live_consumers:
                    continue
                prompt_graph.pop(node_id, None)
                changed = True

        if metadata["downgraded_count"]:
            metadata["status"] = "applied"
            metadata["warnings"] = sorted(set(metadata["warnings"]))
            log_image_event("optional_adapter_loadimage_downgraded", run_id=run_id, payload=metadata)
        elif remaining:
            metadata["status"] = "required_invalid_loadimage_found"
        return remaining, metadata

    def _validate_prompt_load_images(self, prompt_graph: dict[str, Any], *, run_id: str) -> list[dict[str, Any]]:
        """Preflight LoadImage refs before /prompt.

        Phase 26.9.17 first tries to normalize saved local/profile paths into
        Comfy input filenames. Any remaining missing optional adapter reference is
        downgraded; required source-image LoadImage failures still stop before the
        queue with a clean Neo error.
        """
        invalid: list[dict[str, Any]] = []
        if not isinstance(prompt_graph, dict):
            return invalid
        for node_id, node in list(prompt_graph.items()):
            if not isinstance(node, dict) or node.get("class_type") != "LoadImage":
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            image_name = str(inputs.get("image") or "").strip()
            if image_name and self._verify_comfy_input_image_name(image_name):
                continue
            if image_name:
                try:
                    comfy_name = self._upload_image_to_comfy_input(image_name, require_verified=True)
                    if comfy_name and self._verify_comfy_input_image_name(comfy_name):
                        inputs["image"] = comfy_name
                        node["inputs"] = inputs
                        log_image_event(
                            "loadimage_reference_uploaded_to_comfy",
                            run_id=run_id,
                            payload={
                                "node_id": str(node_id),
                                "source": image_name,
                                "comfy_name": comfy_name,
                                "phase": "SD-V054-26.9.17",
                            },
                        )
                        continue
                except Exception as exc:  # noqa: BLE001
                    log_image_event(
                        "loadimage_reference_upload_failed",
                        run_id=run_id,
                        level="WARNING",
                        payload={"node_id": str(node_id), "image": image_name, "error": str(exc), "phase": "SD-V054-26.9.17"},
                    )
            invalid.append({"node_id": str(node_id), "image": image_name, "class_type": "LoadImage"})

        invalid, downgrade_metadata = self._downgrade_optional_adapter_load_images(prompt_graph, invalid, run_id=run_id)
        if invalid:
            record_generation_error(
                run_id=run_id,
                message="Comfy LoadImage validation failed before queue.",
                payload={
                    "invalid_load_images": invalid,
                    "optional_adapter_loadimage_validation": downgrade_metadata,
                    "warning": "source_image_missing_or_unreadable",
                },
            )
        return invalid

    def _upload_image_to_comfy_input(self, source_image: str, *, require_verified: bool = False, upload_path: Path | None = None) -> str:
        """Upload a local source image to Comfy's input folder and return LoadImage filename.

        Comfy's LoadImage node can only read Comfy input-folder filenames. V2 may
        hold Neo-owned absolute paths, /api/image/source-file URLs, or bare stored
        Neo filenames. This method resolves those refs, uploads real files through
        Comfy's /upload/image endpoint, and optionally verifies the returned name
        before graph compilation.
        """
        source_image = str(source_image or "").strip()
        if not source_image:
            return ""
        if source_image.startswith(("http://", "https://")):
            if require_verified:
                raise FileNotFoundError(f"Comfy source image cannot be uploaded from an external URL directly: {source_image}")
            return Path(parse.urlparse(source_image).path).name

        path = upload_path or self._resolve_neo_image_ref_path(source_image)
        if path is None:
            # A Comfy-owned input may already be valid, including a nested input
            # such as neo_studio_controlnet/map.png. Preserve that relative name
            # instead of reducing it to a basename and probing the wrong folder.
            if self._verify_comfy_input_image_name(source_image):
                return source_image.replace("\\", "/")
            existing_name = Path(parse.urlparse(source_image).path).name
            if existing_name and self._verify_comfy_input_image_name(existing_name):
                return existing_name
            if require_verified:
                raise FileNotFoundError(f"Comfy source image is not available as a Neo file or Comfy input file: {source_image}")
            return existing_name

        boundary = f"----NeoStudioBoundary{uuid4().hex}"
        prefix = "neo_mask" if "mask" in path.name.lower() else "neo_img2img"
        # Content-addressed handoff names keep Comfy's LoadImage identity stable
        # across repeated Neo generations. The old per-run UUID changed the
        # LoadImage input every queue, invalidating downstream Qwen image/text
        # conditioning caches and forcing the large text encoder back into VRAM.
        file_bytes = path.read_bytes()
        content_hash = hashlib.sha256(file_bytes).hexdigest()[:20]
        suffix = path.suffix.lower() if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} else ".png"
        filename = f"{prefix}_cache_{content_hash}{suffix}"
        # Reuse an existing content-addressed Comfy input when possible. This is
        # safe because the filename is derived from the file bytes, not the local
        # path or original filename.
        if self._verify_comfy_input_image_name(filename):
            return filename
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode())
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(file_bytes)
        body.extend(f"\r\n--{boundary}\r\n".encode())
        body.extend(b'Content-Disposition: form-data; name="type"\r\n\r\ninput')
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        req = request.Request(
            self._url("/upload/image"),
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with request.urlopen(req, timeout=max(self.timeout, 10)) as response:
            raw = response.read().decode("utf-8").strip()
            uploaded_name = filename
            if raw:
                try:
                    payload = json.loads(raw)
                    uploaded_name = payload.get("name") or filename
                except json.JSONDecodeError:
                    uploaded_name = filename
        if require_verified and not self._verify_comfy_input_image_name(uploaded_name):
            raise FileNotFoundError(f"Uploaded source image was not verified in Comfy input: {uploaded_name}")
        return uploaded_name

    def _ip_adapter_extension_block(self, extensions: object) -> dict[str, Any] | None:
        if not isinstance(extensions, dict):
            return None
        if isinstance(extensions.get("image.ip_adapter"), dict):
            return extensions.get("image.ip_adapter")
        for key in ("extensions", "payloads"):
            nested = extensions.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("image.ip_adapter"), dict):
                return nested.get("image.ip_adapter")
        return None

    def _replace_ip_adapter_extension_block(self, extensions: object, block: dict[str, Any]) -> object:
        if not isinstance(extensions, dict):
            return extensions
        cloned = deepcopy(extensions)
        if isinstance(cloned.get("image.ip_adapter"), dict):
            cloned["image.ip_adapter"] = block
            return cloned
        for key in ("extensions", "payloads"):
            nested = cloned.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("image.ip_adapter"), dict):
                nested["image.ip_adapter"] = block
                cloned[key] = nested
                return cloned
        cloned["image.ip_adapter"] = block
        return cloned

    def _layerdiffuse_extension_block(self, extensions: object) -> dict[str, Any] | None:
        if not isinstance(extensions, dict):
            return None
        if isinstance(extensions.get("image.layerdiffuse"), dict):
            return extensions.get("image.layerdiffuse")
        for key in ("extensions", "payloads"):
            nested = extensions.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("image.layerdiffuse"), dict):
                return nested.get("image.layerdiffuse")
        return None

    def _replace_layerdiffuse_extension_block(self, extensions: object, block: dict[str, Any]) -> object:
        if not isinstance(extensions, dict):
            return extensions
        cloned = deepcopy(extensions)
        if isinstance(cloned.get("image.layerdiffuse"), dict):
            cloned["image.layerdiffuse"] = block
            return cloned
        for key in ("extensions", "payloads"):
            nested = cloned.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("image.layerdiffuse"), dict):
                nested["image.layerdiffuse"] = block
                cloned[key] = nested
                return cloned
        cloned["image.layerdiffuse"] = block
        return cloned

    def _extensions_with_layerdiffuse_comfy_handoffs(self, extensions: object, *, run_id: str, params: dict[str, Any] | None = None) -> object:
        """Upload LayerDiffuse slot images to Comfy input before workflow replacement.

        LayerDiffuse image-conditioned modes use standard Comfy LoadImage nodes.
        The UI stores drag/drop uploads and preview handoffs as Neo-owned local
        paths when possible, but Comfy can only load input-folder filenames.
        This runtime-only handoff mirrors the img2img/IP Adapter path: copy/upload
        local slot images to Comfy, replace the executable slot value with the
        returned Comfy filename, and preserve the original refs in metadata.
        """
        block = self._layerdiffuse_extension_block(extensions)
        if not isinstance(block, dict) or not block.get("enabled"):
            return extensions
        inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
        params = dict(params or {})
        slot_fields = ("source_image_id", "background_image_id", "foreground_image_id", "blended_image_id", "replace_target_id")
        if not any(str(inputs.get(field) or block.get(field) or "").strip() for field in slot_fields):
            return extensions
        new_block = deepcopy(block)
        new_inputs = deepcopy(inputs)
        new_assets = deepcopy(new_block.get("assets") if isinstance(new_block.get("assets"), dict) else {})
        handoffs: list[dict[str, Any]] = []
        for field in slot_fields:
            raw_ref = str(inputs.get(field) or block.get(field) or "").strip()
            if not raw_ref:
                continue
            resolved_path = self._resolve_neo_image_ref_path(raw_ref)
            upload_path = None
            normalization: dict[str, Any] = {}
            if resolved_path is not None:
                upload_path, normalization = self._prepare_layerdiffuse_upload_image(
                    resolved_path,
                    params=params,
                    run_id=run_id,
                    field=field,
                )
            try:
                comfy_name = self._upload_image_to_comfy_input(raw_ref, require_verified=True, upload_path=upload_path)
            except Exception as exc:  # noqa: BLE001
                record_generation_error(
                    run_id=run_id,
                    message=f"Failed to upload LayerDiffuse {field} to Comfy input.",
                    exc=exc,
                    payload={"field": field, "source_image": raw_ref, "normalization": normalization},
                )
                raise
            if not comfy_name:
                continue
            new_inputs[field] = comfy_name
            handoffs.append({
                "field": field,
                "source": raw_ref,
                "source_path": str(resolved_path) if resolved_path else "",
                "upload_path": str(upload_path) if upload_path else "",
                "comfy_input_name": comfy_name,
                "exists_after_upload": True,
                "file_size_bytes": upload_path.stat().st_size if upload_path and upload_path.exists() else (resolved_path.stat().st_size if resolved_path and resolved_path.exists() else 0),
                "normalization": normalization,
            })
        if not handoffs:
            return extensions
        new_block["inputs"] = new_inputs
        new_assets["comfy_input_handoffs"] = handoffs
        new_assets["source_refs_before_comfy_handoff"] = {item["field"]: item["source"] for item in handoffs}
        new_block["assets"] = new_assets
        metadata = deepcopy(new_block.get("metadata") if isinstance(new_block.get("metadata"), dict) else {})
        metadata["comfy_input_handoff"] = True
        metadata["comfy_input_handoff_count"] = len(handoffs)
        new_block["metadata"] = metadata
        log_image_event("layerdiffuse_image_handoff", run_id=run_id, payload={"handoffs": handoffs})
        return self._replace_layerdiffuse_extension_block(extensions, new_block)

    def _extensions_with_ip_adapter_comfy_handoffs(self, extensions: object, *, run_id: str) -> object:
        """Upload Neo-owned IP Adapter reference images to Comfy input before compile.

        Phase 26.9.16 hardens standalone IPAdapter execution. A stale/missing
        optional reference image must not create a Comfy LoadImage node that later
        fails the whole queue with HTTP 400. Invalid optional units are disabled
        for this job and preserved only as metadata.
        """
        block = self._ip_adapter_extension_block(extensions)
        if not isinstance(block, dict) or not block.get("enabled"):
            return extensions
        inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
        units = inputs.get("units") if isinstance(inputs.get("units"), list) else []
        if not units:
            return extensions
        new_block = deepcopy(block)
        new_inputs = deepcopy(inputs)
        new_units: list[dict[str, Any]] = []
        handoffs: list[dict[str, Any]] = []
        disabled_units: list[dict[str, Any]] = []
        warnings: list[str] = []
        deduplication_units: list[dict[str, Any]] = []
        reference_assets = ((block.get("assets") or {}).get("reference_images") or {}) if isinstance(block.get("assets"), dict) else {}

        def unique_refs(values: list[Any]) -> list[str]:
            seen: set[str] = set()
            clean: list[str] = []
            for value in values:
                text = str(value or "").strip()
                key = text.replace("\\", "/").casefold()
                if not text or key in seen:
                    continue
                seen.add(key)
                clean.append(text)
            return clean

        for unit in units:
            if not isinstance(unit, dict):
                new_units.append(unit)
                continue
            new_unit = deepcopy(unit)
            raw_names = unit.get("image_names") if isinstance(unit.get("image_names"), list) else []
            if unit.get("image_name") and unit.get("image_name") not in raw_names:
                raw_names = [unit.get("image_name"), *raw_names]
            raw_names = unique_refs(raw_names)
            uid = str(unit.get("uid") or "unit_1")
            asset_bucket = (reference_assets.get(uid) or reference_assets.get("default")) if isinstance(reference_assets, dict) else None
            asset_items = asset_bucket if isinstance(asset_bucket, list) else [asset_bucket] if asset_bucket else []
            asset_refs: list[str] = []
            seen_asset_identities: set[str] = set()
            for item in asset_items:
                if isinstance(item, dict):
                    source_ref = str(item.get("ref") or item.get("path") or item.get("url") or item.get("id") or "").strip()
                    strong_identity = str(item.get("asset_id") or item.get("stored_filename") or "").strip().casefold()
                    normalized_source_ref = source_ref.replace("\\", "/").casefold()
                    identity_key = f"asset:{strong_identity}" if strong_identity else f"ref:{normalized_source_ref}"
                else:
                    source_ref = str(item or "").strip()
                    normalized_source_ref = source_ref.replace("\\", "/").casefold()
                    identity_key = f"ref:{normalized_source_ref}"
                if not source_ref or identity_key in seen_asset_identities:
                    continue
                seen_asset_identities.add(identity_key)
                asset_refs.append(source_ref)
            source_names = asset_refs or raw_names
            comfy_names: list[str] = []
            failed_refs: list[str] = []
            for image_ref in source_names:
                source = str(image_ref or "").strip()
                if not source:
                    continue
                try:
                    comfy_name = self._upload_image_to_comfy_input(source, require_verified=True)
                    if comfy_name:
                        if comfy_name.replace("\\", "/").casefold() not in {name.replace("\\", "/").casefold() for name in comfy_names}:
                            comfy_names.append(comfy_name)
                        if comfy_name != source:
                            handoffs.append({"source": source, "comfy_name": comfy_name, "unit": str(unit.get("uid") or "")})
                    else:
                        failed_refs.append(source)
                except Exception as exc:  # noqa: BLE001
                    failed_refs.append(source)
                    record_generation_error(
                        run_id=run_id,
                        message="Optional IP Adapter reference image was missing or unreadable; unit disabled for this job.",
                        exc=exc,
                        payload={"source": source, "unit": unit.get("uid"), "warning": "ipadapter_reference_image_missing_unit_disabled"},
                    )
            if not comfy_names:
                new_unit["enabled"] = False
                new_unit["image_names"] = []
                new_unit["image_name"] = ""
                new_unit.pop("_runtime_comfy_image_names", None)
                disabled_units.append({"uid": str(unit.get("uid") or ""), "missing_refs": failed_refs or source_names})
                warnings.append("ipadapter_reference_image_missing_unit_disabled")
            else:
                new_unit["image_names"] = comfy_names
                new_unit["image_name"] = comfy_names[0]
                new_unit["_runtime_comfy_image_names"] = list(comfy_names)
            deduplication_units.append({
                "uid": uid,
                "submitted_count": len(raw_names),
                "durable_asset_count": len(asset_refs),
                "source_count": len(source_names),
                "runtime_count": len(comfy_names),
                "removed_count": max(0, len(raw_names) - len(source_names)),
                "source_policy": "durable_assets" if asset_refs else "unit_references",
            })
            new_units.append(new_unit)
        active_units = [u for u in new_units if not isinstance(u, dict) or u.get("enabled", True)]
        if not any(isinstance(u, dict) and u.get("enabled", True) and (u.get("image_names") or u.get("image_name")) for u in new_units):
            new_block["enabled"] = False
            active_units = []
            warnings.append("ipadapter_all_reference_images_missing_extension_disabled")
        new_inputs["units"] = active_units
        new_block["inputs"] = new_inputs
        metadata = new_block.get("metadata") if isinstance(new_block.get("metadata"), dict) else {}
        new_block["metadata"] = {
            **metadata,
            "runtime_comfy_handoffs": handoffs,
            "asset_resolution": "neo_data_or_local_path_uploaded_to_comfy_input",
            "disabled_units": disabled_units,
            "warnings": sorted(set([*list(metadata.get("warnings") or []), *warnings])) if isinstance(metadata.get("warnings"), list) else sorted(set(warnings)),
            "reference_deduplication": {
                "policy": "durable_asset_identity_before_runtime_comfy_handoff",
                "unit_count": len(deduplication_units),
                "removed_count": sum(int(item.get("removed_count") or 0) for item in deduplication_units),
                "units": deduplication_units,
            },
            "phase_26_9_16_standalone_route_validation": True,
        }
        if handoffs or disabled_units:
            log_image_event("ip_adapter_reference_handoff", run_id=run_id, payload={"handoffs": handoffs, "disabled_units": disabled_units, "warnings": warnings})
        return self._replace_ip_adapter_extension_block(extensions, new_block)

    def _apply_non_checkpoint_extension_patches(
        self,
        compiled: CompiledJob,
        job: NeoJob,
        route: Any,
        *,
        # Historical source-lock anchor: allowed_extension_ids: tuple[str, ...] = ("lora_stack", "image.controlnet")
        allowed_extension_ids: tuple[str, ...] | None = None,
        fail_closed_on_unapplied_lora: bool = True,
    ) -> CompiledJob:
        """Apply shared workflow extension hooks to family-specific Comfy compilers.

        SDXL/SD1.5 checkpoint graphs are patched inline where they are built.
        Flux, Qwen, Z-Image, and HiDream compilers live in provider-owned modules,
        so this helper patches their returned backend payload without borrowing one
        family compiler from another. LoRA Stack only activates when the engine-independent family/loader/mode
        compatibility matrix allows it, the active compiler emits valid graph anchors,
        and Comfy exposes the required LoRA loader node.
        """
        if str(getattr(route, "mode", "") or job.mode or "").strip().lower() in {"inpaint", "outpaint"}:
            compiled = patch_masked_edit_workflow(
                compiled,
                job=job,
                route=route,
                backend_capabilities=self.discover_backend_capabilities(),
            )
        if not has_comfy_workflow_extension_requests(job.extensions):
            return compiled
        backend_payload = dict(compiled.backend_payload or {})
        workflow = backend_payload.get("prompt")
        if not isinstance(workflow, dict):
            return compiled
        actual_params = backend_payload.get("actual_params") if isinstance(backend_payload.get("actual_params"), dict) else {}
        route_payload = {
            **route.as_dict(),
            "comfy_base_url": self.base_url,
            "workflow_mode": "generate" if route.mode == "txt2img" else route.mode,
            "engine": str(actual_params.get("masked_edit_engine") or actual_params.get("inpaint_engine") or getattr(route, "engine", "") or "native"),
            "route_state": "available" if route.status == "available" else route.status,
            "params": actual_params,
            "actual_params": actual_params,
            "seed": actual_params.get("seed"),
            "actual_seed": actual_params.get("actual_seed", actual_params.get("seed")),
            "requested_seed": actual_params.get("requested_seed"),
            "width": actual_params.get("width"),
            "height": actual_params.get("height"),
            "sampler": actual_params.get("sampler"),
            "scheduler": actual_params.get("scheduler"),
            "cfg": actual_params.get("cfg"),
            "steps": actual_params.get("steps"),
            "denoise": actual_params.get("denoise"),
            "model": actual_params.get("model"),
        }
        # Do not run every shared extension hook here. Each extension enters this
        # provider-owned path only when its exact family/loader/mode contract is
        # active. High-Res Lab runs before ADetailer so the detail pass can
        # consume the refined image without borrowing another family compiler.
        if allowed_extension_ids is None:
            allowed_extension_ids = self._non_checkpoint_allowed_extension_ids(route_payload)
        patch_extensions = self._non_checkpoint_patch_extensions(job.extensions, allowed_extension_ids=allowed_extension_ids)
        if not patch_extensions:
            return compiled
        lora_patch_profile = actual_params.get("_neo_lora_patch_profile") if isinstance(actual_params.get("_neo_lora_patch_profile"), dict) else backend_payload.get("_neo_lora_patch_profile")
        adetailer_route_contract = actual_params.get("_neo_adetailer_route_contract") if isinstance(actual_params.get("_neo_adetailer_route_contract"), dict) else backend_payload.get("_neo_adetailer_route_contract")
        if tuple(allowed_extension_ids) == ("lora_stack",) and isinstance(lora_patch_profile, dict):
            profile_route = lora_patch_profile.get("route") if isinstance(lora_patch_profile.get("route"), dict) else {}
            compatibility_route_key = str(
                lora_patch_profile.get("compatibility_route_key")
                or profile_route.get("compatibility_route_key")
                or profile_route.get("route_key")
                or route_payload.get("route_key")
                or ""
            )
            workflow_route_key = str(
                lora_patch_profile.get("workflow_route_key")
                or profile_route.get("workflow_route_key")
                or compatibility_route_key
            )
            workflow_engine = str(
                lora_patch_profile.get("workflow_engine")
                or profile_route.get("workflow_engine")
                or profile_route.get("engine")
                or route_payload.get("engine")
                or "native"
            )
            route_payload["route_state"] = str(profile_route.get("route_state") or route_payload.get("route_state") or "experimental_available")
            route_payload["reason"] = str(profile_route.get("reason") or "")
            route_payload["route_key"] = compatibility_route_key
            route_payload["compatibility_route_key"] = compatibility_route_key
            route_payload["workflow_route_key"] = workflow_route_key
            route_payload["engine"] = workflow_engine
            route_payload["workflow_engine"] = workflow_engine
            route_payload["compatibility_engine_independent"] = True
        sampler_node_id = str((lora_patch_profile or {}).get("sampler_node_id") or actual_params.get("_neo_sampler_node_id") or self._find_primary_ksampler_node_id(workflow) or "8")
        patch_result = apply_comfy_workflow_extension_patches(
            workflow,
            extensions=patch_extensions,
            route=route_payload,
            available_nodes=self._object_info_for_extensions(patch_extensions, route=route_payload),
            cfg=actual_params.get("cfg", actual_params.get("sampler_cfg", 1.0)),
            model_ref=(lora_patch_profile or {}).get("model_ref") if isinstance(lora_patch_profile, dict) else None,
            clip_ref=(lora_patch_profile or {}).get("clip_ref") if isinstance(lora_patch_profile, dict) else None,
            sampler_node_id=sampler_node_id,
            sampler_model_input=str((lora_patch_profile or {}).get("sampler_model_input") or "model"),
            lora_patch_profile=lora_patch_profile if isinstance(lora_patch_profile, dict) else None,
            adetailer_route_contract=adetailer_route_contract if isinstance(adetailer_route_contract, dict) else None,
        )
        backend_payload["prompt"] = patch_result.get("workflow", workflow)
        extension_metadata = patch_result.get("extensions") or {"used": [], "payloads": {}, "workflow_patches": [], "validation": []}
        backend_payload["extensions"] = extension_metadata
        compile_status = compiled.compile_status
        if isinstance(actual_params, dict):
            actual_params = dict(actual_params)
            workflow_patches = extension_metadata.get("workflow_patches") if isinstance(extension_metadata.get("workflow_patches"), list) else []
            if workflow_patches:
                actual_params["extension_workflow_patches"] = workflow_patches
            lora_patch = next((item for item in workflow_patches if isinstance(item, dict) and item.get("extension_id") == "lora_stack"), None)
            if str(route_payload.get("engine") or "") == "lanpaint" and isinstance(lora_patch, dict):
                patched_nodes = list(lora_patch.get("patched_model_consumer_nodes") or [])
                differential_node = patched_nodes[0] if patched_nodes else ""
                actual_params["lanpaint_lora_lineage"] = {
                    "schema_version": "neo.image.lanpaint_lora_lineage.v1",
                    "engine": "lanpaint",
                    "family": str(route_payload.get("family") or "krea2_turbo"),
                    "loader": str(route_payload.get("loader") or "gguf"),
                    "route_key": str(route_payload.get("compatibility_route_key") or route_payload.get("route_key") or "krea2_turbo:gguf:inpaint"),
                    "compatibility_route_key": str(route_payload.get("compatibility_route_key") or route_payload.get("route_key") or "krea2_turbo:gguf:inpaint"),
                    "workflow_route_key": str(route_payload.get("workflow_route_key") or "krea2_turbo:gguf:inpaint:lanpaint"),
                    "workflow_engine": str(route_payload.get("workflow_engine") or route_payload.get("engine") or "lanpaint"),
                    "compatibility_engine_independent": True,
                    "support_matrix_state": str(route_payload.get("route_state") or "experimental_available"),
                    "lora_mode": str(actual_params.get("lanpaint_lora_mode") or ("model_only" if route_payload.get("family") == "krea2_turbo" else "model_and_clip")),
                    "lora_loader": str(lora_patch.get("node_class") or ("LoraLoaderModelOnly" if route_payload.get("family") == "krea2_turbo" else "LoraLoader")),
                    "lora_node_ids": list(lora_patch.get("node_ids") or []),
                    "stack_order": list(lora_patch.get("lora_names") or []),
                    "base_model_ref": deepcopy(lora_patch.get("previous_model_ref") or []),
                    "final_lora_model_ref": deepcopy(lora_patch.get("patched_model_ref") or []),
                    "family_model_transform_consumer_node": str(differential_node),
                    "differential_model_consumer_node": str(differential_node) if route_payload.get("family") == "krea2_turbo" else "",
                    "patched_model_consumer_nodes": patched_nodes,
                    "clip_patched": bool(lora_patch.get("patched_clip_encode_nodes")),
                    "physical_validation": "required",
                }
            if str(route_payload.get("engine") or "") == "lanpaint":
                actual_params = refresh_lanpaint_replay_contract(
                    actual_params,
                    provider_id=str(route_payload.get("provider_id") or self.manifest.provider_id),
                    workflow_prompt=backend_payload.get("prompt") if isinstance(backend_payload.get("prompt"), dict) else {},
                )
            if fail_closed_on_unapplied_lora:
                validation_payload = dict(backend_payload.get("validation") or {})
                compile_status = self._enforce_explicit_lora_execution(
                    extensions=patch_extensions,
                    route_payload=route_payload,
                    extension_metadata=extension_metadata,
                    validation_payload=validation_payload,
                    actual_params=actual_params,
                    compile_status=compile_status,
                )
                backend_payload["validation"] = validation_payload

            validation_payload = dict(backend_payload.get("validation") or {})
            compile_status, scene_director_proof = self._enforce_scene_director_execution(
                extensions=job.extensions,
                route_payload=route_payload,
                workflow=backend_payload.get("prompt") if isinstance(backend_payload.get("prompt"), dict) else {},
                extension_metadata=extension_metadata,
                validation_payload=validation_payload,
                actual_params=actual_params,
                compile_status=compile_status,
            )
            backend_payload["validation"] = validation_payload
            if isinstance(scene_director_proof, dict):
                backend_payload["scene_director_execution_proof"] = deepcopy(scene_director_proof)
            else:
                backend_payload.pop("scene_director_execution_proof", None)

            validation_payload = dict(backend_payload.get("validation") or {})
            compile_status, high_res_proof = self._enforce_high_res_execution(
                extensions=job.extensions,
                route_payload=route_payload,
                workflow=backend_payload.get("prompt") if isinstance(backend_payload.get("prompt"), dict) else {},
                extension_metadata=extension_metadata,
                validation_payload=validation_payload,
                actual_params=actual_params,
                compile_status=compile_status,
            )
            backend_payload["validation"] = validation_payload
            if isinstance(high_res_proof, dict):
                backend_payload["high_res_execution_proof"] = deepcopy(high_res_proof)
            else:
                backend_payload.pop("high_res_execution_proof", None)
            backend_payload["actual_params"] = actual_params
        return compiled.model_copy(update={"compile_status": compile_status, "backend_payload": backend_payload})

    def _apply_context_latent_hook(self, compiled: CompiledJob, job: NeoJob, route: Any) -> CompiledJob:
        """Patch the live Flux Kontext reference-latent adapter into inpaint.

        This hook is intentionally narrow.  The RMBG node consumes an existing
        conditioning object, latent, and mask; it cannot be treated as a
        generic background-removal or Qwen conditioning node.
        """
        backend_payload = dict(compiled.backend_payload or {})
        actual_params = dict(
            backend_payload.get("actual_params")
            if isinstance(backend_payload.get("actual_params"), dict)
            else (job.params or {})
        )
        request = normalize_context_latent(actual_params)
        if not request.get("enabled"):
            return compiled
        actual_params["_neo_context_latent"] = request

        def gated(message: str, state: str) -> CompiledJob:
            validation = dict(backend_payload.get("validation") or {})
            errors = list(validation.get("errors") or [])
            errors.append(message)
            validation["errors"] = errors
            validation["ok"] = False
            actual_params["_neo_context_latent_provider_hook_state"] = state
            actual_params["_neo_context_latent_provider_hook_reason"] = message
            backend_payload["validation"] = validation
            backend_payload["actual_params"] = actual_params
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})

        family = str(getattr(route, "family", "") or job.family or "").strip().lower()
        loader = str(getattr(route, "loader", "") or job.loader or "").strip().lower()
        mode = str(getattr(route, "mode", "") or job.mode or "").strip().lower()
        variant = str(actual_params.get("flux_variant") or actual_params.get("variant") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if family != "flux" or loader != "diffusion_model" or mode != "inpaint" or variant != "kontext":
            return gated(
                "Context latent assist is limited to Flux Kontext + Safetensors/Components + Inpaint; the selected route was not changed or silently downgraded.",
                "route_gated",
            )
        if not isinstance(backend_payload.get("prompt"), dict) or compiled.compile_status != "compiled":
            return gated("Context latent assist requires a compiled Comfy workflow before queueing.", "compile_gated_no_workflow")

        capabilities = self.discover_backend_capabilities()
        catalog = capabilities.get("context_latent") if isinstance(capabilities, dict) else {}
        resolved = resolve_context_latent(catalog, request.get("node_class"))
        if not resolved.get("ready"):
            return gated(str(resolved.get("reason") or "No live Flux Kontext reference-latent node is available."), "live_node_gated")

        workflow = dict(backend_payload.get("prompt") or {})
        sampler_candidates: list[tuple[int, str]] = []
        for raw_id, node in workflow.items():
            if not isinstance(node, dict) or str(node.get("class_type") or "") != "KSampler":
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if isinstance(inputs.get("positive"), list) and isinstance(inputs.get("latent_image"), list):
                try:
                    score = int(str(raw_id))
                except (TypeError, ValueError):
                    score = len(sampler_candidates)
                sampler_candidates.append((score, str(raw_id)))
        if not sampler_candidates:
            return gated("Context latent assist requires a KSampler with positive conditioning and a latent image input.", "workflow_gated_no_sampler")
        sampler_candidates.sort(reverse=True)
        sampler_id = sampler_candidates[0][1]
        sampler = workflow[sampler_id]
        sampler_inputs = sampler.get("inputs") if isinstance(sampler.get("inputs"), dict) else {}
        conditioning_ref = list(sampler_inputs.get("positive") or [])
        latent_ref = list(sampler_inputs.get("latent_image") or [])
        mask_ref: list[Any] | None = None
        mask_candidates: list[tuple[int, list[Any]]] = []
        for raw_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            class_type = str(node.get("class_type") or "")
            if class_type in {"SetLatentNoiseMask", "VAEEncodeForInpaint"} and isinstance(inputs.get("mask"), list):
                try:
                    score = int(str(raw_id))
                except (TypeError, ValueError):
                    score = len(mask_candidates)
                mask_candidates.append((score, list(inputs["mask"])))
        if mask_candidates:
            mask_candidates.sort(reverse=True)
            mask_ref = mask_candidates[0][1]
        if mask_ref is None:
            for raw_id, node in workflow.items():
                if isinstance(node, dict) and str(node.get("class_type") or "") == "LoadImageMask":
                    mask_ref = [str(raw_id), 0]
                    break
        if not mask_ref:
            return gated("Context latent assist requires the existing Inpaint mask; no live mask reference was found in the compiled graph.", "workflow_gated_no_mask")

        input_names = set(str(item) for item in (resolved.get("input_names") or []))
        node_inputs: dict[str, Any] = {
            "conditioning": conditioning_ref,
            "latent": latent_ref,
            "mask": mask_ref,
            "expand": int(request.get("expand") or 0),
            "blur": float(request.get("blur") or 0.0),
            "mask_only": bool(request.get("mask_only", True)),
        }
        missing = sorted(set(node_inputs).difference(input_names))
        if missing:
            return gated("The live context-latent node is missing required input(s): " + ", ".join(missing), "live_input_gated")
        node_id = self._next_workflow_node_id(workflow)
        workflow[node_id] = {
            "class_type": str(resolved.get("node_class") or ""),
            "inputs": node_inputs,
            "_meta": {
                "title": "Neo Flux Kontext Reference Latent Mask",
                "schema_id": "neo.image.background_removal.context_latent.v1",
                "source": "Image 1 latent + existing inpaint mask",
            },
        }
        sampler_inputs["positive"] = [node_id, 0]
        sampler_inputs["latent_image"] = [node_id, 1]
        sampler["inputs"] = sampler_inputs
        actual_params["_neo_context_latent_provider_hook_state"] = "workflow_patched"
        actual_params["_neo_context_latent_provider"] = {
            "schema_id": "neo.image.background_removal.context_latent.v1",
            "node_class": resolved.get("node_class"),
            "node_id": node_id,
            "conditioning_input": conditioning_ref,
            "latent_input": latent_ref,
            "mask_input": mask_ref,
            "patched_positive": [node_id, 0],
            "patched_latent": [node_id, 1],
            "route": {"family": family, "loader": loader, "mode": mode, "variant": variant},
            "policy": "live_exact_node_no_family_or_loader_fallback",
        }
        backend_payload["prompt"] = workflow
        backend_payload["actual_params"] = actual_params
        notes = list(backend_payload.get("phase_notes") or [])
        notes.append("RMBG-6 patched live Flux Kontext Reference Latent Mask using Image 1 latent context and the existing inpaint mask.")
        backend_payload["phase_notes"] = notes
        return compiled.model_copy(update={"backend_payload": backend_payload})

    def compile_job(self, job: NeoJob) -> CompiledJob:
        job = self._runtime_job(job)
        validation = self.validate_job(job)
        route = select_comfy_compile_route(job)
        if not route.can_compile:
            for blocker in route.blockers:
                if blocker not in validation.warnings:
                    validation.warnings.append(blocker)
            return CompiledJob(
                provider_id=self.manifest.provider_id,
                compile_status="mock_compiled",
                backend_payload={
                    "provider_id": self.manifest.provider_id,
                    "backend": "comfyui",
                    "validation": model_to_dict(validation),
                    "compile_route": route.as_dict(),
                    "neo_job": model_to_dict(job),
                    "phase_notes": [
                        "Phase 12.7 routes this family+loader+mode contract but does not enable advanced workflow compilers yet.",
                        "No Comfy prompt graph was generated for this route.",
                    ],
                },
            )
        if route.compiler_id == "comfy.lanpaint.family_aware.v1":
            # LanPaint is a complete standalone workflow. The shared LoRA Stack
            # hook is an optional interoperability seam that runs only after the
            # user explicitly enables a compatible base/global LoRA row.
            compiled = compile_lanpaint_family_inpaint(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_non_checkpoint_extension_patches(
                compiled,
                job,
                route,
                allowed_extension_ids=("lora_stack",),
                fail_closed_on_unapplied_lora=True,
            )

        if route.compiler_id in {"comfy.krea2", "comfy.krea2_gguf"}:
            compiled = compile_krea2_workflow(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        if route.compiler_id == "comfy.flux_fill":
            compiled = compile_flux_fill_workflow(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        if route.compiler_id == "comfy.flux_krea":
            compiled = compile_flux_krea_workflow(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        if route.compiler_id in {"comfy.flux_native", "comfy.flux_klein"}:
            params = job.params or {}
            flux_variant = str(params.get("flux_variant") or params.get("variant") or "").strip().lower().replace(" ", "_").replace("-", "_")
            if route.compiler_id == "comfy.flux_klein" or flux_variant in {"klein", "flux2_klein", "flux_2_klein", "klein_4b", "klein_9b", "klein_4b_distilled", "klein_9b_distilled"}:
                compiled = compile_flux_klein_txt2img(
                    provider_id=self.manifest.provider_id,
                    base_url=self.base_url,
                    job=job,
                    validation=validation,
                    route=route,
                    capabilities=self.feature_capability_payload(),
                )
            else:
                compiled = compile_flux_native_txt2img(
                    provider_id=self.manifest.provider_id,
                    base_url=self.base_url,
                    job=job,
                    validation=validation,
                    route=route,
                    capabilities=self.feature_capability_payload(),
                )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)
        if route.compiler_id in {"comfy.flux_gguf", "comfy.flux_gguf.klein", "comfy.flux_gguf.krea"}:
            compiled = compile_flux_gguf_txt2img(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        if route.compiler_id == "comfy.qwen_native":
            compiled = compile_qwen_native_txt2img(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        if route.compiler_id == "comfy.qwen_native_edit":
            compiled = compile_qwen_native_edit(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        if route.compiler_id == "comfy.qwen_rapid_aio_checkpoint":
            compiled = compile_qwen_rapid_aio_checkpoint(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        if route.compiler_id == "comfy.qwen_gguf":
            compiled = compile_qwen_gguf_txt2img(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)
        if route.compiler_id in {"comfy.z_image_native", "comfy.z_image_gguf"}:
            compiled = compile_z_image_txt2img(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)
        if route.compiler_id in {"comfy.hidream_native", "comfy.hidream_gguf"}:
            compiled = compile_hidream_txt2img(
                provider_id=self.manifest.provider_id,
                base_url=self.base_url,
                job=job,
                validation=validation,
                route=route,
                capabilities=self.feature_capability_payload(),
                backend_capabilities=self.discover_backend_capabilities(),
            )
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)
        if route.compiler_id in {"comfy.anima_native", "comfy.anima_gguf"}:
            compiled = compile_anima_image(provider_id=self.manifest.provider_id, base_url=self.base_url, job=job, validation=validation, route=route, capabilities=self.feature_capability_payload(), backend_capabilities=self.discover_backend_capabilities())
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)
        if route.compiler_id in {"comfy.ideogram4_native", "comfy.ideogram4_gguf"}:
            compiled = compile_ideogram4_txt2img(provider_id=self.manifest.provider_id, base_url=self.base_url, job=job, validation=validation, route=route, capabilities=self.feature_capability_payload(), backend_capabilities=self.discover_backend_capabilities())
            return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(self._apply_non_checkpoint_extension_patches(compiled, job, route), job, route), job, route)

        params = job.params or {}
        sd_defaults = resolve_sd_checkpoint_defaults(route.family)
        model = job.model or params.get("model") or "provider_default"
        vae = params.get("vae") or "automatic"
        sampler = params.get("sampler") or "euler"
        scheduler = params.get("scheduler") or "normal"
        requested_seed = int(params.get("requested_seed", params.get("seed", -1)))
        seed = int(params.get("actual_seed", params.get("seed", requested_seed)))
        if seed < 0:
            # Safety fallback for non-core callers. /api/image/generate resolves this before queue.
            seed = int(time.time() * 1000) % 2147483647
        conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
        conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
        clip_skip = normalize_comfy_clip_skip(params.get("clip_skip", sd_defaults.clip_skip))
        effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
        effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""
        actual_params = {
            **params,
            "prompt": effective_prompt,
            "positive_prompt": effective_prompt,
            "effective_positive": effective_prompt,
            "source_positive": job.prompt or "",
            "negative_prompt": effective_negative,
            "effective_negative": effective_negative,
            "source_negative": job.negative_prompt or "",
            "seed": seed,
            "actual_seed": seed,
            "requested_seed": requested_seed,
            "prompt_conditioning_mode": conditioning_mode,
            "clamp": conditioning_mode,
            "clip_skip": clip_skip["clip_skip"],
            "clip_skip_backend": clip_skip,
            "prompt_conditioning": {
                "mode": conditioning_mode,
                "display_mode": conditioning.get("display_mode"),
                "changed": bool(conditioning.get("changed")),
                "weighted_tags": int(conditioning.get("weighted_tags") or 0),
                "clamped_tags": int(conditioning.get("clamped_tags") or 0),
                "positive": conditioning.get("positive") or {},
                "negative": conditioning.get("negative") or {},
            },
        }

        runtime_mode = job.mode
        is_img2img = runtime_mode in {"img2img", "image_to_image"}
        is_inpaint = runtime_mode == "inpaint"
        is_outpaint = runtime_mode == "outpaint"
        source_image = self._source_image_value(params)
        mask_image = self._mask_image_value(params)
        source_image_name = str(params.get("comfy_source_image_name") or self._comfy_input_image_name(source_image))
        mask_image_name = str(params.get("comfy_mask_image_name") or self._comfy_input_image_name(mask_image))
        default_denoise = sd_defaults.denoise_inpaint if (is_inpaint or is_outpaint) else (sd_defaults.denoise_img2img if is_img2img else sd_defaults.denoise_txt2img)
        denoise = float(params.get("denoise", params.get("strength", default_denoise)))
        workflow_type = route.workflow_type or f"image.{runtime_mode}.{route.family}"
        outpaint_payload = normalize_outpaint_payload(params, default_width=int(params.get("width", sd_defaults.width) or sd_defaults.width), default_height=int(params.get("height", sd_defaults.height) or sd_defaults.height)) if is_outpaint else None
        actual_params.update({
            "workflow_type": workflow_type,
            "source_image": source_image,
            "mask_image": mask_image,
            "comfy_source_image_name": source_image_name,
            "comfy_mask_image_name": mask_image_name,
            "denoise": denoise,
            "mask_blur": int(params.get("mask_blur", 0) or 0),
            "mask_grow": int(params.get("mask_grow", params.get("grow_mask_by", 6)) or 0),
            **({
                "_neo_checkpoint_inpaint_mask_source": "LoadImageMask.red_channel",
                "_neo_checkpoint_inpaint_mask_normalized": True,
            } if is_inpaint and route.family in {"sdxl", "sd15"} else {}),
            **({"outpaint_payload": outpaint_payload, "_neo_outpaint_contract": outpaint_payload} if outpaint_payload else {}),
            "sd_checkpoint_family_profile": {
                "family": sd_defaults.family,
                "label": sd_defaults.workflow_label,
                "default_width": sd_defaults.width,
                "default_height": sd_defaults.height,
                "default_steps": sd_defaults.steps,
                "default_cfg": sd_defaults.cfg,
                "compiler": "comfy.checkpoint_sd",
            },
        })

        clip_source = ["1", 1]
        workflow: dict[str, Any] = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": model if model != "provider_default" else "model.safetensors"},
            },
        }
        if clip_skip["enabled"]:
            workflow["9"] = {
                "class_type": "CLIPSetLastLayer",
                "inputs": {"clip": ["1", 1], "stop_at_clip_layer": clip_skip["stop_at_clip_layer"]},
            }
            clip_source = ["9", 0]

        workflow.update({
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": effective_prompt, "clip": clip_source},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": effective_negative, "clip": clip_source},
            },
        })

        latent_source = ["4", 0]
        latent_source_override: list[Any] | None = None
        if is_img2img or is_inpaint or is_outpaint:
            if not source_image_name:
                validation.errors.append(f"{runtime_mode} requires a Comfy input image name after source handoff.")
                validation.ok = False
            workflow["4"] = {
                "class_type": "LoadImage",
                "inputs": {"image": source_image_name, "upload": "image"},
            }
            if is_outpaint:
                if not outpaint_payload or outpaint_padding_total(outpaint_payload) <= 0:
                    validation.errors.append("Outpaint requires padding on at least one side.")
                    validation.ok = False
                padding = (outpaint_payload or {}).get("padding", {}) if isinstance(outpaint_payload, dict) else {}
                mask = (outpaint_payload or {}).get("mask", {}) if isinstance(outpaint_payload, dict) else {}
                workflow["11"] = {
                    "class_type": "ImagePadForOutpaint",
                    "inputs": {
                        "image": ["4", 0],
                        "left": int(padding.get("left", 0) or 0),
                        "top": int(padding.get("top", 0) or 0),
                        "right": int(padding.get("right", 0) or 0),
                        "bottom": int(padding.get("bottom", 0) or 0),
                        "feathering": int(mask.get("feather", 16) or 16),
                    },
                }
                workflow["10"] = {
                    "class_type": "VAEEncodeForInpaint",
                    "inputs": {"pixels": ["11", 0], "vae": ["1", 2], "mask": ["11", 1], "grow_mask_by": 0},
                }
            elif is_inpaint:
                if not mask_image_name:
                    validation.errors.append("Inpaint requires a Comfy mask image name after mask handoff.")
                    validation.ok = False
                mask_grow = int(params.get("mask_grow", params.get("grow_mask_by", 6)) or 0)
                mask_blur = int(params.get("mask_blur", 0) or 0)
                selection_target_raw = str(params.get("inpaint_selection_target") or params.get("inpaint_mask_target") or "masked_area").strip().lower()
                selection_target = "not_masked_area" if selection_target_raw in {"not_masked", "not_masked_area", "inverse", "unmasked", "outside_mask"} else "masked_area"
                context_mode_raw = str(params.get("inpaint_context_mode") or params.get("inpaint_area") or "masked_region_focus").strip().lower()
                context_mode = "masked_region_focus" if context_mode_raw in {"masked_region_focus", "only_masked", "only_masked_region", "masked_only"} else "full_image_context"
                # Neo mask files are normal RGB/PNG mask images, not guaranteed
                # alpha-channel images. LoadImage's second output reads alpha,
                # which can become empty/no-op for RGB masks and made SDXL/SD1.5
                # inpaint appear to do nothing. Use LoadImageMask from the red
                # channel, then optionally normalize grow/blur. V25.9.16 restores
                # V1 parity controls: mask target can be inverted, and context can
                # be full-image latent context or masked-region inpaint encoder focus.
                workflow["11"] = {
                    "class_type": "LoadImageMask",
                    "inputs": {"image": mask_image_name, "channel": "red"},
                }
                mask_source = ["11", 0]
                next_mask_id = 12
                mask_inverted = selection_target == "not_masked_area"
                if mask_inverted:
                    workflow[str(next_mask_id)] = {
                        "class_type": "InvertMask",
                        "inputs": {"mask": mask_source},
                    }
                    mask_source = [str(next_mask_id), 0]
                    next_mask_id += 1
                if mask_grow > 0 or mask_blur > 0:
                    workflow[str(next_mask_id)] = {
                        "class_type": "GrowMaskWithBlur",
                        "inputs": {
                            "mask": mask_source,
                            "expand": mask_grow,
                            "blur_radius": mask_blur,
                            "tapered_corners": True,
                            "flip_input": False,
                            "fill_holes": False,
                            "incremental_expandrate": 0,
                            "lerp_alpha": 1,
                            "decay_factor": 1,
                        },
                    }
                    mask_source = [str(next_mask_id), 0]
                    next_mask_id += 1
                if context_mode == "masked_region_focus":
                    workflow["10"] = {
                        "class_type": "VAEEncodeForInpaint",
                        "inputs": {"pixels": ["4", 0], "vae": ["1", 2], "mask": mask_source, "grow_mask_by": 0},
                    }
                    actual_params["_neo_checkpoint_inpaint_encoder"] = "VAEEncodeForInpaint"
                else:
                    workflow["10"] = {
                        "class_type": "VAEEncode",
                        "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
                    }
                    workflow[str(next_mask_id)] = {
                        "class_type": "SetLatentNoiseMask",
                        "inputs": {"samples": ["10", 0], "mask": mask_source},
                    }
                    latent_source_override = [str(next_mask_id), 0]
                    actual_params["_neo_checkpoint_inpaint_encoder"] = "VAEEncode_SetLatentNoiseMask"
                actual_params.update({
                    "inpaint_selection_target": selection_target,
                    "inpaint_context_mode": context_mode,
                    "_neo_checkpoint_inpaint_selection_target": selection_target,
                    "_neo_checkpoint_inpaint_context_mode": context_mode,
                    "_neo_checkpoint_inpaint_mask_inverted": mask_inverted,
                    "_neo_checkpoint_inpaint_area_parity": {
                        "schema": "neo.image.checkpoint_inpaint_area_parity.v25_9_16",
                        "phase": "V25.9.16",
                        "selection_target": selection_target,
                        "context_mode": context_mode,
                        "mask_inverted": mask_inverted,
                        "mask_ref": list(mask_source),
                        "policy": "V1 parity: choose masked vs not-masked edit target and full-image vs masked-region context without changing Flux/Qwen provider routes.",
                    },
                })
            else:
                workflow["10"] = {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
                }
            latent_source = list(latent_source_override) if latent_source_override is not None else ["10", 0]
        else:
            workflow["4"] = {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": int(params.get("width", sd_defaults.width)),
                    "height": int(params.get("height", sd_defaults.height)),
                    "batch_size": int(params.get("batch_count", 1)),
                },
            }

        batch_count = int(params.get("batch_count", params.get("batch_size", 1)))
        if (is_img2img or is_inpaint or is_outpaint) and batch_count > 1:
            workflow["90"] = {"class_type": "RepeatLatentBatch", "inputs": {"samples": list(latent_source), "amount": batch_count}}
            latent_source = ["90", 0]

        workflow.update({
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": int(params.get("steps", sd_defaults.steps)),
                    "cfg": float(params.get("cfg", sd_defaults.cfg)),
                    "sampler_name": sampler if sampler != "provider_default" else "euler",
                    "scheduler": scheduler if scheduler != "provider_default" else "normal",
                    "denoise": denoise,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": latent_source,
                },
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
            },
            "7": {
                "class_type": "PreviewImage",
                "inputs": {"images": ["6", 0]},
            },
        })

        if vae not in {"automatic", "provider_default", ""}:
            workflow["8"] = {"class_type": "VAELoader", "inputs": {"vae_name": vae}}
            workflow["6"]["inputs"]["vae"] = ["8", 0]
            if is_img2img or is_inpaint or is_outpaint:
                workflow["10"]["inputs"]["vae"] = ["8", 0]

        if is_inpaint or is_outpaint:
            masked_compiled = patch_masked_edit_workflow(
                CompiledJob(
                    provider_id=self.manifest.provider_id,
                    compile_status="compiled" if validation.ok else "mock_compiled",
                    backend_payload={
                        "prompt": workflow,
                        "actual_params": actual_params,
                        "validation": model_to_dict(validation),
                    },
                ),
                job=job,
                route=route,
                backend_capabilities=self.discover_backend_capabilities(),
            )
            masked_payload = dict(masked_compiled.backend_payload or {})
            workflow = masked_payload.get("prompt") if isinstance(masked_payload.get("prompt"), dict) else workflow
            actual_params = masked_payload.get("actual_params") if isinstance(masked_payload.get("actual_params"), dict) else actual_params
            masked_validation = masked_payload.get("validation") if isinstance(masked_payload.get("validation"), dict) else {}
            for message in masked_validation.get("errors") or []:
                if message not in validation.errors:
                    validation.errors.append(str(message))
            for message in masked_validation.get("warnings") or []:
                if message not in validation.warnings:
                    validation.warnings.append(str(message))
            if masked_compiled.compile_status == "mock_compiled" or masked_validation.get("ok") is False:
                validation.ok = False

        route_payload = {
            **route.as_dict(),
            "comfy_base_url": self.base_url,
            "workflow_mode": "generate" if route.mode == "txt2img" else route.mode,
            "route_state": "available" if route.status == "available" else route.status,
            "params": actual_params,
            "actual_params": actual_params,
            "seed": actual_params.get("seed"),
            "actual_seed": actual_params.get("actual_seed", actual_params.get("seed")),
            "requested_seed": actual_params.get("requested_seed"),
            "width": actual_params.get("width"),
            "height": actual_params.get("height"),
            "sampler": actual_params.get("sampler"),
            "scheduler": actual_params.get("scheduler"),
            "cfg": actual_params.get("cfg"),
            "steps": actual_params.get("steps"),
            "denoise": actual_params.get("denoise"),
            "model": actual_params.get("model"),
        }
        checkpoint_lora_patch_profile = build_lora_patch_profile(
            route=route_payload,
            model_ref=["1", 0],
            clip_ref=["1", 1],
            sampler_node_id="5",
            sampler_model_input="model",
            loader_node_class="LoraLoader",
            source="comfy_provider.checkpoint_compiler",
            strategy="lora_loader_model_clip_chain",
            validated=True,
        )
        actual_params["_neo_lora_patch_profile"] = checkpoint_lora_patch_profile
        checkpoint_vae_ref = ["8", 0] if "8" in workflow and workflow.get("8", {}).get("class_type") == "VAELoader" else ["1", 2]
        checkpoint_adetailer_route_contract = build_adetailer_route_contract(
            route=route_payload,
            image_ref=list(actual_params.get("_neo_masked_edit_output_ref") or ["6", 0]),
            model_ref=["1", 0],
            clip_ref=["1", 1],
            vae_ref=checkpoint_vae_ref,
            positive_ref=["2", 0],
            negative_ref=["3", 0],
            sampler_node_id="5",
            model_sampling_ref=["1", 0],
            model_sampling_state="passthrough",
            source="comfy_provider.checkpoint_compiler",
            compiler_id=str(route_payload.get("compiler_id") or "comfy.checkpoint_sd"),
            validated=True,
            notes=["Phase 1 explicit route contract; no inferred checkpoint fallbacks are permitted downstream."],
        )
        actual_params["_neo_adetailer_route_contract"] = checkpoint_adetailer_route_contract
        runtime_extensions = self._extensions_with_legacy_controlnet_params(job.extensions, actual_params)
        extension_patch_result = apply_comfy_workflow_extension_patches(
            workflow,
            extensions=runtime_extensions,
            route=route_payload,
            available_nodes=self._object_info_for_extensions(runtime_extensions, route=route_payload),
            cfg=actual_params.get("cfg", sd_defaults.cfg),
            model_ref=["1", 0],
            clip_ref=["1", 1],
            sampler_node_id="5",
            sampler_model_input="model",
            lora_patch_profile=checkpoint_lora_patch_profile,
            adetailer_route_contract=checkpoint_adetailer_route_contract,
        )
        workflow = extension_patch_result["workflow"]
        extension_metadata = extension_patch_result.get("extensions") or {"used": [], "payloads": {}, "workflow_patches": [], "validation": [], "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}}

        # V1 parity safety net: ControlNet used to be submitted as top-level
        # queue params.  If stale V2 extension state still says disabled, force a
        # second ControlNet-only patch from those V1 fields instead of silently
        # dropping the feature.  This is deliberately checkpoint-path local and
        # does not change Flux/Qwen/GGUF routes.
        if self._legacy_controlnet_params_active(actual_params) and not self._controlnet_patch_applied(extension_metadata):
            legacy_controlnet_extensions = self._extensions_with_legacy_controlnet_params({}, actual_params)
            forced_controlnet_result = apply_comfy_workflow_extension_patches(
                workflow,
                extensions=legacy_controlnet_extensions,
                route=route_payload,
                available_nodes=self._object_info_for_extensions(legacy_controlnet_extensions, route=route_payload),
                cfg=actual_params.get("cfg", sd_defaults.cfg),
                model_ref=["1", 0],
                clip_ref=["1", 1],
                sampler_node_id="5",
                sampler_model_input="model",
                lora_patch_profile=checkpoint_lora_patch_profile,
                adetailer_route_contract=checkpoint_adetailer_route_contract,
            )
            workflow = forced_controlnet_result.get("workflow", workflow)
            extension_metadata = self._merge_extension_metadata(extension_metadata, forced_controlnet_result.get("extensions") or {})

        if extension_metadata.get("workflow_patches"):
            actual_params["extension_workflow_patches"] = extension_metadata.get("workflow_patches")

        checkpoint_validation_payload = model_to_dict(validation)
        checkpoint_compile_status = "compiled" if validation.ok else "mock_compiled"
        checkpoint_compile_status = self._enforce_explicit_lora_execution(
            extensions=runtime_extensions,
            route_payload=route_payload,
            extension_metadata=extension_metadata,
            validation_payload=checkpoint_validation_payload,
            actual_params=actual_params,
            compile_status=checkpoint_compile_status,
        )

        backend_payload = {
            "provider_id": self.manifest.provider_id,
            "backend": "comfyui",
            "base_url": self.base_url,
            "validation": checkpoint_validation_payload,
            "prompt": workflow,
            "client_id": f"neo-studio-v2-{uuid4().hex[:8]}",
            "actual_params": actual_params,
            "runtime_progress_source": "comfyui.websocket_and_history",
            "compile_route": route.as_dict(),
            "capabilities": self.feature_capability_payload(),
            "phase_notes": [
                "Phase 12.8 provider checkpoint compiler supports real SD 1.5 txt2img/img2img/inpaint routes.",
                "Basic checkpoint txt2img/img2img/inpaint graphs are supported for SDXL and SD 1.5 route contracts with family-specific defaults.",
                "Phase 12.19 treats outpaint as a dedicated mode with a normalized padding/mask/final-size payload.",
                "Neo may use PreviewImage for live preview and SaveImage for final backend-native handoff; Neo_Data remains the canonical results store.",
                f"Prompt conditioning mode: {conditioning_mode}.",
                f"Clip Skip: {clip_skip['clip_skip']} ({clip_skip['backend_mode']}).",
            ],
            "prompt_conditioning": conditioning,
            "extensions": extension_metadata,
        }
        debug_run_id = str(params.get("debug_run_id") or job.job_id or "").strip()
        if debug_run_id:
            backend_payload["debug_run_id"] = debug_run_id
            backend_payload["debug_log_paths"] = record_compiled_workflow(
                run_id=debug_run_id,
                provider_id=self.manifest.provider_id,
                backend_payload=backend_payload,
            )
        compiled_job = CompiledJob(
            provider_id=self.manifest.provider_id,
            compile_status=checkpoint_compile_status,
            backend_payload=backend_payload,
        )
        return self._apply_comfy_latent_capture_hook(self._apply_comfy_latent_branch_restore_hook(compiled_job, job, route), job, route)

    def run_job(self, job: NeoJob) -> ProviderRunResult:
        runtime_job = self._runtime_job(job)
        run_id = runtime_job.job_id or f"neo-run-{uuid4().hex[:8]}"
        log_image_event(
            "run_start",
            run_id=run_id,
            payload={
                "provider_id": self.manifest.provider_id,
                "family": runtime_job.family,
                "loader": runtime_job.loader,
                "mode": runtime_job.mode,
            },
        )
        params = dict(runtime_job.params or {})
        stale_warnings = list(params.get("_neo_route_validation_warnings") or []) if isinstance(params.get("_neo_route_validation_warnings"), list) else []
        if str(params.get("_neo_derived_action_type") or "").lower() == "img2img" and not (self._source_image_value(params) or params.get("comfy_source_image_name")):
            params["_neo_derived_action_type"] = "txt2img"
            params["_neo_preview_action_execution_cleared"] = True
            stale_warnings.append("stale_img2img_preview_action_cleared_no_source_image")
            preview_action = params.get("_neo_preview_action") if isinstance(params.get("_neo_preview_action"), dict) else {}
            if preview_action:
                cleaned_preview_action = dict(preview_action)
                cleaned_preview_action["execution_cleared"] = True
                cleaned_preview_action["execution_clear_reason"] = "stale_img2img_preview_action_cleared_no_source_image"
                params["_neo_preview_action"] = cleaned_preview_action
            params["_neo_route_validation_warnings"] = sorted(set(stale_warnings))
        if runtime_job.mode in {"img2img", "image_to_image", "inpaint", "outpaint", "edit"}:
            source_image = self._source_image_value(params)
            source_visibility = self._source_visibility_mask_payload(params)
            if source_image and source_visibility.get("enabled"):
                try:
                    original_source_image = source_image
                    source_image, visibility_metadata = self._prepare_visibility_masked_source(
                        source_image,
                        source_visibility,
                        run_id=run_id,
                        field="source_image",
                    )
                    params["source_image_original"] = original_source_image
                    params["source_image"] = source_image
                    params["source_image_path"] = source_image
                    params["source_image_name"] = Path(source_image).name
                    params["source_visibility_mask"] = visibility_metadata or source_visibility
                    params.pop("comfy_source_image_name", None)
                except Exception as exc:  # noqa: BLE001
                    record_generation_error(run_id=run_id, message="Failed to apply the source visibility mask.", exc=exc, payload={"source_image": source_image, "source_visibility_mask": source_visibility})
                    raise
            if source_image and not params.get("comfy_source_image_name"):
                try:
                    comfy_name = self._upload_image_to_comfy_input(source_image)
                    params["comfy_source_image_name"] = comfy_name
                    params["source_image_uploaded_to_comfy"] = bool(comfy_name)
                    log_image_event("source_image_handoff", run_id=run_id, payload={"source_image": source_image, "comfy_source_image_name": comfy_name, "visibility_mask_applied": bool(source_visibility.get("enabled"))})
                except Exception as exc:  # noqa: BLE001
                    record_generation_error(run_id=run_id, message="Failed to upload source image to Comfy input.", exc=exc, payload={"source_image": source_image})
                    raise
            krea2_identity_stack_active = (
                runtime_job.family in {"krea2", "krea2_turbo"}
                and runtime_job.loader in {"diffusion_model", "gguf"}
                and runtime_job.mode in {"img2img", "edit", "inpaint", "outpaint"}
                and str(params.get("krea2_edit_engine") or params.get("edit_engine") or "").strip().lower().replace("-", "_") in {"identity_edit", "krea2_identity_edit", "identity"}
            )
            extra_source_stack_active = (
                runtime_job.family in {"qwen_image_edit_2509", "qwen_image_edit_2511"} and runtime_job.loader in {"diffusion_model", "gguf"} and runtime_job.mode in {"img2img", "edit"}
            ) or (
                runtime_job.family in {"qwen_rapid_aio"} and runtime_job.loader == "gguf" and runtime_job.mode == "img2img"
            ) or (
                runtime_job.family == "flux" and runtime_job.loader == "gguf" and runtime_job.mode in {"img2img", "inpaint", "outpaint"}
            ) or krea2_identity_stack_active
            if extra_source_stack_active:
                if krea2_identity_stack_active:
                    stack_label = "krea2_identity_reference_handoff"
                    family_label = "Krea 2 Identity Edit"
                    source_lanes = (2,)
                else:
                    stack_label = "qwen_reference_image_handoff" if runtime_job.family in {"qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "qwen_image_edit_2511"} else "flux_reference_image_handoff"
                    family_label = "Qwen" if runtime_job.family in {"qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "qwen_image_edit_2511"} else "Flux"
                    source_lanes = (2, 3)
                for lane in source_lanes:
                    extra_source = self._extra_source_image_value(params, lane)
                    comfy_key = f"comfy_source_image_{lane}_name"
                    if extra_source and not params.get(comfy_key):
                        try:
                            comfy_extra = self._upload_image_to_comfy_input(extra_source)
                            params[comfy_key] = comfy_extra
                            params[f"source_image_{lane}_uploaded_to_comfy"] = bool(comfy_extra)
                            log_image_event(stack_label, run_id=run_id, payload={"lane": lane, "source_image": extra_source, comfy_key: comfy_extra})
                        except Exception as exc:  # noqa: BLE001
                            record_generation_error(run_id=run_id, message=f"Failed to upload {family_label} reference image{lane} to Comfy input.", exc=exc, payload={"lane": lane, "source_image": extra_source})
                            raise
            if runtime_job.mode == "inpaint":
                mask_image = self._mask_image_value(params)
                if mask_image and not params.get("comfy_mask_image_name"):
                    try:
                        comfy_mask = self._upload_image_to_comfy_input(mask_image)
                        params["comfy_mask_image_name"] = comfy_mask
                        params["mask_image_uploaded_to_comfy"] = bool(comfy_mask)
                        log_image_event("mask_image_handoff", run_id=run_id, payload={"mask_image": mask_image, "comfy_mask_image_name": comfy_mask})
                    except Exception as exc:  # noqa: BLE001
                        record_generation_error(run_id=run_id, message="Failed to upload inpaint mask to Comfy input.", exc=exc, payload={"mask_image": mask_image})
                        raise
            try:
                params = self._prepare_image_stitch_handoff(
                    params,
                    run_id=run_id,
                    family=runtime_job.family,
                    loader=runtime_job.loader,
                    mode=runtime_job.mode,
                )
            except Exception as exc:  # noqa: BLE001
                record_generation_error(run_id=run_id, message="Failed to upload Stitch Images inputs to Comfy.", exc=exc, payload={"family": runtime_job.family, "loader": runtime_job.loader, "mode": runtime_job.mode})
                raise
            runtime_job = runtime_job.copy(update={"job_id": run_id, "params": params})
        else:
            runtime_job = runtime_job.copy(update={"job_id": run_id})
        runtime_extensions = self._extensions_with_ip_adapter_comfy_handoffs(runtime_job.extensions, run_id=run_id)
        runtime_extensions = self._extensions_with_layerdiffuse_comfy_handoffs(runtime_extensions, run_id=run_id, params=params)
        if runtime_extensions is not runtime_job.extensions:
            runtime_job = runtime_job.copy(update={"extensions": runtime_extensions})
        compiled_payload_for_error: dict[str, Any] | None = None
        queue_payload_for_error: dict[str, Any] | None = None
        phase7_object_info: dict[str, Any] | None = None
        phase7_preflight = validate_image_feature_request(runtime_job, object_info=None, backend_reachable=True)
        if not phase7_preflight.get("ok"):
            message = "; ".join(str(item) for item in (phase7_preflight.get("errors") or [])) or "Image family compatibility preflight failed."
            record_generation_error(run_id=run_id, message=message, payload={"family_compatibility": phase7_preflight})
            return ProviderRunResult(
                job_id=run_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=message,
                runtime={"debug_logs": {"run_id": run_id}, "family_compatibility": phase7_preflight},
            )
        multi_request = (runtime_job.params or {}).get("multi_ksampler") if isinstance(runtime_job.params, dict) else {}
        live_phase7_check = bool(
            res4lyf_sampler_requested(runtime_job.params if isinstance(runtime_job.params, dict) else {})
            or (isinstance(multi_request, dict) and multi_request.get("enabled"))
            or (
                str(runtime_job.mode or "").strip().lower() in {"inpaint", "outpaint"}
                and bool((runtime_job.params or {}).get("crop_stitch_enabled") or (runtime_job.params or {}).get("masked_crop_stitch_enabled"))
            )
        )
        if live_phase7_check:
            try:
                raw_phase7_info = self._get_json("/object_info")
                phase7_object_info = raw_phase7_info if isinstance(raw_phase7_info, dict) else {}
            except Exception:
                phase7_object_info = {}
            phase7_preflight = validate_image_feature_request(runtime_job, object_info=phase7_object_info, backend_reachable=bool(phase7_object_info))
            if not phase7_preflight.get("ok"):
                message = "; ".join(str(item) for item in (phase7_preflight.get("errors") or [])) or "Image family compatibility preflight failed."
                record_generation_error(run_id=run_id, message=message, payload={"family_compatibility": phase7_preflight})
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=message,
                    runtime={"debug_logs": {"run_id": run_id}, "family_compatibility": phase7_preflight},
                )
        phase7_params = dict(runtime_job.params or {})
        if str(runtime_job.surface or "image").strip().lower() == "image":
            phase7_params["_neo_family_compatibility"] = phase7_preflight.get("compatibility") or {}
            runtime_job = runtime_job.copy(update={"params": phase7_params})
        params = phase7_params
        try:
            compiled = self.compile_job(runtime_job)
            compiled = self._patch_compiled_image_stitch(compiled, runtime_job)
            if isinstance(compiled.backend_payload, dict) and isinstance(compiled.backend_payload.get("prompt"), dict):
                try:
                    multi_graph, multi_actual, multi_meta = patch_multi_ksampler_workflow(
                        compiled.backend_payload.get("prompt"),
                        actual_params=compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload.get("actual_params"), dict) else dict(runtime_job.params or {}),
                        params=runtime_job.params if isinstance(runtime_job.params, dict) else {},
                    )
                    compiled.backend_payload["prompt"] = multi_graph
                    compiled.backend_payload["actual_params"] = multi_actual
                    compiled.backend_payload["multi_ksampler"] = multi_meta
                    if multi_meta.get("enabled"):
                        notes = list(compiled.backend_payload.get("phase_notes") or [])
                        notes.append(f"Multi-KSampler applied as {multi_meta.get('stage_count')} sequential latent refinement stages.")
                        compiled.backend_payload["phase_notes"] = notes
                except MultiKSamplerError as exc:
                    record_generation_error(
                        run_id=run_id,
                        message="Multi-KSampler validation blocked before Comfy queue.",
                        payload={"error": str(exc), "params": (runtime_job.params or {}).get("multi_ksampler") if isinstance(runtime_job.params, dict) else {}},
                    )
                    return ProviderRunResult(
                        job_id=run_id,
                        provider_id=self.manifest.provider_id,
                        status="failed",
                        message=str(exc),
                        runtime={"debug_logs": {"run_id": run_id}, "multi_ksampler": {"enabled": True, "state": "blocked", "error": str(exc)}},
                    )
                if res4lyf_sampler_requested(runtime_job.params if isinstance(runtime_job.params, dict) else {}):
                    try:
                        live_object_info = phase7_object_info if isinstance(phase7_object_info, dict) and phase7_object_info else self._get_json("/object_info")
                        res_graph, res_actual, res_meta = patch_res4lyf_sampler_backend(
                            compiled.backend_payload.get("prompt"),
                            actual_params=compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload.get("actual_params"), dict) else dict(runtime_job.params or {}),
                            params=runtime_job.params if isinstance(runtime_job.params, dict) else {},
                            object_info=live_object_info if isinstance(live_object_info, dict) else {},
                        )
                        compiled.backend_payload["prompt"] = res_graph
                        compiled.backend_payload["actual_params"] = res_actual
                        compiled.backend_payload["res4lyf_sampler"] = res_meta
                        notes = list(compiled.backend_payload.get("phase_notes") or [])
                        notes.append(f"RES4LYF ClownsharKSampler applied to stage(s): {', '.join(str(row.get('stage')) for row in (res_meta.get('stages') or []))}.")
                        compiled.backend_payload["phase_notes"] = notes
                    except Exception as exc:
                        # Keep this fail-closed: a selected custom sampler backend must never
                        # degrade silently to core KSampler when RES4LYF is missing or changed.
                        record_generation_error(
                            run_id=run_id,
                            message="RES4LYF ClownsharKSampler validation blocked before Comfy queue.",
                            payload={"error": str(exc), "sampler_backend": (runtime_job.params or {}).get("sampler_backend") if isinstance(runtime_job.params, dict) else ""},
                        )
                        return ProviderRunResult(
                            job_id=run_id,
                            provider_id=self.manifest.provider_id,
                            status="failed",
                            message=str(exc),
                            runtime={"debug_logs": {"run_id": run_id}, "res4lyf_sampler": {"enabled": True, "state": "blocked", "error": str(exc)}},
                        )
                multi_actual_for_verify = compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload.get("actual_params"), dict) else {}
                multi_meta_for_verify = multi_actual_for_verify.get("multi_ksampler") if isinstance(multi_actual_for_verify.get("multi_ksampler"), dict) else {}
                if multi_meta_for_verify.get("enabled"):
                    multi_verification = verify_multi_ksampler_workflow(
                        compiled.backend_payload.get("prompt") if isinstance(compiled.backend_payload.get("prompt"), dict) else {},
                        multi_actual_for_verify,
                    )
                    multi_actual_for_verify = dict(multi_actual_for_verify)
                    multi_meta_for_verify = dict(multi_actual_for_verify.get("multi_ksampler") or {})
                    multi_meta_for_verify["integrity"] = multi_verification
                    multi_actual_for_verify["multi_ksampler"] = multi_meta_for_verify
                    compiled.backend_payload["actual_params"] = multi_actual_for_verify
                    compiled.backend_payload["multi_ksampler"] = multi_meta_for_verify
                    if multi_verification.get("status") == "mismatch":
                        error_rows = multi_verification.get("mismatches") or []
                        message = "Multi-KSampler final graph integrity mismatch before Comfy queue."
                        if error_rows:
                            first = error_rows[0]
                            message += f" {first.get('field')}: {first.get('expected')} -> {first.get('observed')}."
                        record_generation_error(
                            run_id=run_id,
                            message=message,
                            payload={"multi_ksampler_integrity": multi_verification},
                        )
                        return ProviderRunResult(
                            job_id=run_id,
                            provider_id=self.manifest.provider_id,
                            status="failed",
                            message=message,
                            runtime={"debug_logs": {"run_id": run_id}, "multi_ksampler": multi_meta_for_verify},
                        )
            integrity_source = (runtime_job.params or {}).get("_neo_parameter_integrity") if isinstance(runtime_job.params, dict) else None
            if isinstance(integrity_source, dict) and isinstance(compiled.backend_payload, dict):
                integrity_actual = compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload.get("actual_params"), dict) else dict(runtime_job.params or {})
                integrity_workflow = compiled.backend_payload.get("prompt") if isinstance(compiled.backend_payload.get("prompt"), dict) else {}
                parameter_integrity = finalize_comfy_parameter_integrity(
                    integrity_source,
                    actual_params=integrity_actual,
                    workflow=integrity_workflow,
                )
                integrity_actual = {**integrity_actual, "_neo_parameter_integrity": parameter_integrity}
                compiled.backend_payload["actual_params"] = integrity_actual
                compiled.backend_payload["parameter_integrity"] = parameter_integrity
                integrity_mismatches = concrete_mismatches(parameter_integrity)
                if integrity_mismatches:
                    message = mismatch_message(parameter_integrity)
                    record_generation_error(
                        run_id=run_id,
                        message="Parameter integrity mismatch blocked before Comfy queue.",
                        payload={"parameter_integrity": parameter_integrity, "backend_payload": compiled.backend_payload},
                    )
                    return ProviderRunResult(
                        job_id=run_id,
                        provider_id=self.manifest.provider_id,
                        status="failed",
                        message=message,
                        runtime={
                            "debug_logs": {"run_id": run_id},
                            "parameter_integrity": parameter_integrity,
                            "actual_params": integrity_actual,
                        },
                    )
            compiled_payload_for_error = compiled.backend_payload
            record_compiled_workflow(run_id=run_id, provider_id=self.manifest.provider_id, backend_payload=compiled.backend_payload)
            validation = compiled.backend_payload.get("validation") or {}
            if validation and not validation.get("ok", False):
                message = "; ".join(validation.get("errors") or ["Provider validation failed"])
                record_generation_error(run_id=run_id, message="Provider validation failed before Comfy queue.", payload={"validation": validation, "backend_payload": compiled.backend_payload})
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=message,
                    runtime={"debug_logs": {"run_id": run_id}},
                )
            extension_meta_for_block = compiled.backend_payload.get("extensions") if isinstance(compiled.backend_payload.get("extensions"), dict) else {}
            extension_validation = extension_meta_for_block.get("validation") if isinstance(extension_meta_for_block.get("validation"), list) else []
            blocking_extension_errors = [item for item in extension_validation if isinstance(item, dict) and item.get("blocked") is True and item.get("ok") is False]
            if blocking_extension_errors:
                message = "; ".join(str(item.get("message") or item.get("reason") or "Extension graph validation failed") for item in blocking_extension_errors[:3])
                record_generation_error(run_id=run_id, message="Extension graph validation failed before Comfy queue.", payload={"extension_validation": blocking_extension_errors, "backend_payload": compiled.backend_payload})
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=message,
                    runtime={"debug_logs": {"run_id": run_id}, "extension_validation": blocking_extension_errors},
                )
            prompt_graph = compiled.backend_payload.get("prompt")
            client_id = compiled.backend_payload.get("client_id")
            if compiled.compile_status != "compiled" or not isinstance(prompt_graph, dict) or not prompt_graph:
                route_payload = compiled.backend_payload.get("compile_route") or {}
                blockers = route_payload.get("blockers") or []
                route_label = "+".join(str(route_payload.get(key) or "") for key in ("family", "loader", "mode")).strip("+")
                latent_validation = compiled.backend_payload.get("latent_branch_resume_validation") if isinstance(compiled.backend_payload.get("latent_branch_resume_validation"), dict) else {}
                route_actual_params = compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload.get("actual_params"), dict) else {}
                latent_reason = str(latent_validation.get("message") or route_actual_params.get("_neo_branch_restore_provider_hook_reason") or "").strip()
                message = latent_reason or "; ".join(blockers) or f"Route is not enabled for Comfy queue: {route_label or runtime_job.mode}."
                record_generation_error(
                    run_id=run_id,
                    message="Provider route was gated before Comfy queue.",
                    payload={"compile_route": route_payload, "backend_payload": compiled.backend_payload},
                )
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=message,
                    runtime={"debug_logs": {"run_id": run_id}, "compile_route": route_payload},
                )
            payload = {
                "prompt": prompt_graph,
                "client_id": client_id,
            }
            queue_payload_for_error = payload
            invalid_load_images = self._validate_prompt_load_images(prompt_graph, run_id=run_id)
            invalid_load_latents = self._validate_prompt_load_latents(prompt_graph, run_id=run_id)
            load_latent_node_count = sum(
                1
                for node in prompt_graph.values()
                if isinstance(node, dict) and node.get("class_type") == "LoadLatent"
            )
            latent_preflight_report = ({
                "schema_version": "neo.image.comfy_latent_replay_validation.v1",
                "ok": not invalid_load_latents,
                "state": "blocked_before_queue" if invalid_load_latents else "provider_artifact_available",
                "node_count": load_latent_node_count,
                "invalid_load_latents": invalid_load_latents,
            } if load_latent_node_count or invalid_load_latents else {})
            if latent_preflight_report:
                compiled.backend_payload["latent_branch_resume_validation"] = latent_preflight_report
            actual_params_for_handoff = compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload.get("actual_params"), dict) else {}
            qwen_stitch_metadata = actual_params_for_handoff.get("qwen_stitch") if isinstance(actual_params_for_handoff.get("qwen_stitch"), dict) else None
            if qwen_stitch_metadata is not None:
                actual_params_for_handoff = dict(actual_params_for_handoff)
                actual_params_for_handoff["qwen_stitch"] = record_qwen_stitch_comfy_handoff(prompt_graph, qwen_stitch_metadata)
                compiled.backend_payload["actual_params"] = actual_params_for_handoff
                queue_payload_for_error["qwen_stitch_handoff"] = actual_params_for_handoff["qwen_stitch"].get("provider_handoff", {})
            if invalid_load_images:
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message="source_image_missing_or_unreadable: one or more LoadImage references are missing or unreadable before Comfy queue.",
                    runtime={"debug_logs": {"run_id": run_id}, "invalid_load_images": invalid_load_images},
                )
            if invalid_load_latents:
                message = str(invalid_load_latents[0].get("message") or "Latent checkpoint is missing or unavailable in ComfyUI.")
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=message,
                    runtime={
                        "debug_logs": {"run_id": run_id},
                        "invalid_load_latents": invalid_load_latents,
                        "latent_branch_resume_validation": latent_preflight_report,
                    },
                )
            branch_resume = compiled.backend_payload.get("latent_branch_resume") if isinstance(compiled.backend_payload.get("latent_branch_resume"), dict) else None
            if branch_resume is not None:
                branch_resume["preflight_state"] = "provider_artifact_available"
                branch_resume["preflight_checked"] = True
                compiled.backend_payload["latent_branch_resume"] = branch_resume
                actual_params_for_handoff = dict(actual_params_for_handoff)
                branch_meta = actual_params_for_handoff.get("_neo_branch_restore") if isinstance(actual_params_for_handoff.get("_neo_branch_restore"), dict) else {}
                if branch_meta:
                    branch_meta = dict(branch_meta)
                    branch_meta["provider_reference_state"] = "verified_available"
                    actual_params_for_handoff["_neo_branch_restore"] = branch_meta
                provider_meta = actual_params_for_handoff.get("_neo_branch_restore_provider") if isinstance(actual_params_for_handoff.get("_neo_branch_restore_provider"), dict) else {}
                if provider_meta:
                    provider_meta = dict(provider_meta)
                    provider_meta["preflight_state"] = "provider_artifact_available"
                    actual_params_for_handoff["_neo_branch_restore_provider"] = provider_meta
                compiled.backend_payload["actual_params"] = actual_params_for_handoff
            # Phase 10.2: the shared Comfy GPU gate must be fed from the final
            # normalized provider payload on every successful compile path.  A
            # previous Phase-5 insertion only assigned this name inside the
            # *failed compile* branch, so normal image jobs could raise
            # UnboundLocalError before /prompt was ever queued.
            gate_payload = compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload, dict) else None
            if isinstance(gate_payload, dict):
                actual_params_for_gate = dict(gate_payload)
                gpu_gate_param_source = "compiled.actual_params"
            elif isinstance(runtime_job.params, dict):
                actual_params_for_gate = dict(runtime_job.params)
                gpu_gate_param_source = "runtime_job.params"
            else:
                actual_params_for_gate = {}
                gpu_gate_param_source = "safe_defaults"
            try:
                gpu_wait_timeout = float(actual_params_for_gate.get("gpu_wait_timeout_seconds") or 900.0)
            except (TypeError, ValueError):
                gpu_wait_timeout = 900.0
            if gpu_wait_timeout <= 0:
                gpu_wait_timeout = 900.0
            gpu_gate_profile_id = str(actual_params_for_gate.get("backend_profile_id") or actual_params_for_gate.get("profile_id") or "")
            compiled.backend_payload["gpu_lifecycle_gate"] = {
                "schema": "neo.image.comfy_gpu_lifecycle_gate.v1",
                "state": "ready",
                "parameter_source": gpu_gate_param_source,
                "wait_timeout_seconds": gpu_wait_timeout,
                "profile_id": gpu_gate_profile_id,
            }
            gpu_manager = get_comfy_gpu_lifecycle_manager()
            try:
                gpu_lease = gpu_manager.acquire(
                    base_url=self.base_url,
                    owner_kind="image",
                    owner_label=f"Image generation · {runtime_job.family or runtime_job.model or 'ComfyUI'}",
                    profile_id=gpu_gate_profile_id,
                    wait_timeout_seconds=gpu_wait_timeout,
                    metadata={"run_id": run_id, "family": runtime_job.family or "", "mode": runtime_job.mode or ""},
                )
            except ComfyGpuBusyError as exc:
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=str(exc),
                    runtime={"debug_logs": {"run_id": run_id}, "gpu_lifecycle": exc.status, "recoverable": True},
                )
            gpu_lease_token = str(gpu_lease.get("token") or "")
            # Phase 4.7.1 / R10D residency repair: normal Comfy generations keep
            # cached model objects resident. LayerDiffuse still gets a one-shot
            # safety reset, but only on the first normal run after an actually
            # queued LayerDiffuse workflow. The guard persists this tiny transition
            # bit across Neo restarts so safety does not require flushing every run.
            actual_params_for_cache_guard = compiled.backend_payload.get("actual_params") if isinstance(compiled.backend_payload.get("actual_params"), dict) else {}
            extension_meta_for_cache_guard = compiled.backend_payload.get("extensions") if isinstance(compiled.backend_payload.get("extensions"), dict) else {}
            contamination_reports = extension_meta_for_cache_guard.get("contamination_reports") if isinstance(extension_meta_for_cache_guard.get("contamination_reports"), dict) else {}
            layer_report = contamination_reports.get("image.layerdiffuse") if isinstance(contamination_reports.get("image.layerdiffuse"), dict) else {}
            residency_guard = get_comfy_model_residency_guard()
            layerdiffuse_requested_for_residency = bool(layer_report.get("layerdiffuse_requested"))
            residency_decision = residency_guard.prequeue_decision(
                self.base_url,
                layerdiffuse_requested=layerdiffuse_requested_for_residency,
                guard_enabled=actual_params_for_cache_guard.get("_neo_layerdiffuse_cache_guard", True) is not False,
            )
            if residency_decision.get("should_free"):
                try:
                    free_response = self._post_json("/free", {"unload_models": True, "free_memory": True}, allow_empty=True)
                    residency_guard.mark_free_succeeded(
                        self.base_url,
                        reason=str(residency_decision.get("reason") or "layerdiffuse_to_normal_transition"),
                        run_id=run_id,
                    )
                    log_image_event(
                        "layerdiffuse_cache_guard_free",
                        run_id=run_id,
                        payload={"response": free_response, "reason": residency_decision.get("reason"), "model_residency": residency_decision},
                    )
                except Exception as exc:  # noqa: BLE001
                    residency_guard.mark_free_failed(
                        self.base_url,
                        reason=str(residency_decision.get("reason") or "layerdiffuse_to_normal_transition"),
                        error=str(exc),
                        run_id=run_id,
                    )
                    log_image_event(
                        "layerdiffuse_cache_guard_free_failed",
                        run_id=run_id,
                        level="WARNING",
                        payload={"error": str(exc), "reason": residency_decision.get("reason"), "model_residency": residency_decision},
                    )
            else:
                log_image_event(
                    "comfy_model_residency_keep",
                    run_id=run_id,
                    payload={"reason": residency_decision.get("reason"), "model_residency": residency_decision},
                )
            record_queue_payload(run_id=run_id, request_payload=payload)
            try:
                response_payload = self._post_json("/prompt", payload)
            except Exception as exc:  # noqa: BLE001
                failure = classify_comfy_runtime_error(exc, phase="queue")
                if bool(failure.get("gpu_state_uncertain")):
                    gpu_manager.guard_unbound_after_queue_uncertainty(
                        gpu_lease_token,
                        cleanup_after=False,
                        reason=str(failure.get("message") or exc),
                    )
                else:
                    gpu_manager.release(gpu_lease_token, state="queue_failed")
                raise
            prompt_id = response_payload.get("prompt_id") or run_id or f"comfy-{uuid4().hex[:8]}"
            if layerdiffuse_requested_for_residency:
                residency_guard.mark_layerdiffuse_queued(self.base_url, prompt_id=str(prompt_id), run_id=run_id)
                residency_decision = {
                    **residency_decision,
                    "reset_required_after_queue": True,
                    "queued_layerdiffuse_prompt_id": str(prompt_id),
                }
            gpu_manager.bind_prompt(gpu_lease_token, prompt_id=prompt_id, cleanup_after=False, watch=True)
            record_compiled_workflow(run_id=prompt_id, provider_id=self.manifest.provider_id, backend_payload=compiled.backend_payload)
            record_queue_payload(run_id=prompt_id, request_payload=payload, response_payload=response_payload)
            if prompt_id != run_id:
                log_image_event("queue_prompt_id_assigned", run_id=run_id, payload={"prompt_id": prompt_id})
            actual_params = compiled.backend_payload.get("actual_params") or {}
            batch_total = int(actual_params.get("batch_count") or (runtime_job.params or {}).get("batch_count") or 1)
            poll_timeout_seconds = 0
            poll_interval_ms = 1500
            poll_timeout_seconds = int(compiled.backend_payload.get("poll_timeout_seconds") or actual_params.get("poll_timeout_seconds") or poll_timeout_seconds)
            poll_interval_ms = int(compiled.backend_payload.get("poll_interval_ms") or actual_params.get("poll_interval_ms") or poll_interval_ms)
            poll_max_attempts = 0 if poll_timeout_seconds <= 0 else max(1, int((poll_timeout_seconds * 1000 + poll_interval_ms - 1) // poll_interval_ms))
            poll_runtime = {"timeout_seconds": poll_timeout_seconds, "interval_ms": poll_interval_ms, "max_attempts": poll_max_attempts, "unlimited": poll_timeout_seconds <= 0}
            actual_params = {**actual_params, "poll_timeout_seconds": poll_timeout_seconds, "poll_interval_ms": poll_interval_ms}
            live_preview_enabled = not bool(actual_params.get("_neo_qwen_inpaint_final_composite"))
            runtime_capabilities = {**self.feature_capability_payload(), "live_preview": live_preview_enabled}
            runtime_extensions = compiled.backend_payload.get("extensions") if isinstance(compiled.backend_payload.get("extensions"), dict) else (runtime_job.extensions if isinstance(runtime_job.extensions, dict) else {})
            route_metadata = build_route_metadata(
                mode=runtime_job.mode,
                provider_id=self.manifest.provider_id,
                backend="comfyui",
                params=actual_params,
                model={"family": runtime_job.family, "loader": runtime_job.loader, "model": runtime_job.model, "vae": actual_params.get("vae", "")},
                extensions=runtime_extensions,
            )
            route_snapshot = build_route_snapshot(
                route_metadata=route_metadata,
                mode=runtime_job.mode,
                provider_id=self.manifest.provider_id,
                backend="comfyui",
                params=actual_params,
                model={"family": runtime_job.family, "loader": runtime_job.loader, "model": runtime_job.model, "vae": actual_params.get("vae", "")},
                extensions=runtime_extensions,
                compile_route=compiled.backend_payload.get("compile_route") if isinstance(compiled.backend_payload.get("compile_route"), dict) else {},
                workflow_prompt=prompt_graph,
            )
            workflow_node_map = {
                str(node_id): str(node.get("class_type") or "")
                for node_id, node in (prompt_graph or {}).items()
                if isinstance(node, dict)
            }
            queued_at = time.time()
            self._queued_jobs[prompt_id] = {
                "actual_params": actual_params,
                "workflow_node_map": workflow_node_map,
                "client_id": compiled.backend_payload.get("client_id"),
                "batch_total": batch_total,
                "started_at": queued_at,
                "started_at_iso": _image_timing_iso(queued_at),
                "queued_at_iso": _image_timing_iso(queued_at),
                "debug_run_id": prompt_id,
                "poll": poll_runtime,
                "family": runtime_job.family,
                "loader": runtime_job.loader,
                "route_metadata": route_metadata,
                "route_snapshot": route_snapshot,
                "extensions": runtime_extensions,
                "latent_branch_resume_validation": latent_preflight_report,
                "gpu_lifecycle": gpu_manager.status(group=gpu_lease.get("resource_group")),
                "model_residency": {**residency_decision, "state": residency_guard.status(self.base_url)},
            }
            registry_record = {}
            try:
                registry_record = self.job_registry.register_queued(
                    job_id=prompt_id,
                    surface=runtime_job.surface or "image",
                    provider_id=self.manifest.provider_id,
                    profile_id=str(actual_params.get("backend_profile_id") or actual_params.get("profile_id") or ""),
                    backend_profile_id=str(actual_params.get("backend_profile_id") or actual_params.get("profile_id") or ""),
                    provider_job_id=prompt_id,
                    local_job_id=run_id,
                    backend="comfyui",
                    mode=runtime_job.mode,
                    family=runtime_job.family or "",
                    loader=runtime_job.loader or "",
                    model=runtime_job.model or "",
                    client_id=compiled.backend_payload.get("client_id") or "",
                    submitted_job=model_to_dict(runtime_job),
                    compiled_backend_payload=compiled.backend_payload,
                    runtime=self._queued_jobs[prompt_id],
                    output_expectations={"kind": "image", "source": "comfyui.history", "batch_total": batch_total, "provider_job_id": prompt_id},
                    message="Queued in ComfyUI.",
                )
            except Exception as exc:  # noqa: BLE001
                log_image_event("job_registry_register_failed", run_id=prompt_id, level="WARNING", payload={"error": str(exc)})
            registry_summary = self._registry_summary(prompt_id) if registry_record else {"ok": False, "job_id": prompt_id}
            log_image_event("queued", run_id=prompt_id, payload={"batch_total": batch_total, "client_id": compiled.backend_payload.get("client_id"), "poll": poll_runtime, "job_registry": registry_summary})
            return ProviderRunResult(
                job_id=prompt_id,
                provider_id=self.manifest.provider_id,
                status="queued",
                message="Queued in ComfyUI.",
                outputs=[],
                client_id=compiled.backend_payload.get("client_id"),
                runtime={"base_url": self.base_url, "live_preview": live_preview_enabled, "debug_logs": {"run_id": prompt_id}, "capabilities": runtime_capabilities, "actual_params": actual_params, "parameter_integrity": actual_params.get("_neo_parameter_integrity") if isinstance(actual_params, dict) else None, "route_snapshot": route_snapshot, "workflow_node_map": workflow_node_map, "latent_branch_resume_validation": latent_preflight_report, "poll": {"timeout_seconds": poll_timeout_seconds, "interval_ms": poll_interval_ms, "max_attempts": poll_max_attempts}, "poll_metadata": {"poll_timeout_seconds": poll_timeout_seconds, "poll_interval_ms": poll_interval_ms}, "run_timing": _image_run_timing(self._queued_jobs.get(prompt_id), completed=False), "progress": {"source": "comfyui", "percent": 5, "label": "Queued in ComfyUI", "batch_total": batch_total, "batch_done": 0}, "job_registry": registry_summary, "gpu_lifecycle": gpu_manager.status(group=gpu_lease.get("resource_group"))},
            )
        except Exception as exc:  # noqa: BLE001
            error_payload: dict[str, Any] = {"job": model_to_dict(runtime_job)}
            if compiled_payload_for_error is not None:
                error_payload["compiled_backend_payload"] = compiled_payload_for_error
            if queue_payload_for_error is not None:
                error_payload["queue_payload"] = queue_payload_for_error
            http_error = self._http_error_details(exc)
            comfy_validation = self._comfy_http_validation_summary(http_error)
            if http_error:
                error_payload["http_error"] = http_error
            if comfy_validation:
                error_payload["comfy_validation"] = comfy_validation
            raw_error = str(exc or "")
            http_error_text = ""
            if http_error:
                try:
                    http_error_text = json.dumps(http_error, ensure_ascii=False, default=str)
                except Exception:  # noqa: BLE001
                    http_error_text = str(http_error)
            combined_error_text = (raw_error + "\n" + http_error_text).lower()
            is_qwen_size_nameerror = (
                "size is not defined" in combined_error_text
                and runtime_job.family in {"qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "qwen_image_edit_2511"}
                and runtime_job.loader == "gguf"
                and runtime_job.mode in {"img2img", "inpaint", "outpaint"}
            )
            if is_qwen_size_nameerror:
                qwen_diag = (compiled_payload_for_error or {}).get("actual_params", {}).get("qwen_edit_node_compatibility", {}) if isinstance(compiled_payload_for_error, dict) else {}
                error_payload["qwen_edit_node_compatibility"] = qwen_diag
                message = (
                    "Qwen Image Edit failed inside ComfyUI: `size is not defined`. "
                    "Neo already sent width/height; this points to an incompatible or half-patched "
                    "TextEncodeQwenImageEditPlus node in ComfyUI. Update/repair ComfyUI's "
                    "comfy_extras/nodes_qwen.py or install a compatible Qwen edit utility node, "
                    "then restart ComfyUI and refresh Neo."
                )
                record_generation_error(run_id=run_id, message=message, exc=exc, payload=error_payload)
                return ProviderRunResult(
                    job_id=run_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=message,
                    runtime={"debug_logs": {"run_id": run_id}, "qwen_edit_node_compatibility": qwen_diag},
                )
            queue_message = f"Failed to queue ComfyUI job: {exc}"
            if comfy_validation:
                node_label = str(comfy_validation.get("class_type") or "Comfy node")
                node_id = str(comfy_validation.get("node_id") or "")
                node_error = str(comfy_validation.get("node_error_message") or comfy_validation.get("error_message") or "Prompt validation failed").strip()
                details = str(comfy_validation.get("details") or "").strip()
                node_suffix = f" {node_id}" if node_id else ""
                queue_message = f"ComfyUI rejected {node_label}{node_suffix}: {node_error}"
                if details:
                    queue_message += f" — {details}"
            record_generation_error(run_id=run_id, message=queue_message, exc=exc, payload=error_payload)
            return ProviderRunResult(
                job_id=run_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=queue_message,
                runtime={
                    "debug_logs": {"run_id": run_id},
                    "http_error": http_error or None,
                    "comfy_validation": comfy_validation or None,
                },
            )

    def poll_job(self, job_id: str) -> ProviderRunResult:
        try:
            runtime = self._load_registered_runtime(job_id)
            poll_runtime = runtime.get("poll") or {"timeout_seconds": 0, "interval_ms": 1500, "max_attempts": 0, "unlimited": True}
            if self._registered_cancel_requested(job_id, runtime):
                runtime["cancel_requested"] = True
                log_image_event("poll_cancelled", run_id=job_id, payload={"batch_done": int(runtime.get("batch_done") or 0)})
                try:
                    self.job_registry.mark_cancelled(job_id, message="Generation stopped by user.", runtime=runtime)
                except Exception as exc:  # noqa: BLE001
                    log_image_event("job_registry_cancel_mark_failed", run_id=job_id, level="WARNING", payload={"error": str(exc)})
                return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="cancelled", message="Generation stopped by user.", runtime={"debug_logs": {"run_id": job_id}, "poll": poll_runtime, "progress": {"source": "comfyui.control", "percent": 100, "label": "Stopped", "batch_total": int(runtime.get("batch_total") or 1), "batch_done": int(runtime.get("batch_done") or 0)}, "actual_params": runtime.get("actual_params") or {}, "route_snapshot": runtime.get("route_snapshot") or {}, "extensions": runtime.get("extensions") or {}, "control": {"cancel_supported": True, "pause_supported": False}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)})
            poll_http_timeout = max(self.timeout, 30.0)
            history = self._get_json(f"/history/{parse.quote(job_id)}", timeout=poll_http_timeout)
            if not history or job_id not in history:
                history_all = self._get_json("/history", timeout=poll_http_timeout)
                if history_all and job_id in history_all:
                    history = history_all
            record_poll_payload(run_id=job_id, job_id=job_id, source="history", payload=history)
            if not history or job_id not in history:
                elapsed = max(0.0, time.time() - float(runtime.get("started_at") or time.time()))
                # History polling is backend-neutral fallback; the UI may receive
                # richer live progress from the provider websocket. Keep this
                # conservative so it never fights the live progress bar.
                percent = min(88, 20 + int(elapsed * 2))
                progress = {"source": "comfyui.history", "percent": percent, "label": "Running in ComfyUI", "batch_total": int(runtime.get("batch_total") or 1), "batch_done": 0}
                runtime["progress"] = progress
                try:
                    self.job_registry.mark_running(job_id, message="ComfyUI job still running or not in history yet.", runtime=runtime, progress=progress, poll_state={"history_has_job": bool(history and job_id in history)})
                except Exception as exc:  # noqa: BLE001
                    log_image_event("job_registry_running_mark_failed", run_id=job_id, level="WARNING", payload={"error": str(exc)})
                log_image_event("poll_running", run_id=job_id, payload={"percent": percent, "history_has_job": bool(history and job_id in history), "job_registry": self._registry_summary(job_id)})
                return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="running", message="ComfyUI job still running or not in history yet.", runtime={"debug_logs": {"run_id": job_id}, "poll": poll_runtime, "run_timing": _image_run_timing(runtime, completed=False), "progress": progress, "actual_params": runtime.get("actual_params") or {}, "route_snapshot": runtime.get("route_snapshot") or {}, "workflow_node_map": runtime.get("workflow_node_map") or {}, "extensions": runtime.get("extensions") or {}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)})
            history_item = history[job_id] if isinstance(history.get(job_id), dict) else {}
            backend_failure = _comfy_history_failure_details(history_item)
            if backend_failure:
                completed_at = time.time()
                runtime["completed_at"] = completed_at
                runtime["backend_error"] = backend_failure
                run_timing = _image_run_timing(runtime, completed=True)
                runtime["run_timing"] = run_timing
                progress = {
                    "source": "comfyui.history",
                    "percent": 100,
                    "label": "Failed in ComfyUI",
                    "batch_total": int(runtime.get("batch_total") or 1),
                    "batch_done": int(runtime.get("batch_done") or 0),
                }
                runtime["progress"] = progress
                message = str(backend_failure.get("summary") or "ComfyUI execution failed.")
                try:
                    self.job_registry.mark_failed(job_id, message=message, error=message, runtime=runtime)
                except Exception as exc:  # noqa: BLE001
                    log_image_event("job_registry_backend_failure_mark_failed", run_id=job_id, level="WARNING", payload={"error": str(exc)})
                log_image_event("poll_backend_execution_failed", run_id=job_id, level="ERROR", payload={"backend_error": backend_failure, "run_timing": run_timing, "job_registry": self._registry_summary(job_id)})
                record_generation_error(
                    run_id=job_id,
                    message=message,
                    payload={"job_id": job_id, "backend_error": backend_failure, "job_registry": self._registry_summary(job_id)},
                )
                get_comfy_gpu_lifecycle_manager().complete_prompt(base_url=self.base_url, prompt_id=job_id, state="failed", cleanup_after=False)
                return ProviderRunResult(
                    job_id=job_id,
                    provider_id=self.manifest.provider_id,
                    status="failed",
                    message=message,
                    outputs=[],
                    runtime={
                        "debug_logs": {"run_id": job_id},
                        "poll": poll_runtime,
                        "run_timing": run_timing,
                        "progress": progress,
                        "backend_error": backend_failure,
                        "actual_params": runtime.get("actual_params") or {},
                        "route_snapshot": runtime.get("route_snapshot") or {},
                        "workflow_node_map": runtime.get("workflow_node_map") or {},
                        "extensions": runtime.get("extensions") or {},
                        "capabilities": self.feature_capability_payload(),
                        "job_registry": self._registry_summary(job_id),
                    },
                )

            outputs = self._extract_outputs(job_id, history_item)
            runtime["batch_done"] = len(outputs)
            completed_at = time.time()
            runtime["completed_at"] = completed_at
            run_timing = _image_run_timing(runtime, completed=True)
            runtime["run_timing"] = run_timing
            if not outputs:
                progress = {"source": "comfyui.history", "percent": 100, "label": "Comfy completed — no output files found", "batch_total": int(runtime.get("batch_total") or 1), "batch_done": 0}
                runtime["progress"] = progress
                try:
                    self.job_registry.mark_output_import_state(
                        job_id,
                        surface="image",
                        status="completed_no_outputs_recoverable",
                        message="ComfyUI history exists, but no image outputs were found yet.",
                        outputs=[],
                        recoverable=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    log_image_event("job_registry_no_outputs_mark_failed", run_id=job_id, level="WARNING", payload={"error": str(exc)})
                log_image_event("poll_completed_no_outputs", run_id=job_id, payload={"run_timing": run_timing, "job_registry": self._registry_summary(job_id)})
                get_comfy_gpu_lifecycle_manager().complete_prompt(base_url=self.base_url, prompt_id=job_id, state="completed_no_outputs", cleanup_after=False)
                return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="completed_no_outputs_recoverable", message="ComfyUI completed, but Neo found no image outputs in history.", outputs=[], runtime={"debug_logs": {"run_id": job_id}, "poll": poll_runtime, "run_timing": run_timing, "progress": progress, "actual_params": runtime.get("actual_params") or {}, "route_snapshot": runtime.get("route_snapshot") or {}, "workflow_node_map": runtime.get("workflow_node_map") or {}, "extensions": runtime.get("extensions") or {}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)})
            progress = {"source": "comfyui.history", "percent": 100, "label": "Completed", "batch_total": int(runtime.get("batch_total") or len(outputs) or 1), "batch_done": len(outputs)}
            runtime["progress"] = progress
            try:
                self.job_registry.mark_completed(job_id, message="ComfyUI job completed.", outputs=outputs, runtime=runtime, progress=progress)
                self.job_registry.mark_output_import_state(job_id, surface="image", status="pending", message="Backend outputs found; waiting for Neo_Data import.", outputs=outputs, recoverable=True)
            except Exception as exc:  # noqa: BLE001
                log_image_event("job_registry_completed_mark_failed", run_id=job_id, level="WARNING", payload={"error": str(exc)})
            log_image_event("poll_completed", run_id=job_id, payload={"output_count": len(outputs), "output_nodes": [item.get("node_id") for item in outputs], "run_timing": run_timing, "job_registry": self._registry_summary(job_id)})
            get_comfy_gpu_lifecycle_manager().complete_prompt(base_url=self.base_url, prompt_id=job_id, state="completed", cleanup_after=False)
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="completed", message="ComfyUI job completed.", outputs=outputs, runtime={"debug_logs": {"run_id": job_id}, "poll": poll_runtime, "run_timing": run_timing, "progress": progress, "actual_params": runtime.get("actual_params") or {}, "route_snapshot": runtime.get("route_snapshot") or {}, "workflow_node_map": runtime.get("workflow_node_map") or {}, "extensions": runtime.get("extensions") or {}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)})
        except Exception as exc:  # noqa: BLE001
            runtime = self._load_registered_runtime(job_id)
            poll_runtime = runtime.get("poll") or {"timeout_seconds": 0, "interval_ms": 1500, "max_attempts": 0, "unlimited": True}
            if self._is_timeout_error(exc):
                elapsed = max(0.0, time.time() - float(runtime.get("started_at") or time.time()))
                percent = min(88, 20 + int(elapsed * 2))
                progress = {"source": "comfyui.history.retry", "percent": percent, "label": "Waiting for ComfyUI history", "batch_total": int(runtime.get("batch_total") or 1), "batch_done": int(runtime.get("batch_done") or 0)}
                runtime["progress"] = progress
                try:
                    self.job_registry.mark_running(job_id, message="ComfyUI history poll timed out; Neo will keep waiting and retry.", runtime=runtime, progress=progress, poll_state={"timeout": True, "error": str(exc)})
                except Exception as registry_exc:  # noqa: BLE001
                    log_image_event("job_registry_timeout_mark_failed", run_id=job_id, level="WARNING", payload={"error": str(registry_exc)})
                log_image_event("poll_retry_timeout", run_id=job_id, payload={"percent": percent, "error": str(exc), "job_registry": self._registry_summary(job_id)})
                return ProviderRunResult(
                    job_id=job_id,
                    provider_id=self.manifest.provider_id,
                    status="running",
                    message="ComfyUI history poll timed out; Neo will keep waiting and retry.",
                    runtime={
                        "debug_logs": {"run_id": job_id},
                        "poll": poll_runtime,
                        "progress": progress,
                        "actual_params": runtime.get("actual_params") or {},
                        "route_snapshot": runtime.get("route_snapshot") or {},
                        "extensions": runtime.get("extensions") or {},
                        "capabilities": self.feature_capability_payload(),
                        "job_registry": self._registry_summary(job_id),
                    },
                )
            try:
                self.job_registry.mark_failed(job_id, message=f"Failed to poll ComfyUI job: {exc}", error=str(exc), runtime=runtime)
            except Exception as registry_exc:  # noqa: BLE001
                log_image_event("job_registry_failed_mark_failed", run_id=job_id, level="WARNING", payload={"error": str(registry_exc)})
            record_generation_error(run_id=job_id, message="Failed to poll ComfyUI job.", exc=exc, payload={"job_id": job_id, "job_registry": self._registry_summary(job_id)})
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="failed", message=f"Failed to poll ComfyUI job: {exc}", runtime={"debug_logs": {"run_id": job_id}, "job_registry": self._registry_summary(job_id)})

    def cancel_job(self, job_id: str) -> ProviderRunResult:
        try:
            try:
                self._post_json("/interrupt", {}, allow_empty=True)
            except TypeError:
                # Backward-compatible fallback for tests/subclasses that still
                # expose the older two-argument helper signature.
                self._post_json("/interrupt", {})
            runtime = self._queued_jobs.setdefault(job_id, self._load_registered_runtime(job_id))
            runtime["cancel_requested"] = True
            runtime["cancelled_at"] = time.time()
            try:
                self.job_registry.request_cancel(job_id)
                self.job_registry.mark_cancelled(job_id, message="ComfyUI interrupt sent. Current generation was stopped.", runtime=runtime)
            except Exception as registry_exc:  # noqa: BLE001
                log_image_event("job_registry_cancel_failed", run_id=job_id, level="WARNING", payload={"error": str(registry_exc)})
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="cancelled",
                message="ComfyUI interrupt sent. Current generation was stopped.",
                runtime={"control": {"cancel_supported": True, "pause_supported": False}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)},
            )
        except Exception as exc:  # noqa: BLE001
            try:
                self.job_registry.mark_failed(job_id, message=f"Failed to interrupt ComfyUI job: {exc}", error=str(exc), runtime=self._load_registered_runtime(job_id))
            except Exception:  # noqa: BLE001
                pass
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=f"Failed to interrupt ComfyUI job: {exc}",
                runtime={"control": {"cancel_supported": True, "pause_supported": False}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)},
            )

    def pause_job(self, job_id: str) -> ProviderRunResult:
        return ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="running",
            message="ComfyUI does not support true pause through the standard API. Use Stop to interrupt the current run.",
            runtime={"control": {"cancel_supported": True, "pause_supported": False}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)},
        )

    def resume_job(self, job_id: str) -> ProviderRunResult:
        return ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="running",
            message="Resume is unavailable because ComfyUI standard pause is unsupported.",
            runtime={"control": {"cancel_supported": True, "pause_supported": False}, "capabilities": self.feature_capability_payload(), "job_registry": self._registry_summary(job_id)},
        )

    def fetch_outputs(self, job_id: str) -> list[dict[str, Any]]:
        result = self.poll_job(job_id)
        return result.outputs

    def _extract_outputs(self, job_id: str, history_item: dict[str, Any]) -> list[dict[str, Any]]:
        latent_outputs: list[dict[str, Any]] = []
        image_outputs: list[dict[str, Any]] = []
        runtime = self._load_registered_runtime(job_id)
        actual_params = runtime.get("actual_params") if isinstance(runtime.get("actual_params"), dict) else {}
        workflow_node_map = runtime.get("workflow_node_map") if isinstance(runtime.get("workflow_node_map"), dict) else {}
        for node_id, node_output in (history_item.get("outputs") or {}).items():
            for image in node_output.get("images") or []:
                query = parse.urlencode({
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                })
                image_outputs.append({
                    "job_id": job_id,
                    "node_id": node_id,
                    "kind": "image",
                    "filename": image.get("filename"),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                    "url": f"{self.base_url}/view?{query}",
                })
            for latent in node_output.get("latents") or node_output.get("latent") or []:
                if not isinstance(latent, dict):
                    continue
                raw_filename = str(latent.get("filename") or "").strip()
                raw_subfolder = str(latent.get("subfolder") or "").strip()
                raw_type = str(latent.get("type") or "output").strip() or "output"
                reference = None
                reference_error = ""
                try:
                    reference = normalize_comfy_artifact_reference(
                        filename=raw_filename,
                        subfolder=raw_subfolder,
                        artifact_type=raw_type,
                    )
                except ComfyArtifactPathError as exc:
                    # Keep the provider output importable so Neo can retain the
                    # bytes, but mark the provider coordinates as unusable for
                    # replay. persist_latent_outputs will record neo_copy_only.
                    reference_error = str(exc)

                query = parse.urlencode({
                    "filename": reference.filename if reference is not None else raw_filename,
                    "subfolder": reference.subfolder if reference is not None else raw_subfolder,
                    "type": reference.artifact_type if reference is not None else raw_type,
                })
                item = {
                    "job_id": job_id,
                    "node_id": node_id,
                    "kind": "latent",
                    "restore_point": self._infer_restore_point_from_latent_output(latent),
                    "filename": reference.filename if reference is not None else raw_filename,
                    "subfolder": reference.subfolder if reference is not None else raw_subfolder,
                    "type": reference.artifact_type if reference is not None else raw_type,
                    "format": "comfy_latent",
                    "url": f"{self.base_url}/view?{query}",
                    "provider_resume_ready": reference is not None,
                }
                if reference is not None:
                    item["comfy_load_name"] = reference.load_name
                    item["provider_reference"] = {
                        "filename": reference.filename,
                        "subfolder": reference.subfolder,
                        "type": reference.artifact_type,
                        "load_name": reference.load_name,
                    }
                if reference_error:
                    item["provider_reference_error"] = reference_error
                latent_outputs.append(item)

        preferred_node_ids: list[str] = []
        if isinstance(actual_params.get("_neo_output_import_node_ids"), list):
            preferred_node_ids = [str(item) for item in actual_params.get("_neo_output_import_node_ids") if str(item or "").strip()]
        if not preferred_node_ids:
            preferred_node_ids = [
                str(item.get("node_id"))
                for item in image_outputs
                if str(workflow_node_map.get(str(item.get("node_id"))) or "") in {"SaveImage", "ImageSave"}
            ]
        preferred_node_set = set(preferred_node_ids)
        if preferred_node_set:
            filtered_images = [item for item in image_outputs if str(item.get("node_id")) in preferred_node_set]
            if filtered_images:
                image_outputs = filtered_images
        else:
            backend_native_images = [item for item in image_outputs if str(item.get("type") or "").strip().lower() == "output"]
            if backend_native_images:
                image_outputs = backend_native_images

        preferred_rank = {node_id: index for index, node_id in enumerate(preferred_node_ids)}

        def _image_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
            node_id = str(item.get("node_id") or "")
            node_rank = preferred_rank.get(node_id, 9999)
            type_rank = 0 if str(item.get("type") or "").strip().lower() == "output" else 1
            try:
                numeric_node_id = int(node_id)
            except Exception:
                numeric_node_id = 10**9
            return (node_rank, type_rank, numeric_node_id, str(item.get("filename") or ""))

        image_outputs.sort(key=_image_sort_key)
        return [*image_outputs, *latent_outputs]
