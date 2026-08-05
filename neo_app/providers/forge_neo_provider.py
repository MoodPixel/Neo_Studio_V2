from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.models.route_matrix import resolve_model_backend_route
from neo_app.providers.base import BaseProvider
from neo_app.providers.forge_neo_bridge import decide_forge_bridge
from neo_app.providers.forge_neo_capabilities import forge_backend_capabilities, forge_discovered_models, forge_snapshot_for_profile
from neo_app.providers.forge_neo_client import ForgeNeoClient, ForgeNeoClientError
from neo_app.providers.forge_neo_compile import compile_forge_neo_job, redact_forge_compile_payload
from neo_app.providers.forge_neo_extensions import validate_forge_extensions
from neo_app.providers.forge_neo_jobs import ForgeNeoJobManager, get_forge_job_manager
from neo_app.providers.forge_neo_loader_translation import translate_forge_loader_bundle
from neo_app.providers.forge_neo_model_classification import ensure_forge_live_discovery
from neo_app.providers.forge_neo_workflow_compilers import FORGE_WORKFLOW_COMPILER_IDS
from neo_app.providers.schema import (
    CompiledJob,
    NeoJob,
    ProviderFeatureCapabilities,
    ProviderManifest,
    ProviderRunResult,
    ProviderValidationResult,
)

_SELECTABLE_ROUTE_STATES = {"available", "experimental_available"}


class ForgeNeoProvider(BaseProvider):
    """Forge Neo Image provider with a Neo-owned durable lifecycle.

    The provider binds a saved Forge profile, validates route-authority-backed
    classic and modern Image workflows, then delegates synchronous Forge REST calls to
    the single-worker durable queue in :mod:`forge_neo_jobs`.
    """

    def __init__(
        self,
        manifest: ProviderManifest,
        *,
        profile: dict[str, Any] | None = None,
        client: ForgeNeoClient | None = None,
        job_manager: ForgeNeoJobManager | None = None,
    ) -> None:
        super().__init__(manifest)
        self.profile = profile if isinstance(profile, dict) else {}
        self.client = client
        self._client_error = ""
        if client is None and self.profile:
            try:
                self.client = ForgeNeoClient.from_profile(self.profile)
            except ForgeNeoClientError as exc:
                self._client_error = str(exc)
        handshake: dict[str, Any] = {}
        if self.client is not None and hasattr(self.client, "bridge_handshake"):
            try:
                handshake = self.client.bridge_handshake(timeout=4.0)
            except Exception:  # noqa: BLE001 - bridge is optional unless the profile requires it.
                handshake = {}
        self.bridge_decision = decide_forge_bridge(self.profile, handshake=handshake)
        if self.bridge_decision.required and not self.bridge_decision.available:
            self._client_error = self.bridge_decision.message
        self.job_manager = job_manager
        if self.job_manager is None and self.client is not None and self.profile and not (self.bridge_decision.required and not self.bridge_decision.available):
            self.job_manager = get_forge_job_manager(self.profile, self.client, bridge_decision=self.bridge_decision)

    def status(self) -> dict[str, Any]:
        snapshot = forge_snapshot_for_profile(self.profile)
        runtime_status = str(snapshot.get("status") or (self.profile.get("runtime") or {}).get("status") or "disconnected")
        reachable = bool(snapshot.get("reachable")) and bool(snapshot.get("api_enabled"))
        return {
            **super().status(),
            "status": "configured",
            "adapter_state": "bridge_lifecycle_ready" if self.bridge_decision.use_bridge else "execution_lifecycle_ready",
            "runtime_status": runtime_status,
            "reachable": reachable,
            "message": (
                ("Forge Neo provider is using the optional durable Bridge lifecycle." if self.bridge_decision.use_bridge else "Forge Neo provider and durable Image lifecycle are ready.")
                if reachable
                else "Forge Neo lifecycle is installed; connect the Forge Admin API before generation."
            ),
        }

    def feature_capabilities(self) -> ProviderFeatureCapabilities:
        bridge = bool(getattr(self.job_manager, "bridge_enabled", False) or self.bridge_decision.use_bridge)
        return ProviderFeatureCapabilities(
            progress=True,
            live_preview=True,
            cancel=True,
            pause=False,
            resume=False,
            clip_skip=True,
            prompt_conditioning=True,
            node_manager=False,
            output_handoff="forge_bridge_result_spool_then_neo_data" if bridge else "forge_response_spool_then_neo_data",
            progress_source="forge_bridge_job" if bridge else "forge_sdapi_progress",
            live_preview_source="forge_bridge_job_preview" if bridge else "forge_sdapi_progress_current_image",
        )

    def discover_models(self) -> list[dict[str, Any]]:
        return forge_discovered_models(self.profile)

    def discover_backend_capabilities(self) -> dict[str, Any]:
        payload = forge_backend_capabilities(self.profile)
        payload["bridge"] = {
            "mode": self.bridge_decision.mode,
            "available": self.bridge_decision.available,
            "selected": self.bridge_decision.use_bridge,
            "required": self.bridge_decision.required,
            "fallback_allowed": self.bridge_decision.fallback_allowed,
        }
        return payload

    @staticmethod
    def _normalized_mode(job: NeoJob) -> str:
        mode = str(job.mode or "txt2img").strip().casefold()
        return {"generate": "txt2img", "image_to_image": "img2img"}.get(mode, mode)

    @staticmethod
    def _extension_ids(extensions: Any) -> set[str]:
        found: set[str] = set()

        def visit(value: Any, key_hint: str = "") -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item, key_hint)
                return
            if not isinstance(value, dict):
                return
            enabled = value.get("enabled")
            identity = value.get("extension_id") or value.get("id") or value.get("key") or key_hint
            if identity and enabled is not False and any(field in value for field in ("enabled", "extension_id", "id", "manifest_id", "config", "state")):
                found.add(str(identity).strip())
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    visit(child, str(key))

        visit(extensions)
        return {item for item in found if item}

    @staticmethod
    def _source_path(params: dict[str, Any]) -> str:
        return str(params.get("source_image") or params.get("source_image_path") or params.get("init_image") or "").strip()

    @staticmethod
    def _mask_path(params: dict[str, Any]) -> str:
        return str(params.get("mask_image") or params.get("mask_image_path") or params.get("inpaint_mask") or "").strip()

    @staticmethod
    def _native_hires_contract(params: dict[str, Any]) -> dict[str, Any]:
        contract = params.get("_neo_derived_action")
        if not isinstance(contract, dict):
            contract = params.get("_neo_preview_action")
        if not isinstance(contract, dict):
            return {}
        if str(contract.get("action_id") or "") != "extension.high_res_lab":
            return {}
        if str(contract.get("dispatch_type") or "") != "run_forge_native_hires":
            return {}
        return contract

    @staticmethod
    def _native_hires_source(contract: dict[str, Any]) -> str:
        source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
        return str(source.get("path") or source.get("saved_path") or source.get("data_uri") or source.get("url") or "").strip()

    @staticmethod
    def _snapshot_bridge_capability(snapshot: dict[str, Any], capability: str) -> bool:
        bridge = snapshot.get("bridge") if isinstance(snapshot.get("bridge"), dict) else {}
        capabilities = bridge.get("capabilities") if isinstance(bridge.get("capabilities"), dict) else {}
        operations = {str(item) for item in capabilities.get("native_operations") or []}
        if capability == "native_post_hires":
            return bool(
                bridge.get("selected")
                and capabilities.get("native_post_hires")
                and "native_txt2img_upscale" in operations
                and capabilities.get("native_post_hires_size_contract")
            )
        return bool(bridge.get("selected") and (capabilities.get(capability) or capability in operations))

    def validate_job(self, job: NeoJob) -> ProviderValidationResult:
        mode = self._normalized_mode(job)
        validation_job = job.model_copy(update={"mode": mode}) if mode != job.mode else job
        result = super().validate_job(validation_job)
        family = str(job.family or "").strip() or "sdxl"
        loader = str(job.loader or "").strip() or "checkpoint"
        params = dict(job.params or {})

        def add_error(message: str) -> None:
            if message not in result.errors:
                result.errors.append(message)
            result.ok = False

        if str(job.surface or "").strip().casefold() != "image":
            add_error("Forge Neo provider foundation supports the Image surface only.")
        route = resolve_model_backend_route(family, loader, mode, "forge")
        if route.state not in _SELECTABLE_ROUTE_STATES:
            add_error(route.reason or f"Forge route is not available for {family}+{loader}+{mode}.")
        if not route.compiler_id or route.compiler_id not in FORGE_WORKFLOW_COMPILER_IDS:
            add_error(f"Forge workflow compiler is unavailable for {family}+{loader}+{mode}.")

        width = _safe_int(params.get("width"), 1024)
        height = _safe_int(params.get("height"), 1024)
        resolution_multiple = max(1, _safe_int(params.get("forge_resolution_multiple"), 64))
        if width < 64 or height < 64:
            add_error("Forge image dimensions must be at least 64 pixels.")
        if width % resolution_multiple or height % resolution_multiple:
            add_error(f"Forge image dimensions must be multiples of {resolution_multiple}.")
        if _safe_int(params.get("steps"), 20) < 1:
            add_error("Forge sampling steps must be at least 1.")
        cfg = _safe_float(params.get("cfg_scale", params.get("cfg", 7.0)), 7.0)
        if cfg < 0:
            add_error("Forge CFG scale cannot be negative.")

        if mode in {"img2img", "inpaint", "outpaint", "edit"}:
            source = self._source_path(params)
            if not source:
                add_error(f"Forge {mode} requires a source image.")
            elif not source.startswith("data:image/") and not Path(source).expanduser().is_file():
                add_error("Forge source image does not exist in Neo-owned storage.")
        if mode == "inpaint":
            mask = self._mask_path(params)
            if not mask:
                add_error("Forge inpaint requires a mask image.")
            elif not mask.startswith("data:image/") and not Path(mask).expanduser().is_file():
                add_error("Forge inpaint mask does not exist in Neo-owned storage.")
        if mode == "outpaint":
            padding = params.get("outpaint_padding")
            side_values = [params.get(f"outpaint_{side}") or params.get(f"outpaint_padding_{side}") for side in ("left", "right", "top", "bottom")]
            if isinstance(padding, dict):
                side_values.extend(padding.get(side) for side in ("left", "right", "top", "bottom"))
            elif padding not in {None, ""}:
                side_values.append(padding)
            if not any(_safe_int(value, 0) > 0 for value in side_values):
                add_error("Forge outpaint requires positive padding on at least one side.")

        snapshot = forge_snapshot_for_profile(self.profile)
        native_hires = self._native_hires_contract(params)
        if native_hires:
            if mode != "txt2img":
                add_error("Forge native post-generation Hires must use the txt2img runtime boundary.")
            source = self._native_hires_source(native_hires)
            if not source:
                add_error("Forge native post-generation Hires requires a selected source output.")
            elif not source.startswith("data:image/") and not Path(source).expanduser().is_file():
                add_error("Forge native post-generation Hires source must be materialized in Neo-owned storage.")
            if not self.bridge_decision.use_bridge or not bool(getattr(self.job_manager, "bridge_enabled", False)):
                add_error("Forge native post-generation Hires requires the selected Neo Forge Bridge lifecycle.")
            if not self._snapshot_bridge_capability(snapshot, "native_post_hires"):
                add_error("The selected Neo Forge Bridge must be 1.2.1+ and expose native_post_hires, native_txt2img_upscale, and native_post_hires_size_contract.")
        if route.state in _SELECTABLE_ROUTE_STATES and route.compiler_id in FORGE_WORKFLOW_COMPILER_IDS:
            loader_translation = translate_forge_loader_bundle(validation_job, snapshot=snapshot)
            for blocker in loader_translation.get("blockers") or []:
                add_error(f"Forge loader translation: {blocker}.")
            for warning in loader_translation.get("warnings") or []:
                if warning not in result.warnings:
                    result.warnings.append(str(warning))

            classification, intersection = ensure_forge_live_discovery(snapshot)
            has_live_inventory = bool(snapshot.get("reachable") or classification.get("models") or classification.get("modules"))
            if has_live_inventory:
                live_route = next(
                    (
                        item for item in intersection.get("routes") or []
                        if isinstance(item, dict)
                        and item.get("family") == family
                        and item.get("loader") == loader
                        and item.get("mode") == mode
                    ),
                    None,
                )
                if isinstance(live_route, dict) and not live_route.get("selectable"):
                    for blocker in live_route.get("blockers") or []:
                        add_error(f"Forge live route: {blocker}.")

        extension_errors, extension_warnings = validate_forge_extensions(
            job.extensions,
            snapshot=snapshot,
            mode=mode,
            family=family,
        )
        for message in extension_errors:
            add_error(message)
        for message in extension_warnings:
            if message not in result.warnings:
                result.warnings.append(message)

        if self.profile and not bool(self.profile.get("enabled", False)):
            add_error("Forge backend profile is disabled.")
        if self._client_error:
            add_error(self._client_error)
        if self.bridge_decision.required and not self.bridge_decision.available:
            add_error("Forge Bridge is required for this profile but the bridge handshake is unavailable.")
        if snapshot:
            snapshot_status = str(snapshot.get("status") or "")
            if snapshot_status not in {"connected", "connected_with_warnings"}:
                result.warnings.append(f"Forge Admin capability snapshot is {snapshot_status or 'unknown'}.")
        return result

    def compile_job(self, job: NeoJob) -> CompiledJob:
        validation = self.validate_job(job)
        if not validation.ok:
            return CompiledJob(
                provider_id=self.manifest.provider_id,
                compile_status="mock_compiled",
                backend_payload={
                    "provider_id": self.manifest.provider_id,
                    "backend": "forge_neo",
                    "validation": model_to_dict(validation),
                    "execution_state": "blocked_validation",
                },
            )
        try:
            compiled = compile_forge_neo_job(job, snapshot=forge_snapshot_for_profile(self.profile))
        except Exception as exc:  # noqa: BLE001 - compile endpoint must remain diagnostic.
            return CompiledJob(
                provider_id=self.manifest.provider_id,
                compile_status="mock_compiled",
                backend_payload={
                    "provider_id": self.manifest.provider_id,
                    "backend": "forge_neo",
                    "validation": model_to_dict(validation),
                    "error": str(exc),
                    "execution_state": "blocked_compile_error",
                },
            )
        compiled["validation"] = model_to_dict(validation)
        return CompiledJob(
            provider_id=self.manifest.provider_id,
            compile_status="compiled",
            backend_payload=compiled,
        )

    def run_job(self, job: NeoJob) -> ProviderRunResult:
        job_id = job.job_id or f"forge-{uuid4().hex[:12]}"
        prepared_job = job.model_copy(update={"job_id": job_id}) if job.job_id != job_id else job
        compiled = self.compile_job(prepared_job)
        if compiled.compile_status != "compiled":
            validation = (compiled.backend_payload or {}).get("validation") or {}
            errors = validation.get("errors") if isinstance(validation, dict) else []
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message="; ".join(errors or []) or str((compiled.backend_payload or {}).get("error") or "Forge job validation failed."),
                outputs=[],
                runtime={
                    "phase": "forge_image_job_lifecycle",
                    "execution_state": "blocked_validation",
                    "compiled": redact_forge_compile_payload(compiled.backend_payload or {}),
                },
            )
        if self.job_manager is None:
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=self._client_error or "Forge lifecycle manager is unavailable for this profile.",
                outputs=[],
                runtime={"phase": "forge_image_job_lifecycle", "execution_state": "missing_client"},
            )
        state = self.job_manager.enqueue(job=model_to_dict(prepared_job), compiled=compiled.backend_payload or {})
        return self._result_from_state(state)

    def poll_job(self, job_id: str) -> ProviderRunResult:
        if self.job_manager is None:
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=self._client_error or "Forge lifecycle manager is unavailable for this profile.",
                runtime={"execution_state": "missing_client"},
            )
        return self._result_from_state(self.job_manager.poll(job_id))

    def cancel_job(self, job_id: str) -> ProviderRunResult:
        if self.job_manager is None:
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=self._client_error or "Forge lifecycle manager is unavailable for this profile.",
                runtime={"execution_state": "missing_client"},
            )
        return self._result_from_state(self.job_manager.cancel(job_id))

    def recover_job(self, job_id: str) -> ProviderRunResult:
        if self.job_manager is None:
            return ProviderRunResult(
                job_id=job_id,
                provider_id=self.manifest.provider_id,
                status="failed",
                message=self._client_error or "Forge lifecycle manager is unavailable for this profile.",
                runtime={"execution_state": "missing_client"},
            )
        return self._result_from_state(self.job_manager.recover(job_id))

    def fetch_live_preview(self, job_id: str) -> dict[str, Any]:
        if self.job_manager is None:
            return {
                "ok": False,
                "provider_id": self.manifest.provider_id,
                "job_id": job_id,
                "is_final": False,
                "message": self._client_error or "Forge lifecycle manager is unavailable for this profile.",
            }
        return {"provider_id": self.manifest.provider_id, **self.job_manager.preview(job_id)}

    def fetch_outputs(self, job_id: str) -> list[dict[str, Any]]:
        if self.job_manager is None:
            return []
        state = self.job_manager.poll(job_id)
        return state.get("outputs") if isinstance(state.get("outputs"), list) else []

    def _result_from_state(self, state: dict[str, Any]) -> ProviderRunResult:
        status = str(state.get("status") or "failed")
        if status not in {
            "queued", "running", "importing", "completed", "completed_with_warnings",
            "failed", "cancelled", "paused", "saved_in_comfy_only", "import_failed",
            "completed_no_outputs_recoverable", "completed_import_failed",
        }:
            status = "failed"
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        runtime.setdefault("capabilities", self.feature_capability_payload())
        runtime.setdefault("control", {"cancel_supported": True, "pause_supported": False})
        return ProviderRunResult(
            job_id=str(state.get("job_id") or ""),
            provider_id=self.manifest.provider_id,
            status=status,
            message=str(state.get("message") or ""),
            outputs=state.get("outputs") if isinstance(state.get("outputs"), list) else [],
            runtime=runtime,
        )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
