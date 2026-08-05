from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ForgeRouteState = Literal[
    "available",
    "experimental_available",
    "implementation_target",
    "planned_gated",
    "provider_gated",
    "unsupported",
]

FORGE_ROUTE_CATALOG_SCHEMA_ID = "neo.provider.forge_route_authority.v1"
FORGE_ROUTE_CATALOG_VERSION = "1.3.0"
FORGE_PROVIDER_LOADER_ID = "forge_model_bundle"


@dataclass(frozen=True)
class ForgeWorkflowPolicy:
    state: ForgeRouteState
    reason: str
    workflow_type: str | None = None
    compiler_id: str | None = None
    requires: tuple[str, ...] = ()
    parameter_profile: str | None = None
    endpoint: str | None = None
    required_settings: tuple[str, ...] = ()
    required_scripts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForgeLoaderPolicy:
    loader_id: str
    model_formats: tuple[str, ...]
    workflows: dict[str, ForgeWorkflowPolicy]
    provider_loader_id: str = FORGE_PROVIDER_LOADER_ID
    primary_model_role: str = "primary_model"
    required_module_roles: tuple[str, ...] = ()
    optional_module_roles: tuple[str, ...] = ()
    neo_role_translation: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForgeFamilyPolicy:
    family_id: str
    architecture_id: str
    loaders: dict[str, ForgeLoaderPolicy]
    upstream_support: str
    detection_hints: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForgeResolvedRoute:
    family: str
    architecture_id: str
    loader: str
    provider_loader_id: str
    mode: str
    state: ForgeRouteState
    reason: str
    workflow_type: str | None = None
    compiler_id: str | None = None
    requires: tuple[str, ...] = ()
    parameter_profile: str | None = None
    endpoint: str | None = None
    model_formats: tuple[str, ...] = ()
    primary_model_role: str = "primary_model"
    required_module_roles: tuple[str, ...] = ()
    optional_module_roles: tuple[str, ...] = ()
    neo_role_translation: dict[str, str] = field(default_factory=dict)
    required_settings: tuple[str, ...] = ()
    required_scripts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _workflow(
    state: ForgeRouteState,
    reason: str,
    *,
    workflow_type: str | None = None,
    compiler_id: str | None = None,
    requires: tuple[str, ...] = (),
    parameter_profile: str | None = None,
    endpoint: str | None = None,
    required_settings: tuple[str, ...] = (),
    required_scripts: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
) -> ForgeWorkflowPolicy:
    return ForgeWorkflowPolicy(
        state=state,
        reason=reason,
        workflow_type=workflow_type,
        compiler_id=compiler_id,
        requires=requires,
        parameter_profile=parameter_profile,
        endpoint=endpoint,
        required_settings=required_settings,
        required_scripts=required_scripts,
        notes=notes,
    )


def _gated_workflows(
    family_label: str,
    *,
    txt2img: ForgeRouteState = "implementation_target",
    img2img: ForgeRouteState = "planned_gated",
    inpaint: ForgeRouteState = "planned_gated",
    outpaint: ForgeRouteState = "planned_gated",
    edit: ForgeRouteState | None = None,
    txt_reason: str | None = None,
    img_reason: str | None = None,
    edit_reason: str | None = None,
    img_required_settings: tuple[str, ...] = (),
    edit_required_scripts: tuple[str, ...] = (),
) -> dict[str, ForgeWorkflowPolicy]:
    policies = {
        "txt2img": _workflow(
            txt2img,
            txt_reason or f"{family_label} is declared by Forge Neo upstream, but Neo Studio has not implemented its Forge-native model-bundle compiler yet.",
            requires=("primary_model",),
            endpoint="/sdapi/v1/txt2img",
        ),
        "img2img": _workflow(
            img2img,
            img_reason or f"{family_label} img2img remains gated until its Forge-native conditioning contract is compiled and validated.",
            requires=("primary_model", "source_image"),
            endpoint="/sdapi/v1/img2img",
            required_settings=img_required_settings,
        ),
        "inpaint": _workflow(
            inpaint,
            f"{family_label} inpaint remains gated until mask semantics are validated for this Forge architecture.",
            requires=("primary_model", "source_image", "mask_image"),
            endpoint="/sdapi/v1/img2img",
        ),
        "outpaint": _workflow(
            outpaint,
            f"{family_label} outpaint requires a Neo-owned canvas/mask compiler and remains gated in Phase 1.",
            requires=("primary_model", "source_image", "outpaint_padding"),
            endpoint="/sdapi/v1/img2img",
        ),
    }
    if edit is not None:
        policies["edit"] = _workflow(
            edit,
            edit_reason or f"{family_label} edit remains gated until its Forge script/API contract is compiled and validated.",
            requires=("primary_model", "source_image"),
            endpoint="/sdapi/v1/img2img",
            required_scripts=edit_required_scripts,
        )
    return policies


def _sd_workflows(family_id: str) -> dict[str, ForgeWorkflowPolicy]:
    family_label = "SDXL" if family_id == "sdxl" else "SD 1.5"
    common_notes = (
        "Forge routes use the A1111-compatible REST API and never borrow Comfy graphs or Comfy node capability claims.",
        "Availability still requires a connected Forge profile and live capability validation.",
    )
    return {
        "txt2img": _workflow(
            "available",
            f"{family_label} checkpoint txt2img is implemented through Neo's Forge SDAPI compiler and durable job lifecycle.",
            workflow_type=f"image.txt2img.{family_id}.forge_sdapi",
            compiler_id="forge.sdapi_checkpoint",
            requires=("checkpoint",),
            parameter_profile="forge_sdapi_checkpoint",
            endpoint="/sdapi/v1/txt2img",
            notes=common_notes,
        ),
        "img2img": _workflow(
            "available",
            f"{family_label} checkpoint img2img is implemented through Neo's Forge SDAPI compiler and durable job lifecycle.",
            workflow_type=f"image.img2img.{family_id}.forge_sdapi",
            compiler_id="forge.sdapi_checkpoint",
            requires=("checkpoint", "source_image"),
            parameter_profile="forge_sdapi_checkpoint",
            endpoint="/sdapi/v1/img2img",
            notes=common_notes,
        ),
        "inpaint": _workflow(
            "available",
            f"{family_label} checkpoint inpaint is implemented through Forge img2img mask fields and Neo's durable job lifecycle.",
            workflow_type=f"image.inpaint.{family_id}.forge_sdapi",
            compiler_id="forge.sdapi_checkpoint",
            requires=("checkpoint", "source_image", "mask_image"),
            parameter_profile="forge_sdapi_checkpoint",
            endpoint="/sdapi/v1/img2img",
            notes=common_notes,
        ),
        "outpaint": _workflow(
            "available",
            f"{family_label} outpaint is compiled by Neo through canvas expansion, a protected-source mask, and Forge img2img submission.",
            workflow_type=f"image.outpaint.{family_id}.forge_sdapi",
            compiler_id="forge.sdapi_outpaint",
            requires=("checkpoint", "source_image", "outpaint_padding"),
            parameter_profile="forge_sdapi_outpaint",
            endpoint="/sdapi/v1/img2img",
            notes=("Outpaint is a Neo-owned preprocessing contract; Forge receives an img2img canvas and mask.",),
        ),
    }


def _component_loader(
    loader_id: str,
    workflows: dict[str, ForgeWorkflowPolicy],
    *,
    model_formats: tuple[str, ...],
    required_module_roles: tuple[str, ...],
    optional_module_roles: tuple[str, ...] = (),
    neo_role_translation: dict[str, str] | None = None,
    notes: tuple[str, ...] = (),
) -> ForgeLoaderPolicy:
    return ForgeLoaderPolicy(
        loader_id=loader_id,
        model_formats=model_formats,
        workflows=workflows,
        required_module_roles=required_module_roles,
        optional_module_roles=optional_module_roles,
        neo_role_translation=neo_role_translation or {},
        notes=notes,
    )


_SD_TRANSLATION = {
    "checkpoint": "primary_model",
    "vae_optional": "additional_module",
    "text_encoder_primary_optional": "additional_module_optional",
    "text_encoder_secondary_optional": "additional_module_optional",
    "clip_skip_optional": "runtime_setting",
}
_FLUX_TRANSLATION = {
    "diffusion_model": "primary_model",
    "gguf_unet": "primary_model",
    "text_encoder_primary": "additional_module",
    "text_encoder_secondary": "additional_module",
    "gguf_text_encoder_primary": "additional_module",
    "gguf_text_encoder_secondary": "additional_module",
    "vae_or_ae": "additional_module",
    "flux_guidance": "generation_parameter",
}
_QWEN_TRANSLATION = {
    "diffusion_model": "primary_model",
    "gguf_unet": "primary_model",
    "qwen_text_encoder": "additional_module",
    "gguf_text_encoder_primary": "additional_module",
    "vae": "additional_module",
    "qwen_mmproj_optional": "additional_module_optional",
}


FORGE_FAMILY_POLICIES: dict[str, ForgeFamilyPolicy] = {
    "sdxl": ForgeFamilyPolicy(
        family_id="sdxl",
        architecture_id="sdxl",
        upstream_support="verified_existing",
        loaders={
            "checkpoint": ForgeLoaderPolicy(
                loader_id="checkpoint",
                model_formats=("safetensors", "ckpt"),
                workflows=_sd_workflows("sdxl"),
                required_module_roles=(),
                optional_module_roles=("vae", "text_encoder_primary", "text_encoder_secondary"),
                neo_role_translation=_SD_TRANSLATION,
                notes=("Forge owns checkpoint architecture detection; Neo keeps the explicit family selection for route honesty.",),
            )
        },
    ),
    "sd15": ForgeFamilyPolicy(
        family_id="sd15",
        architecture_id="sd15",
        upstream_support="verified_existing",
        loaders={
            "checkpoint": ForgeLoaderPolicy(
                loader_id="checkpoint",
                model_formats=("safetensors", "ckpt"),
                workflows=_sd_workflows("sd15"),
                required_module_roles=(),
                optional_module_roles=("vae", "text_encoder_primary", "text_encoder_secondary"),
                neo_role_translation=_SD_TRANSLATION,
                notes=("Forge owns checkpoint architecture detection; Neo keeps the explicit family selection for route honesty.",),
            )
        },
    ),
    "flux": ForgeFamilyPolicy(
        family_id="flux",
        architecture_id="flux1",
        upstream_support="declared_upstream",
        loaders={
            "diffusion_model": _component_loader(
                "diffusion_model",
                {
                    "txt2img": _workflow(
                        "available",
                        "Flux 1 txt2img is compiled through a translated Forge primary model plus explicit additional modules.",
                        workflow_type="image.txt2img.flux.forge_sdapi",
                        compiler_id="forge.sdapi_modern_txt2img",
                        requires=("primary_model",),
                        parameter_profile="forge_flux1",
                        endpoint="/sdapi/v1/txt2img",
                    ),
                    "img2img": _workflow(
                        "experimental_available",
                        "Flux 1 img2img is compiled through Forge img2img with the translated Flux model bundle.",
                        workflow_type="image.img2img.flux.forge_sdapi",
                        compiler_id="forge.sdapi_modern_img2img",
                        requires=("primary_model", "source_image"),
                        parameter_profile="forge_flux1",
                        endpoint="/sdapi/v1/img2img",
                    ),
                    "inpaint": _workflow("planned_gated", "Flux 1 inpaint stays gated until Fill-model identity and mask semantics are enforced.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Flux 1 outpaint stays gated until the Forge Fill-model contract is validated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=("safetensors",),
                required_module_roles=("text_encoder_primary", "text_encoder_secondary", "vae_or_ae"),
                neo_role_translation=_FLUX_TRANSLATION,
                notes=("Neo's component loader is translated to one Forge primary model plus Forge additional modules; Comfy nodes are not part of this route.",),
            ),
            "gguf": _component_loader(
                "gguf",
                {
                    "txt2img": _workflow(
                        "available",
                        "Flux 1 GGUF txt2img uses GGUF as the Forge primary model with explicit encoder and AE/VAE modules.",
                        workflow_type="image.txt2img.flux_gguf.forge_sdapi",
                        compiler_id="forge.sdapi_modern_txt2img",
                        requires=("primary_model",),
                        parameter_profile="forge_flux1",
                        endpoint="/sdapi/v1/txt2img",
                    ),
                    "img2img": _workflow(
                        "experimental_available",
                        "Flux 1 GGUF img2img uses the translated Forge GGUF model bundle and standard img2img source conditioning.",
                        workflow_type="image.img2img.flux_gguf.forge_sdapi",
                        compiler_id="forge.sdapi_modern_img2img",
                        requires=("primary_model", "source_image"),
                        parameter_profile="forge_flux1",
                        endpoint="/sdapi/v1/img2img",
                    ),
                    "inpaint": _workflow("planned_gated", "Flux 1 GGUF inpaint stays gated until Fill-model identity and mask semantics are enforced.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Flux 1 GGUF outpaint stays gated until the Forge Fill-model contract is validated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=("gguf",),
                required_module_roles=("text_encoder_primary", "text_encoder_secondary", "vae_or_ae"),
                neo_role_translation=_FLUX_TRANSLATION,
                notes=("GGUF is a primary Forge model format, not a Comfy graph-loader contract.",),
            ),
        },
    ),
    "flux2_klein": ForgeFamilyPolicy(
        family_id="flux2_klein",
        architecture_id="flux2_klein",
        upstream_support="declared_upstream",
        loaders={
            loader: _component_loader(
                loader,
                {
                    "txt2img": _workflow(
                        "available",
                        "Flux.2 Klein txt2img is compiled through the translated Forge model bundle.",
                        workflow_type="image.txt2img.flux2_klein.forge_sdapi",
                        compiler_id="forge.sdapi_modern_txt2img",
                        requires=("primary_model",),
                        parameter_profile="forge_flux2_klein",
                        endpoint="/sdapi/v1/txt2img",
                    ),
                    "img2img": _workflow(
                        "experimental_available",
                        "Flux.2 Klein img2img is compiled only when Forge exposes and enables regular img2img for Klein; extra reference images may use the verified Forge ImageStitch Integrated contract.",
                        workflow_type="image.img2img.flux2_klein.forge_sdapi",
                        compiler_id="forge.sdapi_modern_img2img",
                        requires=("primary_model", "source_image"),
                        parameter_profile="forge_flux2_klein",
                        endpoint="/sdapi/v1/img2img",
                        required_settings=("flux2_klein_regular_img2img",),
                    ),
                    "inpaint": _workflow("planned_gated", "Flux.2 Klein inpaint has no validated Forge mask contract in Neo Studio.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Flux.2 Klein outpaint has no validated Forge mask contract in Neo Studio.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=(("gguf",) if loader == "gguf" else ("safetensors",)),
                required_module_roles=("qwen3_text_encoder", "vae_or_ae"),
                neo_role_translation={
                    "diffusion_model": "primary_model",
                    "gguf_unet": "primary_model",
                    "qwen3_text_encoder": "additional_module",
                    "gguf_text_encoder_primary": "additional_module",
                    "vae_or_ae": "additional_module",
                    "flux_guidance": "generation_parameter",
                },
            )
            for loader in ("diffusion_model", "gguf")
        },
        detection_hints=("flux.2", "klein"),
    ),
    "krea2": ForgeFamilyPolicy(
        family_id="krea2",
        architecture_id="krea2_raw",
        upstream_support="declared_upstream",
        loaders={
            loader: _component_loader(
                loader,
                {
                    "txt2img": _workflow("available", "Krea 2 RAW txt2img is compiled through its translated Forge model bundle.", workflow_type="image.txt2img.krea2.forge_sdapi", compiler_id="forge.sdapi_modern_txt2img", requires=("primary_model",), parameter_profile="forge_krea2_raw", endpoint="/sdapi/v1/txt2img"),
                    "img2img": _workflow("planned_gated", "Krea 2 RAW img2img remains gated until its conditioning contract is validated.", requires=("primary_model", "source_image"), endpoint="/sdapi/v1/img2img"),
                    "inpaint": _workflow("planned_gated", "Krea 2 RAW inpaint remains gated.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Krea 2 RAW outpaint remains gated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=(("gguf",) if loader == "gguf" else ("safetensors",)),
                required_module_roles=("qwen3vl_4b_text_encoder", "qwen_image_vae"),
                neo_role_translation={
                    "diffusion_model": "primary_model",
                    "gguf_unet": "primary_model",
                    "qwen3vl_4b_text_encoder": "additional_module",
                    "qwen_image_vae": "additional_module",
                    "krea2_clip_loader": "provider_architecture_contract",
                },
            )
            for loader in ("diffusion_model", "gguf")
        },
        detection_hints=("krea-2", "raw"),
    ),
    "krea2_turbo": ForgeFamilyPolicy(
        family_id="krea2_turbo",
        architecture_id="krea2_turbo",
        upstream_support="declared_upstream",
        loaders={
            loader: _component_loader(
                loader,
                {
                    "txt2img": _workflow("available", "Krea 2 Turbo txt2img is compiled through its translated Forge model bundle.", workflow_type="image.txt2img.krea2_turbo.forge_sdapi", compiler_id="forge.sdapi_modern_txt2img", requires=("primary_model",), parameter_profile="forge_krea2_turbo", endpoint="/sdapi/v1/txt2img"),
                    "img2img": _workflow("planned_gated", "Krea 2 Turbo img2img remains gated until its conditioning contract is validated.", requires=("primary_model", "source_image"), endpoint="/sdapi/v1/img2img"),
                    "inpaint": _workflow("planned_gated", "Krea 2 Turbo inpaint remains gated.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Krea 2 Turbo outpaint remains gated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=(("gguf",) if loader == "gguf" else ("safetensors",)),
                required_module_roles=("qwen3vl_4b_text_encoder", "qwen_image_vae"),
                neo_role_translation={
                    "diffusion_model": "primary_model",
                    "gguf_unet": "primary_model",
                    "qwen3vl_4b_text_encoder": "additional_module",
                    "qwen_image_vae": "additional_module",
                    "krea2_clip_loader": "provider_architecture_contract",
                },
            )
            for loader in ("diffusion_model", "gguf")
        },
        detection_hints=("krea-2", "turbo"),
    ),
    "qwen_image": ForgeFamilyPolicy(
        family_id="qwen_image",
        architecture_id="qwen_image",
        upstream_support="declared_upstream",
        loaders={
            loader: _component_loader(
                loader,
                {
                    "txt2img": _workflow("available", "Qwen Image txt2img is compiled through its translated Forge model bundle.", workflow_type="image.txt2img.qwen_image.forge_sdapi", compiler_id="forge.sdapi_modern_txt2img", requires=("primary_model",), parameter_profile="forge_qwen_image", endpoint="/sdapi/v1/txt2img"),
                    "img2img": _workflow("planned_gated", "Qwen Image base-model img2img remains gated; use the explicit Qwen Image Edit family for editing.", requires=("primary_model", "source_image"), endpoint="/sdapi/v1/img2img"),
                    "inpaint": _workflow("planned_gated", "Qwen Image inpaint remains gated.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Qwen Image outpaint remains gated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=(("gguf",) if loader == "gguf" else ("safetensors",)),
                required_module_roles=("qwen_text_encoder", "vae"),
                optional_module_roles=("mmproj",),
                neo_role_translation=_QWEN_TRANSLATION,
            )
            for loader in ("diffusion_model", "gguf")
        },
        detection_hints=("qwen",),
    ),
    "qwen_image_edit_2509": ForgeFamilyPolicy(
        family_id="qwen_image_edit_2509",
        architecture_id="qwen_image_edit",
        upstream_support="declared_upstream",
        loaders={
            loader: _component_loader(
                loader,
                {
                    "txt2img": _workflow("planned_gated", "Qwen Image Edit is not exposed as a normal Forge txt2img route.", requires=("primary_model",), endpoint="/sdapi/v1/txt2img"),
                    "img2img": _workflow("experimental_available", "Qwen Image Edit 2509 img2img is compiled through Forge img2img; additional reference images are supported only when the selected Forge profile exposes the verified ImageStitch Integrated API contract.", workflow_type="image.img2img.qwen_image_edit_2509.forge_sdapi", compiler_id="forge.sdapi_qwen_edit", requires=("primary_model", "source_image"), parameter_profile="forge_qwen_image_edit", endpoint="/sdapi/v1/img2img", notes=("Without the verified three-argument ImageStitch Integrated contract, extra reference images remain fail-closed while single-source editing stays available.",)), 
                    "inpaint": _workflow("planned_gated", "Qwen Image Edit inpaint mask semantics remain gated.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Qwen Image Edit outpaint remains gated until its edit mask contract is validated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                    "edit": _workflow("experimental_available", "Qwen Image Edit 2509 edit is compiled through Forge img2img; additional reference images use ImageStitch Integrated only when its API contract is verified live.", workflow_type="image.edit.qwen_image_edit_2509.forge_sdapi", compiler_id="forge.sdapi_qwen_edit", requires=("primary_model", "source_image"), parameter_profile="forge_qwen_image_edit", endpoint="/sdapi/v1/img2img", notes=("Neo requires the verified ImageStitch Integrated script signature before sending multiple references; otherwise it fails closed without disabling single-source edit.",)), 
                },
                model_formats=(("gguf",) if loader == "gguf" else ("safetensors",)),
                required_module_roles=("qwen_text_encoder", "vae"),
                optional_module_roles=("mmproj",),
                neo_role_translation=_QWEN_TRANSLATION,
            )
            for loader in ("diffusion_model", "gguf")
        },
        detection_hints=("qwen", "edit"),
        notes=("Forge identifies Edit models from qwen/edit path signals; Phase 2 intersects that classification with modules, scripts, settings, endpoints, and the selected profile.",),
    ),
    "qwen_rapid_aio": ForgeFamilyPolicy(
        family_id="qwen_rapid_aio",
        architecture_id="qwen_rapid_aio_unverified",
        upstream_support="no_verified_direct_contract",
        loaders={
            "checkpoint_aio": ForgeLoaderPolicy(
                loader_id="checkpoint_aio",
                model_formats=("safetensors",),
                workflows={
                    mode: _workflow(
                        "unsupported",
                        "Qwen Rapid AIO is a Comfy bundled-checkpoint contract and must not be treated as a generic Forge checkpoint.",
                    )
                    for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit")
                },
                neo_role_translation={"qwen_rapid_aio_checkpoint": "unsupported_bundle_contract"},
            ),
            "gguf": ForgeLoaderPolicy(
                loader_id="gguf",
                model_formats=("gguf",),
                workflows={
                    mode: _workflow(
                        "provider_gated",
                        "Qwen Rapid AIO GGUF has no verified Forge architecture identity or module contract in Neo Studio.",
                    )
                    for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit")
                },
                neo_role_translation={"gguf_unet": "primary_model"},
            ),
        },
    ),
    "z_image": ForgeFamilyPolicy(
        family_id="z_image",
        architecture_id="z_image",
        upstream_support="declared_upstream",
        loaders={
            loader: _component_loader(
                loader,
                {
                    "txt2img": _workflow("available", "Z-Image txt2img is compiled through its translated Forge model bundle.", workflow_type="image.txt2img.z_image.forge_sdapi", compiler_id="forge.sdapi_modern_txt2img", requires=("primary_model",), parameter_profile="forge_z_image", endpoint="/sdapi/v1/txt2img"),
                    "img2img": _workflow("planned_gated", "Z-Image img2img remains gated until its conditioning contract is validated.", requires=("primary_model", "source_image"), endpoint="/sdapi/v1/img2img"),
                    "inpaint": _workflow("planned_gated", "Z-Image inpaint remains gated.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Z-Image outpaint remains gated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=(("gguf",) if loader == "gguf" else ("safetensors",)),
                required_module_roles=("qwen3_text_encoder", "ae_or_vae"),
                neo_role_translation={
                    "diffusion_model": "primary_model",
                    "gguf_unet": "primary_model",
                    "qwen3_text_encoder": "additional_module",
                    "ae_or_vae": "additional_module",
                },
            )
            for loader in ("diffusion_model", "gguf")
        },
        detection_hints=("z-image",),
    ),
    "z_image_turbo": ForgeFamilyPolicy(
        family_id="z_image_turbo",
        architecture_id="z_image_turbo",
        upstream_support="declared_upstream",
        loaders={
            loader: _component_loader(
                loader,
                {
                    "txt2img": _workflow("available", "Z-Image Turbo txt2img is compiled through its translated Forge model bundle.", workflow_type="image.txt2img.z_image_turbo.forge_sdapi", compiler_id="forge.sdapi_modern_txt2img", requires=("primary_model",), parameter_profile="forge_z_image_turbo", endpoint="/sdapi/v1/txt2img"),
                    "img2img": _workflow("planned_gated", "Z-Image Turbo img2img remains gated until its conditioning contract is validated.", requires=("primary_model", "source_image"), endpoint="/sdapi/v1/img2img"),
                    "inpaint": _workflow("planned_gated", "Z-Image Turbo inpaint remains gated.", requires=("primary_model", "source_image", "mask_image"), endpoint="/sdapi/v1/img2img"),
                    "outpaint": _workflow("planned_gated", "Z-Image Turbo outpaint remains gated.", requires=("primary_model", "source_image", "outpaint_padding"), endpoint="/sdapi/v1/img2img"),
                },
                model_formats=(("gguf",) if loader == "gguf" else ("safetensors",)),
                required_module_roles=("qwen3_text_encoder", "ae_or_vae"),
                neo_role_translation={
                    "diffusion_model": "primary_model",
                    "gguf_unet": "primary_model",
                    "qwen3_text_encoder": "additional_module",
                    "ae_or_vae": "additional_module",
                },
            )
            for loader in ("diffusion_model", "gguf")
        },
        detection_hints=("z-image", "turbo"),
    ),
    "wan_image": ForgeFamilyPolicy(
        family_id="wan_image",
        architecture_id="wan_2_2_video",
        upstream_support="provider_gated_image_surface",
        loaders={
            loader: ForgeLoaderPolicy(
                loader_id=loader,
                model_formats=(("api",) if loader == "api_model" else ("gguf",) if loader == "gguf" else ("safetensors",)),
                workflows={
                    mode: _workflow("provider_gated", "Wan support in Forge Neo is video-oriented and is not a verified Neo Image-surface route.")
                    for mode in ("txt2img", "img2img", "inpaint", "outpaint")
                },
            )
            for loader in ("diffusion_model", "gguf", "api_model")
        },
    ),
    "hunyuan_image": ForgeFamilyPolicy(
        family_id="hunyuan_image",
        architecture_id="hunyuan_unverified",
        upstream_support="no_verified_current_route",
        loaders={
            loader: ForgeLoaderPolicy(
                loader_id=loader,
                model_formats=(("api",) if loader == "api_model" else ("gguf",) if loader == "gguf" else ("safetensors",)),
                workflows={
                    mode: _workflow("provider_gated", "Hunyuan Image has no verified current Forge Neo route contract in Neo Studio.")
                    for mode in ("txt2img", "img2img", "inpaint", "outpaint")
                },
            )
            for loader in ("diffusion_model", "gguf", "api_model")
        },
    ),
    "hidream": ForgeFamilyPolicy(
        family_id="hidream",
        architecture_id="hidream_unverified",
        upstream_support="no_verified_current_route",
        loaders={
            loader: ForgeLoaderPolicy(
                loader_id=loader,
                model_formats=(("api",) if loader == "api_model" else ("gguf",) if loader == "gguf" else ("safetensors",)),
                workflows={
                    mode: _workflow("provider_gated", "HiDream has no verified current Forge Neo route contract in Neo Studio.")
                    for mode in ("txt2img", "img2img", "inpaint", "outpaint")
                },
            )
            for loader in ("diffusion_model", "gguf", "api_model")
        },
    ),
    "other": ForgeFamilyPolicy(
        family_id="other",
        architecture_id="unclassified",
        upstream_support="unsupported_without_classification",
        loaders={
            loader: ForgeLoaderPolicy(
                loader_id=loader,
                model_formats=(("api",) if loader == "api_model" else ("gguf",) if loader == "gguf" else ("safetensors", "ckpt")),
                workflows={
                    mode: _workflow("unsupported", "Unclassified models cannot use Forge generation routes without an explicit Neo family contract.")
                    for mode in ("txt2img", "img2img", "inpaint", "outpaint")
                },
            )
            for loader in ("checkpoint", "diffusion_model", "unet", "gguf", "api_model")
        },
    ),
    "flux1_fill": ForgeFamilyPolicy(
        family_id="flux1_fill",
        architecture_id="flux1_fill_legacy_alias",
        upstream_support="legacy_internal_alias",
        loaders={
            "diffusion_model": ForgeLoaderPolicy(
                loader_id="diffusion_model",
                model_formats=("safetensors",),
                workflows={
                    mode: _workflow("unsupported", "flux1_fill is a legacy/internal Neo alias and is not a normal Forge family route.")
                    for mode in ("txt2img", "img2img", "inpaint", "outpaint")
                },
            )
        },
    ),
}


def get_forge_family_policy(family: str) -> ForgeFamilyPolicy | None:
    return FORGE_FAMILY_POLICIES.get(str(family or "").strip())


def resolve_forge_route(family: str, loader: str, mode: str) -> ForgeResolvedRoute:
    family_id = str(family or "").strip()
    loader_id = str(loader or "").strip()
    mode_id = str(mode or "txt2img").strip() or "txt2img"
    family_policy = get_forge_family_policy(family_id)
    if family_policy is None:
        return ForgeResolvedRoute(
            family=family_id,
            architecture_id="unknown",
            loader=loader_id,
            provider_loader_id=FORGE_PROVIDER_LOADER_ID,
            mode=mode_id,
            state="unsupported",
            reason=f"No Forge route-authority family contract exists for {family_id}.",
        )
    loader_policy = family_policy.loaders.get(loader_id)
    if loader_policy is None:
        return ForgeResolvedRoute(
            family=family_id,
            architecture_id=family_policy.architecture_id,
            loader=loader_id,
            provider_loader_id=FORGE_PROVIDER_LOADER_ID,
            mode=mode_id,
            state="unsupported",
            reason=f"Loader {loader_id} is not declared by the Forge route authority for family {family_id}.",
            notes=family_policy.notes,
        )
    workflow = loader_policy.workflows.get(mode_id)
    if workflow is None:
        return ForgeResolvedRoute(
            family=family_id,
            architecture_id=family_policy.architecture_id,
            loader=loader_id,
            provider_loader_id=loader_policy.provider_loader_id,
            mode=mode_id,
            state="unsupported",
            reason=f"Mode {mode_id} is not declared by the Forge route authority for {family_id}+{loader_id}.",
            model_formats=loader_policy.model_formats,
            primary_model_role=loader_policy.primary_model_role,
            required_module_roles=loader_policy.required_module_roles,
            optional_module_roles=loader_policy.optional_module_roles,
            neo_role_translation=loader_policy.neo_role_translation,
            notes=(*family_policy.notes, *loader_policy.notes),
        )
    return ForgeResolvedRoute(
        family=family_id,
        architecture_id=family_policy.architecture_id,
        loader=loader_id,
        provider_loader_id=loader_policy.provider_loader_id,
        mode=mode_id,
        state=workflow.state,
        reason=workflow.reason,
        workflow_type=workflow.workflow_type,
        compiler_id=workflow.compiler_id,
        requires=workflow.requires,
        parameter_profile=workflow.parameter_profile,
        endpoint=workflow.endpoint,
        model_formats=loader_policy.model_formats,
        primary_model_role=loader_policy.primary_model_role,
        required_module_roles=loader_policy.required_module_roles,
        optional_module_roles=loader_policy.optional_module_roles,
        neo_role_translation=loader_policy.neo_role_translation,
        required_settings=workflow.required_settings,
        required_scripts=workflow.required_scripts,
        notes=(*family_policy.notes, *loader_policy.notes, *workflow.notes),
    )


def forge_route_authority_payload() -> dict[str, Any]:
    families: list[dict[str, Any]] = []
    for family_id in sorted(FORGE_FAMILY_POLICIES):
        family = FORGE_FAMILY_POLICIES[family_id]
        loaders: list[dict[str, Any]] = []
        for loader_id in sorted(family.loaders):
            loader = family.loaders[loader_id]
            loaders.append(
                {
                    "loader_id": loader.loader_id,
                    "provider_loader_id": loader.provider_loader_id,
                    "model_formats": list(loader.model_formats),
                    "primary_model_role": loader.primary_model_role,
                    "required_module_roles": list(loader.required_module_roles),
                    "optional_module_roles": list(loader.optional_module_roles),
                    "neo_role_translation": dict(loader.neo_role_translation),
                    "notes": list(loader.notes),
                    "workflows": {mode: asdict(policy) for mode, policy in sorted(loader.workflows.items())},
                }
            )
        families.append(
            {
                "family_id": family.family_id,
                "architecture_id": family.architecture_id,
                "upstream_support": family.upstream_support,
                "detection_hints": list(family.detection_hints),
                "notes": list(family.notes),
                "loaders": loaders,
            }
        )
    return {
        "schema_id": FORGE_ROUTE_CATALOG_SCHEMA_ID,
        "version": FORGE_ROUTE_CATALOG_VERSION,
        "provider_id": "forge",
        "provider_loader_id": FORGE_PROVIDER_LOADER_ID,
        "policy": {
            "provider_native_model_bundle": True,
            "gguf_is_primary_model_format": True,
            "comfy_nodes_are_not_forge_capabilities": True,
            "upstream_declaration_does_not_imply_neo_compiler_availability": True,
            "live_profile_intersection_required_before_generation": True,
            "loader_translation_schema_id": "neo.provider.forge_loader_translation.v1",
            "workflow_compiler_schema_id": "neo.provider.forge_workflow_compilers.v1",
        },
        "families": families,
    }


def forge_selectable_route_summary(*, enabled_modes: set[str] | None = None) -> dict[str, Any]:
    enabled_modes = (
        {"txt2img", "img2img", "inpaint", "outpaint", "edit"}
        if enabled_modes is None
        else set(enabled_modes)
    )
    families: set[str] = set()
    loaders: set[str] = set()
    modes: set[str] = set()
    routes: list[dict[str, Any]] = []
    for family_id, family in FORGE_FAMILY_POLICIES.items():
        for loader_id, loader in family.loaders.items():
            for mode, workflow in loader.workflows.items():
                if mode not in enabled_modes or workflow.state not in {"available", "experimental_available"}:
                    continue
                families.add(family_id)
                loaders.add(loader_id)
                modes.add(mode)
                routes.append({
                    "family": family_id,
                    "loader": loader_id,
                    "mode": mode,
                    "state": workflow.state,
                    "compiler_id": workflow.compiler_id,
                    "workflow_type": workflow.workflow_type,
                    "parameter_profile": workflow.parameter_profile,
                    "endpoint": workflow.endpoint,
                })
    return {
        "schema_id": FORGE_ROUTE_CATALOG_SCHEMA_ID,
        "version": FORGE_ROUTE_CATALOG_VERSION,
        "families": sorted(families),
        "loaders": sorted(loaders),
        "modes": [mode for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit") if mode in modes],
        "routes": sorted(routes, key=lambda item: (item["family"], item["loader"], item["mode"])),
    }
