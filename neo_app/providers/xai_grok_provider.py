from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.providers.base import BaseProvider
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderFeatureCapabilities, ProviderManifest, ProviderRunResult
from neo_app.runtime.job_registry import get_generation_job_registry

# Sync image APIs finish in one request, but the Image surface still polls after
# queueing. Keep those results available when the provider instance is rebuilt.
_XAI_GROK_IMAGE_JOBS: dict[str, ProviderRunResult] = {}

_ALLOWED_IMAGE_ASPECT_RATIOS = {
    "auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
    "2:1", "1:2", "19.5:9", "9:19.5", "20:9", "9:20",
}
_ALLOWED_IMAGE_RESOLUTIONS = {"1k", "2k"}
_ALLOWED_VIDEO_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
_ALLOWED_VIDEO_RESOLUTIONS = {"480p", "720p", "1080p"}
_IMAGE_MODES = {"txt2img", "generate", "img2img", "image_to_image", "multi_image_edit"}
_VIDEO_MODES = {"txt2vid", "text_to_video", "img2vid", "image_to_video"}
_XAI_PENDING_VIDEO_STATUSES = {"pending", "queued", "running", "processing", "in_progress"}


class XaiGrokProvider(BaseProvider):
    """Shared xAI adapter for Neo's existing Image and Video workspaces.

    P3 does not create Grok-specific workspaces. The active backend profile owns
    surface capability/parameter visibility while this provider owns strict API
    payloads, image sync completion, and durable asynchronous video polling.
    """

    def __init__(self, manifest: ProviderManifest, profile: dict[str, Any] | None = None) -> None:
        super().__init__(manifest)
        self.profile = profile or {}

    def feature_capabilities(self) -> ProviderFeatureCapabilities:
        if self._profile_surface() == "video":
            return ProviderFeatureCapabilities(
                progress=True,
                live_preview=False,
                cancel=False,
                pause=False,
                resume=False,
                clip_skip=False,
                prompt_conditioning=False,
                node_manager=False,
                output_handoff="neo_owned_cloud_output",
                progress_source="provider_polling",
                live_preview_source="none",
            )
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
        is_video = self._profile_surface() == "video"
        fallback = ["grok-imagine-video", "grok-imagine-video-1.5"] if is_video else ["grok-imagine-image", "grok-imagine-image-quality"]
        models = model_block.get("available_models") or fallback
        kind = "video_model" if is_video else "image_model"
        return [
            {"id": str(model), "name": str(model), "kind": kind, "provider_id": "xai_grok"}
            for model in models
            if str(model or "").strip()
        ]

    def compile_job(self, job: NeoJob) -> CompiledJob:
        try:
            payload = self._build_provider_payload(job)
            endpoint = self._endpoint_for_job(job)
            return CompiledJob(
                provider_id=self.manifest.provider_id,
                compile_status="compiled",
                backend_payload={
                    "endpoint": endpoint,
                    "payload": _redact_large_media(payload),
                    "mode": self._normalize_mode(job.mode, surface=job.surface),
                    "surface": job.surface,
                    "profile_id": self._profile_id(job),
                    "provider_polling": job.surface == "video",
                },
            )
        except Exception as exc:  # noqa: BLE001 - compile remains diagnostic.
            return CompiledJob(
                provider_id=self.manifest.provider_id,
                compile_status="mock_compiled",
                backend_payload={"error": str(exc), "mode": job.mode, "surface": job.surface},
            )

    def run_job(self, job: NeoJob) -> ProviderRunResult:
        if str(job.surface or "").strip().lower() == "video":
            return self._run_video_job(job)
        return self._run_image_job(job)

    def _run_image_job(self, job: NeoJob) -> ProviderRunResult:
        job_id = job.job_id or f"xai-grok-image-{uuid4().hex[:10]}"
        try:
            self._validate_runtime_profile(job)
            endpoint = self._endpoint_for_job(job)
            xai_payload = self._build_image_payload(job)
            raw_response = self._post_json(endpoint, xai_payload)
            outputs = self._normalize_image_outputs(raw_response, job_id=job_id)
            actual_params = self._image_actual_params(job, xai_payload)
            result = ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="completed",
                message=f"Grok Imagine {self._normalize_mode(job.mode, surface='image')} completed.",
                outputs=outputs,
                runtime={
                    "provider_id": "xai_grok",
                    "surface": "image",
                    "mode": self._normalize_mode(job.mode, surface="image"),
                    "endpoint": endpoint,
                    "actual_params": actual_params,
                    "route_snapshot": {
                        "provider_id": "xai_grok",
                        "backend": "cloud_api",
                        "family": "grok_imagine",
                        "loader": "api_model",
                        "mode": self._normalize_mode(job.mode, surface="image"),
                        "parameter_profile": "grok_imagine_image_api",
                    },
                    "extensions": self._extension_runtime_snapshot(job),
                    "source_images": actual_params.get("source_images") or [],
                    "respect_moderation": _extract_first(raw_response, "respect_moderation"),
                    "raw_response_keys": sorted(raw_response.keys()) if isinstance(raw_response, dict) else [],
                    "phase": "V25.9.20-P3-unified-workspace-routing",
                    "sync_completion": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 - provider result must be normalized.
            result = ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=str(exc),
                outputs=[],
                runtime={
                    "provider_id": "xai_grok",
                    "surface": "image",
                    "mode": self._normalize_mode(job.mode, surface="image"),
                    "error_type": exc.__class__.__name__,
                },
            )
        _XAI_GROK_IMAGE_JOBS[job_id] = result
        return result

    def _run_video_job(self, job: NeoJob) -> ProviderRunResult:
        job_id = job.job_id or f"xai-grok-video-{uuid4().hex[:12]}"
        try:
            self._validate_runtime_profile(job)
            endpoint = self._endpoint_for_job(job)
            xai_payload = self._build_video_payload(job)
            raw_response = self._post_json(endpoint, xai_payload)
            external_request_id = str(raw_response.get("request_id") or raw_response.get("id") or "").strip()
            if not external_request_id:
                raise ValueError("Grok Video did not return a request_id.")

            mode = self._normalize_mode(job.mode, surface="video")
            result_id = f"grok_video_{mode}_{uuid4().hex[:10]}"
            profile_id = self._profile_id(job)
            runtime = {
                "provider_id": "xai_grok",
                "surface": "video",
                "mode": mode,
                "endpoint": endpoint,
                "external_request_id": external_request_id,
                "result_id": result_id,
                "actual_params": self._video_actual_params(job, xai_payload),
                "route_snapshot": {
                    "provider_id": "xai_grok",
                    "backend": "cloud_api",
                    "family": "grok_imagine",
                    "loader": "api_model",
                    "mode": mode,
                    "parameter_profile": "grok_imagine_video_api",
                },
                "progress": {"percent": 5, "stage": "submitted", "label": "Submitted to Grok Video"},
                "phase": "V25.9.20-P3-unified-workspace-routing",
                "provider_polling": True,
            }
            registry = get_generation_job_registry()
            registry.register_queued(
                job_id=job_id,
                surface="video",
                provider_id="xai_grok",
                profile_id=profile_id,
                backend_profile_id=profile_id,
                provider_job_id=external_request_id,
                local_job_id=job_id,
                backend="cloud_api",
                mode=mode,
                family="grok_imagine",
                loader="api_model",
                model=str(xai_payload.get("model") or ""),
                submitted_job=model_to_dict(job),
                runtime=runtime,
                output_expectations={"kind": "video", "neo_owned_copy_required": True, "result_id": result_id},
                message="Grok Video request submitted.",
            )
            persisted = self._register_video_ledger(
                job=job,
                result_id=result_id,
                mode=mode,
                xai_payload=xai_payload,
                queued=True,
                external_request_id=external_request_id,
            )
            runtime["neo_persisted"] = persisted
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="queued",
                message="Grok Video request submitted. Neo will poll xAI and save the completed MP4 locally.",
                outputs=[],
                runtime=runtime,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                get_generation_job_registry().mark_failed(job_id, surface="video", message=str(exc), error=str(exc))
            except Exception:
                pass
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=str(exc),
                outputs=[],
                runtime={"provider_id": "xai_grok", "surface": "video", "error_type": exc.__class__.__name__},
            )

    def poll_job(self, job_id: str) -> ProviderRunResult:
        if job_id in _XAI_GROK_IMAGE_JOBS:
            return _XAI_GROK_IMAGE_JOBS[job_id]
        record = get_generation_job_registry().get(job_id, surface="video") or get_generation_job_registry().get(job_id)
        if record and str(record.get("provider_id") or "") == "xai_grok":
            return self._poll_video_job(job_id, record)
        return ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="failed",
            message="Grok job result was not found in Neo's runtime registry.",
            outputs=[],
        )

    def _poll_video_job(self, job_id: str, record: dict[str, Any]) -> ProviderRunResult:
        registry = get_generation_job_registry()
        current_status = str(record.get("status") or "").strip().lower()
        if current_status == "completed":
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="completed",
                message=str(record.get("message") or "Grok Video completed."),
                outputs=record.get("outputs") if isinstance(record.get("outputs"), list) else [],
                runtime=record.get("runtime") if isinstance(record.get("runtime"), dict) else {},
            )
        if current_status in {"failed", "cancelled"}:
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed" if current_status == "failed" else "cancelled",
                message=str(record.get("message") or record.get("error") or "Grok Video failed."),
                outputs=[],
                runtime=record.get("runtime") if isinstance(record.get("runtime"), dict) else {},
            )

        runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
        external_request_id = str(record.get("provider_job_id") or runtime.get("external_request_id") or "").strip()
        if not external_request_id:
            registry.mark_failed(job_id, surface="video", message="Grok Video request ID is missing.")
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="failed", message="Grok Video request ID is missing.")

        endpoint = self._video_poll_endpoint(external_request_id)
        try:
            raw = self._get_json(endpoint)
        except Exception as exc:  # keep transient network errors pollable unless auth/API failure is explicit.
            message = f"Grok Video poll failed: {exc}"
            registry.mark_running(
                job_id,
                surface="video",
                message=message,
                runtime={"external_request_id": external_request_id, "last_poll_error": str(exc)},
                progress={"percent": 15, "stage": "poll_retry", "label": "Waiting to retry Grok Video status"},
            )
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="running",
                message=message,
                outputs=[],
                runtime={**runtime, "external_request_id": external_request_id, "last_poll_error": str(exc), "progress": {"percent": 15, "stage": "poll_retry"}},
            )

        provider_status = str(raw.get("status") or "pending").strip().lower()
        if provider_status in _XAI_PENDING_VIDEO_STATUSES:
            previous_percent = _safe_int((record.get("progress") or {}).get("percent"), 10) if isinstance(record.get("progress"), dict) else 10
            percent = min(90, max(15, previous_percent + 5))
            progress = {"percent": percent, "stage": "generating", "label": "Grok Video is generating"}
            updated = registry.mark_running(
                job_id,
                surface="video",
                message="Grok Video is still generating.",
                runtime={"external_request_id": external_request_id, "provider_status": provider_status, "last_provider_response": _redact_large_media(raw)},
                progress=progress,
                poll_state={"provider_status": provider_status, "polled_at": _utc_now()},
            )
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="running",
                message="Grok Video is still generating.",
                outputs=[],
                runtime=updated.get("runtime") if isinstance(updated.get("runtime"), dict) else {**runtime, "progress": progress},
            )

        if provider_status in {"failed", "expired"}:
            detail = _compact_error_body(raw.get("error") or raw.get("message") or raw)
            message = f"Grok Video request {provider_status}: {detail or 'No provider detail was returned.'}"
            registry.mark_failed(
                job_id,
                surface="video",
                message=message,
                error=message,
                runtime={"external_request_id": external_request_id, "provider_status": provider_status, "last_provider_response": _redact_large_media(raw)},
            )
            self._update_failed_video_ledger(record, message)
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=message,
                outputs=[],
                runtime={**runtime, "provider_status": provider_status, "external_request_id": external_request_id},
            )

        if provider_status != "done":
            message = f"Grok Video returned unsupported status '{provider_status}'."
            registry.mark_failed(job_id, surface="video", message=message, error=message, runtime={"last_provider_response": _redact_large_media(raw)})
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="failed", message=message)

        video = raw.get("video") if isinstance(raw.get("video"), dict) else {}
        video_url = str(video.get("url") or raw.get("url") or "").strip()
        if not video_url:
            message = "Grok Video completed but did not return a video URL."
            registry.mark_failed(job_id, surface="video", message=message, error=message, runtime={"last_provider_response": _redact_large_media(raw)})
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="failed", message=message)

        result_id = str(runtime.get("result_id") or (record.get("output_expectations") or {}).get("result_id") or f"grok_video_{uuid4().hex[:10]}")
        mode = self._normalize_mode(record.get("mode") or "txt2vid", surface="video")
        try:
            local_path = self._download_video_output(video_url, result_id=result_id, mode=mode)
            job_data = record.get("submitted_job") if isinstance(record.get("submitted_job"), dict) else {}
            job = NeoJob(**job_data) if job_data else NeoJob(surface="video", subtab=mode, mode=mode, provider_id="xai_grok", family="grok_imagine", loader="api_model", params={})
            xai_payload = self._build_video_payload(job)
            persisted = self._register_video_ledger(
                job=job,
                result_id=result_id,
                mode=mode,
                xai_payload=xai_payload,
                queued=False,
                external_request_id=external_request_id,
                provider_response=raw,
            )
            files = (((persisted.get("record") or {}).get("outputs") or {}).get("files") or []) if isinstance(persisted, dict) else []
            outputs = [
                {
                    "kind": "video",
                    "provider_id": "xai_grok",
                    "filename": item.get("filename") or local_path.name,
                    "local_path": str(local_path),
                    "url": item.get("url") or "",
                    "metadata": {
                        "duration": video.get("duration"),
                        "respect_moderation": video.get("respect_moderation"),
                        "external_request_id": external_request_id,
                        "result_id": result_id,
                    },
                }
                for item in (files or [{"filename": local_path.name, "url": ""}])
            ]
            completed_runtime = {
                **runtime,
                "external_request_id": external_request_id,
                "provider_status": "done",
                "result_id": result_id,
                "neo_persisted": persisted,
                "provider_response": _redact_large_media(raw),
                "progress": {"percent": 100, "stage": "completed", "label": "Grok Video saved to Neo"},
            }
            registry.mark_completed(
                job_id,
                surface="video",
                message="Grok Video completed and was saved to Neo.",
                outputs=outputs,
                runtime=completed_runtime,
                progress=completed_runtime["progress"],
            )
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="completed",
                message="Grok Video completed and was saved to Neo.",
                outputs=outputs,
                runtime=completed_runtime,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"Grok Video completed, but Neo could not save the output: {exc}"
            registry.mark_failed(job_id, surface="video", message=message, error=message, runtime={"external_request_id": external_request_id, "provider_status": "done"})
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="failed", message=message, runtime={**runtime, "external_request_id": external_request_id})

    def fetch_outputs(self, job_id: str) -> list[dict[str, Any]]:
        image_result = _XAI_GROK_IMAGE_JOBS.get(job_id)
        if image_result:
            return model_to_dict(image_result).get("outputs", [])
        record = get_generation_job_registry().get(job_id, surface="video") or get_generation_job_registry().get(job_id)
        return record.get("outputs", []) if isinstance(record, dict) and isinstance(record.get("outputs"), list) else []

    def cancel_job(self, job_id: str) -> ProviderRunResult:
        image_result = _XAI_GROK_IMAGE_JOBS.get(job_id)
        if image_result and image_result.status == "completed":
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="completed", message="Grok Imagine image requests complete synchronously and cannot be cancelled.", outputs=image_result.outputs, runtime=image_result.runtime)
        record = get_generation_job_registry().get(job_id, surface="video") or get_generation_job_registry().get(job_id)
        if record:
            return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status=str(record.get("status") or "running"), message="xAI Video cancellation is not exposed in P3. The job remains active.", outputs=record.get("outputs") or [], runtime=record.get("runtime") or {})
        return ProviderRunResult(job_id=job_id, provider_id=self.manifest.provider_id, status="cancelled", message="No active Grok job was found.")

    def _validate_runtime_profile(self, job: NeoJob) -> None:
        surface = str(job.surface or "").strip().lower()
        if surface not in {"image", "video"}:
            raise ValueError(f"xAI Grok does not support Neo surface '{job.surface}' in P3.")
        profile_surface = self._profile_surface()
        if profile_surface and profile_surface != surface:
            raise ValueError(f"Backend profile '{self._profile_id(job)}' is bound to {profile_surface}, not {surface}.")
        if self.profile.get("enabled") is False:
            raise ValueError("Grok backend profile is disabled in Admin > Backends.")
        normalized = self._normalize_mode(job.mode, surface=surface)
        allowed = {"txt2img", "img2img", "multi_image_edit"} if surface == "image" else {"txt2vid", "img2vid"}
        if normalized not in allowed:
            raise ValueError(f"Grok Imagine {surface.title()} does not support mode '{job.mode}' in P3.")
        if not self.profile:
            raise ValueError(f"Grok backend profile is required. Select the seeded {surface.title()} Grok profile in Admin > Backends.")
        if str(self.profile.get("connection_type") or "").strip().lower() != "cloud_api":
            raise ValueError("Grok backend profile must use connection_type=cloud_api.")
        key_status = _resolve_api_key(self.profile)
        if not key_status.get("api_key_value"):
            env = key_status.get("api_key_env") or (self.profile.get("connection") or {}).get("api_key_env") or "XAI_API_KEY"
            linked = key_status.get("credential_profile_id") or (self.profile.get("connection") or {}).get("credential_profile_id") or ""
            linked_note = f" or save it on linked profile {linked}" if linked else ""
            raise ValueError(f"Grok API key is missing. Set {env}{linked_note} in Admin > Backends.")

    def _build_provider_payload(self, job: NeoJob) -> dict[str, Any]:
        return self._build_video_payload(job) if str(job.surface or "").lower() == "video" else self._build_image_payload(job)

    def _endpoint_for_job(self, job: NeoJob) -> str:
        defaults = self._defaults()
        surface = str(job.surface or self._profile_surface() or "image").strip().lower()
        normalized = self._normalize_mode(job.mode, surface=surface)
        if surface == "video":
            path = str(defaults.get("generation_path") or "/videos/generations")
        elif normalized == "txt2img":
            path = str(defaults.get("generation_path") or "/images/generations")
        else:
            path = str(defaults.get("edit_path") or "/images/edits")
        return self._absolute_endpoint(path)

    def _video_poll_endpoint(self, request_id: str) -> str:
        template = str(self._defaults().get("poll_path") or "/videos/{request_id}")
        return self._absolute_endpoint(template.replace("{request_id}", request_id))

    def _absolute_endpoint(self, path: str) -> str:
        base_url = str((self.profile.get("connection") or {}).get("base_url") or "https://api.x.ai/v1").rstrip("/")
        return f"{base_url}/{str(path or '').lstrip('/')}"

    def _build_image_payload(self, job: NeoJob) -> dict[str, Any]:
        params = job.params if isinstance(job.params, dict) else {}
        defaults = self._defaults()
        model_block = self.profile.get("model") if isinstance(self.profile.get("model"), dict) else {}
        available_models = [str(item).strip() for item in (model_block.get("available_models") or []) if str(item or "").strip()]
        model = str(job.model or params.get("model") or model_block.get("default_model") or "grok-imagine-image").strip()
        if available_models and model not in available_models:
            model = str(model_block.get("default_model") or available_models[0] or "grok-imagine-image").strip()
        prompt = str(job.prompt or params.get("prompt") or params.get("positive_prompt") or "").strip()
        if not prompt:
            raise ValueError("Prompt is required for Grok Imagine image requests.")
        payload: dict[str, Any] = {"model": model, "prompt": prompt}
        payload["n"] = max(1, min(10, _safe_int(params.get("n", params.get("image_count", defaults.get("n", 1))), 1)))
        aspect_ratio = str(params.get("aspect_ratio") or defaults.get("aspect_ratio") or "").strip()
        if aspect_ratio in _ALLOWED_IMAGE_ASPECT_RATIOS:
            payload["aspect_ratio"] = aspect_ratio
        resolution = str(params.get("resolution") or defaults.get("resolution") or "").strip().lower()
        if resolution in _ALLOWED_IMAGE_RESOLUTIONS:
            payload["resolution"] = resolution
        response_format = str(params.get("response_format") or defaults.get("response_format") or "b64_json").strip()
        if response_format in {"b64_json", "url"}:
            payload["response_format"] = response_format

        normalized_mode = self._normalize_mode(job.mode, surface="image")
        if normalized_mode != "txt2img":
            image_records = self._image_input_records(params, max_images=3)
            image_inputs = [record["payload"] for record in image_records]
            if not image_inputs:
                raise ValueError("Grok image edit requires a source image before generation.")
            if len(image_inputs) == 1:
                payload["image"] = image_inputs[0]
            else:
                payload["images"] = image_inputs[:3]
            payload["_neo_source_images"] = [record["metadata"] for record in image_records]
        return payload

    def _build_video_payload(self, job: NeoJob) -> dict[str, Any]:
        params = job.params if isinstance(job.params, dict) else {}
        defaults = self._defaults()
        model_block = self.profile.get("model") if isinstance(self.profile.get("model"), dict) else {}
        available_models = [str(item).strip() for item in (model_block.get("available_models") or []) if str(item or "").strip()]
        model = str(job.model or params.get("model") or params.get("model_name") or model_block.get("default_model") or "grok-imagine-video").strip()
        if available_models and model not in available_models:
            model = str(model_block.get("default_model") or available_models[0] or "grok-imagine-video").strip()
        prompt = str(job.prompt or params.get("prompt") or params.get("positive_prompt") or "").strip()
        if not prompt:
            raise ValueError("Prompt is required for Grok Video requests.")
        mode = self._normalize_mode(job.mode, surface="video")
        if mode not in {"txt2vid", "img2vid"}:
            raise ValueError(f"Unsupported Grok Video mode: {job.mode}")

        duration = max(1, min(15, _safe_int(params.get("duration_seconds", params.get("duration", defaults.get("duration_seconds", 4))), 4)))
        aspect_ratio = str(params.get("aspect_ratio") or defaults.get("aspect_ratio") or "16:9").strip()
        if aspect_ratio not in _ALLOWED_VIDEO_ASPECT_RATIOS:
            aspect_ratio = "16:9"
        resolution = str(params.get("resolution") or defaults.get("resolution") or "720p").strip().lower()
        if resolution not in _ALLOWED_VIDEO_RESOLUTIONS:
            resolution = "720p"
        if resolution == "1080p" and not (mode == "img2vid" and model == "grok-imagine-video-1.5"):
            raise ValueError("1080p Grok Video requires model grok-imagine-video-1.5 in Image-to-Video mode.")

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if mode == "img2vid":
            image_records = self._image_input_records(params, max_images=1, include_type=False)
            if not image_records:
                raise ValueError("Grok Image-to-Video requires a source image.")
            payload["image"] = image_records[0]["payload"]
            payload["_neo_source_images"] = [image_records[0]["metadata"]]
        return payload

    def _image_input_records(self, params: dict[str, Any], *, max_images: int = 3, include_type: bool = True) -> list[dict[str, Any]]:
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
            image_payload = {"url": url}
            if include_type:
                image_payload["type"] = "image_url"
            records.append({
                "payload": image_payload,
                "metadata": {
                    "lane": candidate.get("lane"),
                    "key": candidate.get("key"),
                    "name": str(candidate.get("name") or Path(value).name or f"source_image_{candidate.get('lane')}").strip(),
                    "role": str(candidate.get("role") or "reference").strip(),
                    "input_kind": kind,
                    "ref": value if not url.startswith("data:image/") else "local_file_data_uri",
                },
            })
            if len(records) >= max_images:
                break
        return records

    def _image_ref_to_url(self, value: str) -> str:
        if value.startswith(("http://", "https://", "data:image/")):
            return value
        path = Path(value).expanduser()
        if path.exists() and path.is_file():
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        # xAI Files API identifiers are accepted as the image URL value. Reject
        # path-looking strings so missing local uploads do not become fake file IDs.
        if "/" not in value and "\\" not in value and value.strip():
            return value.strip()
        raise ValueError(f"Source image not found: {value}")

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = {key: value for key, value in payload.items() if not str(key).startswith("_neo_")}
        return self._request_json(endpoint, method="POST", payload=clean)

    def _get_json(self, endpoint: str) -> dict[str, Any]:
        return self._request_json(endpoint, method="GET")

    def _request_json(self, endpoint: str, *, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        connection = self.profile.get("connection") if isinstance(self.profile.get("connection"), dict) else {}
        timeout = float(connection.get("timeout_seconds") or (900 if self._profile_surface() == "video" else 120))
        api_key = _resolve_api_key(self.profile).get("api_key_value")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Neo-Studio/V25.9.20-P3",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(endpoint, data=body, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - configured xAI API endpoint.
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
        if parsed.get("error") and str(parsed.get("status") or "").lower() not in {"failed", "expired"}:
            raise ValueError(f"Grok API error: {_compact_error_body(parsed.get('error'))}")
        return parsed

    def _download_video_output(self, video_url: str, *, result_id: str, mode: str) -> Path:
        from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part

        category = "img2vid" if mode == "img2vid" else "txt2vid"
        output_dir = get_video_output_paths(category, create=True).output_dir
        parsed_suffix = Path(urlparse(video_url).path).suffix.lower()
        suffix = parsed_suffix if parsed_suffix in {".mp4", ".webm", ".mov", ".mkv"} else ".mp4"
        target = output_dir / f"{sanitize_path_part(result_id, 'grok_video')}_1{suffix}"
        timeout = float((self.profile.get("connection") or {}).get("timeout_seconds") or 900)
        req = request.Request(video_url, headers={"User-Agent": "Neo-Studio/Grok-Video-Output-Import"})
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - provider-issued temporary output URL.
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/html" in content_type or "application/json" in content_type:
                    preview = response.read(500).decode("utf-8", errors="replace")
                    raise ValueError(f"Provider output URL returned {content_type}: {_compact_error_body(preview)}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
        except error.HTTPError as exc:
            raise ValueError(f"Grok Video output download failed ({exc.code}).") from exc
        except error.URLError as exc:
            raise ValueError(f"Grok Video output download failed: {exc.reason}") from exc
        if not target.exists() or target.stat().st_size <= 0:
            raise ValueError("Downloaded Grok Video output is empty.")
        return target

    def _normalize_image_outputs(self, raw: dict[str, Any], *, job_id: str) -> list[dict[str, Any]]:
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
                "metadata": {key: value for key, value in item.items() if key not in {"b64_json", "base64"}},
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

    def _register_video_ledger(
        self,
        *,
        job: NeoJob,
        result_id: str,
        mode: str,
        xai_payload: dict[str, Any],
        queued: bool,
        external_request_id: str,
        provider_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from neo_app.video.output_records import register_video_generation_result

        profile_id = self._profile_id(job)
        source_images = xai_payload.get("_neo_source_images") if isinstance(xai_payload.get("_neo_source_images"), list) else []
        request_payload = {
            "profile_id": profile_id,
            "route_id": f"xai_grok.api_model.{mode}",
            "family": "grok_imagine",
            "loader": "api_model",
            "generation_type": mode,
            "prompt": job.prompt or (job.params or {}).get("positive_prompt") or "",
            "negative_prompt": "",
            "source_image": (job.params or {}).get("source_image") or "",
            "source_image_name": (job.params or {}).get("source_image_name") or "",
        }
        result_payload = {
            "ok": True,
            "queued": queued,
            "result_id": result_id,
            "route_id": f"xai_grok.api_model.{mode}",
            "parameters": self._video_actual_params(job, xai_payload),
            "source": {
                "source_image": (job.params or {}).get("source_image") or "",
                "source_image_name": (job.params or {}).get("source_image_name") or "",
                "source_images": source_images,
            },
            "backend": {
                "profile": {"profile_id": profile_id, "provider_id": "xai_grok"},
                "profile_id": profile_id,
                "base_url": str((self.profile.get("connection") or {}).get("base_url") or "https://api.x.ai/v1"),
            },
            "queue_response": {"request_id": external_request_id} if queued else {},
            "provider_response": _redact_large_media(provider_response or {}),
        }
        if not queued and provider_response and isinstance(provider_response.get("video"), dict):
            result_payload["output_metadata"] = {
                "provider_id": "xai_grok",
                "external_request_id": external_request_id,
                "duration": provider_response["video"].get("duration"),
                "respect_moderation": provider_response["video"].get("respect_moderation"),
            }
        return register_video_generation_result(result_payload, request_payload)

    def _update_failed_video_ledger(self, record: dict[str, Any], message: str) -> None:
        try:
            from neo_app.video.output_records import register_video_generation_result

            runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
            submitted = record.get("submitted_job") if isinstance(record.get("submitted_job"), dict) else {}
            mode = self._normalize_mode(record.get("mode") or "txt2vid", surface="video")
            result_id = str(runtime.get("result_id") or (record.get("output_expectations") or {}).get("result_id") or "")
            register_video_generation_result(
                {
                    "ok": False,
                    "queued": False,
                    "result_id": result_id,
                    "route_id": f"xai_grok.api_model.{mode}",
                    "error": message,
                    "parameters": runtime.get("actual_params") if isinstance(runtime.get("actual_params"), dict) else {},
                    "backend": {"profile": {"profile_id": record.get("profile_id") or self.profile.get("profile_id") or ""}},
                },
                {
                    "profile_id": record.get("profile_id") or self.profile.get("profile_id") or "",
                    "route_id": f"xai_grok.api_model.{mode}",
                    "family": "grok_imagine",
                    "loader": "api_model",
                    "generation_type": mode,
                    "prompt": submitted.get("prompt") or "",
                },
            )
        except Exception:
            pass

    def _image_actual_params(self, job: NeoJob, xai_payload: dict[str, Any]) -> dict[str, Any]:
        params = dict(job.params or {})
        source_images = xai_payload.get("_neo_source_images") if isinstance(xai_payload.get("_neo_source_images"), list) else []
        params.update({
            "provider_id": "xai_grok",
            "api_endpoint": self._endpoint_for_job(job),
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
                "schema_version": "neo.xai_grok.replay_contract.v25_9_20_p3",
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
        params["unsupported_inline_controls"] = [
            key for key in ("steps", "cfg", "sampler", "scheduler", "seed", "denoise", "lora", "controlnet", "adetailer", "highres")
            if key in params
        ]
        return params

    def _video_actual_params(self, job: NeoJob, xai_payload: dict[str, Any]) -> dict[str, Any]:
        source_images = xai_payload.get("_neo_source_images") if isinstance(xai_payload.get("_neo_source_images"), list) else []
        return {
            "provider_id": "xai_grok",
            "profile_id": self._profile_id(job),
            "backend": "cloud_api",
            "model": xai_payload.get("model"),
            "mode": self._normalize_mode(job.mode, surface="video"),
            "duration_seconds": xai_payload.get("duration"),
            "aspect_ratio": xai_payload.get("aspect_ratio"),
            "resolution": xai_payload.get("resolution"),
            "source_image_count": len(source_images),
            "source_images": source_images,
            "unsupported_inline_controls": [
                key for key in ("negative_prompt", "steps", "guidance", "cfg", "sampler", "scheduler", "seed", "frames", "fps", "vae", "text_encoder", "vram_profile", "performance_profile")
                if key in (job.params or {})
            ],
        }

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

    def _profile_surface(self) -> str:
        return str(self.profile.get("surface") or "").strip().lower()

    def _profile_id(self, job: NeoJob | None = None) -> str:
        params = job.params if job and isinstance(job.params, dict) else {}
        return str(self.profile.get("profile_id") or params.get("backend_profile_id") or params.get("profile_id") or "").strip()

    @staticmethod
    def _normalize_mode(mode: Any, *, surface: str) -> str:
        clean = str(mode or "").strip().lower()
        if surface == "video":
            if clean in {"txt2vid", "text_to_video", "text-to-video", "t2v", "generate"}:
                return "txt2vid"
            if clean in {"img2vid", "image_to_video", "image-to-video", "i2v"}:
                return "img2vid"
            return clean
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


def _redact_large_media(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload or {}, default=str))

    def scrub_image(item: Any) -> Any:
        if isinstance(item, dict) and str(item.get("url") or "").startswith("data:image/"):
            return {**item, "url": "data:image/*;base64,<redacted>"}
        return item

    if isinstance(redacted.get("image"), dict):
        redacted["image"] = scrub_image(redacted["image"])
    if isinstance(redacted.get("images"), list):
        redacted["images"] = [scrub_image(item) for item in redacted["images"]]
    return redacted


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_api_key(profile: dict[str, Any]) -> dict[str, Any]:
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
        env_name = str(connection.get("api_key_env") or "XAI_API_KEY").strip() or "XAI_API_KEY"
        if auth_mode == "env":
            value = str(os.getenv(env_name) or "").strip()
            return {"auth_mode": "env", "api_key_is_configured": bool(value), "api_key_source": "env", "api_key_env": env_name, "api_key_value": value}
        value = str(connection.get("api_key_value") or os.getenv(env_name) or "").strip()
        return {"auth_mode": "manual", "api_key_is_configured": bool(value), "api_key_source": "manual", "api_key_env": env_name, "api_key_value": value}
