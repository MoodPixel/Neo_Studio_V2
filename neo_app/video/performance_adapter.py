from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

from neo_app.video.performance_profiles import build_video_performance_contract
from neo_app.video.video_performance_probe import video_performance_probe_payload
from neo_app.video.low_vram_adapter import low_vram_probe_payload

SCHEMA_VERSION: Final[str] = "neo.video.performance_adapter.vg13"
PHASE: Final[str] = "V-G13"


def build_video_performance_adapter_payload(
    payload: dict[str, Any] | None = None,
    *,
    object_info: dict[str, Any] | None = None,
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    route_id: str | None = None,
) -> dict[str, Any]:
    data = deepcopy(payload if isinstance(payload, dict) else {})
    contract = build_video_performance_contract(
        family=family or data.get("family"),
        loader=loader or data.get("loader"),
        generation_type=generation_type or data.get("generation_type") or data.get("mode"),
        performance_profile=data.get("performance_profile"),
        vram_profile=data.get("vram_profile"),
        values=data,
    )
    probe = video_performance_probe_payload(
        object_info or {},
        family=family or data.get("family"),
        loader=loader or data.get("loader"),
        generation_type=generation_type or data.get("generation_type") or data.get("mode"),
        performance_profile=data.get("performance_profile"),
        values=data,
    )
    selected = contract.get("selected", {}) if isinstance(contract.get("selected"), dict) else {}
    sage_active = bool(selected.get("enable_sage_attention"))
    teacache_active = bool(selected.get("enable_teacache"))
    low_vram_active = bool(selected.get("enable_cpu_offload") or selected.get("enable_vae_offload") or selected.get("enable_block_swap"))
    graph_mutations: list[dict[str, Any]] = []
    if sage_active:
        graph_mutations.append({"adapter": "sage_attention", "phase": "V-G11", "status": "active"})
    if teacache_active:
        graph_mutations.append({"adapter": "teacache", "phase": "V-G12", "status": "active"})
    if low_vram_active:
        graph_mutations.append({"adapter": "low_vram", "phase": "V-G13", "status": "active"})

    low_vram_probe = low_vram_probe_payload(object_info or {}, family=family or data.get("family"), values=data)

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "surface": "video",
        "route_id": route_id or contract.get("route_id", ""),
        "profile": contract.get("profile", {}),
        "selected": contract.get("selected", {}),
        "optimizer_support": contract.get("optimizer_support", {}),
        "probe": probe,
        "low_vram_probe": low_vram_probe,
        "queue_ready": bool(contract.get("queue_ready", True) and probe.get("queue_ready", True) and low_vram_probe.get("queue_ready", True)),
        "graph_mutation_ready": bool(graph_mutations and contract.get("queue_ready", True) and probe.get("queue_ready", True) and low_vram_probe.get("queue_ready", True)),
        "graph_mutations": graph_mutations,
        "active_graph_mutations": graph_mutations,
        "warnings": list(dict.fromkeys([*(contract.get("warnings", []) or []), *(probe.get("warnings", []) or []), *(low_vram_probe.get("warnings", []) or [])])),
        "errors": list(dict.fromkeys([*(contract.get("errors", []) or []), *(probe.get("errors", []) or []), *(low_vram_probe.get("errors", []) or [])])),
        "action_items": list(dict.fromkeys([*(contract.get("action_items", []) or []), *(probe.get("action_items", []) or []), *(low_vram_probe.get("action_items", []) or [])])),
        "rules": [
            "V-G13 is the shared Video performance adapter contract with active Sage Attention, WAN TeaCache, and WAN low-VRAM support.",
            "Offload/block-swap is a stability adapter and may slow generation while reducing VRAM pressure.",
        ],
    }
