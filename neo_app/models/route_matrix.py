from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from neo_app.models.registry import get_family, get_families

RouteState = Literal[
    "available",
    "experimental_available",
    "implementation_target",
    "planned_gated",
    "provider_gated",
    "unsupported",
]

ROUTE_STATES: tuple[str, ...] = (
    "available",
    "experimental_available",
    "implementation_target",
    "planned_gated",
    "provider_gated",
    "unsupported",
)

ROUTE_STATE_UI_POLICY: dict[str, dict[str, Any]] = {
    "available": {
        "selectable": True,
        "visible_in_normal_ui": True,
        "visible_in_diagnostics": True,
        "badge": None,
    },
    "experimental_available": {
        "selectable": True,
        "visible_in_normal_ui": True,
        "visible_in_diagnostics": True,
        "badge": "Experimental",
    },
    "implementation_target": {
        "selectable": False,
        "visible_in_normal_ui": False,
        "visible_in_diagnostics": True,
        "badge": "Implementation target",
    },
    "planned_gated": {
        "selectable": False,
        "visible_in_normal_ui": False,
        "visible_in_diagnostics": True,
        "badge": "Planned",
    },
    "provider_gated": {
        "selectable": False,
        "visible_in_normal_ui": False,
        "visible_in_diagnostics": True,
        "badge": "Provider gated",
    },
    "unsupported": {
        "selectable": False,
        "visible_in_normal_ui": False,
        "visible_in_diagnostics": False,
        "badge": None,
    },
}

BACKEND_ALIASES = {
    "comfyui_portable": "comfyui",
    "comfy": "comfyui",
    "automatic1111": "a1111",
    "sd_webui": "a1111",
    "forge": "forge",
}

IMAGE_MODES: tuple[str, ...] = ("txt2img", "img2img", "inpaint", "outpaint")
IMAGE_ROUTE_MODES_WITH_EDIT: tuple[str, ...] = IMAGE_MODES + ("edit",)


@dataclass(frozen=True)
class RouteMatrixEntry:
    family: str
    loader: str
    backend: str
    mode: str
    state: RouteState
    reason: str = ""
    workflow_type: str | None = None
    compiler_id: str | None = None
    requires: list[str] = field(default_factory=list)
    parameter_profile: str | None = None
    provider_nodes: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def selectable(self) -> bool:
        return bool(ROUTE_STATE_UI_POLICY[self.state]["selectable"])

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"selectable": self.selectable, "ui_policy": ROUTE_STATE_UI_POLICY[self.state]}


def normalize_backend(backend: str | None) -> str:
    value = str(backend or "comfyui").strip() or "comfyui"
    return BACKEND_ALIASES.get(value, value)


def normalize_mode(mode: str | None) -> str:
    value = str(mode or "txt2img").strip() or "txt2img"
    return {
        "generate": "txt2img",
        "text_to_image": "txt2img",
        "image_to_image": "img2img",
    }.get(value, value)


# Phase 12.24: this table is a product/runtime contract. It declares what the
# UI may show and what the backend may compile. It does not replace provider
# discovery; providers must still validate assets/nodes before queueing.
_COMFY_EXPLICIT_ROUTES: dict[tuple[str, str, str], RouteMatrixEntry] = {}


def _add_comfy(entry: RouteMatrixEntry) -> None:
    _COMFY_EXPLICIT_ROUTES[(entry.family, entry.loader, entry.mode)] = entry


for _family in ("sdxl", "sd15"):
    for _mode in IMAGE_MODES:
        _workflow = f"image.{_mode}.{_family}_checkpoint" if _mode == "outpaint" else f"image.{_mode}.{_family}"
        _requires = ["checkpoint"]
        if _mode in {"img2img", "inpaint", "outpaint"}:
            _requires.append("source_image")
        if _mode == "inpaint":
            _requires.append("mask_image")
        if _mode == "outpaint":
            _requires.append("outpaint_padding")
        _reason = (
            "V25.9.20 Pass A locks SDXL Safetensors / Checkpoint as the only normal Image SDXL route; all four base modes use the V2 Comfy checkpoint compiler."
            if _family == "sdxl"
            else "V25.9.20 Pass B locks SD 1.5 Safetensors / Checkpoint as the only normal Image SD 1.5 route; all four base modes keep the V2 Comfy checkpoint compiler."
        )
        _notes = (
            ["SDXL split diffusion_model/unet component routes are intentionally not selectable in normal UI until a separate validated route exists."]
            if _family == "sdxl"
            else ["SD 1.5 legacy unet/split-component roles are intentionally not selectable in normal UI until a separate validated route exists."]
        )
        _add_comfy(RouteMatrixEntry(
            family=_family,
            loader="checkpoint",
            backend="comfyui",
            mode=_mode,
            state="available",
            workflow_type=_workflow,
            compiler_id="comfy.checkpoint_sd",
            requires=_requires,
            parameter_profile="sd_checkpoint",
            reason=_reason,
            notes=_notes,
        ))

_add_comfy(RouteMatrixEntry(
    family="flux",
    loader="diffusion_model",
    backend="comfyui",
    mode="txt2img",
    state="available",
    workflow_type="image.txt2img.flux_native",
    compiler_id="comfy.flux_native",
    requires=["diffusion_model", "text_encoder_primary", "text_encoder_secondary", "vae_or_ae", "flux_guidance"],
    parameter_profile="flux_native",
    reason="V25.9.20 Pass C locks Flux 1 Safetensors / Components as the safe split-component txt2img route; P0 reclassifies missing checkpoint image-conditioned modes as implementation targets instead of future gates.",
    notes=[
        "Flux 1 safetensors/components uses Comfy UNETLoader + DualCLIPLoader + VAELoader + FluxGuidance.",
        "Visible UI label is Safetensors / Components; internal loader remains diffusion_model.",
        "Flux 2 Klein is promoted to visible family flux2_klein in Pass D; this Flux 1 route keeps only Flux 1 component behavior."
    ],
))
_add_comfy(RouteMatrixEntry(
    family="flux",
    loader="gguf",
    backend="comfyui",
    mode="txt2img",
    state="available",
    workflow_type="image.txt2img.flux_gguf",
    compiler_id="comfy.flux_gguf",
    requires=["gguf_unet", "gguf_text_encoder_primary", "gguf_text_encoder_secondary", "vae_or_ae", "flux_guidance"],
    parameter_profile="flux_gguf",
    provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_dual_loader": "DualCLIPLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
    reason="V25.9.20 Pass C keeps Flux 1 GGUF txt2img available through the validated provider-owned GGUF compiler; Flux 2 Klein auto-detect behavior is preserved for Pass D instead of being exposed as a Flux 1 UI architecture selector.",
    notes=[
        "Flux 1 GGUF uses the GGUF runtime card with automatic route resolution, not a manual Architecture dropdown.",
        "Legacy Flux 1 GGUF requires GGUF model, text encoders, AE/VAE, and Flux guidance."
    ],
))
_add_comfy(RouteMatrixEntry(
    family="flux",
    loader="diffusion_model",
    backend="comfyui",
    mode="img2img",
    state="experimental_available",
    workflow_type="image.img2img.flux_native",
    compiler_id="comfy.flux_native",
    requires=["diffusion_model", "text_encoder_primary", "text_encoder_secondary", "vae_or_ae", "source_image", "flux_guidance"],
    parameter_profile="flux_native",
    provider_nodes={"diffusion_model_loader": "UNETLoader", "dual_clip_loader": "DualCLIPLoader", "source_loader": "LoadImage", "source_encoder": "VAEEncode"},
    reason="V25.9.20 Pass O2 enables Flux 1 Safetensors / Components img2img as an experimental Image-1 VAEEncode latent-anchor workflow. Fill modes remain owned by Flux 1 Fill.",
    notes=[
        "img2img does not imply inpaint/outpaint support for the normal Flux 1 base model.",
        "Do not fallback to Flux GGUF, SD checkpoint, Qwen, or generic image-conditioned compilers.",
    ],
))
_add_comfy(RouteMatrixEntry(
    family="flux",
    loader="diffusion_model",
    backend="comfyui",
    mode="inpaint",
    state="experimental_available",
    workflow_type="image.inpaint.flux_fill_internal",
    compiler_id="comfy.flux_fill",
    requires=["diffusion_model", "text_encoder_primary", "text_encoder_secondary", "vae_or_ae", "source_image", "mask_image", "flux_guidance"],
    parameter_profile="flux_native",
    provider_nodes={
        "diffusion_model_loader": "UNETLoader",
        "dual_clip_loader": "DualCLIPLoader",
        "inpaint_conditioning": "InpaintModelConditioning",
        "sampling_patch": "DifferentialDiffusion",
    },
    reason="V25.9.20 P1 resolves Flux 1 Safetensors / Components inpaint through the internal Flux Fill workflow instead of exposing Flux 1 Fill as a separate normal family.",
    notes=[
        "Use a FLUX.1 Fill-dev/compatible fill diffusion model in the normal Flux 1 Safetensors / Components model picker.",
        "Do not fallback to Flux GGUF, SD checkpoint, Qwen, or generic inpaint compilers.",
        "Flux Fill is an internal workflow variant for Flux 1 inpaint/outpaint, not a Model Family dropdown entry.",
    ],
))
_add_comfy(RouteMatrixEntry(
    family="flux",
    loader="diffusion_model",
    backend="comfyui",
    mode="outpaint",
    state="experimental_available",
    workflow_type="image.outpaint.flux_fill_internal",
    compiler_id="comfy.flux_fill",
    requires=["diffusion_model", "text_encoder_primary", "text_encoder_secondary", "vae_or_ae", "source_image", "outpaint_padding", "flux_guidance"],
    parameter_profile="flux_native",
    provider_nodes={
        "diffusion_model_loader": "UNETLoader",
        "dual_clip_loader": "DualCLIPLoader",
        "inpaint_conditioning": "InpaintModelConditioning",
        "sampling_patch": "DifferentialDiffusion",
        "outpaint_pad": "ImagePadForOutpaint",
    },
    reason="V25.9.20 P1 resolves Flux 1 Safetensors / Components outpaint through the internal Flux Fill workflow instead of exposing Flux 1 Fill as a separate normal family.",
    notes=[
        "Use a FLUX.1 Fill-dev/compatible fill diffusion model in the normal Flux 1 Safetensors / Components model picker.",
        "Do not fallback to Flux GGUF, SD checkpoint, Qwen, or generic outpaint compilers.",
        "Flux Fill is an internal workflow variant for Flux 1 inpaint/outpaint, not a Model Family dropdown entry.",
    ],
))

# V25.9.20 P1 — Legacy flux1_fill alias is diagnostics-only; normal UI uses flux + diffusion_model.
for _mode in ("inpaint", "outpaint"):
    _requires = ["diffusion_model", "text_encoder_primary", "text_encoder_secondary", "vae_or_ae", "source_image", "flux_guidance"]
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="flux1_fill",
        loader="diffusion_model",
        backend="comfyui",
        mode=_mode,
        state="unsupported",
        workflow_type=None,
        compiler_id=None,
        requires=_requires,
        parameter_profile=None,
        provider_nodes={
            "diffusion_model_loader": "UNETLoader",
            "dual_clip_loader": "DualCLIPLoader",
            "inpaint_conditioning": "InpaintModelConditioning",
            "sampling_patch": "DifferentialDiffusion",
            "outpaint_pad": "ImagePadForOutpaint",
        },
        reason="V25.9.20 P1 keeps flux1_fill as a legacy/internal alias only; normal Image UI must use family=flux + loader=diffusion_model + inpaint/outpaint.",
        notes=[
            "Flux 1 Fill is no longer a normal visible Model Family dropdown option.",
            "Use the normal Flux 1 Safetensors / Components route; it resolves internally to compiler_id=comfy.flux_fill.",
        ],
    ))
for _mode in ("img2img", "inpaint", "outpaint"):
    _requires = ["gguf_unet", "gguf_text_encoder_primary", "vae_or_ae", "source_image", "flux_guidance"]
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="flux",
        loader="gguf",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.flux_gguf",
        compiler_id="comfy.flux_gguf",
        requires=_requires,
        parameter_profile="flux_gguf",
        provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_dual_loader": "DualCLIPLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
        reason="V25.9.20 Pass C keeps Flux 1 GGUF image-conditioned routes available through the provider-owned source stack; inpaint adds latent noise mask + DifferentialDiffusion, and outpaint consumes ImagePadForOutpaint output 1 as the latent noise mask instead of preserving gray padding. Runtime validation was already recorded after Phase M14.2/M14.3.",
        notes=[
            "Flux 1 GGUF image-conditioned support stays inside comfy.flux_gguf and must not fallback across families/loaders.",
            "ControlNet/LoRA/High-Res extensions still resolve their own route-specific support instead of inheriting base-route availability blindly."
        ],
    ))


# V25.9.20 P4 — FLUX.2 Klein component workflow promotion.
for _mode in ("txt2img", "img2img", "edit", "inpaint", "outpaint"):
    _requires = ["diffusion_model", "qwen3_text_encoder", "vae_or_ae", "flux_guidance"]
    if _mode in {"img2img", "edit", "inpaint", "outpaint"}:
        _requires.append("source_image")
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _provider_nodes = {
        "diffusion_model_loader": "UNETLoader",
        "single_clip_loader": "CLIPLoader(type=flux2)",
        "vae_loader": "VAELoader",
        "text_conditioning": "CLIPTextEncode + FluxGuidance",
        "latent_node": "EmptyFlux2LatentImage" if _mode == "txt2img" else "LoadImage + VAEEncode",
        "sampler": "KSampler",
    }
    if _mode in {"inpaint", "outpaint"}:
        _provider_nodes.update({"latent_mask": "SetLatentNoiseMask", "sampling_patch": "DifferentialDiffusion"})
    if _mode == "outpaint":
        _provider_nodes["outpaint_pad"] = "ImagePadForOutpaint"
    _add_comfy(RouteMatrixEntry(
        family="flux2_klein",
        loader="diffusion_model",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type="image.txt2img.flux2_klein" if _mode == "txt2img" else f"image.{_mode}.flux2_klein_native",
        compiler_id="comfy.flux_klein",
        requires=_requires,
        parameter_profile="flux2_klein_native",
        provider_nodes=_provider_nodes,
        reason="V25.9.20 P4 promotes Flux 2 Klein Safetensors / Components img2img/edit/inpaint/outpaint to real Klein-native compiler routes instead of keeping them as implementation targets.",
        notes=[
            "Internal loader remains diffusion_model, but normal UI labels it Safetensors / Components.",
            "Flux 2 Klein component routing uses a single Qwen3 encoder, CLIPLoader(type=flux2), FluxGuidance, and Flux2 VAE handling.",
            "img2img/edit encode Image 1 as the Flux2 latent anchor; inpaint/outpaint add mask/canvas through SetLatentNoiseMask and DifferentialDiffusion.",
            "Do not fallback to Flux 1 Fill, Flux 1 native, Flux GGUF, SD checkpoint, or Qwen image edit compilers.",
        ],
    ))
for _mode in IMAGE_ROUTE_MODES_WITH_EDIT:
    _requires = ["gguf_unet", "gguf_text_encoder_primary", "vae_or_ae", "flux_guidance"]
    if _mode in {"img2img", "edit", "inpaint", "outpaint"}:
        _requires.append("source_image")
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="flux2_klein",
        loader="gguf",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.flux2_klein_gguf",
        compiler_id="comfy.flux_gguf.klein",
        requires=_requires,
        parameter_profile="flux2_klein_gguf",
        provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "single_clip_loader": "CLIPLoader or CLIPLoaderGGUF", "latent_node": "EmptyFlux2LatentImage"},
        reason="V25.9.20 Pass D / Pass O1 validates the Flux 2 Klein GGUF Image-1 latent-anchor img2img/edit route. Optional Image 2/Image 3 stay replay/reference metadata until a dedicated local Flux2 multi-reference conditioning node is validated.",
        notes=[
            "Single Qwen3 text encoder only; no legacy Flux 1 dual-encoder GGUF layout.",
            "Klein GGUF compiler resolves CLIPLoader(type=flux2) for safetensors encoders or CLIPLoaderGGUF(type=flux2) for GGUF encoders.",
            "img2img/edit/inpaint/outpaint expose Image 1 plus optional Image 2/Image 3 source lanes; Pass O1 validates the local img2img/edit compiler shape as an Image-1 VAEEncode latent-anchor route while extra lanes are preserved for replay/future Flux2 multi-reference conditioning. ControlNet/LoRA/High-Res remain route-specific extension decisions.",
        ],
    ))

_add_comfy(RouteMatrixEntry(
    family="qwen_image",
    loader="gguf",
    backend="comfyui",
    mode="txt2img",
    state="available",
    workflow_type="image.txt2img.qwen_gguf",
    compiler_id="comfy.qwen_gguf",
    requires=["gguf_unet", "gguf_text_encoder_primary", "vae"],
    parameter_profile="qwen_gguf",
    provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
    reason="V25.9.20 Pass F locks Qwen Image Edit GGUF txt2img as a single-source-capable local route; txt2img does not require mmproj.",
    notes=["Normal Qwen Image Edit stays single-source; multi-source Qwen editing is reserved for the separate Qwen Image Edit 2509 family pass."],
))
for _mode in ("img2img", "inpaint", "outpaint"):
    _requires = ["gguf_unet", "gguf_text_encoder_primary", "vae", "qwen_mmproj", "source_image"]
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="qwen_image",
        loader="gguf",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.qwen_gguf",
        compiler_id="comfy.qwen_gguf",
        requires=_requires,
        parameter_profile="qwen_gguf",
        provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
        reason="V25.9.20 Pass F locks Qwen Image Edit GGUF image-conditioned routes as single-source routes; they require mmproj plus source/mask/padding inputs as applicable.",
        notes=["source_image_2/source_image_3 are not consumed by qwen_image GGUF; Qwen Image Edit 2509 will own multi-source behavior."],
    ))

# V25.9.20 Pass E — Qwen Rapid AIO visible family route lock.
for _mode in IMAGE_ROUTE_MODES_WITH_EDIT:
    _requires = ["qwen_rapid_aio_checkpoint"]
    if _mode in {"img2img", "inpaint", "outpaint", "edit"}:
        _requires.append("source_image")
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="qwen_rapid_aio",
        loader="checkpoint_aio",
        backend="comfyui",
        mode=_mode,
        state="experimental_available",
        workflow_type=f"image.{_mode}.qwen_rapid_aio",
        compiler_id="comfy.qwen_rapid_aio_checkpoint",
        requires=_requires,
        parameter_profile="qwen_rapid_aio",
        provider_nodes={"checkpoint_loader": "CheckpointLoaderSimple", "conditioning": "TextEncodeQwenImageEditPlus"},
        reason="V25.9.20 Pass E / Pass N3 / P2 locks Qwen Rapid AIO Safetensors / Bundled normal workflows: txt2img, img2img/edit, inpaint, and outpaint compile through the AIO checkpoint graph with bundled component cleanup.",
        notes=[
            "Internal loader remains checkpoint_aio, but normal UI labels it Safetensors / Bundled.",
            "P2: AIO checkpoint route keeps external encoder/VAE/MMProj/GGUF/split diffusion fields hidden and pruned before compile; no Advanced Override leaks hidden components into the graph.",
            "Pass N3: img2img/edit may use optional Image 2/Image 3 through Qwen edit conditioning; inpaint/outpaint stay single-source mask/canvas routes.",
        ],
    ))

_add_comfy(RouteMatrixEntry(
    family="qwen_rapid_aio",
    loader="gguf",
    backend="comfyui",
    mode="txt2img",
    state="available",
    workflow_type="image.txt2img.qwen_rapid_aio_gguf",
    compiler_id="comfy.qwen_gguf",
    requires=["gguf_unet", "gguf_text_encoder_primary", "vae"],
    parameter_profile="qwen_gguf",
    provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
    reason="V25.9.20 Pass E adds Qwen Rapid AIO GGUF as a visible family route by reusing Neo's existing Qwen single-encoder GGUF compiler; txt2img does not require mmproj.",
    notes=[
        "GGUF is a first-class Main Model Type for Qwen Rapid AIO because Neo has been using the Qwen Rapid AIO GGUF route in runtime testing.",
        "This is Neo route support, not a claim that every official AIO repository ships GGUF files.",
    ],
))
for _mode in ("img2img", "edit", "inpaint", "outpaint"):
    _requires = ["gguf_unet", "gguf_text_encoder_primary", "vae", "qwen_mmproj", "source_image"]
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="qwen_rapid_aio",
        loader="gguf",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.qwen_rapid_aio_gguf",
        compiler_id="comfy.qwen_gguf",
        requires=_requires,
        parameter_profile="qwen_gguf",
        provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
        reason="V25.9.20 Pass E / Pass N3 completes Qwen Rapid AIO GGUF normal workflow coverage: edit aliases to img2img, while inpaint/outpaint use the source/mmproj mask/canvas stack.",
        notes=["Requires source image plus Qwen MMProj sidecar; img2img/edit may consume optional Image 2/Image 3, inpaint also requires mask, outpaint also requires padding."],
    ))
# Qwen native txt2img is available through the original split-model compiler.
_add_comfy(RouteMatrixEntry(
    family="qwen_image",
    loader="diffusion_model",
    backend="comfyui",
    mode="txt2img",
    state="available",
    workflow_type="image.txt2img.qwen_native",
    compiler_id="comfy.qwen_native",
    requires=["diffusion_model", "qwen_text_encoder", "vae"],
    parameter_profile="qwen_native",
    reason="V25.9.20 P3 promotes Qwen Image Edit Safetensors / Components txt2img through the existing split diffusion-model route while the image-conditioned modes compile through the native edit route.",
    notes=["Visible family is Qwen Image Edit; internal family_id remains qwen_image for compatibility.", "P3 cleanup removes old gate-first language: image-conditioned Qwen component workflows are implementation-complete routes, not parked planned gates."],
))
_add_comfy(RouteMatrixEntry(
    family="qwen_image",
    loader="diffusion_model",
    backend="comfyui",
    mode="img2img",
    state="available",
    workflow_type="image.img2img.qwen_native_edit",
    compiler_id="comfy.qwen_native_edit",
    requires=["qwen_image_edit_model", "qwen_text_encoder", "vae", "source_image"],
    parameter_profile="qwen_native",
    reason="V25.9.20 P3 promotes normal Qwen Image Edit Safetensors / Components img2img as a real single-source native edit workflow.",
    notes=["Do not consume source_image_2/source_image_3 on qwen_image; Qwen Image Edit 2509 owns the multi-source family behavior.", "Qwen image-conditioned routing uses TextEncodeQwenImageEditPlus with Image 1 only for normal qwen_image."],
))
_add_comfy(RouteMatrixEntry(
    family="qwen_image",
    loader="diffusion_model",
    backend="comfyui",
    mode="edit",
    state="available",
    workflow_type="image.edit.qwen_native_edit",
    compiler_id="comfy.qwen_native_edit",
    requires=["qwen_image_edit_model", "qwen_text_encoder", "vae", "source_image"],
    parameter_profile="qwen_native",
    reason="V25.9.20 P3 promotes the edit alias for normal Qwen Image Edit as a real single-source native edit workflow.",
    notes=["Edit aliases to img2img inside the provider compiler and ignores extra source lanes for qwen_image."],
))
for _mode in ("inpaint", "outpaint"):
    _requires = ["diffusion_model", "qwen_text_encoder", "vae", "source_image"]
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="qwen_image",
        loader="diffusion_model",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.qwen_native_edit",
        compiler_id="comfy.qwen_native_edit",
        requires=_requires,
        parameter_profile="qwen_native",
        provider_nodes={"diffusion_model_loader": "UNETLoader", "text_encoder_loader": "CLIPLoader", "conditioning": "TextEncodeQwenImageEditPlus"},
        reason="V25.9.20 P3 promotes Qwen Image Edit Safetensors / Components single-source native mask/canvas workflows as implemented routes.",
        notes=[
            "Normal Qwen Image Edit remains single-source only; source_image_2/source_image_3 are ignored for this family.",
            "Inpaint uses source VAEEncode + SetLatentNoiseMask + ModelSamplingAuraFlow + DifferentialDiffusion + final masked composite; outpaint uses ImagePadForOutpaint plus a padded latent canvas.",
            "P3 cleanup: this is a selectable workflow route, not a planned gate or placeholder.",
        ],
    ))


# V25.9.20 Pass G — Qwen Image Edit 2509 visible family route lock.
# 2509 owns multi-source Qwen editing; normal qwen_image stays single-source.
for _mode in ("img2img", "edit"):
    _add_comfy(RouteMatrixEntry(
        family="qwen_image_edit_2509",
        loader="diffusion_model",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.qwen_image_edit_2509",
        compiler_id="comfy.qwen_native_edit",
        requires=["qwen_image_edit_model", "qwen_text_encoder", "vae", "source_image"],
        parameter_profile="qwen_2509_native",
        provider_nodes={"diffusion_model_loader": "UNETLoader", "text_encoder_loader": "CLIPLoader", "conditioning": "TextEncodeQwenImageEditPlus"},
        reason="V25.9.20 P3 promotes Qwen Image Edit 2509 Safetensors / Components img2img/edit as real multi-source native edit workflows with 1–3 source images.",
        notes=[
            "source_image_2/source_image_3 are consumed only by qwen_image_edit_2509, not normal qwen_image.",
            "No fallback to Qwen Rapid AIO checkpoint, normal Qwen Image Edit single-source, Flux, SD checkpoint, or Z-Image routes.",
        ],
    ))
_add_comfy(RouteMatrixEntry(
    family="qwen_image_edit_2509",
    loader="diffusion_model",
    backend="comfyui",
    mode="txt2img",
    state="available",
    workflow_type="image.txt2img.qwen_image_edit_2509_native",
    compiler_id="comfy.qwen_native",
    requires=["diffusion_model", "qwen_text_encoder", "vae"],
    parameter_profile="qwen_2509_native",
    provider_nodes={"diffusion_model_loader": "UNETLoader", "text_encoder_loader": "CLIPLoader", "sampling_patch": "ModelSamplingAuraFlow"},
    reason="V25.9.20 P3 removes the old 2509 Safetensors / Components txt2img gate and routes no-source generation through the Qwen native component compiler for matrix completeness.",
    notes=["2509 remains primarily an edit family; img2img/edit own 1–3 sources while inpaint/outpaint own single-source mask/canvas workflows."],
))
for _mode in ("inpaint", "outpaint"):
    _requires = ["diffusion_model", "qwen_text_encoder", "vae", "source_image"]
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="qwen_image_edit_2509",
        loader="diffusion_model",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.qwen_image_edit_2509",
        compiler_id="comfy.qwen_native_edit",
        requires=_requires,
        parameter_profile="qwen_2509_native",
        provider_nodes={"diffusion_model_loader": "UNETLoader", "text_encoder_loader": "CLIPLoader", "conditioning": "TextEncodeQwenImageEditPlus"},
        reason="V25.9.20 P3 promotes Qwen Image Edit 2509 Safetensors / Components mask/canvas workflows through the native Qwen edit compiler.",
        notes=[
            "2509 owns 1–3 source lanes for img2img/edit only; inpaint/outpaint intentionally prune to single-source mask/canvas routes.",
            "Inpaint uses source VAEEncode + SetLatentNoiseMask + ModelSamplingAuraFlow + DifferentialDiffusion + final masked composite; outpaint uses ImagePadForOutpaint plus a padded latent canvas.",
            "P3 cleanup: these are implemented selectable routes, not future-gated placeholders.",
        ],
    ))
_add_comfy(RouteMatrixEntry(
    family="qwen_image_edit_2509",
    loader="gguf",
    backend="comfyui",
    mode="txt2img",
    state="available",
    workflow_type="image.txt2img.qwen_image_edit_2509_gguf",
    compiler_id="comfy.qwen_gguf",
    requires=["gguf_unet", "gguf_text_encoder_primary", "vae"],
    parameter_profile="qwen_2509_gguf",
    provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
    reason="V25.9.20 Pass G adds Qwen Image Edit 2509 GGUF through the existing Qwen single-encoder GGUF compiler; txt2img does not require MMProj.",
    notes=["GGUF txt2img is route-compatible but 2509's main product reason is multi-source img2img/edit."],
))
for _mode in ("img2img", "edit"):
    _add_comfy(RouteMatrixEntry(
        family="qwen_image_edit_2509",
        loader="gguf",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.qwen_image_edit_2509_gguf",
        compiler_id="comfy.qwen_gguf",
        requires=["gguf_unet", "gguf_text_encoder_primary", "vae", "qwen_mmproj", "source_image"],
        parameter_profile="qwen_2509_gguf",
        provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF", "conditioning": "TextEncodeQwenImageEditPlus"},
        reason="V25.9.20 Pass G enables Qwen Image Edit 2509 GGUF multi-source img2img/edit with 1–3 source images through the Qwen edit encoder.",
        notes=["source_image_2/source_image_3 are consumed by this family for img2img/edit when provided."],
    ))
for _mode in ("inpaint", "outpaint"):
    _requires = ["gguf_unet", "gguf_text_encoder_primary", "vae", "qwen_mmproj", "source_image"]
    if _mode == "inpaint":
        _requires.append("mask_image")
    if _mode == "outpaint":
        _requires.append("outpaint_padding")
    _add_comfy(RouteMatrixEntry(
        family="qwen_image_edit_2509",
        loader="gguf",
        backend="comfyui",
        mode=_mode,
        state="available",
        workflow_type=f"image.{_mode}.qwen_image_edit_2509_gguf",
        compiler_id="comfy.qwen_gguf",
        requires=_requires,
        parameter_profile="qwen_2509_gguf",
        provider_nodes={"gguf_unet_loader": "UnetLoaderGGUF", "gguf_clip_single_loader": "CLIPLoaderGGUF"},
        reason="V25.9.20 Pass G maps Qwen Image Edit 2509 GGUF inpaint/outpaint to the existing Qwen GGUF source/mask/padding compiler; multi-source composition remains scoped to img2img/edit.",
        notes=["Inpaint/outpaint use source image + mask/padding; extra source lanes are not consumed for these modes."],
    ))

# V25.9.20 Pass F: qwen_image no longer exposes checkpoint_aio; Qwen Rapid AIO owns bundled checkpoint routes.


# V25.9.20 Pass H — ZImage base visible family route lock.
for _loader in ("diffusion_model", "gguf"):
    _add_comfy(RouteMatrixEntry(
        family="z_image",
        loader=_loader,
        backend="comfyui",
        mode="txt2img",
        state="available",
        workflow_type="image.txt2img.z_image_native" if _loader == "diffusion_model" else "image.txt2img.z_image_gguf",
        compiler_id="comfy.z_image_native" if _loader == "diffusion_model" else "comfy.z_image_gguf",
        requires=["diffusion_model" if _loader == "diffusion_model" else "gguf_unet", "qwen3_text_encoder", "vae_or_ae", "aura_sampling"],
        parameter_profile="z_image_native" if _loader == "diffusion_model" else "z_image_gguf",
        provider_nodes={
            "model_loader": "UNETLoader" if _loader == "diffusion_model" else "UnetLoaderGGUF",
            "text_encoder_loader": "CLIPLoader/CLIPLoaderGGUF(type=lumina2)",
            "sampling_patch": "ModelSamplingAuraFlow",
            "latent_node": "EmptySD3LatentImage",
        },
        reason="V25.9.20 Pass H/P5 locks ZImage base txt2img plus P5 Safetensors / Components img2img/inpaint/outpaint; Turbo stays separate.",
        notes=[
            "Normal UI labels diffusion_model as Safetensors / Components and gguf as GGUF.",
            "ZImage base requires a ZImage model, Qwen3 text encoder, AE/VAE, AuraFlow sampling, and sampler nodes discovered from Comfy object_info before compile.",
            "P5 image modes use the same ZImage component stack with Image 1 source, mask, or outpaint padding branches.",
            "ZImage Turbo remains a separate visible family pass rather than a normal ZImage Architecture/Turbo toggle.",
        ],
    ))
    for _mode in ("img2img", "inpaint", "outpaint"):
        _requires = ["diffusion_model" if _loader == "diffusion_model" else "gguf_unet", "qwen3_text_encoder", "vae_or_ae", "source_image"]
        if _mode == "inpaint":
            _requires.append("mask_image")
        if _mode == "outpaint":
            _requires.append("outpaint_padding")
        if _loader == "diffusion_model":
            _add_comfy(RouteMatrixEntry(
                family="z_image",
                loader=_loader,
                backend="comfyui",
                mode=_mode,
                state="available",
                workflow_type=f"image.{_mode}.z_image_native",
                compiler_id="comfy.z_image_native",
                requires=_requires,
                parameter_profile="z_image_native",
                provider_nodes={
                    "model_loader": "UNETLoader",
                    "text_encoder_loader": "CLIPLoader(type=lumina2)",
                    "sampling_patch": "ModelSamplingAuraFlow",
                    "source_branch": "LoadImage + VAEEncode",
                    "mask_branch": "LoadImageMask + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "inpaint" else ("ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "outpaint" else "source latent anchor"),
                },
                reason="V25.9.20 P5 promotes ZImage base Safetensors / Components img2img/inpaint/outpaint as real native workflows using the ZImage component stack; no Flux/Qwen/SD/Turbo fallback.",
                notes=[
                    "P5 scope is checkpoint/safetensors/component ZImage only; GGUF image routes remain implementation targets for a separate pass.",
                    "Do not fallback to Qwen, Flux, SD, or Turbo routes for ZImage component image modes.",
                    "Image 1 is the only consumed source lane; Image 2/Image 3 stay hidden/pruned for this base workflow.",
                    "Inpaint requires a mask image; outpaint requires at least one padding side.",
                ],
            ))
        else:
            _add_comfy(RouteMatrixEntry(
                family="z_image",
                loader=_loader,
                backend="comfyui",
                mode=_mode,
                state="available",
                workflow_type=f"image.{_mode}.z_image_gguf",
                compiler_id="comfy.z_image_gguf",
                requires=_requires,
                parameter_profile="z_image_gguf",
                provider_nodes={
                    "model_loader": "UnetLoaderGGUF",
                    "text_encoder_loader": "CLIPLoaderGGUF(type=lumina2)",
                    "sampling_patch": "ModelSamplingAuraFlow",
                    "source_branch": "LoadImage + VAEEncode",
                    "mask_branch": "LoadImageMask + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "inpaint" else ("ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "outpaint" else "source latent anchor"),
                },
                reason="V25.9.20 P8.4 syncs ZImage GGUF img2img/inpaint/outpaint to available because the provider-owned ZImage GGUF compiler supports the same Image 1 source/mask/padding route shape; High-Res Lab must not create a fallback route.",
                notes=[
                    "Do not fallback to SD checkpoint, Flux, Qwen, ZImage Turbo, or generic image-conditioned compilers.",
                    "GGUF image modes use the ZImage GGUF model loader plus Qwen3/lumina2 text encoder, AE/VAE, ModelSamplingAuraFlow, and route-specific source/mask/padding branches.",
                    "Image 1 is the only consumed source lane; Image 2/Image 3 stay hidden/pruned for this base workflow.",
                ],
            ))


# V25.9.20 Pass I — ZImage Turbo visible family route lock.
for _loader in ("diffusion_model", "gguf"):
    _add_comfy(RouteMatrixEntry(
        family="z_image_turbo",
        loader=_loader,
        backend="comfyui",
        mode="txt2img",
        state="available",
        workflow_type="image.txt2img.z_image_turbo_native" if _loader == "diffusion_model" else "image.txt2img.z_image_turbo_gguf",
        compiler_id="comfy.z_image_native" if _loader == "diffusion_model" else "comfy.z_image_gguf",
        requires=["diffusion_model" if _loader == "diffusion_model" else "gguf_unet", "qwen3_text_encoder", "vae_or_ae", "aura_sampling"],
        parameter_profile="z_image_turbo_native" if _loader == "diffusion_model" else "z_image_turbo_gguf",
        provider_nodes={
            "model_loader": "UNETLoader" if _loader == "diffusion_model" else "UnetLoaderGGUF",
            "text_encoder_loader": "CLIPLoader/CLIPLoaderGGUF(type=lumina2)",
            "sampling_patch": "ModelSamplingAuraFlow",
            "latent_node": "EmptySD3LatentImage",
        },
        reason="V25.9.20 Pass I/P6 locks ZImage Turbo as a separate low-step family for Safetensors / Components txt2img/img2img/inpaint/outpaint and GGUF txt2img. ControlNet stays separate.",
        notes=[
            "Normal UI labels diffusion_model as Safetensors / Components and gguf as GGUF.",
            "ZImage Turbo forces turbo conditioning and low-step/low-CFG defaults by family route, not by a normal turbo_mode dropdown.",
            "P8.5 image modes use the selected ZImage Turbo loader stack with Image 1 source, mask, or outpaint padding branches.",
            "Do not fallback to base ZImage high-step defaults, SD checkpoint, Flux, Qwen, or generic image-conditioned compilers.",
        ],
    ))
    for _mode in ("img2img", "inpaint", "outpaint"):
        _requires = ["diffusion_model" if _loader == "diffusion_model" else "gguf_unet", "qwen3_text_encoder", "vae_or_ae", "source_image"]
        if _mode == "inpaint":
            _requires.append("mask_image")
        if _mode == "outpaint":
            _requires.append("outpaint_padding")
        if _loader == "diffusion_model":
            _add_comfy(RouteMatrixEntry(
                family="z_image_turbo",
                loader=_loader,
                backend="comfyui",
                mode=_mode,
                state="available",
                workflow_type=f"image.{_mode}.z_image_turbo_native",
                compiler_id="comfy.z_image_native",
                requires=_requires,
                parameter_profile="z_image_turbo_native",
                provider_nodes={
                    "model_loader": "UNETLoader",
                    "text_encoder_loader": "CLIPLoader(type=lumina2)",
                    "sampling_patch": "ModelSamplingAuraFlow",
                    "negative_conditioning": "ConditioningZeroOut",
                    "source_branch": "LoadImage + VAEEncode",
                    "mask_branch": "LoadImageMask + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "inpaint" else ("ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "outpaint" else "source latent anchor"),
                },
                reason="V25.9.20 P6 promotes ZImage Turbo Safetensors / Components img2img/inpaint/outpaint as real Turbo-native workflows using the ZImage component stack with family-forced Turbo defaults; no base/Qwen/Flux/SD fallback.",
                notes=[
                    "P8.5 scope includes both checkpoint/safetensors/component and GGUF ZImage Turbo image routes.",
                    "Image 1 is the only consumed source lane; Image 2/Image 3 stay hidden/pruned for this Turbo workflow.",
                    "Inpaint requires a mask image; outpaint requires at least one padding side.",
                    "Turbo image modes keep ConditioningZeroOut negative conditioning and low-step/low-CFG defaults.",
                ],
            ))
        else:
            _add_comfy(RouteMatrixEntry(
                family="z_image_turbo",
                loader=_loader,
                backend="comfyui",
                mode=_mode,
                state="available",
                workflow_type=f"image.{_mode}.z_image_turbo_gguf",
                compiler_id="comfy.z_image_gguf",
                requires=_requires,
                parameter_profile="z_image_turbo_gguf",
                provider_nodes={
                    "model_loader": "UnetLoaderGGUF",
                    "text_encoder_loader": "CLIPLoaderGGUF(type=lumina2)",
                    "sampling_patch": "ModelSamplingAuraFlow",
                    "negative_conditioning": "ConditioningZeroOut",
                    "source_branch": "LoadImage + VAEEncode",
                    "mask_branch": "LoadImageMask + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "inpaint" else ("ImagePadForOutpaint + SetLatentNoiseMask + DifferentialDiffusion" if _mode == "outpaint" else "source latent anchor"),
                },
                reason="V25.9.20 P8.5 syncs ZImage Turbo GGUF img2img/inpaint/outpaint to available because the provider-owned ZImage GGUF compiler supports the same Turbo Image 1 source/mask/padding route shape with family-forced low-step/low-CFG defaults; High-Res Lab must not create a fallback route.",
                notes=[
                    "Do not fallback to base ZImage, SD checkpoint, Flux, Qwen, or generic image-conditioned compilers.",
                    "GGUF image modes use the ZImage Turbo GGUF model loader plus Qwen3/lumina2 text encoder, AE/VAE, ModelSamplingAuraFlow, ConditioningZeroOut negative conditioning, and route-specific source/mask/padding branches.",
                    "Image 1 is the only consumed source lane; Image 2/Image 3 stay hidden/pruned for this Turbo workflow.",
                ],
            ))

for _loader in ("diffusion_model", "gguf"):
    _add_comfy(RouteMatrixEntry(
        family="hidream",
        loader=_loader,
        backend="comfyui",
        mode="txt2img",
        state="available",
        workflow_type="image.txt2img.hidream_native" if _loader == "diffusion_model" else "image.txt2img.hidream_gguf",
        compiler_id="comfy.hidream_native" if _loader == "diffusion_model" else "comfy.hidream_gguf",
        requires=["diffusion_model" if _loader == "diffusion_model" else "gguf_unet", "text_encoder_primary", "vae_or_ae", "hidream_variant"],
        parameter_profile="hidream",
        reason="HiDream first route is txt2img; image-conditioned modes are variant-specific gates.",
    ))
    for _mode in ("img2img", "inpaint", "outpaint"):
        _add_comfy(RouteMatrixEntry(
            family="hidream",
            loader=_loader,
            backend="comfyui",
            mode=_mode,
            state="planned_gated",
            requires=["hidream_variant"],
            reason="HiDream image-conditioned workflows stay variant-gated until each variant declares support.",
        ))

for _family in ("wan_image", "hunyuan_image"):
    for _loader in ("diffusion_model", "gguf", "api_model"):
        for _mode in IMAGE_MODES:
            _add_comfy(RouteMatrixEntry(
                family=_family,
                loader=_loader,
                backend="comfyui",
                mode=_mode,
                state="provider_gated",
                reason=(
                    "Wan is currently tracked as provider-gated for the Image surface; do not expose fake Image-tab workflows."
                    if _family == "wan_image"
                    else "Hunyuan Image requires exact branch/workflow selection before any Image route becomes available."
                ),
            ))


def _loader_supported_by_family(family: str, loader: str) -> bool:
    family_model = get_family(family)
    return bool(family_model and loader in family_model.supported_loaders)


def _forge_or_a1111_route(family: str, loader: str, mode: str, backend: str) -> RouteMatrixEntry:
    if family in {"sdxl", "sd15"} and loader == "checkpoint" and mode in {"txt2img", "img2img", "inpaint"}:
        return RouteMatrixEntry(
            family=family,
            loader=loader,
            backend=backend,
            mode=mode,
            state="planned_gated",
            requires=["checkpoint"] + (["source_image"] if mode in {"img2img", "inpaint"} else []) + (["mask_image"] if mode == "inpaint" else []),
            reason=f"{backend} checkpoint {mode} is expected later, but the V2 base has not validated this backend route yet.",
        )
    if family in {"sdxl", "sd15"} and loader == "checkpoint" and mode == "outpaint":
        return RouteMatrixEntry(
            family=family,
            loader=loader,
            backend=backend,
            mode=mode,
            state="planned_gated",
            requires=["checkpoint", "source_image", "outpaint_padding"],
            reason=f"{backend} outpaint needs an exact script/API route before it is selectable.",
        )
    if family in {"wan_image", "hunyuan_image"}:
        return RouteMatrixEntry(family=family, loader=loader, backend=backend, mode=mode, state="provider_gated", reason=f"{family} is gated for {backend} until a provider route is selected.")
    if family in {"flux", "flux2_klein", "qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "z_image", "z_image_turbo", "hidream"}:
        return RouteMatrixEntry(family=family, loader=loader, backend=backend, mode=mode, state="planned_gated", reason=f"{family}+{loader}+{mode} for {backend} is not part of the V2 base contract yet.")
    return RouteMatrixEntry(family=family, loader=loader, backend=backend, mode=mode, state="unsupported", reason=f"No {backend} route contract exists for {family}+{loader}+{mode}.")


def resolve_model_backend_route(family: str, loader: str, mode: str, backend: str | None = "comfyui") -> RouteMatrixEntry:
    backend_id = normalize_backend(backend)
    mode_id = normalize_mode(mode)
    family_id = str(family or "").strip()
    loader_id = str(loader or "").strip()

    if not _loader_supported_by_family(family_id, loader_id):
        return RouteMatrixEntry(
            family=family_id,
            loader=loader_id,
            backend=backend_id,
            mode=mode_id,
            state="unsupported",
            reason=f"Loader {loader_id} is not declared for family {family_id}.",
        )

    if backend_id == "comfyui":
        entry = _COMFY_EXPLICIT_ROUTES.get((family_id, loader_id, mode_id))
        if entry:
            return entry
        return RouteMatrixEntry(
            family=family_id,
            loader=loader_id,
            backend=backend_id,
            mode=mode_id,
            state="unsupported",
            reason=f"No Comfy route contract exists for {family_id}+{loader_id}+{mode_id}.",
        )

    if backend_id in {"forge", "a1111"}:
        return _forge_or_a1111_route(family_id, loader_id, mode_id, backend_id)

    return RouteMatrixEntry(
        family=family_id,
        loader=loader_id,
        backend=backend_id,
        mode=mode_id,
        state="provider_gated",
        reason=f"Backend {backend_id} has no route adapter contract yet.",
    )


def list_model_backend_routes(backend: str | None = None) -> list[dict[str, Any]]:
    backends = [normalize_backend(backend)] if backend else ["comfyui", "forge", "a1111"]
    rows: list[dict[str, Any]] = []
    for family in get_families():
        if "image" not in family.surfaces:
            continue
        for backend_id in backends:
            for loader in family.supported_loaders:
                modes = IMAGE_ROUTE_MODES_WITH_EDIT if family.family_id in {"qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509"} else IMAGE_MODES
                for mode in modes:
                    rows.append(resolve_model_backend_route(family.family_id, loader, mode, backend_id).as_dict())
    return rows


def available_modes_for_route(family: str, loader: str, backend: str | None = "comfyui", *, include_experimental: bool = True) -> list[str]:
    modes: list[str] = []
    selectable_states = {"available"} | ({"experimental_available"} if include_experimental else set())
    for mode in IMAGE_MODES:
        entry = resolve_model_backend_route(family, loader, mode, backend)
        if entry.state in selectable_states:
            modes.append(mode)
    return modes
