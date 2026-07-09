"""Provider-facing error normalization for the Image surface.

Image Stabilization Pass 08 keeps provider/runtime behavior unchanged, but stops raw
backend exception text from leaking into the UI.  The helpers here return small,
local-only diagnostic payloads that the frontend can render with useful recovery
copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ImageProviderError:
    code: str
    title: str
    message: str
    recovery_actions: tuple[str, ...]
    raw_detail: str = ""

    def to_payload(self, *, operation: str, profile_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "status": "failed",
            "schema": "neo.image.provider_error.v1",
            "error_code": self.code,
            "title": self.title,
            "message": self.message,
            "detail": self.message,
            "operation": operation,
            "recovery_actions": list(self.recovery_actions),
        }
        if profile_id:
            payload["profile_id"] = profile_id
        if job_id:
            payload["job_id"] = job_id
        if self.raw_detail:
            payload["raw_detail"] = self.raw_detail[:500]
        return payload


def _stringify_error(error: BaseException | str | dict[str, Any] | Any) -> str:
    if isinstance(error, dict):
        for key in ("message", "detail", "error", "reason"):
            value = error.get(key)
            if value:
                return str(value)
        return str(error)
    return str(error or "").strip()


def normalize_image_provider_error(
    error: BaseException | str | dict[str, Any] | Any,
    *,
    operation: str = "image_generation",
    profile_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Convert provider/runtime failures into stable UI-safe Image errors."""

    raw = _stringify_error(error)
    lowered = raw.lower()

    if not raw:
        normalized = ImageProviderError(
            "image_provider_unknown_error",
            "Image provider failed",
            "The active image provider failed, but did not return a useful error message.",
            (
                "Check the active backend profile.",
                "Confirm the provider is running.",
                "Open Admin → Packaging / Runtime Hardening for environment checks.",
            ),
        )
    elif any(token in lowered for token in ("connection refused", "failed to establish", "connection error", "max retries", "winerror 10061", "connect call failed")):
        normalized = ImageProviderError(
            "image_provider_unreachable",
            "Image provider is not reachable",
            "The active image backend is not reachable. The provider may be offline or the configured URL may be wrong.",
            (
                "Start ComfyUI or the selected image backend.",
                "Check the backend URL/profile in Admin.",
                "Refresh provider status, then try Generate again.",
            ),
            raw,
        )
    elif any(token in lowered for token in ("timeout", "timed out", "read timed out", "deadline")):
        normalized = ImageProviderError(
            "image_provider_timeout",
            "Image provider timed out",
            "The active image backend did not respond before the request timed out.",
            (
                "Check whether the backend is still generating or stuck.",
                "Try a smaller image, fewer steps, or a lighter workflow.",
                "Restart the backend if it is not responding.",
            ),
            raw,
        )
    elif any(token in lowered for token in ("model not found", "checkpoint not found", "missing model", "no such model", "cannot find model")):
        normalized = ImageProviderError(
            "image_model_missing",
            "Required model is missing",
            "The image backend could not find a required checkpoint/model for this workflow.",
            (
                "Select an installed model/checkpoint.",
                "Rescan backend models in Admin if the file was added recently.",
                "Check custom node/model paths if this is a Comfy workflow.",
            ),
            raw,
        )
    elif "size is not defined" in lowered and ("qwen" in lowered or "textencodeqwenimageeditplus" in lowered or "comfyui" in lowered):
        normalized = ImageProviderError(
            "qwen_edit_node_incompatible",
            "Qwen edit node is incompatible",
            "Qwen Image Edit failed inside ComfyUI because the active TextEncodeQwenImageEditPlus node referenced an undefined `size` value. Neo width/height are present; this is a Comfy Qwen node compatibility issue.",
            (
                "Update or repair ComfyUI's comfy_extras/nodes_qwen.py, then restart ComfyUI.",
                "If using Qwen-Image-Edit-Rapid-AIO, compare its nodes_qwen.py patch with the currently installed file.",
                "After restarting ComfyUI, reopen Neo and test the backend profile again.",
            ),
            raw,
        )
    elif any(token in lowered for token in ("workflow", "node", "prompt outputs failed", "invalid prompt", "bad request")):
        normalized = ImageProviderError(
            "image_workflow_invalid",
            "Image workflow was rejected",
            "The image backend rejected the workflow payload. A node, parameter, or workflow route may be invalid.",
            (
                "Check the selected workflow mode and provider profile.",
                "Disable recent extensions/custom nodes and try again.",
                "Use Validate in the Image header to check required inputs first.",
            ),
            raw,
        )
    elif any(token in lowered for token in ("output", "file not found", "no outputs", "missing file")):
        normalized = ImageProviderError(
            "image_output_missing",
            "Image output was not found",
            "The image backend finished or responded, but Neo could not find a usable output file.",
            (
                "Check the backend output folder.",
                "Refresh Results after the backend finishes writing files.",
                "Confirm Neo has permission to read the output path.",
            ),
            raw,
        )
    else:
        normalized = ImageProviderError(
            "image_provider_error",
            "Image provider error",
            "The active image provider returned an error while handling this request.",
            (
                "Check the backend console/log for the exact provider-side error.",
                "Try a simpler workflow to isolate the issue.",
                "Confirm the selected backend profile matches this Image workflow.",
            ),
            raw,
        )
    return normalized.to_payload(operation=operation, profile_id=profile_id, job_id=job_id)
