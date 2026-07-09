from __future__ import annotations

from neo_app.core.pydantic_compat import model_to_dict
from abc import ABC, abstractmethod
from uuid import uuid4

from neo_app.providers.schema import (
    BackendCapabilityDiscoveryResult,
    BackendLoaderCapability,
    CompiledJob,
    NeoJob,
    ProviderFeatureCapabilities,
    ProviderManifest,
    ProviderRunResult,
    ProviderValidationResult,
)


class BaseProvider(ABC):
    """Provider contract for Neo Studio V2.

    Surfaces produce generic NeoJob objects. Providers validate and translate those
    generic jobs into backend-specific payloads. Real backends will replace the
    mock run behavior in later phases.
    """

    def __init__(self, manifest: ProviderManifest) -> None:
        self.manifest = manifest

    def status(self) -> dict:
        return {
            "provider_id": self.manifest.provider_id,
            "display_name": self.manifest.display_name,
            "status": self.manifest.status,
            "connection_kind": self.manifest.connection_kind,
            "surfaces": self.manifest.surfaces,
        }

    def discover_models(self) -> list[dict]:
        return []

    def discover_capabilities(self) -> list[dict]:
        return [model_to_dict(capability) for capability in self.manifest.capabilities]

    def discover_backend_capabilities(self) -> dict:
        """Return backend-neutral loader capability discovery for this provider.

        Mock/future providers return manifest-level availability only. Real
        providers may override this with backend probes such as Comfy `/object_info`.
        """
        loaders = {
            loader_id: BackendLoaderCapability(
                loader_id=loader_id,
                available=self.manifest.status not in {"disabled", "missing_config", "error"},
                notes=["Manifest-declared loader capability only; no backend probe implemented for this provider yet."],
            )
            for loader_id in self.manifest.supported_loaders
        }
        result = BackendCapabilityDiscoveryResult(
            provider_id=self.manifest.provider_id,
            backend=self.manifest.provider_id,
            discovery_status="mock",
            reachable=self.manifest.status not in {"disabled", "missing_config", "error"},
            object_info_available=False,
            loaders=loaders,
            warnings=["Provider has no real backend capability discovery implementation yet."],
        )
        return model_to_dict(result)


    def feature_capabilities(self) -> ProviderFeatureCapabilities:
        """Return runtime feature flags for this provider.

        Surfaces use this contract to decide which controls to enable. The
        default provider is conservative so future/mock backends do not inherit
        Comfy-specific progress, preview, cancel, or node-manager behavior.
        """
        return ProviderFeatureCapabilities()

    def feature_capability_payload(self) -> dict:
        return model_to_dict(self.feature_capabilities())

    def validate_job(self, job: NeoJob) -> ProviderValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if job.surface not in self.manifest.surfaces:
            errors.append(f"Provider does not support surface: {job.surface}")
        if job.mode not in self.manifest.supported_modes:
            errors.append(f"Provider does not support mode: {job.mode}")
        if job.family and self.manifest.supported_families and job.family not in self.manifest.supported_families:
            errors.append(f"Provider does not support family: {job.family}")
        if job.loader and self.manifest.supported_loaders and job.loader not in self.manifest.supported_loaders:
            errors.append(f"Provider does not support loader: {job.loader}")
        if self.manifest.status in {"disabled", "missing_config", "error"}:
            warnings.append(f"Provider status is {self.manifest.status}; run may not be available yet.")

        return ProviderValidationResult(
            ok=not errors,
            provider_id=self.manifest.provider_id,
            errors=errors,
            warnings=warnings,
        )

    @abstractmethod
    def compile_job(self, job: NeoJob) -> CompiledJob:
        raise NotImplementedError

    def run_job(self, job: NeoJob) -> ProviderRunResult:
        validation = self.validate_job(job)
        if not validation.ok:
            return ProviderRunResult(
                job_id=job.job_id or f"failed-{uuid4().hex[:8]}",
                provider_id=self.manifest.provider_id,
                status="failed",
                message="; ".join(validation.errors),
            )
        return ProviderRunResult(
            job_id=job.job_id or f"mock-{uuid4().hex[:8]}",
            provider_id=self.manifest.provider_id,
            status="queued",
            message="Mock provider accepted the job. Real execution comes in a later provider phase.",
        )

    def poll_job(self, job_id: str) -> ProviderRunResult:
        return ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="completed",
            message="Mock job completed.",
        )

    def cancel_job(self, job_id: str) -> ProviderRunResult:
        return ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="cancelled",
            message="Mock job cancelled.",
        )



    def pause_job(self, job_id: str) -> ProviderRunResult:
        return ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="running",
            message="Pause is not supported by this provider.",
            runtime={"control": {"pause_supported": False}},
        )

    def resume_job(self, job_id: str) -> ProviderRunResult:
        return ProviderRunResult(
            job_id=job_id,
            provider_id=self.manifest.provider_id,
            status="running",
            message="Resume is not supported by this provider.",
            runtime={"control": {"pause_supported": False}},
        )

    def fetch_live_preview(self, job_id: str) -> dict:
        return {
            "ok": False,
            "provider_id": self.manifest.provider_id,
            "job_id": job_id,
            "is_final": False,
            "message": "No HTTP preview exposed yet for this provider.",
        }

    def fetch_outputs(self, job_id: str) -> list[dict]:
        return []
