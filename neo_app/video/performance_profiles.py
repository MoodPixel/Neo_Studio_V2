from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from neo_app.video.route_matrix import (
    find_video_route,
    normalize_video_family,
    normalize_video_generation_type,
    normalize_video_loader,
)

SCHEMA_VERSION: Final[str] = "neo.video.performance_profiles.vg13"
PHASE: Final[str] = "V-G13"


@dataclass(frozen=True)
class VideoPerformanceProfile:
    id: str
    label: str
    target: str
    intent: str
    vram_profile: str
    defaults: dict[str, Any] = field(default_factory=dict)
    optimizer_policy: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return asdict(self)


PERFORMANCE_PROFILES: Final[dict[str, VideoPerformanceProfile]] = {
    "safe_12gb": VideoPerformanceProfile(
        "safe_12gb",
        "Safe 12GB",
        "12GB VRAM",
        "Stable first local tests with quantized models, tiled decode, and short clips.",
        "low",
        {"decode_mode": "tiled", "tile_size": 384, "batch_count": 1, "prefer_low_step_mode": True},
        {
            "quantized_loader": "required_when_available",
            "low_step_mode": "recommended",
            "sage_attention": "off_by_default",
            "teacache": "off_by_default",
            "offload": "off_by_default",
            "torch_compile": "off",
        },
        ("Use this before raising resolution or frame count.", "Best match for WAN GGUF + LightX2V first tests."),
    ),
    "balanced_12gb": VideoPerformanceProfile(
        "balanced_12gb",
        "Balanced 12GB",
        "12GB VRAM",
        "Usable preview quality while keeping batch/decode guards active.",
        "balanced",
        {"decode_mode": "tiled", "tile_size": 512, "batch_count": 1},
        {"quantized_loader": "preferred", "low_step_mode": "optional", "sage_attention": "manual", "teacache": "manual", "offload": "manual", "torch_compile": "advanced"},
        ("Raise one dimension at a time: frames first is usually the most expensive.",),
    ),
    "fast_draft": VideoPerformanceProfile(
        "fast_draft",
        "Fast Draft",
        "8-12GB VRAM",
        "Shortest test clips for checking prompt, motion, and node wiring.",
        "low",
        {"decode_mode": "tiled", "tile_size": 384, "batch_count": 1, "prefer_low_step_mode": True, "prefer_short_clip": True},
        {"quantized_loader": "required_when_available", "low_step_mode": "strongly_recommended", "sage_attention": "manual", "teacache": "manual", "offload": "manual", "torch_compile": "off"},
        ("Use for smoke tests, not final quality.",),
    ),
    "extreme_low_vram": VideoPerformanceProfile(
        "extreme_low_vram",
        "Extreme Low VRAM",
        "8-10GB survival mode",
        "Survival mode for routes that barely fit: offload/block-swap capable adapters later.",
        "low",
        {"decode_mode": "tiled", "tile_size": 384, "batch_count": 1, "prefer_low_step_mode": True, "prefer_offload": True, "prefer_short_clip": True},
        {"quantized_loader": "required", "low_step_mode": "required_when_available", "sage_attention": "off_by_default", "teacache": "off_by_default", "offload": "recommended", "torch_compile": "off"},
        ("Expect slower runtime.", "Designed to support future offload/block-swap adapters."),
    ),
    "custom": VideoPerformanceProfile(
        "custom",
        "Custom",
        "advanced",
        "Manual optimizer switches with runtime preflight protection.",
        "manual",
        {"decode_mode": "tiled", "batch_count": 1},
        {"quantized_loader": "manual", "low_step_mode": "manual", "sage_attention": "manual", "teacache": "manual", "offload": "manual", "torch_compile": "manual"},
        ("Custom can still be blocked by route/runtime preflight.",),
    ),
}

PROFILE_ALIASES: Final[dict[str, str]] = {
    "safe": "safe_12gb",
    "safe12": "safe_12gb",
    "safe_12": "safe_12gb",
    "12gb": "safe_12gb",
    "balanced": "balanced_12gb",
    "balanced12": "balanced_12gb",
    "draft": "fast_draft",
    "fast": "fast_draft",
    "low": "safe_12gb",
    "extreme": "extreme_low_vram",
    "survival": "extreme_low_vram",
    "manual": "custom",
    "experimental": "custom",
}

ROUTE_OPTIMIZER_SUPPORT: Final[dict[str, dict[str, Any]]] = {
    "wan22.gguf.img2vid_14b_dual_noise": {
        "quantized_loader": {"status": "active", "label": "GGUF loader"},
        "low_step_mode": {"status": "active", "label": "LightX2V 4-step", "field": "enable_lightx2v"},
        "video_lora": {"status": "active", "label": "WAN dual-branch Video LoRA"},
        "tiled_decode": {"status": "active", "label": "Tiled decode"},
        "sage_attention": {"status": "active", "phase": "V-G11", "label": "Sage Attention KJ"},
        "teacache": {"status": "active", "phase": "V-G12", "label": "TeaCache"},
        "offload": {"status": "active", "phase": "V-G13", "label": "CPU offload"},
        "block_swap": {"status": "active", "phase": "V-G13", "label": "WAN block swap"},
        "torch_compile": {"status": "advanced_planned", "label": "torch.compile"},
        "vae_offload": {"status": "active", "phase": "V-G13", "label": "VAE offload / tiled decode"},
    },
    "ltx23.gguf.txt2vid": {
        "quantized_loader": {"status": "active", "label": "GGUF / FP8 loader"},
        "low_step_mode": {"status": "route_dependent", "label": "LTX distilled few-step mode"},
        "tiled_decode": {"status": "active", "label": "Tiled VAE decode"},
        "sage_attention": {"status": "active", "phase": "V-G11", "label": "Sage Attention"},
        "teacache": {"status": "active_metadata", "phase": "V-G12", "label": "TeaCache"},
        "offload": {"status": "active_preflight", "phase": "V-G13", "label": "LTX low-VRAM/offload loaders"},
        "vae_offload": {"status": "active", "phase": "V-G13", "label": "VAE offload / tiled decode"},
        "torch_compile": {"status": "advanced_planned", "label": "torch.compile"},
    },
    "ltx23.gguf.img2vid": {
        "quantized_loader": {"status": "active", "label": "GGUF / FP8 loader"},
        "low_step_mode": {"status": "route_dependent", "label": "LTX distilled few-step mode"},
        "tiled_decode": {"status": "active", "label": "Tiled VAE decode"},
        "sage_attention": {"status": "active", "phase": "V-G11", "label": "Sage Attention"},
        "teacache": {"status": "active_metadata", "phase": "V-G12", "label": "TeaCache"},
        "offload": {"status": "active_preflight", "phase": "V-G13", "label": "LTX low-VRAM/offload loaders"},
        "vae_offload": {"status": "active", "phase": "V-G13", "label": "VAE offload / tiled decode"},
        "torch_compile": {"status": "advanced_planned", "label": "torch.compile"},
    },
}

GENERIC_LTX_SUPPORT: Final[dict[str, dict[str, Any]]] = {
    "low_step_mode": {"status": "route_dependent", "label": "LTX distilled few-step mode"},
    "tiled_decode": {"status": "active", "label": "Tiled VAE decode"},
    "sage_attention": {"status": "active", "phase": "V-G11", "label": "Sage Attention"},
    "teacache": {"status": "active_metadata", "phase": "V-G12", "label": "TeaCache"},
    "offload": {"status": "active_preflight", "phase": "V-G13", "label": "LTX low-VRAM/offload loaders"},
    "vae_offload": {"status": "active", "phase": "V-G13", "label": "VAE offload / tiled decode"},
    "torch_compile": {"status": "advanced_planned", "label": "torch.compile"},
}


def normalize_video_performance_profile(value: str | None) -> str:
    key = str(value or "safe_12gb").strip().lower().replace("-", "_").replace(" ", "_")
    return PROFILE_ALIASES.get(key, key if key in PERFORMANCE_PROFILES else "safe_12gb")


def _route_support(route_id: str, family: str, loader: str) -> dict[str, Any]:
    if route_id in ROUTE_OPTIMIZER_SUPPORT:
        return deepcopy(ROUTE_OPTIMIZER_SUPPORT[route_id])
    if family == "ltx23":
        support = deepcopy(GENERIC_LTX_SUPPORT)
        if loader == "gguf":
            support["quantized_loader"] = {"status": "active", "label": "GGUF / FP8 loader"}
        else:
            support["quantized_loader"] = {"status": "available_if_gguf", "label": "Prefer GGUF / FP8 for low VRAM"}
        return support
    if family == "wan22":
        return {
            "tiled_decode": {"status": "active", "label": "Tiled decode where supported"},
            "sage_attention": {"status": "active", "phase": "V-G11", "label": "Sage Attention KJ"},
            "teacache": {"status": "active", "phase": "V-G12", "label": "TeaCache"},
            "offload": {"status": "active", "phase": "V-G13", "label": "Offload / block swap"},
        }
    return {}


def _requested_optimizer_flags(values: dict[str, Any]) -> dict[str, bool]:
    def b(key: str) -> bool:
        value = values.get(key)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    return {
        "sage_attention": b("enable_sage_attention"),
        "teacache": b("enable_teacache"),
        "offload": b("enable_cpu_offload"),
        "vae_offload": b("enable_vae_offload"),
        "block_swap": b("enable_block_swap"),
        "torch_compile": b("enable_torch_compile"),
    }


def build_video_performance_contract(
    *,
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    performance_profile: str | None = None,
    vram_profile: str | None = None,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incoming = deepcopy(values or {})
    nf = normalize_video_family(family or incoming.get("family"))
    nl = normalize_video_loader(loader or incoming.get("loader"))
    nt = normalize_video_generation_type(generation_type or incoming.get("generation_type") or incoming.get("mode"))
    route = find_video_route(nf, nl, nt, include_planned=True)
    route_id = route.route_id if route else ""
    profile_id = normalize_video_performance_profile(performance_profile or incoming.get("performance_profile"))
    profile = PERFORMANCE_PROFILES[profile_id]
    support = _route_support(route_id, nf, nl)
    requested = _requested_optimizer_flags(incoming)

    warnings: list[str] = []
    errors: list[str] = []
    action_items: list[str] = []
    selected: dict[str, Any] = {
        "performance_profile": profile_id,
        "vram_profile": str(vram_profile or incoming.get("vram_profile") or profile.vram_profile),
        "enable_sage_attention": requested["sage_attention"],
        "sage_attention_mode": str(incoming.get("sage_attention_mode") or "auto"),
        "sage_attention_target": str(incoming.get("sage_attention_target") or "both"),
        "enable_teacache": requested["teacache"],
        "teacache_profile": str(incoming.get("teacache_profile") or "conservative"),
        "teacache_target": str(incoming.get("teacache_target") or "both"),
        "enable_cpu_offload": bool(incoming.get("enable_cpu_offload", False)),
        "enable_vae_offload": bool(incoming.get("enable_vae_offload", False)),
        "enable_block_swap": bool(incoming.get("enable_block_swap", False)),
        "block_swap_target": str(incoming.get("block_swap_target") or "both"),
        "block_swap_blocks": int(float(incoming.get("block_swap_blocks") or 12)),
        "enable_torch_compile": bool(incoming.get("enable_torch_compile", False)),
    }

    for key, is_enabled in requested.items():
        if not is_enabled:
            continue
        status = str((support.get(key) or {}).get("status") or "unsupported")
        if status in {"planned", "advanced_planned", "route_dependent", "available_if_gguf", "active_metadata", "unsupported"}:
            phase = (support.get(key) or {}).get("phase") or "future adapter phase"
            errors.append(f"{key} is selected, but this route only exposes it as {status}; graph mutation is reserved for {phase}.")
            action_items.append(f"Disable {key} until its dedicated adapter phase is implemented for this route.")

    if profile_id == "extreme_low_vram":
        warnings.append("Extreme Low VRAM profile is a stability profile; it may run slower once offload/block-swap adapters are active.")
    if nl != "gguf" and profile_id in {"safe_12gb", "fast_draft", "extreme_low_vram"}:
        warnings.append("For low-VRAM video routes, GGUF/FP8 loaders are usually safer than full safetensors/UNET loaders.")

    graph_mutations: list[dict[str, Any]] = []
    if requested["sage_attention"] and not errors:
        graph_mutations.append({"adapter": "sage_attention", "phase": "V-G11", "status": "active"})
    if requested["teacache"] and not errors:
        graph_mutations.append({"adapter": "teacache", "phase": "V-G12", "status": "active"})
    if (requested.get("offload") or requested.get("block_swap") or requested.get("vae_offload")) and not errors:
        graph_mutations.append({"adapter": "low_vram", "phase": "V-G13", "status": "active"})

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "surface": "video",
        "route_id": route_id,
        "route": route.payload() if route else None,
        "family": nf,
        "loader": nl,
        "generation_type": nt,
        "selected": selected,
        "profile": profile.payload(),
        "optimizer_support": support,
        "requested_optimizers": requested,
        "queue_ready": not errors,
        "graph_mutation_ready": bool(graph_mutations),
        "graph_mutations": graph_mutations,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": list(dict.fromkeys(errors)),
        "action_items": list(dict.fromkeys(action_items)),
        "recommended_order": [
            "First verify base quantized route + low-step mode.",
            "Then test TeaCache conservatively.",
            "Then test Sage Attention.",
            "Only combine optimizers after each one works alone.",
        ],
        "rules": [
            "V-G13 defines a shared Video performance contract for WAN/LTX with Sage, WAN TeaCache, and WAN low-VRAM adapters active.",
            "TeaCache and low-VRAM graph insertion are active for the WAN GGUF dual-noise route; LTX low-VRAM loaders are detected/preflighted but still route-specific.",
            "If a future optimizer is enabled before its adapter exists, runtime preflight blocks queueing instead of silently ignoring it.",
        ],
    }


def apply_video_performance_defaults(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(payload if isinstance(payload, dict) else {})
    contract = build_video_performance_contract(values=data)
    profile_defaults = dict((contract.get("profile") or {}).get("defaults") or {})
    selected = dict(contract.get("selected") or {})
    effective = {**profile_defaults, **data}
    effective.setdefault("performance_profile", selected.get("performance_profile", "safe_12gb"))
    effective.setdefault("vram_profile", selected.get("vram_profile", "low"))
    effective.setdefault("batch_count", 1)
    return effective


def video_performance_profile_payload(
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    mode: str | None = None,
    performance_profile: str | None = None,
    vram_profile: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    incoming = {k: v for k, v in values.items() if v is not None}
    return build_video_performance_contract(
        family=family,
        loader=loader,
        generation_type=generation_type or mode,
        performance_profile=performance_profile,
        vram_profile=vram_profile,
        values=incoming,
    )


def video_performance_preflight_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(payload if isinstance(payload, dict) else {})
    return build_video_performance_contract(values=data)
