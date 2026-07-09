from __future__ import annotations

from collections import Counter
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.surfaces.registry import get_surface, list_surfaces

BLUEPRINT_SCHEMA_ID = "neo.surface.blueprint.v1"
BLUEPRINT_AREAS = [
    "workspace",
    "inputs",
    "provider_backend",
    "creative_controls",
    "extensions",
    "run_queue",
    "results",
    "history",
    "memory_context",
    "inspector",
]

AREA_LABELS = {
    "workspace": "Workspace",
    "inputs": "Inputs",
    "provider_backend": "Provider / Backend",
    "creative_controls": "Creative Controls",
    "extensions": "Extensions",
    "run_queue": "Run / Queue",
    "results": "Results",
    "history": "History",
    "memory_context": "Memory / Context",
    "inspector": "Inspector",
}

AREA_DESCRIPTIONS = {
    "workspace": "Main creator workspace, editor, board, chat, or project canvas.",
    "inputs": "Source text, media, prompts, filters, scripts, references, and import targets.",
    "provider_backend": "Provider, backend, model, route, loader, and capability selection.",
    "creative_controls": "Parameters, setup controls, masks, instructions, and reusable presets.",
    "extensions": "Surface-scoped extension mounts rendered through the shared extension UI contract.",
    "run_queue": "Generate, compile, queue, batch, and runtime actions.",
    "results": "Outputs, previews, generated records, and reusable result actions.",
    "history": "Saved records, archives, previous outputs, replay, and reuse history.",
    "memory_context": "Memory, retrieval, continuity, lore, context packs, and cross-surface handoffs.",
    "inspector": "Expert diagnostics, payloads, health details, and implementation-neutral status views.",
}

SLOT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("extensions", ("extension", "extensions")),
    ("provider_backend", ("backend", "provider", "model", "route", "loader", "capabilit", "families", "profiles")),
    ("run_queue", ("run", "queue", "batch", "compile", "runtime", "job", "actions")),
    ("results", ("result", "results", "output", "preview", "export")),
    ("history", ("history", "records", "archive", "saved", "library", "libraries", "reuse")),
    ("memory_context", ("memory", "context", "retrieval", "lore", "canon", "continuity", "handoff", "namespace", "engine_bridge")),
    ("inspector", ("inspector", "diagnostic", "payload", "health", "validation", "logs", "changelog")),
    ("inputs", ("input", "inputs", "source", "prompt", "script", "caption", "tags", "character", "filters", "assets", "notes")),
    ("creative_controls", ("param", "control", "settings", "preset", "setup", "mask", "canvas", "instruction", "editor", "profile")),
    ("workspace", ("workspace", "guide", "chat", "thread", "project", "projects", "board", "studio", "builder", "rail", "list")),
]

AREA_FALLBACKS_BY_SURFACE = {
    "admin": ["workspace", "provider_backend", "extensions", "memory_context", "history", "inspector"],
    "image": ["inputs", "provider_backend", "creative_controls", "extensions", "run_queue", "results", "history", "inspector"],
    "prompt_captioning": ["inputs", "provider_backend", "creative_controls", "results", "history", "memory_context", "inspector"],
    "roleplay": ["workspace", "inputs", "provider_backend", "run_queue", "history", "memory_context", "inspector"],
    "assistant": ["workspace", "inputs", "provider_backend", "memory_context", "history", "inspector"],
    "video": ["inputs", "provider_backend", "creative_controls", "extensions", "run_queue", "results", "history", "inspector"],
    "voice": ["inputs", "provider_backend", "creative_controls", "extensions", "run_queue", "results", "history", "inspector"],
    "music": ["inputs", "provider_backend", "creative_controls", "extensions", "run_queue", "results", "history", "inspector"],
    "board": ["workspace", "inputs", "extensions", "history", "memory_context", "inspector"],
}


def _area_for_slot(slot: str) -> str:
    normalized = str(slot or "").lower().replace("-", "_")
    for area, tokens in SLOT_RULES:
        if any(token in normalized for token in tokens):
            return area
    return "workspace"


def _blueprint_area(area_id: str, slots: list[str] | None = None, required: bool = True) -> dict[str, Any]:
    return {
        "area_id": area_id,
        "label": AREA_LABELS.get(area_id, area_id.replace("_", " ").title()),
        "description": AREA_DESCRIPTIONS.get(area_id, "Shared surface area."),
        "slots": sorted(set(slots or [])),
        "required": bool(required),
    }


def _subtab_blueprint(surface_id: str, subtab: dict[str, Any]) -> dict[str, Any]:
    slots = [str(slot) for slot in subtab.get("slots", []) if str(slot or "").strip()]
    area_slots: dict[str, list[str]] = {area: [] for area in BLUEPRINT_AREAS}
    for slot in slots:
        area_slots[_area_for_slot(slot)].append(slot)

    fallback_areas = AREA_FALLBACKS_BY_SURFACE.get(surface_id, ["workspace", "inputs", "results", "inspector"])
    active_areas = [area for area in BLUEPRINT_AREAS if area_slots.get(area) or area in fallback_areas]
    areas = [_blueprint_area(area, area_slots.get(area, []), required=area in fallback_areas) for area in active_areas]
    missing_required = [area for area in fallback_areas if not area_slots.get(area)]
    return {
        "subtab_id": subtab.get("subtab_id") or "workspace",
        "display_name": subtab.get("display_name") or str(subtab.get("subtab_id") or "Workspace").title(),
        "description": subtab.get("description") or "",
        "slots": slots,
        "areas": areas,
        "coverage": {
            "declared_slot_count": len(slots),
            "active_area_count": len(active_areas),
            "missing_required_areas": missing_required,
        },
    }


def surface_blueprint(surface_id: str) -> dict[str, Any] | None:
    surface = get_surface(surface_id)
    if surface is None:
        return None
    payload = model_to_dict(surface)
    subtabs = [_subtab_blueprint(surface.surface_id, dict(subtab)) for subtab in payload.get("subtabs", [])]
    declared_areas = Counter()
    for subtab in subtabs:
        for area in subtab.get("areas", []):
            if area.get("slots"):
                declared_areas[area["area_id"]] += len(area.get("slots") or [])

    standard_areas = [_blueprint_area(area, [], required=area in AREA_FALLBACKS_BY_SURFACE.get(surface.surface_id, [])) for area in BLUEPRINT_AREAS]
    return {
        "schema_id": BLUEPRINT_SCHEMA_ID,
        "blueprint_version": "1.0.0",
        "surface_id": surface.surface_id,
        "display_name": surface.display_name,
        "description": surface.description,
        "status": payload.get("status", "planned"),
        "provider_types": payload.get("provider_types", []),
        "extension_targets": payload.get("extension_targets", []),
        "memory_policy": payload.get("memory_policy", {}),
        "standard_areas": standard_areas,
        "subtabs": subtabs,
        "coverage": {
            "subtab_count": len(subtabs),
            "declared_area_counts": dict(declared_areas),
            "canonical_area_count": len(BLUEPRINT_AREAS),
        },
        "policy": "Surfaces should compose these shared areas instead of inventing one-off UI structures.",
    }


def surface_blueprint_payload(include_disabled: bool = True) -> dict[str, Any]:
    surfaces = list_surfaces(include_disabled=include_disabled)
    blueprints = [surface_blueprint(surface.surface_id) for surface in surfaces]
    blueprints = [item for item in blueprints if item]
    return {
        "schema_id": BLUEPRINT_SCHEMA_ID,
        "blueprint_version": "1.0.0",
        "areas": [_blueprint_area(area) for area in BLUEPRINT_AREAS],
        "surfaces": blueprints,
        "policy": "Every surface should expose a stable anatomy: workspace, inputs, backend, controls, extensions, run queue, results, history, memory/context, and inspector.",
    }
