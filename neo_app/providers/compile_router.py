from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
import json

from neo_app.providers.schema import NeoJob
from neo_app.models.route_matrix import normalize_backend, resolve_model_backend_route


MODE_ALIASES = {
    "generate": "txt2img",
    "text_to_image": "txt2img",
    "image_to_image": "img2img",
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

    Comfy remains the only fully implemented image provider compiler in the V2
    base. Other backends resolve against the route matrix and return gated
    CompileRoute objects until their adapters are implemented and validated.
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
        phase="Phase 12.31 — Backend Plug-in Contract",
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
        if family == "flux" and loader == "diffusion_model" and mode == "txt2img":
            workflow_type = "image.txt2img.flux_native"
            compiler_id = "comfy.flux_native"
            phase = "Phase 12.9 / V25.9.20 Pass O2 — Flux Native Workflow Foundation"
            warnings.append("P1 Flux 1 lock: Safetensors / Components supports txt2img, img2img, and internal Flux Fill inpaint/outpaint without exposing Flux Fill as a separate family.")
        if family == "flux" and loader == "diffusion_model" and mode == "img2img":
            workflow_type = "image.img2img.flux_native"
            compiler_id = "comfy.flux_native"
            phase = "V25.9.20 Pass O2 — Flux 1 Img2Img Workflow Implementation"
            warnings.append("P1 Flux 1 lock: img2img uses Image 1 as a VAEEncode latent anchor; inpaint/outpaint use the internal Flux Fill compiler through the normal Flux 1 family.")
        if family == "flux" and loader == "diffusion_model" and mode in {"inpaint", "outpaint"}:
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
        if family == "qwen_rapid_aio" and loader == "checkpoint_aio":
            workflow_type = f"image.{mode}.qwen_rapid_aio"
            compiler_id = "comfy.qwen_rapid_aio_checkpoint"
            phase = "V25.9.20 Pass E / Pass N3 / P2 — Qwen Rapid AIO Checkpoint Route Cleanup"
            warnings.append("P2 Qwen Rapid AIO visible family: Safetensors / Bundled uses CheckpointLoaderSimple + Qwen edit conditioning, resolves provider_default through qwen_rapid_aio_checkpoint, and prunes external encoder/VAE/MMProj/split-model fields.")
        if family == "flux" and loader == "gguf":
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
