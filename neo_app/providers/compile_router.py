from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
import json

from neo_app.providers.schema import NeoJob
from neo_app.models.route_matrix import normalize_backend, resolve_model_backend_route
from neo_app.image.flux1_krea_contract import is_flux1_krea_route, resolve_flux1_variant
from neo_app.image.krea2_contract import resolve_krea2_variant
from neo_app.image.lanpaint_family_adapter import PHASE14_STATE, PHASE15_STATE, PHASE16_STATE, PHASE17_STATE, get_lanpaint_family_adapter

# Phase 15 keeps the shared compiler contract while onboarding the SD family through exact adapters.
# Shared compiler ID: comfy.lanpaint.family_aware.v1


MODE_ALIASES = {
    "generate": "txt2img",
    "text_to_image": "txt2img",
    "image_to_image": "img2img",
}


INPAINT_ENGINE_ALIASES = {
    "": "native",
    "default": "native",
    "native": "native",
    "standard": "native",
    "normal": "native",
    "lan_paint": "lanpaint",
    "lanpaint": "lanpaint",
}

SUPPORTED_COMFY_ROUTES = {
    ("sdxl", "checkpoint", "txt2img"),
    ("sdxl", "checkpoint", "img2img"),
    ("sdxl", "checkpoint", "inpaint"),
    ("sdxl", "checkpoint", "outpaint"),
    ("sd15", "checkpoint", "txt2img"),
    ("sd15", "checkpoint", "img2img"),
    ("sd15", "checkpoint", "inpaint"),
    ("sd15", "checkpoint", "outpaint"),
    ("flux", "diffusion_model", "txt2img"),
    ("flux", "diffusion_model", "img2img"),
    ("flux", "diffusion_model", "inpaint"),
    ("flux", "diffusion_model", "outpaint"),
    ("flux1_fill", "diffusion_model", "inpaint"),
    ("flux1_fill", "diffusion_model", "outpaint"),
    ("flux", "gguf", "txt2img"),
    ("flux", "gguf", "img2img"),
    ("flux", "gguf", "inpaint"),
    ("flux", "gguf", "outpaint"),
    ("flux2_klein", "diffusion_model", "txt2img"),
    ("flux2_klein", "diffusion_model", "img2img"),
    ("flux2_klein", "diffusion_model", "edit"),
    ("flux2_klein", "diffusion_model", "inpaint"),
    ("flux2_klein", "diffusion_model", "outpaint"),
    ("flux2_klein", "gguf", "txt2img"),
    ("flux2_klein", "gguf", "img2img"),
    ("flux2_klein", "gguf", "edit"),
    ("flux2_klein", "gguf", "inpaint"),
    ("flux2_klein", "gguf", "outpaint"),
    ("krea2", "diffusion_model", "txt2img"),
    ("krea2", "diffusion_model", "img2img"),
    ("krea2", "diffusion_model", "inpaint"),
    ("krea2", "diffusion_model", "outpaint"),
    ("krea2", "gguf", "txt2img"),
    ("krea2", "gguf", "img2img"),
    ("krea2", "gguf", "inpaint"),
    ("krea2", "gguf", "outpaint"),
    ("krea2_turbo", "diffusion_model", "txt2img"),
    ("krea2_turbo", "diffusion_model", "img2img"),
    ("krea2_turbo", "diffusion_model", "inpaint"),
    ("krea2_turbo", "diffusion_model", "outpaint"),
    ("krea2_turbo", "gguf", "txt2img"),
    ("krea2_turbo", "gguf", "img2img"),
    ("krea2_turbo", "gguf", "inpaint"),
    ("krea2_turbo", "gguf", "outpaint"),
    ("qwen_image", "diffusion_model", "txt2img"),
    ("qwen_image", "diffusion_model", "img2img"),
    ("qwen_image", "diffusion_model", "inpaint"),
    ("qwen_image", "diffusion_model", "outpaint"),
    ("qwen_image", "diffusion_model", "edit"),
    ("qwen_image", "gguf", "txt2img"),
    ("qwen_image", "gguf", "img2img"),
    ("qwen_image", "gguf", "inpaint"),
    ("qwen_image", "gguf", "outpaint"),
    ("qwen_image_edit_2509", "diffusion_model", "txt2img"),
    ("qwen_image_edit_2509", "diffusion_model", "img2img"),
    ("qwen_image_edit_2509", "diffusion_model", "inpaint"),
    ("qwen_image_edit_2509", "diffusion_model", "outpaint"),
    ("qwen_image_edit_2509", "diffusion_model", "edit"),
    ("qwen_image_edit_2509", "gguf", "txt2img"),
    ("qwen_image_edit_2509", "gguf", "img2img"),
    ("qwen_image_edit_2509", "gguf", "inpaint"),
    ("qwen_image_edit_2509", "gguf", "outpaint"),
    ("qwen_image_edit_2509", "gguf", "edit"),
    ("qwen_image_edit_2511", "diffusion_model", "txt2img"),
    ("qwen_image_edit_2511", "diffusion_model", "img2img"),
    ("qwen_image_edit_2511", "diffusion_model", "inpaint"),
    ("qwen_image_edit_2511", "diffusion_model", "outpaint"),
    ("qwen_image_edit_2511", "diffusion_model", "edit"),
    ("qwen_image_edit_2511", "gguf", "txt2img"),
    ("qwen_image_edit_2511", "gguf", "img2img"),
    ("qwen_image_edit_2511", "gguf", "inpaint"),
    ("qwen_image_edit_2511", "gguf", "outpaint"),
    ("qwen_image_edit_2511", "gguf", "edit"),
    ("qwen_rapid_aio", "checkpoint_aio", "txt2img"),
    ("qwen_rapid_aio", "checkpoint_aio", "img2img"),
    ("qwen_rapid_aio", "checkpoint_aio", "inpaint"),
    ("qwen_rapid_aio", "checkpoint_aio", "outpaint"),
    ("qwen_rapid_aio", "checkpoint_aio", "edit"),
    ("qwen_rapid_aio", "gguf", "txt2img"),
    ("qwen_rapid_aio", "gguf", "img2img"),
    ("qwen_rapid_aio", "gguf", "edit"),
    ("qwen_rapid_aio", "gguf", "inpaint"),
    ("qwen_rapid_aio", "gguf", "outpaint"),
    ("z_image", "diffusion_model", "txt2img"),
    ("z_image", "diffusion_model", "img2img"),
    ("z_image", "diffusion_model", "inpaint"),
    ("z_image", "diffusion_model", "outpaint"),
    ("z_image", "gguf", "txt2img"),
    ("z_image", "gguf", "img2img"),
    ("z_image", "gguf", "inpaint"),
    ("z_image", "gguf", "outpaint"),
    ("z_image_turbo", "diffusion_model", "txt2img"),
    ("z_image_turbo", "diffusion_model", "img2img"),
    ("z_image_turbo", "diffusion_model", "inpaint"),
    ("z_image_turbo", "diffusion_model", "outpaint"),
    ("z_image_turbo", "gguf", "txt2img"),
    ("z_image_turbo", "gguf", "img2img"),
    ("z_image_turbo", "gguf", "inpaint"),
    ("z_image_turbo", "gguf", "outpaint"),
    ("hidream", "diffusion_model", "txt2img"),
    ("hidream", "gguf", "txt2img"),
    ("hidream", "diffusion_model", "inpaint"),
    ("hidream", "gguf", "inpaint"),
    ("anima", "diffusion_model", "txt2img"),
    ("anima", "gguf", "txt2img"),
    ("anima", "diffusion_model", "img2img"),
    ("anima", "gguf", "img2img"),
    ("anima", "diffusion_model", "inpaint"),
    ("anima", "gguf", "inpaint"),
    ("ideogram4", "diffusion_model", "txt2img"),
    ("ideogram4", "gguf", "txt2img"),
    ("ideogram4", "diffusion_model", "inpaint"),
    ("ideogram4", "gguf", "inpaint"),
}

PLANNED_COMFY_ROUTES = {}

PROVIDER_GATED_FAMILIES = {"wan_image", "hunyuan_image"}
VARIANT_GATED_MODES = {"inpaint", "outpaint", "edit"}


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "models" / "model_family_manifest.json"


@lru_cache(maxsize=1)
def _supported_loaders_by_family() -> dict[str, list[str]]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {str(item.get("family_id")): list(item.get("supported_loaders") or []) for item in payload.get("families", [])}


def _family_supports_loader(family: str, loader: str) -> bool | None:
    supported = _supported_loaders_by_family().get(family)
    if supported is None:
        return None
    return loader in supported


@dataclass(frozen=True)
class CompileRoute:
    """Provider-local compile route decision.

    The route is intentionally backend-neutral at the key level:
    family + loader + mode. Provider-specific compiler ids are diagnostics for
    this provider only and must not become Image-surface contracts.
    """

    provider_id: str
    backend: str
    family: str
    loader: str
    mode: str
    requested_mode: str
    status: str
    engine: str = "native"
    compiler_id: str | None = None
    workflow_type: str | None = None
    phase: str | None = None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_compile(self) -> bool:
        return self.status == "available" and bool(self.compiler_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "backend": self.backend,
            "family": self.family,
            "loader": self.loader,
            "mode": self.mode,
            "requested_mode": self.requested_mode,
            "status": self.status,
            "engine": self.engine,
            "compiler_id": self.compiler_id,
            "workflow_type": self.workflow_type,
            "phase": self.phase,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def normalize_compile_mode(mode: str | None) -> str:
    normalized = str(mode or "txt2img").strip() or "txt2img"
    return MODE_ALIASES.get(normalized, normalized)


def normalize_compile_family(family: str | None) -> str:
    # Existing Phase 11 Comfy jobs often did not send family yet. Preserve that
    # behavior by treating omitted family as SDXL checkpoint.
    return str(family or "sdxl").strip() or "sdxl"


def normalize_compile_loader(loader: str | None) -> str:
    # Existing Phase 11 Comfy jobs often did not send loader yet. Preserve that
    # behavior by treating omitted loader as checkpoint.
    return str(loader or "checkpoint").strip() or "checkpoint"


def normalize_inpaint_engine(value: Any) -> str:
    normalized = str(value or "native").strip().lower().replace("-", "_").replace(" ", "_")
    return INPAINT_ENGINE_ALIASES.get(normalized, normalized or "native")


def _route_state_to_compile_status(state: str) -> str:
    return {
        "available": "available",
        "experimental_available": "available",
        "implementation_target": "implementation_target",
        "planned_gated": "planned",
        "provider_gated": "provider_gated",
        "unsupported": "unsupported",
    }.get(state, state)


def select_backend_compile_route(job: NeoJob) -> CompileRoute:
    """Backend plug-in compile decision.

    Each backend resolves independently against the route matrix. Comfy and the
    validated Forge Neo checkpoint routes own separate compilers; other backends
    return gated CompileRoute objects until their adapters are implemented.
    """

    backend = normalize_backend(job.provider_id)
    if backend == "comfyui":
        return select_comfy_compile_route(job)

    requested_mode = str(job.mode or "txt2img")
    mode = normalize_compile_mode(requested_mode)
    family = normalize_compile_family(job.family)
    loader = normalize_compile_loader(job.loader)
    route = resolve_model_backend_route(family, loader, mode, backend)
    status = _route_state_to_compile_status(route.state)
    blocker = route.reason or f"{backend} route is not enabled for {family}+{loader}+{mode}."
    return CompileRoute(
        provider_id=job.provider_id,
        backend=backend,
        family=family,
        loader=loader,
        mode=mode,
        requested_mode=requested_mode,
        status=status,
        compiler_id=route.compiler_id,
        workflow_type=route.workflow_type,
        phase="Forge Neo Phase 3 — Image Job Lifecycle" if backend == "forge" and status == "available" else "Phase 12.31 — Backend Plug-in Contract",
        blockers=[] if route.selectable and route.compiler_id else [blocker],
        warnings=["Backend route contract resolved without borrowing Comfy compiler support."] if backend in {"forge", "a1111"} else [],
    )


def select_comfy_compile_route(job: NeoJob) -> CompileRoute:
    requested_mode = str(job.mode or "txt2img")
    mode = normalize_compile_mode(requested_mode)
    family = normalize_compile_family(job.family)
    loader = normalize_compile_loader(job.loader)
    key = (family, loader, mode)
    supports_loader = _family_supports_loader(family, loader)
    params = job.params or {}
    inpaint_engine = normalize_inpaint_engine(
        (params.get("inpaint_engine") or params.get("engine")) if mode == "inpaint" else "native"
    )
    selected_model = job.model or params.get("gguf_unet") or params.get("gguf_model") or params.get("diffusion_model") or params.get("model") or params.get("unet") or ""
    flux1_variant = resolve_flux1_variant(params.get("flux_variant") or params.get("variant") or "dev", selected_model) if family == "flux" else ""
    flux1_krea = family == "flux" and is_flux1_krea_route(flux1_variant, selected_model)
    krea2_variant = resolve_krea2_variant(family, selected_model) if family in {"krea2", "krea2_turbo"} else ""

    if mode == "inpaint" and inpaint_engine != "native":
        if inpaint_engine != "lanpaint":
            return CompileRoute(
                provider_id=job.provider_id,
                backend="comfyui",
                family=family,
                loader=loader,
                mode=mode,
                requested_mode=requested_mode,
                status="unsupported",
                engine=inpaint_engine,
                blockers=[f"Unknown inpaint engine {inpaint_engine!r}. Supported engines are native and lanpaint."],
            )
        adapter = get_lanpaint_family_adapter(
            family,
            loader=loader,
            provider_id=job.provider_id,
            mode=mode,
            engine="lanpaint",
        )
        binding = adapter.get("binding") or {}
        stabilization = adapter.get("stabilization") or {}
        if binding.get("selectable") and binding.get("compiler_id"):
            graph_profile = str(binding.get("graph_profile") or "")
            is_krea = graph_profile == "krea2_differential_crop_stitch_v1"
            newly_bound = bool(stabilization.get("new_binding_activated"))
            phase15_new = bool(stabilization.get("new_binding_activated_phase15"))
            phase16_new = bool(stabilization.get("new_binding_activated_phase16"))
            phase17_new = bool(stabilization.get("new_binding_activated_phase17"))
            phase18_new = bool(stabilization.get("new_binding_activated_phase18"))
            phase20_new = bool(stabilization.get("new_binding_activated_phase20"))
            phase21_new = bool(stabilization.get("new_binding_activated_phase21"))
            parity_note = (
                "Phase 21 onboards HiDream-I1 full/dev/fast through a four-encoder ModelSamplingSD3 LanPaint contract; Hunyuan remains held."
                if phase21_new
                else (
                "Phase 20 completes independent Z-Image Base/Turbo LanPaint contracts."
                if phase20_new
                else (
                "Phase 18 onboards Qwen Image Edit 2509/2511 variant contracts."
                if phase18_new
                else (
                "Phase 17 onboards Flux.2 Dev/Klein through separate encoder and variant contracts."
                if phase17_new
                else (
                "Phase 16 onboards Flux.1 Dev/Schnell through shared architecture compatibility and variant-specific defaults."
                if phase16_new
                else (
                    "Phase 15 onboards this SD family/loader through its dedicated adapter and compiler-owned graph anchors."
                    if phase15_new
                    else (
                    "Phase 14 activates Krea 2 Turbo safetensors through the same Differential Diffusion crop/stitch topology as the existing GGUF route."
                    if newly_bound
                    else "Existing LanPaint binding remains parity-stabilized without changing its family graph semantics."
                    )
                )
                )
                )
                )
                )
            )
            return CompileRoute(
                provider_id=job.provider_id,
                backend="comfyui",
                family=family,
                loader=loader,
                mode=mode,
                requested_mode=requested_mode,
                status="available",
                engine="lanpaint",
                compiler_id=str(binding.get("compiler_id")),
                workflow_type=str(binding.get("workflow_type") or "image.inpaint.lanpaint"),
                phase=str(binding.get("phase") or "LanPaint Route Family Phase 17 — Flux.2 Dev and Klein onboarding"),
                warnings=[
                    ("Krea 2 Turbo keeps the approved DifferentialDiffusionAdvanced crop/stitch graph." if is_krea else "The universal family adapter selects the family-owned transform, conditioning and loader contracts."),
                    parity_note,
                    "The route remains experimental until physical ComfyUI generation validation passes.",
                ],
            )
        return CompileRoute(
            provider_id=job.provider_id,
            backend="comfyui",
            family=family,
            loader=loader,
            mode=mode,
            requested_mode=requested_mode,
            status="implementation_target",
            engine="lanpaint",
            blockers=[
                f"No exact LanPaint compiler binding exists for this family/loader. Adapter state: {binding.get('state') or 'unresolved'}; Phase 21 does not activate unrelated scaffold routes."
            ],
        )

    if supports_loader is False:
        return CompileRoute(
            provider_id=job.provider_id,
            backend="comfyui",
            family=family,
            loader=loader,
            mode=mode,
            requested_mode=requested_mode,
            status="unsupported",
            blockers=[f"Loader {loader} is not supported by family {family}; refusing mixed family/loader route."],
        )

    if key in SUPPORTED_COMFY_ROUTES:
        workflow_type = f"image.{mode}.{family}"
        phase = "Phase 12.8 — SD 1.5 Checkpoint Workflows" if family == "sd15" else "Phase 12.7 — Provider Compile Router"
        compiler_id = "comfy.checkpoint_sd"
        warnings = []
        if family == "sd15" and mode == "inpaint":
            warnings.append("SD 1.5 inpaint route uses the checkpoint inpaint graph; best results require an inpaint-capable SD 1.5 checkpoint.")
        if family in {"sdxl", "sd15"} and loader == "checkpoint" and mode == "outpaint":
            workflow_type = f"image.outpaint.{family}_checkpoint"
            compiler_id = "comfy.checkpoint_sd"
            phase = "Phase 12.19 — Outpaint Contract Unification"
            warnings.append("Checkpoint outpaint uses the V2 outpaint contract and provider-owned canvas/inpaint graph route.")
        if family in {"krea2", "krea2_turbo"} and loader in {"diffusion_model", "gguf"}:
            suffix = "gguf" if loader == "gguf" else "native"
            family_token = "krea2_turbo" if krea2_variant == "turbo" else "krea2"
            workflow_type = f"image.{mode}.{family_token}_{suffix}"
            compiler_id = "comfy.krea2_gguf" if loader == "gguf" else "comfy.krea2"
            phase = "Phase M16 — Krea 2 RAW + Turbo Architecture Audit & Support"
            if loader == "gguf":
                warnings.append("M16 Krea 2 GGUF is experimental: the main transformer uses a Krea2-capable GGUF loader while Qwen3-VL-4B remains native via CLIPLoader(type=krea2).")
            elif mode == "txt2img":
                warnings.append("M16 Krea 2 native txt2img uses UNETLoader + CLIPLoader(type=krea2) + Qwen Image VAE with family-owned RAW/Turbo defaults.")
            else:
                warnings.append("M16 Krea 2 image-conditioned modes are explicit Neo latent adapters and remain experimental; no FLUX/Qwen Image fallback is used.")
        if family == "flux" and loader == "diffusion_model" and mode == "txt2img":
            if flux1_krea:
                workflow_type = "image.txt2img.flux1_krea_native"
                compiler_id = "comfy.flux_krea"
                phase = "Phase M15 — FLUX.1 Krea Support"
                warnings.append("M15 Krea lock: Krea is a FLUX.1 Dev-compatible variant using UNETLoader + DualCLIPLoader(T5XXL/CLIP-L) + AE/VAE + FluxGuidance.")
            else:
                workflow_type = "image.txt2img.flux_native"
                compiler_id = "comfy.flux_native"
                phase = "Phase 12.9 / V25.9.20 Pass O2 — Flux Native Workflow Foundation"
                warnings.append("P1 Flux 1 lock: Safetensors / Components supports txt2img, img2img, and internal Flux Fill inpaint/outpaint without exposing Flux Fill as a separate family.")
        if family == "flux" and loader == "diffusion_model" and mode == "img2img":
            if flux1_krea:
                workflow_type = "image.img2img.flux1_krea_native"
                compiler_id = "comfy.flux_krea"
                phase = "Phase M15 — FLUX.1 Krea Support"
                warnings.append("M15 Krea lock: img2img keeps the selected Krea model and encodes Image 1 as the FLUX.1 latent anchor.")
            else:
                workflow_type = "image.img2img.flux_native"
                compiler_id = "comfy.flux_native"
                phase = "V25.9.20 Pass O2 — Flux 1 Img2Img Workflow Implementation"
                warnings.append("P1 Flux 1 lock: img2img uses Image 1 as a VAEEncode latent anchor; inpaint/outpaint use the internal Flux Fill compiler through the normal Flux 1 family.")
        if family == "flux" and loader == "diffusion_model" and mode in {"inpaint", "outpaint"}:
            if flux1_krea:
                workflow_type = f"image.{mode}.flux1_krea_native"
                compiler_id = "comfy.flux_krea"
                phase = "Phase M15 — FLUX.1 Krea Support"
                warnings.append("M15 Krea lock: masked Krea workflows keep the selected Krea model and use VAEEncode + SetLatentNoiseMask + DifferentialDiffusion instead of silently swapping to FLUX.1 Fill.")
            else:
                workflow_type = f"image.{mode}.flux_fill_internal"
                compiler_id = "comfy.flux_fill"
                phase = "V25.9.20 P1 — Flux 1 Internal Flux Fill Route Cleanup"
                warnings.append("P1 Flux 1 lock: inpaint/outpaint resolve through the internal FLUX.1 Fill-dev workflow while Flux Fill stays out of the normal family dropdown.")
        if family == "flux1_fill" and loader == "diffusion_model" and mode in {"inpaint", "outpaint"}:
            workflow_type = f"image.{mode}.flux_fill_legacy_alias"
            compiler_id = "comfy.flux_fill"
            phase = "V25.9.20 P1 — Flux 1 Fill Legacy Alias"
            warnings.append("P1 compatibility alias: flux1_fill still compiles for saved jobs, but the normal UI must route through family=flux + diffusion_model + inpaint/outpaint.")
        if family == "flux2_klein" and loader == "diffusion_model":
            workflow_type = "image.txt2img.flux2_klein" if mode == "txt2img" else f"image.{mode}.flux2_klein_native"
            compiler_id = "comfy.flux_klein"
            phase = "V25.9.20 P4 — Flux Klein Checkpoint/Safetensors Workflows"
            if mode == "txt2img":
                warnings.append("P4 Flux 2 Klein lock: Safetensors / Components uses a single Qwen3 Flux2 compiler and keeps txt2img on EmptyFlux2LatentImage.")
            elif mode in {"img2img", "edit"}:
                warnings.append("P4 Flux 2 Klein lock: Safetensors / Components img2img/edit uses Image 1 as a Flux2 VAEEncode latent anchor through the Klein-native compiler.")
            elif mode == "inpaint":
                warnings.append("P4 Flux 2 Klein lock: Safetensors / Components inpaint uses Image 1 + mask with SetLatentNoiseMask + DifferentialDiffusion; no Flux 1 Fill fallback.")
            elif mode == "outpaint":
                warnings.append("P4 Flux 2 Klein lock: Safetensors / Components outpaint uses ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion; no Flux 1 Fill fallback.")
        if family == "flux2_klein" and loader == "gguf":
            workflow_type = f"image.{mode}.flux2_klein_gguf"
            compiler_id = "comfy.flux_gguf.klein"
            phase = "V25.9.20 Pass D / Pass O1 — Flux 2 Klein Img2Img/Edit Workflow Validation"
            warnings.append("Pass O1 Flux 2 Klein lock: GGUF img2img/edit uses Image 1 as the VAEEncode latent anchor with a single-Qwen3 Flux2/Klein provider route; optional Image 2/Image 3 remain replay/reference lanes until a dedicated local multi-reference conditioning node is validated.")
        if family == "qwen_image" and loader == "diffusion_model" and mode == "txt2img":
            workflow_type = "image.txt2img.qwen_native"
            compiler_id = "comfy.qwen_native"
            phase = "V25.9.20 P3 — Qwen Image Edit Workflow Promotion"
            warnings.append("P3 Qwen Image Edit lock: Safetensors / Components txt2img uses the split diffusion-model route; image-conditioned modes use the native edit compiler.")
        if family == "qwen_image" and loader == "diffusion_model" and mode in {"img2img", "edit", "inpaint", "outpaint"}:
            workflow_type = f"image.{mode}.qwen_native_edit"
            compiler_id = "comfy.qwen_native_edit"
            phase = "V25.9.20 P3 — Qwen Image Edit Workflow Promotion"
            warnings.append("P3 Qwen Image Edit lock: normal Qwen Image Edit is single-source only; inpaint/outpaint use native mask/canvas workflows.")
        if family == "qwen_image_edit_2509" and loader == "diffusion_model" and mode == "txt2img":
            workflow_type = "image.txt2img.qwen_image_edit_2509_native"
            compiler_id = "comfy.qwen_native"
            phase = "V25.9.20 P3 — Qwen Image Edit 2509 Workflow Promotion"
            warnings.append("P3 Qwen Image Edit 2509 lock: no-source component generation routes through the Qwen native compiler for matrix completeness; 2509 remains primarily an edit family.")
        if family == "qwen_image_edit_2509" and loader == "diffusion_model" and mode in {"img2img", "edit", "inpaint", "outpaint"}:
            workflow_type = f"image.{mode}.qwen_image_edit_2509"
            compiler_id = "comfy.qwen_native_edit"
            phase = "V25.9.20 P3 — Qwen Image Edit 2509 Workflow Promotion"
            warnings.append("P3 Qwen Image Edit 2509 lock: img2img/edit can consume Image 1 plus optional Image 2/Image 3; inpaint/outpaint are implemented single-source mask/canvas workflows.")
        if family == "qwen_image_edit_2511" and loader == "diffusion_model" and mode == "txt2img":
            workflow_type = "image.txt2img.qwen_image_edit_2511_native"
            compiler_id = "comfy.qwen_native"
            phase = "Phase 18 — Qwen Image Edit 2511 Family Onboarding"
            warnings.append("Phase 18 2511 txt2img is an experimental no-source compatibility route; plain qwen_image remains recommended for text-to-image.")
        if family == "qwen_image_edit_2511" and loader == "diffusion_model" and mode in {"img2img", "edit", "inpaint", "outpaint"}:
            workflow_type = f"image.{mode}.qwen_image_edit_2511"
            compiler_id = "comfy.qwen_native_edit"
            phase = "Phase 18 — Qwen Image Edit 2511 Family Onboarding"
            warnings.append("Phase 18 Qwen Image Edit 2511: img2img/edit support Image 1 plus optional Image 2/Image 3; inpaint/outpaint are single-canvas workflows.")
        if family == "qwen_rapid_aio" and loader == "checkpoint_aio":
            workflow_type = f"image.{mode}.qwen_rapid_aio"
            compiler_id = "comfy.qwen_rapid_aio_checkpoint"
            phase = "V25.9.20 Pass E / Pass N3 / P2 — Qwen Rapid AIO Checkpoint Route Cleanup"
            warnings.append("P2 Qwen Rapid AIO visible family: Safetensors / Bundled uses CheckpointLoaderSimple + Qwen edit conditioning, resolves provider_default through qwen_rapid_aio_checkpoint, and prunes external encoder/VAE/MMProj/split-model fields.")
        if family == "flux" and loader == "gguf":
            if flux1_krea:
                workflow_type = f"image.{mode}.flux1_krea_gguf"
                compiler_id = "comfy.flux_gguf.krea"
                phase = "Phase M15 — FLUX.1 Krea Support"
                warnings.append("M15 Krea GGUF lock: Krea remains the FLUX.1 dual-encoder architecture and reuses the provider-owned GGUF latent/mask routes with T5XXL + CLIP-L.")
            else:
                workflow_type = f"image.{mode}.flux_gguf"
                compiler_id = "comfy.flux_gguf"
                phase = "Phase M14.3 — Flux GGUF Runtime Validation + Source Stack Parity" if mode in {"img2img", "inpaint", "outpaint"} else "Phase 12.10 — Flux GGUF txt2img Migration"
                if mode == "txt2img":
                    warnings.append("Pass C Flux 1 lock: GGUF txt2img uses the established provider-owned Flux 1 GGUF route.")
                elif mode == "inpaint":
                    warnings.append("Pass C Flux 1 lock: GGUF inpaint requires source image + mask and uses SetLatentNoiseMask + DifferentialDiffusion.")
                elif mode == "outpaint":
                    warnings.append("Pass C Flux 1 lock: GGUF outpaint requires source image + padding and uses ImagePadForOutpaint.")
                else:
                    warnings.append("Pass C Flux 1 lock: GGUF img2img requires a source image and uses source VAEEncode latent initialization.")
        if family == "z_image" and loader in {"diffusion_model", "gguf"} and mode == "txt2img":
            workflow_type = "image.txt2img.z_image_native" if loader == "diffusion_model" else "image.txt2img.z_image_gguf"
            compiler_id = "comfy.z_image_native" if loader == "diffusion_model" else "comfy.z_image_gguf"
            phase = "V25.9.20 Pass H/P5 — ZImage Family Lock"
            warnings.append("Pass H/P5 ZImage lock: base ZImage uses the native Qwen3/lumina2 + AE/VAE + ModelSamplingAuraFlow stack; Turbo gets its own family pass.")
        if family == "z_image" and loader == "diffusion_model" and mode in {"img2img", "inpaint", "outpaint"}:
            workflow_type = f"image.{mode}.z_image_native"
            compiler_id = "comfy.z_image_native"
            phase = "V25.9.20 P5/P8.4 — ZImage Checkpoint/Safetensors Workflows"
            if mode == "img2img":
                warnings.append("P5/P8.4 ZImage lock: Safetensors / Components img2img uses Image 1 as a VAEEncode latent anchor through the native ZImage compiler.")
            elif mode == "inpaint":
                warnings.append("P5/P8.4 ZImage lock: Safetensors / Components inpaint uses Image 1 + mask with SetLatentNoiseMask + DifferentialDiffusion; no Qwen/Flux/SD fallback.")
            elif mode == "outpaint":
                warnings.append("P5/P8.4 ZImage lock: Safetensors / Components outpaint uses ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion; no Qwen/Flux/SD fallback.")
        if family == "z_image" and loader == "gguf" and mode in {"img2img", "inpaint", "outpaint"}:
            workflow_type = f"image.{mode}.z_image_gguf"
            compiler_id = "comfy.z_image_gguf"
            phase = "V25.9.20 P8.4 — ZImage GGUF Image Workflows Sync"
            if mode == "img2img":
                warnings.append("P8.4 ZImage GGUF lock: img2img uses Image 1 as a VAEEncode latent anchor through the provider-owned ZImage GGUF compiler.")
            elif mode == "inpaint":
                warnings.append("P8.4 ZImage GGUF lock: inpaint uses Image 1 + mask with SetLatentNoiseMask + DifferentialDiffusion; no Qwen/Flux/SD fallback.")
            elif mode == "outpaint":
                warnings.append("P8.4 ZImage GGUF lock: outpaint uses ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion; no Qwen/Flux/SD fallback.")
        if family == "z_image_turbo" and loader in {"diffusion_model", "gguf"} and mode == "txt2img":
            workflow_type = "image.txt2img.z_image_turbo_native" if loader == "diffusion_model" else "image.txt2img.z_image_turbo_gguf"
            compiler_id = "comfy.z_image_native" if loader == "diffusion_model" else "comfy.z_image_gguf"
            phase = "V25.9.20 Pass I/P6 — ZImage Turbo Family Lock"
            warnings.append("Pass I/P8.5 ZImage Turbo lock: Turbo is its own visible family with forced low-step/low-CFG defaults; P8.5 enables component and GGUF img2img/inpaint/outpaint without base ZImage fallback.")
        if family == "z_image_turbo" and loader in {"diffusion_model", "gguf"} and mode in {"img2img", "inpaint", "outpaint"}:
            workflow_type = f"image.{mode}.z_image_turbo_native" if loader == "diffusion_model" else f"image.{mode}.z_image_turbo_gguf"
            compiler_id = "comfy.z_image_native" if loader == "diffusion_model" else "comfy.z_image_gguf"
            phase = "V25.9.20 P8.5 — ZImage Turbo Safetensors + GGUF Workflows"
            if mode == "img2img":
                warnings.append("P8.5 ZImage Turbo lock: image mode uses Image 1 as a VAEEncode latent anchor with family-forced low-step/low-CFG Turbo defaults for the selected loader.")
            elif mode == "inpaint":
                warnings.append("P8.5 ZImage Turbo lock: inpaint uses Image 1 + mask with SetLatentNoiseMask + DifferentialDiffusion and zeroed negative conditioning for the selected loader.")
            elif mode == "outpaint":
                warnings.append("P8.5 ZImage Turbo lock: outpaint uses ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion and zeroed negative conditioning for the selected loader.")
        if family == "hidream" and loader in {"diffusion_model", "gguf"} and mode == "txt2img":
            workflow_type = "image.txt2img.hidream_native" if loader == "diffusion_model" else "image.txt2img.hidream_gguf"
            compiler_id = "comfy.hidream_native" if loader == "diffusion_model" else "comfy.hidream_gguf"
            phase = "Phase 12.16 — HiDream Registry + First Workflow"
            warnings.append("HiDream txt2img requires discovered model, text encoder, VAE/AE, and sampler nodes before graph compile; image-conditioned modes remain variant-gated.")
        if family == "hidream" and loader in {"diffusion_model", "gguf"} and mode == "inpaint" and engine == "lanpaint":
            workflow_type = "image.inpaint.lanpaint"
            compiler_id = "comfy.lanpaint.family_aware.v1"
            phase = "Phase 21 — HiDream-I1 LanPaint Onboarding + Hunyuan Video Hold"
            warnings.append("Phase 21 enables HiDream-I1 LanPaint inpainting with four text encoders and ModelSamplingSD3. E1/E1.1 and O1 remain variant-gated; HunyuanVideo remains held for Video.")
        if family == "anima" and loader in {"diffusion_model", "gguf"} and mode in {"txt2img", "img2img"}:
            workflow_type = f"image.{mode}.anima_native" if loader == "diffusion_model" else f"image.{mode}.anima_gguf"
            compiler_id = "comfy.anima_native" if loader == "diffusion_model" else "comfy.anima_gguf"
            phase = "Phase 22 — Anima Model Family + Image Workflows"
            warnings.append("Phase 22 Anima uses Qwen3 0.6B conditioning and Qwen Image VAE; img2img is a source VAEEncode latent route.")
        if family == "anima" and loader in {"diffusion_model", "gguf"} and mode == "inpaint" and engine == "lanpaint":
            workflow_type = "image.inpaint.lanpaint"; compiler_id = "comfy.lanpaint.family_aware.v1"; phase = "Phase 22 — Anima LanPaint"
            warnings.append("Phase 22 enables Anima through the basic LanPaint KSampler crop/stitch adapter.")
        if family == "ideogram4" and loader in {"diffusion_model", "gguf"} and mode == "txt2img":
            workflow_type = "image.txt2img.ideogram4_native" if loader == "diffusion_model" else "image.txt2img.ideogram4_gguf"
            compiler_id = "comfy.ideogram4_native" if loader == "diffusion_model" else "comfy.ideogram4_gguf"
            phase = "Phase 22 — Ideogram 4 Model Family + txt2img"
            warnings.append("Ideogram 4 requires paired main/unconditional models and the advanced dual-model sampler graph.")
        if family == "ideogram4" and loader in {"diffusion_model", "gguf"} and mode == "inpaint" and engine == "lanpaint":
            workflow_type = "image.inpaint.lanpaint"; compiler_id = "comfy.lanpaint.family_aware.v1"; phase = "Phase 22 — Ideogram 4 LanPaint Advanced"
            warnings.append("Ideogram 4 LanPaint uses LanPaint_SamplerCustomAdvanced; the basic KSampler path is forbidden.")
        if family == "qwen_image" and loader == "gguf":
            workflow_type = f"image.{mode}.qwen_gguf"
            compiler_id = "comfy.qwen_gguf"
            phase = "V25.9.20 Pass F — Qwen Image Edit GGUF Single-Source Lock"
            if mode == "txt2img":
                warnings.append("Pass F Qwen Image Edit GGUF txt2img does not require mmproj.")
            else:
                warnings.append("Pass F Qwen Image Edit GGUF image route is single-source only and requires source image + mmproj; inpaint also requires mask, outpaint also requires padding.")
        if family == "qwen_rapid_aio" and loader == "gguf":
            workflow_type = f"image.{mode}.qwen_rapid_aio_gguf"
            compiler_id = "comfy.qwen_gguf"
            phase = "V25.9.20 Pass E / Pass N3 — Qwen Rapid AIO GGUF Workflow Implementation"
            if mode == "txt2img":
                warnings.append("Pass N3 Qwen Rapid AIO GGUF txt2img uses the existing Qwen single-encoder GGUF compiler and does not require mmproj.")
            elif mode in {"img2img", "edit"}:
                warnings.append("Pass N3 Qwen Rapid AIO GGUF img2img/edit requires source image + Qwen MMProj and can consume optional Image 2/Image 3.")
            else:
                warnings.append("Pass N3 Qwen Rapid AIO GGUF image route requires source image + Qwen MMProj; inpaint also requires mask, outpaint also requires padding.")
        if family == "qwen_image_edit_2509" and loader == "gguf":
            workflow_type = f"image.{mode}.qwen_image_edit_2509_gguf"
            compiler_id = "comfy.qwen_gguf"
            phase = "V25.9.20 Pass G — Qwen Image Edit 2509 GGUF Route"
            if mode == "txt2img":
                warnings.append("Pass G Qwen Image Edit 2509 GGUF txt2img uses the Qwen single-encoder GGUF compiler and does not require mmproj.")
            elif mode in {"img2img", "edit"}:
                warnings.append("Pass G Qwen Image Edit 2509 GGUF image edit can consume Image 1 plus optional Image 2/Image 3 and requires Qwen MMProj.")
            else:
                warnings.append("Pass G Qwen Image Edit 2509 GGUF inpaint/outpaint uses the existing single-source source/mask/padding graph and requires Qwen MMProj.")
        if family == "qwen_image_edit_2511" and loader == "gguf":
            workflow_type = f"image.{mode}.qwen_image_edit_2511_gguf"
            compiler_id = "comfy.qwen_gguf"
            phase = "Phase 18 — Qwen Image Edit 2511 Family Onboarding"
            if mode == "txt2img":
                warnings.append("Phase 18 2511 GGUF txt2img is an experimental no-source compatibility route and does not require MMProj.")
            elif mode in {"img2img", "edit"}:
                warnings.append("Phase 18 2511 GGUF multi-source edit requires a matching Qwen2.5-VL MMProj sidecar and supports Image 1 plus optional Image 2/Image 3.")
            else:
                warnings.append("Phase 18 2511 GGUF inpaint/outpaint uses Image 1 as the canvas and requires the matching Qwen2.5-VL MMProj sidecar.")
        return CompileRoute(
            provider_id=job.provider_id,
            backend="comfyui",
            family=family,
            loader=loader,
            mode=mode,
            requested_mode=requested_mode,
            status="available",
            compiler_id=compiler_id,
            workflow_type=workflow_type,
            phase=phase,
            warnings=warnings,
        )

    if key in PLANNED_COMFY_ROUTES:
        return CompileRoute(
            provider_id=job.provider_id,
            backend="comfyui",
            family=family,
            loader=loader,
            mode=mode,
            requested_mode=requested_mode,
            status="planned",
            phase=PLANNED_COMFY_ROUTES[key],
            blockers=[f"Compile route is declared but not enabled yet: {family}+{loader}+{mode}."],
        )

    if family in PROVIDER_GATED_FAMILIES:
        phase = "Phase 12.17 — Wan Image Provider-Gated Support" if family == "wan_image" else "Phase 12.18 — Hunyuan Image Provider-Gated Support"
        blocker = (
            "Wan Image routes are provider-gated; txt2img/img2img/inpaint/outpaint stay disabled until a confirmed image workflow/compiler exists."
            if family == "wan_image"
            else "Hunyuan Image routes are provider-gated; txt2img/img2img/inpaint/outpaint stay disabled until an exact model branch and backend workflow/compiler are selected."
        )
        return CompileRoute(
            provider_id=job.provider_id,
            backend="comfyui",
            family=family,
            loader=loader,
            mode=mode,
            requested_mode=requested_mode,
            status="provider_gated",
            phase=phase,
            blockers=[blocker],
        )

    if mode in VARIANT_GATED_MODES:
        return CompileRoute(
            provider_id=job.provider_id,
            backend="comfyui",
            family=family,
            loader=loader,
            mode=mode,
            requested_mode=requested_mode,
            status="variant_gated",
            phase="Variant-specific workflow phase",
            blockers=[f"{family}+{loader}+{mode} requires a variant-specific compiler route."],
        )

    return CompileRoute(
        provider_id=job.provider_id,
        backend="comfyui",
        family=family,
        loader=loader,
        mode=mode,
        requested_mode=requested_mode,
        status="unsupported",
        blockers=[f"No Comfy compile route registered for {family}+{loader}+{mode}."],
    )
