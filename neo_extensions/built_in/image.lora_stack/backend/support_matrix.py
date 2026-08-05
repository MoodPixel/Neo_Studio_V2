from __future__ import annotations

import hashlib
import json

from typing import Any

try:  # Keep extension tests importable even if the model route system is unavailable.
    from neo_app.models.route_matrix import resolve_model_backend_route
except Exception:  # pragma: no cover - defensive fallback for isolated extension loading
    resolve_model_backend_route = None  # type: ignore[assignment]

AVAILABLE = "available"
EXPERIMENTAL = "experimental_available"
IMPLEMENTATION_TARGET = "implementation_target"
PLANNED = "planned_gated"
PROVIDER_GATED = "provider_gated"
UNSUPPORTED = "unsupported"

MATRIX_SCHEMA_VERSION = "neo.image.lora_stack.route_matrix.v8"
MANIFEST_SYNC_PHASE = "L12-global-lora-engine-decoupling"
MATRIX_SOURCE = "neo_extensions/built_in/image.lora_stack/backend/support_matrix.py"
MATRIX_GENERATOR = "support_matrix.manifest_route_states"

STATE_ORDER = (AVAILABLE, EXPERIMENTAL, IMPLEMENTATION_TARGET, PLANNED, PROVIDER_GATED, UNSUPPORTED)
ACTIVE_STATES = {AVAILABLE, EXPERIMENTAL}
GATED_STATES = {IMPLEMENTATION_TARGET, PLANNED, PROVIDER_GATED, UNSUPPORTED}
KNOWN_STATES = set(STATE_ORDER)
SUPPORTED_BACKENDS = ("comfyui", "comfyui_portable")
SUPPORTED_MODES = ("generate", "img2img", "edit", "inpaint", "outpaint")
SUPPORTED_ENGINES = ("native", "lanpaint")
SUPPORTED_LOADERS = ("checkpoint", "checkpoint_aio", "diffusion_model", "gguf")
SUPPORTED_FAMILIES = (
    "sdxl",
    "sd15",
    "sd35",
    "flux",
    "flux2_dev",
    "flux2_klein",
    "krea2",
    "krea2_turbo",
    "qwen_image",
    "qwen_rapid_aio",
    "qwen_image_edit_2509",
    "qwen_image_edit_2511",
    "z_image",
    "z_image_turbo",
    "hidream",
    "anima",
    "ideogram4",
    "wan_image",
    "hunyuan_image",
)

MODE_ALIASES = {
    "txt2img": "generate",
    "text_to_image": "generate",
    "image_to_image": "img2img",
}

COMFY_MODE_ALIASES = {
    "generate": "txt2img",
}

MODEL_CLIP_PATCH_STRATEGIES = ("lora_loader_model_clip_chain", "lora_loader_model_clip_consumer_rewire")
MODEL_ONLY_PATCH_STRATEGIES = ("lora_loader_model_only_chain", "lora_loader_model_only_consumer_rewire")
PROVIDER_SPECIFIC_PATCH_STRATEGY = "provider_specific"
NO_PATCH_STRATEGY = "none"
SUPPORTED_GRAPH_PATCH_STRATEGIES = (
    *MODEL_CLIP_PATCH_STRATEGIES,
    *MODEL_ONLY_PATCH_STRATEGIES,
    PROVIDER_SPECIFIC_PATCH_STRATEGY,
    NO_PATCH_STRATEGY,
)


def normalize_workflow_mode(mode: str | None) -> str:
    value = str(mode or "generate").strip() or "generate"
    return MODE_ALIASES.get(value, value)


def _comfy_workflow_mode(mode: str | None) -> str:
    normalized = normalize_workflow_mode(mode)
    return COMFY_MODE_ALIASES.get(normalized, normalized)


def normalize_route_engine(engine: str | None) -> str:
    value = str(engine or "").strip().lower()
    if value in {"", "default", "standard"}:
        return ""
    return value


def _key(family: str, loader: str, mode: str, engine: str | None = None) -> tuple[str, str, str]:
    """Return the engine-independent LoRA compatibility identity.

    ``engine`` remains accepted for source compatibility with older callers, but
    it is deliberately excluded from the key. Native Inpaint and LanPaint share
    the same family/loader/workflow LoRA compatibility policy; each compiler owns
    its own graph anchors through the patch profile.
    """
    _ = engine
    return ((family or "").strip(), (loader or "").strip(), normalize_workflow_mode(mode))


# L0 support matrix is deliberately explicit. It is the LoRA Stack source of
# truth, not a mirror of base Image route availability. If a base route exists
# but no LoRA patch profile is proven, this matrix must return
# implementation_target instead of borrowing another family/loader graph.
ROUTE_SUPPORT: dict[tuple[str, str, str], dict[str, Any]] = {}


def _add(
    family: str,
    loader: str,
    mode: str,
    state: str,
    reason: str,
    graph_patch: str,
    *,
    roles: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    loader_node_class: str | None = None,
    requires_model: bool = True,
    requires_clip: bool = True,
    patch_profile_required: bool = False,
    validated: bool = False,
    enablement_pass: str = "",
    engine: str = "",
) -> None:
    if state not in KNOWN_STATES:
        raise ValueError(f"Unknown LoRA Stack route state: {state}")
    normalized_mode = normalize_workflow_mode(mode)
    normalized_engine = normalize_route_engine(engine)
    if normalized_engine:
        raise ValueError("LoRA compatibility rows must be engine-independent; store engine-specific graph anchors in compiler patch profiles.")
    ROUTE_SUPPORT[_key(family, loader, normalized_mode)] = {
        "backend": "comfyui",
        "family": family,
        "loader": loader,
        "mode": normalized_mode,
        "engine": "",
        "compatibility_engine_independent": True,
        "state": state,
        "route_state": state,
        "reason": reason,
        "graph_patch": graph_patch,
        "loader_node_class": loader_node_class or ("LoraLoader" if graph_patch != "none" else None),
        "requires_model": requires_model,
        "requires_clip": requires_clip,
        "patch_profile_required": patch_profile_required,
        "required_roles": roles,
        "notes": notes,
        "validated": validated,
        "enablement_pass": enablement_pass,
    }


for _mode in ("generate", "img2img", "inpaint", "outpaint"):
    _add(
        "sdxl", "checkpoint", _mode, AVAILABLE,
        "Validated checkpoint compiler route exposes checkpoint MODEL and CLIP refs; standard Comfy LoraLoader chain is supported.",
        "lora_loader_model_clip_chain",
        roles=("checkpoint_model", "checkpoint_clip", "LoraLoader"),
        loader_node_class="LoraLoader",
        validated=True,
        enablement_pass="L6.sd_checkpoint",
    )
    _add(
        "sd15", "checkpoint", _mode, EXPERIMENTAL,
        "Checkpoint graph shape matches the validated SDXL patch path, but route-specific SD1.5 visual validation is still required.",
        "lora_loader_model_clip_chain",
        roles=("checkpoint_model", "checkpoint_clip", "LoraLoader"),
        loader_node_class="LoraLoader",
        validated=False,
        enablement_pass="L6.sd_checkpoint",
    )

# L6 family-by-family enablement rules. Routes are active only where Neo has
# both a selectable base compiler route and a compiler-owned LoRA patch profile.
# All non-checkpoint family routes remain experimental until physical Comfy
# validation is completed with real LoRA files for that family/loader.
def _add_model_clip_experimental(
    family: str,
    loader: str,
    mode: str,
    reason: str,
    *,
    graph_patch: str = "lora_loader_model_clip_consumer_rewire",
    notes: tuple[str, ...] = (),
    enablement_pass: str = "L6",
    engine: str = "",
) -> None:
    _add(
        family, loader, mode, EXPERIMENTAL,
        reason,
        graph_patch,
        roles=("model_ref", "clip_ref", "LoraLoader"),
        notes=(
            "L6 experimental enablement: compiler emits a LoRA patch profile; real-model/real-LoRA validation is still required before available promotion.",
            "No fallback to SD checkpoint, GGUF, checkpoint_aio, or adjacent family wiring is allowed.",
            *notes,
        ),
        loader_node_class="LoraLoader",
        patch_profile_required=True,
        validated=False,
        enablement_pass=enablement_pass,
        engine=engine,
    )


def _add_model_clip_chain_experimental(
    family: str,
    loader: str,
    mode: str,
    reason: str,
    *,
    notes: tuple[str, ...] = (),
    enablement_pass: str = "L6",
    engine: str = "",
) -> None:
    _add_model_clip_experimental(
        family, loader, mode, reason,
        graph_patch="lora_loader_model_clip_chain",
        notes=notes,
        enablement_pass=enablement_pass,
        engine=engine,
    )


def _add_model_only_experimental(
    family: str,
    loader: str,
    mode: str,
    reason: str,
    *,
    graph_patch: str = "lora_loader_model_only_consumer_rewire",
    notes: tuple[str, ...] = (),
    enablement_pass: str = "L6",
    engine: str = "",
) -> None:
    _add(
        family, loader, mode, EXPERIMENTAL,
        reason,
        graph_patch,
        roles=("model_ref", "LoraLoaderModelOnly"),
        notes=(
            "L6 experimental enablement: compiler emits a model-only LoRA patch profile; real-model/real-LoRA validation is still required before available promotion.",
            "No fallback to checkpoint, FLUX, Klein, or adjacent family graph assumptions is allowed.",
            *notes,
        ),
        loader_node_class="LoraLoaderModelOnly",
        requires_clip=False,
        patch_profile_required=True,
        validated=False,
        enablement_pass=enablement_pass,
        engine=engine,
    )


# Phase 15 SD 3.5 split/GGUF LanPaint routes publish model+CLIP
# compatibility independently from the selected inpaint engine.  Native SD3.5
# remains separately gated until a native compiler is onboarded.
for _loader in ("diffusion_model", "gguf"):
    _add_model_clip_experimental(
        "sd35", _loader, "inpaint",
        "Phase 15 SD 3.5 LanPaint compiler emits exact split model and triple-encoder CLIP anchors for optional LoRA rewiring.",
        notes=("TripleCLIPLoader/TripleCLIPLoaderGGUF and ModelSamplingSD3 remain family-owned graph contracts.",),
        enablement_pass="L15.sd35_lanpaint",
    )

# Flux 1: components and GGUF use provider-owned profiles. Normal edit remains
# unsupported because Flux edit is not a base route.
for _loader in ("diffusion_model", "gguf"):
    for _mode in ("generate", "img2img", "inpaint", "outpaint"):
        _add_model_clip_experimental(
            "flux", _loader, _mode,
            "Flux 1 compiler emits explicit model/clip refs for this route; LoRA Stack can rewire exact model and clip consumers experimentally without borrowing checkpoint or Klein wiring.",
            notes=("Inpaint/outpaint routes are internal Flux Fill variants but still resolve through the normal Flux family route contract.",),
            enablement_pass="L6.flux",
        )

# Phase 17 Flux.2 Dev LanPaint routes share model+CLIP compatibility across
# Native Inpaint and LanPaint. Other Flux.2 Dev workflow modes remain gated.
for _loader in ("diffusion_model", "gguf"):
    _add_model_clip_experimental(
        "flux2_dev", _loader, "inpaint",
        "Phase 17 Flux.2 Dev LanPaint compiler emits exact model and Mistral3 CLIP anchors for optional LoRA rewiring.",
        notes=("GGUF transformer routes keep the proven native Mistral3 Flux2 encoder until a GGUF encoder contract is validated.",),
        enablement_pass="L17.flux2_dev_lanpaint",
    )

# Flux 2 Klein: component and GGUF routes include txt2img, image anchor, edit,
# inpaint, and outpaint compiler profiles.
for _loader in ("diffusion_model", "gguf"):
    for _mode in SUPPORTED_MODES:
        _add_model_clip_experimental(
            "flux2_klein", _loader, _mode,
            "Flux 2 Klein compiler emits model/clip refs for this mode; LoRA Stack can patch matching consumers experimentally through the Klein-owned route profile.",
            notes=("Do not fallback to Flux 1 dual-encoder assumptions; Klein owns the single Qwen3 Flux2/Klein route shape.",),
            enablement_pass="L6.flux2_klein",
        )

# Krea 2 RAW/Turbo: every supported workflow mode shares one model-only LoRA
# compatibility policy for each loader. Native Inpaint and LanPaint remain
# separate graph compilers and must each emit their own valid patch anchors.
for _family in ("krea2", "krea2_turbo"):
    for _loader in ("diffusion_model", "gguf"):
        for _mode in ("generate", "img2img", "inpaint", "outpaint"):
            _add_model_only_experimental(
                _family, _loader, _mode,
                "Krea 2 compiler emits a model-only LoRA patch profile for this route; LoRA Stack can patch the selected RAW/Turbo transformer experimentally without assuming a CLIP-side LoRA path.",
                notes=("Krea 2 uses model-only LoRA patching through LoraLoaderModelOnly. Compatibility is shared by Native Inpaint and LanPaint; each compiler supplies its own model-consumer anchors.",),
                enablement_pass="L6.krea2.m16_3",
            )

# Phase 12 removes engine-specific compatibility overlays. Krea 2, Qwen Image,
# Z-Image and Z-Image Turbo inpaint compatibility is declared once per
# family/loader/mode above or below. Native Inpaint and LanPaint use the same
# LoRA policy while retaining separate compiler-owned graph anchors.


# Qwen GGUF + native component families. Native diffusion_model moved from
# implementation_target to experimental because the compiler-owned profile exists
# for generation and edit/mask/canvas routes.
for _family in ("qwen_image", "qwen_image_edit_2509", "qwen_image_edit_2511"):
    for _loader in ("diffusion_model", "gguf"):
        for _mode in SUPPORTED_MODES:
            _add_model_clip_experimental(
                _family, _loader, _mode,
                "Qwen compiler emits explicit single-encoder model/clip refs for this route; LoRA Stack can patch exact model/clip consumers experimentally without falling back across Qwen family variants.",
                notes=("2509 owns multi-source img2img/edit; normal qwen_image remains single-source. LoRA graph insertion stays base-pass only in L6.",),
                enablement_pass=f"L6.{_family}",
            )

# Qwen Rapid AIO: GGUF uses Qwen single-encoder consumer rewire. Bundled
# checkpoint_aio uses a checkpoint-style chain from CheckpointLoaderSimple outputs,
# but remains experimental because AIO LoRA compatibility depends on the bundle.
for _mode in SUPPORTED_MODES:
    _add_model_clip_experimental(
        "qwen_rapid_aio", "gguf", _mode,
        "Qwen Rapid AIO GGUF route emits Qwen single-encoder model/clip refs; LoRA Stack can patch matching consumers experimentally.",
        notes=("Requires Qwen-compatible GGUF LoRA assets and the selected runtime node support.",),
        enablement_pass="L6.qwen_rapid_aio",
    )
    _add_model_clip_chain_experimental(
        "qwen_rapid_aio", "checkpoint_aio", _mode,
        "Qwen Rapid AIO bundled checkpoint route emits CheckpointLoaderSimple MODEL/CLIP refs; LoRA Stack can chain standard LoraLoader experimentally on top of the AIO checkpoint graph.",
        notes=("No external encoder/VAE/MMProj field fallback is introduced; this only patches the compiler-owned AIO checkpoint outputs.",),
        enablement_pass="L6.qwen_rapid_aio",
    )

# ZImage / ZImage Turbo: image modes are promoted to experimental because the
# native/GGUF compilers now emit owned patch profiles for Image 1 source/mask/
# outpaint routes. Edit is intentionally absent because it is not a base route.
for _family in ("z_image", "z_image_turbo"):
    for _loader in ("diffusion_model", "gguf"):
        for _mode in ("generate", "img2img", "inpaint", "outpaint"):
            _add_model_clip_experimental(
                _family, _loader, _mode,
                "ZImage compiler emits model/clip refs for this route; LoRA Stack can patch exact AuraFlow/ZImage model and Qwen3/lumina2 clip consumers experimentally.",
                notes=("Image 1 is the only consumed source lane; no fallback to base/turbo sibling, Qwen, Flux, or SD routes.",),
                enablement_pass=f"L6.{_family}",
            )

# HiDream-I1 generation and LanPaint inpainting expose compiler-owned model and
# four-encoder CLIP anchors. E1/E1.1/O1 and other image-conditioned modes stay
# gated until their distinct architectures receive dedicated compilers.
for _loader in ("diffusion_model", "gguf"):
    _add_model_clip_experimental(
        "hidream", _loader, "generate",
        "HiDream txt2img compiler emits model/clip refs; LoRA Stack can patch generation consumers experimentally.",
        notes=("HiDream-I1 LanPaint support does not promote E1/E1.1/O1 or native image-conditioned workflows.",),
        enablement_pass="L6.hidream",
    )
    _add_model_clip_experimental(
        "hidream", _loader, "inpaint",
        "Phase 21 HiDream-I1 LanPaint compiler emits model and quadruple-CLIP refs and proves consumer rewiring before ModelSamplingSD3 and LanPaint sampling.",
        notes=("Compatibility is engine-independent; the HiDream-I1 LanPaint compiler owns exact model/CLIP graph anchors and Phase 19 catalog binding.",),
        enablement_pass="Phase21.hidream_i1_lanpaint",
    )
for _mode in ("img2img", "outpaint"):
    _add("hidream", "diffusion_model", _mode, PLANNED, "HiDream image-conditioned workflow remains variant-gated; no LoRA patch path is validated.", "none", patch_profile_required=True, enablement_pass="L6.hidream")
    _add("hidream", "gguf", _mode, PLANNED, "HiDream GGUF image-conditioned workflow remains variant-gated; no LoRA patch path is validated.", "none", patch_profile_required=True, enablement_pass="L6.hidream")

# Phase 22 Anima uses model-only LoRA anchors for txt2img, img2img and LanPaint inpaint.
for _loader in ("diffusion_model", "gguf"):
    for _mode in ("generate", "img2img", "inpaint"):
        _add(
            "anima", _loader, _mode, EXPERIMENTAL,
            "Phase 22 Anima compiler emits a model-only patch profile for the selected workflow and exact provider catalog binding.",
            "lora_loader_model_only_consumer_rewire",
            roles=("model_ref", "sampler_model_input"),
            notes=("Anima official Turbo LoRA is model-only; CLIP remains unpatched.",),
            loader_node_class="LoraLoaderModelOnly", requires_clip=False, patch_profile_required=True, enablement_pass="Phase22.anima",
        )
    _add("anima", _loader, "outpaint", PLANNED, "Anima outpainting is outside Phase 22.", "none", patch_profile_required=True, enablement_pass="Phase22.anima")
    _add("anima", _loader, "edit", UNSUPPORTED, "Anima does not expose a Neo Image edit route in Phase 22.", "none", patch_profile_required=False, enablement_pass="Phase22.anima")

# Ideogram 4 uses paired main/unconditional models. Generic LoRA insertion is
# deliberately blocked until both branches have a proven patch contract.
for _loader in ("diffusion_model", "gguf"):
    for _mode in ("generate", "inpaint"):
        _add("ideogram4", _loader, _mode, PLANNED, "Ideogram 4 dual-model LoRA patching is not proven; explicit LoRA requests must fail closed.", "none", patch_profile_required=True, enablement_pass="Phase22.ideogram4_dual_model_hold")
    for _mode in ("img2img", "outpaint", "edit"):
        _add("ideogram4", _loader, _mode, UNSUPPORTED if _mode in {"img2img","edit"} else PLANNED, "This Ideogram 4 workflow is not active in Phase 22.", "none", patch_profile_required=False, enablement_pass="Phase22.ideogram4_route_guard")

# Explicit unsupported edit routes for families whose Image base matrix does not
# expose edit. This prevents the global edit mode from inheriting wildcard states.
for _family in ("flux", "z_image", "z_image_turbo", "hidream", "anima", "ideogram4"):
    for _loader in ("diffusion_model", "gguf"):
        _add(_family, _loader, "edit", UNSUPPORTED, "This family/loader does not expose a Neo Image edit base route; LoRA Stack must not synthesize one.", "none", patch_profile_required=False, enablement_pass="L6.unsupported_edit_guard")

for _mode in ("generate", "img2img", "inpaint", "outpaint"):
    for _family in ("wan_image", "hunyuan_image"):
        for _loader in ("diffusion_model", "gguf"):
            _add(
                _family, _loader, _mode, PROVIDER_GATED,
                "Provider/compiler route is not validated for LoRA graph patching in this V2 build; future support must use a dedicated provider-specific adapter, often with model-only LoRA patch semantics.",
                PROVIDER_SPECIFIC_PATCH_STRATEGY,
                roles=("model_ref", "provider_specific_adapter"),
                notes=("L5 recognizes provider_specific as a strategy placeholder but intentionally does not mutate generic Image graphs through it.",),
                loader_node_class=None,
                requires_clip=False,
                patch_profile_required=True,
                enablement_pass="L6.provider_specific_gates",
            )

WORKSPACE_SUPPORT: dict[str, dict[str, str]] = {
    "assets": {"state": AVAILABLE, "reason": "Canonical LoRA Stack owner workspace with full stack and library editing."},
    "generations": {"state": UNSUPPORTED, "reason": "LoRA Stack UI is owned by Image Assets. Generation collects the applied Assets payload at submit time but does not mount the panel."},
    "reference": {"state": UNSUPPORTED, "reason": "LoRA Stack UI is owned by Image Assets. Reference keeps its own image/reference controls and does not mount the LoRA panel."},
    "finish": {"state": UNSUPPORTED, "reason": "LoRA Stack UI is owned by Image Assets. Finish tools stay separate; finish-only LoRA intent remains metadata/route-gated."},
    "results": {"state": UNSUPPORTED, "reason": "Results can display LoRA metadata/replay info but must not actively edit the generation graph."},
}


def _base_route_state(backend: str, family: str, loader: str, mode: str) -> str:
    if backend not in SUPPORTED_BACKENDS:
        return PROVIDER_GATED
    if resolve_model_backend_route is None:
        return PLANNED
    try:
        route = resolve_model_backend_route(family, loader, _comfy_workflow_mode(mode), backend)
        return str(route.state)
    except Exception:
        return PLANNED


def _default_support_state(backend: str, family: str, loader: str, mode: str) -> str:
    if backend and backend not in SUPPORTED_BACKENDS:
        return PROVIDER_GATED
    if mode not in SUPPORTED_MODES:
        return UNSUPPORTED
    if family not in SUPPORTED_FAMILIES or loader not in SUPPORTED_LOADERS:
        return UNSUPPORTED
    base_state = _base_route_state(backend or "comfyui", family, loader, mode)
    if base_state == UNSUPPORTED:
        return UNSUPPORTED
    if base_state == PROVIDER_GATED:
        return PROVIDER_GATED
    if base_state == PLANNED:
        return PLANNED
    if base_state == IMPLEMENTATION_TARGET:
        return IMPLEMENTATION_TARGET
    if base_state in ACTIVE_STATES:
        return IMPLEMENTATION_TARGET
    return PLANNED


def _support_row(family: str, loader: str, mode: str, engine: str | None = None) -> dict[str, Any] | None:
    # Engine is graph metadata only. Compatibility is resolved exclusively from
    # family + loader + workflow mode.
    _ = engine
    return ROUTE_SUPPORT.get(_key(family, loader, mode))


def route_state(backend: str | None, family: str | None, loader: str | None, mode: str | None, engine: str | None = None) -> str:
    backend_value = str(backend or "comfyui")
    family_value = str(family or "")
    loader_value = str(loader or "")
    mode_value = normalize_workflow_mode(mode)
    if backend_value not in SUPPORTED_BACKENDS:
        return PROVIDER_GATED
    if not mode_value or mode_value not in SUPPORTED_MODES:
        return UNSUPPORTED
    support = _support_row(family_value, loader_value, mode_value, engine)
    if support:
        return str(support["state"])
    return _default_support_state(backend_value, family_value, loader_value, mode_value)


def support_reason(backend: str | None, family: str | None, loader: str | None, mode: str | None, engine: str | None = None) -> str:
    backend_value = str(backend or "comfyui")
    family_value = str(family or "")
    loader_value = str(loader or "")
    mode_value = normalize_workflow_mode(mode)
    if backend_value not in SUPPORTED_BACKENDS:
        return "Backend is not a supported Comfy backend for LoRA Stack."
    if not mode_value or mode_value not in SUPPORTED_MODES:
        return "Workspace or workflow mode is not an active generation graph target for LoRA Stack."
    support = _support_row(family_value, loader_value, mode_value, engine)
    if support:
        return str(support["reason"])
    state = _default_support_state(backend_value, family_value, loader_value, mode_value)
    if state == UNSUPPORTED:
        return "This family/loader/workflow mode is not a valid Neo Image base route for LoRA Stack."
    if state == PROVIDER_GATED:
        return "Base provider route is gated; LoRA Stack must not patch this graph."
    if state == IMPLEMENTATION_TARGET:
        return "Base route exists, but LoRA Stack needs a compiler-owned patch profile before graph insertion is allowed."
    if state == PLANNED:
        return "LoRA graph wiring for this family/loader/mode is planned but not proven; no fallback assumptions are allowed."
    return "LoRA Stack route state resolved from the explicit engine-independent source-of-truth matrix."


def graph_patch_strategy(backend: str | None, family: str | None, loader: str | None, mode: str | None, engine: str | None = None) -> str:
    backend_value = str(backend or "comfyui")
    if backend_value not in SUPPORTED_BACKENDS:
        return "none"
    support = _support_row(str(family or ""), str(loader or ""), normalize_workflow_mode(mode), engine)
    return str(support["graph_patch"]) if support else "none"


def route_support(backend: str | None, family: str | None, loader: str | None, mode: str | None, engine: str | None = None) -> dict[str, Any]:
    backend_value = str(backend or "comfyui")
    family_value = str(family or "")
    loader_value = str(loader or "")
    mode_value = normalize_workflow_mode(mode)
    engine_value = normalize_route_engine(engine)
    state = route_state(backend_value, family_value, loader_value, mode_value)
    support = _support_row(family_value, loader_value, mode_value)
    base_state = _base_route_state(backend_value, family_value, loader_value, mode_value)
    compatibility_route_key = f"{family_value}:{loader_value}:{mode_value}"
    workflow_route_key = compatibility_route_key + (f":{engine_value}" if engine_value and engine_value != "native" else "")
    return {
        "backend": backend_value,
        "family": family_value,
        "loader": loader_value,
        "mode": mode_value,
        "workflow_mode": mode_value,
        "engine": engine_value or "native",
        "workflow_engine": engine_value or "native",
        "effective_engine": engine_value or "native",
        "engine_explicit": bool(engine_value),
        "compatibility_engine_independent": True,
        "state": state,
        "route_state": state,
        "base_route_state": base_state,
        "reason": support_reason(backend_value, family_value, loader_value, mode_value),
        "graph_patch": str(support["graph_patch"]) if support else "none",
        "loader_node_class": support.get("loader_node_class") if support else None,
        "requires_model": bool(support.get("requires_model", True)) if support else True,
        "requires_clip": bool(support.get("requires_clip", True)) if support else True,
        "patch_profile_required": bool(support.get("patch_profile_required", state == IMPLEMENTATION_TARGET)) if support else state == IMPLEMENTATION_TARGET,
        "required_roles": list(support["required_roles"] if support else ()),
        "notes": list(support["notes"] if support else ()),
        "validated": bool(support.get("validated", state == AVAILABLE)) if support else False,
        "enablement_pass": str(support.get("enablement_pass") or "") if support else "",
        "active": state in ACTIVE_STATES,
        "route_key": compatibility_route_key,
        "compatibility_route_key": compatibility_route_key,
        "workflow_route_key": workflow_route_key,
    }


def support_matrix() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family in SUPPORTED_FAMILIES:
        for loader in SUPPORTED_LOADERS:
            for mode in SUPPORTED_MODES:
                rows.append(route_support("comfyui", family, loader, mode))
    return rows


def active_patch_routes() -> list[dict[str, Any]]:
    return [row for row in support_matrix() if row["state"] in ACTIVE_STATES]


def gated_routes() -> list[dict[str, Any]]:
    return [row for row in support_matrix() if row["state"] in GATED_STATES]


def workspace_support_matrix() -> list[dict[str, str]]:
    return [{"workspace_app": key, **value} for key, value in WORKSPACE_SUPPORT.items()]


def _manifest_mode_aliases(mode: str) -> tuple[str, ...]:
    normalized = normalize_workflow_mode(mode)
    if normalized == "generate":
        return ("generate", "txt2img")
    return (normalized,)


def _manifest_backend_aliases() -> tuple[str, ...]:
    # The frontend normalizes comfyui_portable -> comfyui, but extension route
    # lookups and diagnostics also support backend-prefixed manifest keys. Keep
    # both aliases in the manifest so registry consumers never depend on legacy
    # family:loader:mode keys only.
    return SUPPORTED_BACKENDS


def _set_manifest_state(states: dict[str, str], key: str, state: str) -> None:
    previous = states.get(key)
    if previous is not None and previous != state:
        raise ValueError(f"Conflicting LoRA Stack manifest route state for {key}: {previous} != {state}")
    states[key] = state


def manifest_route_keys_for_row(row: dict[str, Any]) -> list[str]:
    family = str(row["family"])
    loader = str(row["loader"])
    mode = str(row["mode"])
    keys: list[str] = []
    for mode_alias in _manifest_mode_aliases(mode):
        for backend in _manifest_backend_aliases():
            keys.append(f"{backend}:{family}:{loader}:{mode_alias}")
        keys.append(f"{family}:{loader}:{mode_alias}")
    return keys


def manifest_route_states() -> dict[str, str]:
    states: dict[str, str] = {}
    for row in support_matrix():
        for key in manifest_route_keys_for_row(row):
            _set_manifest_state(states, key, str(row["state"]))
    for key, value in WORKSPACE_SUPPORT.items():
        _set_manifest_state(states, key, str(value["state"]))
    _set_manifest_state(states, "*", PLANNED)
    return states



def manifest_route_policies() -> dict[str, dict[str, Any]]:
    """Return engine-independent LoRA compatibility metadata for the UI.

    Node ids and graph anchors are intentionally absent; those remain compiler
    patch-profile data. The manifest publishes only compatibility semantics.
    """
    policies: dict[str, dict[str, Any]] = {}
    for row in support_matrix():
        mode = "model_only" if not bool(row.get("requires_clip", True)) else "model_and_clip"
        policy = {
            "state": str(row.get("state") or PLANNED),
            "lora_mode": mode,
            "loader_node_class": row.get("loader_node_class"),
            "requires_model": bool(row.get("requires_model", True)),
            "requires_clip": bool(row.get("requires_clip", True)),
            "graph_patch": str(row.get("graph_patch") or "none"),
            "patch_profile_required": bool(row.get("patch_profile_required")),
            "compatibility_engine_independent": True,
        }
        for key in manifest_route_keys_for_row(row):
            previous = policies.get(key)
            if previous is not None and previous != policy:
                raise ValueError(f"Conflicting LoRA Stack manifest route policy for {key}")
            policies[key] = dict(policy)
    return policies

def manifest_sync_checksum() -> str:
    payload = {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "supported_backends": list(SUPPORTED_BACKENDS),
        "supported_families": list(SUPPORTED_FAMILIES),
        "supported_loaders": list(SUPPORTED_LOADERS),
        "supported_modes": list(SUPPORTED_MODES),
        "workflow_engine_metadata_values": list(SUPPORTED_ENGINES),
        "compatibility_dimensions": {"backend": True, "family": True, "loader": True, "workflow_mode": True, "engine": False},
        "route_states": manifest_route_states(),
        "route_policies": manifest_route_policies(),
        "workspace_support": workspace_support_matrix(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manifest_sync_contract() -> dict[str, Any]:
    graph_patches = sorted({str(row["graph_patch"]) for row in support_matrix()})
    return {
        "phase": MANIFEST_SYNC_PHASE,
        "schema_version": MATRIX_SCHEMA_VERSION,
        "source": MATRIX_SOURCE,
        "generator": MATRIX_GENERATOR,
        "checksum": manifest_sync_checksum(),
        "states": list(STATE_ORDER),
        "active_states": [AVAILABLE, EXPERIMENTAL],
        "gated_states": [IMPLEMENTATION_TARGET, PLANNED, PROVIDER_GATED, UNSUPPORTED],
        "supported_backends": list(SUPPORTED_BACKENDS),
        "workflow_modes": list(SUPPORTED_MODES),
        "workflow_engine_metadata_values": list(SUPPORTED_ENGINES),
        "compatibility_dimensions": {"backend": True, "family": True, "loader": True, "workflow_mode": True, "engine": False},
        "loaders": list(SUPPORTED_LOADERS),
        "families": list(SUPPORTED_FAMILIES),
        "graph_patch_strategies": graph_patches,
        "supported_patch_strategies": list(SUPPORTED_GRAPH_PATCH_STRATEGIES),
        "patch_node_classes": ["LoraLoader", "LoraLoaderModelOnly"],
        "manifest_keys": {
            "backend_prefixed": True,
            "legacy_unprefixed": True,
            "generate_txt2img_aliases": True,
            "workspace_state_keys": list(WORKSPACE_SUPPORT.keys()),
            "engine_specific_route_keys": False,
            "route_policy_map": True,
        },
        "row_contract": [
            "backend",
            "family",
            "loader",
            "workflow_mode",
            "workflow_engine",
            "compatibility_engine_independent",
            "route_state",
            "base_route_state",
            "graph_patch",
            "loader_node_class",
            "requires_model",
            "requires_clip",
            "patch_profile_required",
            "validated",
            "enablement_pass",
            "reason",
        ],
        "patch_profile_contract": {
            "schema_version": "neo.image.lora_stack.patch_profile.v2",
            "engine_aware": False,
            "engine_is_graph_metadata_only": True,
            "owner": "compiler",
            "required_fields": ["model_ref", "clip_ref", "sampler_node_id", "sampler_model_input", "loader_node_class", "strategy", "source"],
            "gating_rule": "If patch_profile_required is true and the active compiler does not emit a valid profile, LoRA Stack preserves payload intent but does not mutate the graph.",
        },
        "rule": "LoRA compatibility is resolved from backend + family + loader + workflow mode only. Native Inpaint and LanPaint share that compatibility policy, while each compiler must emit its own model/clip graph anchors. No family or loader fallback and no checkpoint/GGUF/checkpoint_aio field mixing.",
        "l0": "Adds implementation_target state, checkpoint_aio loader coverage, edit-mode matrix coverage, base-route-aware default gating, and generated manifest route states from support_matrix.py.",
        "l1": "Mounts the LoRA Stack UI only inside Image Assets. Generation submission still collects the applied Assets payload with other workspace extensions, but Generation, Reference, Finish, and Results do not mount the editable LoRA panel.",
        "l2": "Hardens manifest sync by generating backend-prefixed keys, legacy keys, generate/txt2img aliases, workspace states, and a checksum from the support matrix source of truth.",
        "l3": "Preserves LoRA apply_to targets through frontend payload cleaning, Scene Director regional assignment metadata, and gated-route requested intent metadata.",
        "l4": "Requires compiler-owned LoRA patch profiles for profile-required routes; graph patching uses declared model_ref, clip_ref, sampler_node_id, sampler_model_input, and loader_node_class instead of hardcoded family fallback refs.",
        "l5": "Upgrades graph insertion to a strategy dispatcher: standard model+clip LoraLoader, model-only LoraLoaderModelOnly, provider-specific adapter placeholders, and explicit no-op routes with metadata-only preservation.",
        "l6": "Historical engine-specific LanPaint overlays are normalized by Phase 12 into engine-independent compatibility rows; compiler-owned graph anchors remain route-specific.",
        "l12": "Decouples LoRA compatibility from Native/LanPaint engine selection. The optional LoRA Stack remains family/loader/mode aware, while patch profiles retain workflow engine and graph route lineage.",
        "workspace_apps": ["assets"],
        "mount_slots": [
            "image.assets.lora_stack",
        ],
        "canonical_workspace_app": "assets",
    }


def support_matrix_snapshot() -> dict[str, Any]:
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "phase": MANIFEST_SYNC_PHASE,
        "source": MATRIX_SOURCE,
        "checksum": manifest_sync_checksum(),
        "supported_backends": list(SUPPORTED_BACKENDS),
        "supported_families": list(SUPPORTED_FAMILIES),
        "supported_loaders": list(SUPPORTED_LOADERS),
        "workflow_modes": list(SUPPORTED_MODES),
        "workflow_engine_metadata_values": list(SUPPORTED_ENGINES),
        "compatibility_dimensions": {"backend": True, "family": True, "loader": True, "workflow_mode": True, "engine": False},
        "route_states": manifest_route_states(),
        "route_policies": manifest_route_policies(),
        "support_matrix": support_matrix(),
        "workspace_support": workspace_support_matrix(),
    }
