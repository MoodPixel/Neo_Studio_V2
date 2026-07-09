from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import json

from neo_app.providers.comfy_provider import ComfyProvider
from neo_app.providers.mock_provider import MockProvider
from neo_app.providers.xai_grok_provider import XaiGrokProvider
from neo_app.providers.schema import NeoJob, ProviderManifest
from neo_app.providers.backend_route_contract import backend_route_contract_payload
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict
from neo_app.runtime.job_registry import get_generation_job_registry

PROVIDER_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = PROVIDER_DIR / "provider_manifest.json"


@lru_cache(maxsize=1)
def get_provider_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def list_providers() -> list[ProviderManifest]:
    payload = get_provider_manifest()
    return [model_from_dict(ProviderManifest, item) for item in payload.get("providers", [])]


def build_provider(manifest: ProviderManifest):
    if manifest.provider_id in {"comfyui", "comfyui_portable"}:
        return ComfyProvider(manifest)
    if manifest.provider_id == "xai_grok":
        return XaiGrokProvider(manifest)
    return MockProvider(manifest)


def get_provider(provider_id: str):
    for manifest in list_providers():
        if manifest.provider_id == provider_id:
            return build_provider(manifest)
    return None


def get_provider_feature_capabilities(provider_id: str) -> dict[str, Any]:
    provider = get_provider(provider_id)
    if provider is None:
        return {
            "progress": False,
            "live_preview": False,
            "cancel": False,
            "pause": False,
            "resume": False,
            "clip_skip": False,
            "prompt_conditioning": False,
            "node_manager": False,
            "output_handoff": "unknown_provider",
            "progress_source": "none",
            "live_preview_source": "none",
        }
    return provider.feature_capability_payload()


def get_provider_backend_capabilities(provider_id: str) -> dict[str, Any]:
    provider = get_provider(provider_id)
    if provider is None:
        return {
            "provider_id": provider_id,
            "backend": "unknown",
            "discovery_version": "0.1.0",
            "discovery_status": "error",
            "reachable": False,
            "object_info_available": False,
            "loaders": {},
            "warnings": [],
            "errors": [f"Unknown provider: {provider_id}"],
        }
    return provider.discover_backend_capabilities()


def list_providers_for_surface(surface_id: str) -> list[ProviderManifest]:
    return [provider for provider in list_providers() if surface_id in provider.surfaces]


def get_provider_payload() -> dict[str, Any]:
    payload = get_provider_manifest()
    return {
        "provider_registry_version": payload.get("provider_registry_version"),
        "providers": [{**model_to_dict(provider), "feature_capabilities": get_provider_feature_capabilities(provider.provider_id)} for provider in list_providers()],
        "capability_discovery": {
            "version": "0.1.0",
            "contract": "backend-neutral loader capability roles; backend node names are diagnostics only",
        },
        "backend_route_contract": backend_route_contract_payload(),
    }


def get_surface_provider_payload(surface_id: str) -> dict[str, Any]:
    return {
        "surface_id": surface_id,
        "providers": [{**model_to_dict(provider), "feature_capabilities": get_provider_feature_capabilities(provider.provider_id)} for provider in list_providers_for_surface(surface_id)],
    }


def validate_job_payload(job_payload: dict[str, Any]) -> dict[str, Any]:
    job = model_from_dict(NeoJob, job_payload)
    provider = get_provider(job.provider_id)
    if provider is None:
        return {
            "ok": False,
            "provider_id": job.provider_id,
            "errors": [f"Unknown provider: {job.provider_id}"],
            "warnings": [],
        }
    return model_to_dict(provider.validate_job(job))


def compile_job_payload(job_payload: dict[str, Any]) -> dict[str, Any]:
    job = model_from_dict(NeoJob, job_payload)
    provider = get_provider(job.provider_id)
    if provider is None:
        return {
            "provider_id": job.provider_id,
            "compile_status": "mock_compiled",
            "backend_payload": {"error": f"Unknown provider: {job.provider_id}"},
        }
    return model_to_dict(provider.compile_job(job))


def _attach_registry_summary(payload: dict[str, Any], *, job: NeoJob | None = None, provider_id: str = "") -> dict[str, Any]:
    job_id = str(payload.get("job_id") or (job.job_id if job else "") or "")
    if not job_id:
        return payload
    try:
        registry = get_generation_job_registry()
        if job is not None:
            record = registry.upsert_from_provider_result(
                job=model_to_dict(job),
                result=payload,
                provider_id=provider_id or payload.get("provider_id") or job.provider_id,
            )
            summary = registry.summary(record.get("job_id") or job_id, surface=record.get("surface"))
        else:
            summary = registry.summary(job_id)
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        runtime["job_registry"] = summary
        payload["runtime"] = runtime
        payload["job_registry"] = summary
    except Exception as exc:  # noqa: BLE001
        payload["job_registry"] = {"schema_id": "neo.runtime.generation_job_registry.v25_2", "ok": False, "job_id": job_id, "error": str(exc)}
    return payload


def run_job_payload(job_payload: dict[str, Any]) -> dict[str, Any]:
    job = model_from_dict(NeoJob, job_payload)
    provider = get_provider(job.provider_id)
    if provider is None:
        payload = {
            "job_id": job.job_id or "unknown-provider",
            "provider_id": job.provider_id,
            "status": "failed",
            "message": f"Unknown provider: {job.provider_id}",
            "outputs": [],
        }
        return _attach_registry_summary(payload, job=job, provider_id=job.provider_id)
    payload = model_to_dict(provider.run_job(job))
    return _attach_registry_summary(payload, job=job, provider_id=provider.manifest.provider_id)


def poll_job_payload(provider_id: str, job_id: str) -> dict[str, Any]:
    provider = get_provider(provider_id)
    if provider is None:
        payload = {
            "job_id": job_id,
            "provider_id": provider_id,
            "status": "failed",
            "message": f"Unknown provider: {provider_id}",
            "outputs": [],
        }
        return _attach_registry_summary(payload, provider_id=provider_id)
    payload = model_to_dict(provider.poll_job(job_id))
    return _attach_registry_summary(payload, provider_id=provider.manifest.provider_id)


def fetch_outputs_payload(provider_id: str, job_id: str) -> dict[str, Any]:
    provider = get_provider(provider_id)
    if provider is None:
        return {"provider_id": provider_id, "job_id": job_id, "outputs": []}
    return {"provider_id": provider_id, "job_id": job_id, "outputs": provider.fetch_outputs(job_id)}
