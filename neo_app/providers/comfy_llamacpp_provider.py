from __future__ import annotations

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.providers.base import BaseProvider
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderFeatureCapabilities
from neo_app.providers.comfy_llamacpp_discovery import discover_comfy_llamacpp


class ComfyLlamaCppProvider(BaseProvider):
    """Logical Prompt & Captioning provider backed by a shared ComfyUI server.

    Phase 3 adds live Prompt/Caption execution through the Prompt & Captioning
    service while this provider continues to own discovery and provider identity.
    """

    def discover_backend_capabilities(self, *, object_info=None, discovery_error: str = "") -> dict:
        return discover_comfy_llamacpp(object_info, discovery_error=discovery_error)

    def feature_capabilities(self) -> ProviderFeatureCapabilities:
        # Keep runtime controls conservative until the Prompt/Caption workflow
        # adapter owns queue/progress/cancel semantics explicitly.
        return ProviderFeatureCapabilities(
            progress=False,
            live_preview=False,
            cancel=False,
            pause=False,
            resume=False,
            clip_skip=False,
            prompt_conditioning=False,
            node_manager=False,
            output_handoff="comfy_history_neo_text_output",
            progress_source="none",
            live_preview_source="none",
        )

    def compile_job(self, job: NeoJob) -> CompiledJob:
        validation = self.validate_job(job)
        return CompiledJob(
            provider_id=self.manifest.provider_id,
            compile_status="mock_compiled",
            backend_payload={
                "neo_job": model_to_dict(job),
                "validation": model_to_dict(validation),
                "provider_phase": "prompt_captioning_comfy_llamacpp_phase3_execution",
                "execution_ready": False,
                "note": "Prompt/Caption execution is compiled from the live backend profile by the Prompt & Captioning service; the generic provider compile endpoint has no live profile/discovery context and remains diagnostic-only.",
            },
        )
