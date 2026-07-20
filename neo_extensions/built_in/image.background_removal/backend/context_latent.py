"""Live-gated RMBG context and latent-assisted route contracts.

The installed ComfyUI-RMBG node is a conditioning adapter, not a standalone
background-removal node.  Neo therefore exposes its contract only to a
validated Flux Kontext inpaint graph that already owns a source latent and
mask.  Node names and input names come from live ``/object_info`` data.
"""
from __future__ import annotations

from typing import Any

from .public_hygiene import portable_model_identifier


SCHEMA_ID = "neo.image.background_removal.context_latent.v1"
SCHEMA_VERSION = 1
SUPPORTED_ROUTE = {
    "family": "flux",
    "variant": "kontext",
    "modes": ("inpaint",),
    "loaders": ("diffusion_model",),
}

NODE_CANDIDATES = (
    "AILab_ReferenceLatentMask",
    "ReferenceLatentMask",
    "KontextReferenceLatentMask",
)
REQUIRED_INPUTS = ("conditioning", "latent", "mask", "expand", "blur", "mask_only")


def _node_inputs(spec: Any) -> set[str]:
    if not isinstance(spec, dict):
        return set()
    input_spec = spec.get("input")
    if not isinstance(input_spec, dict):
        return set()
    names: set[str] = set()
    for section in ("required", "optional", "hidden"):
        values = input_spec.get(section)
        if isinstance(values, dict):
            names.update(str(name).strip() for name in values if str(name).strip())
    return names


def _available_node(object_info: Any, candidate: str) -> tuple[str, set[str]] | None:
    if not isinstance(object_info, dict):
        return None
    for raw_name, spec in object_info.items():
        actual = str(raw_name or "").strip()
        if actual.casefold() != candidate.casefold():
            continue
        safe_name = portable_model_identifier(actual, "node") or actual
        return safe_name, _node_inputs(spec)
    return None


def build_context_latent_catalog(object_info: dict[str, Any] | None) -> dict[str, Any]:
    """Return the exact live node/input contract without guessing compatibility."""

    available = bool(object_info)
    profiles: list[dict[str, Any]] = []
    for candidate in NODE_CANDIDATES:
        match = _available_node(object_info, candidate)
        if not match:
            continue
        node_class, input_names = match
        missing = sorted(set(REQUIRED_INPUTS).difference(input_names))
        profiles.append({
            "id": "flux_kontext_reference_latent_mask",
            "label": "Flux Kontext · Reference Latent Mask",
            "node_class": node_class,
            "input_names": sorted(input_names),
            "required_inputs": list(REQUIRED_INPUTS),
            "available": not missing,
            "missing_inputs": missing,
            "route": dict(SUPPORTED_ROUTE),
        })
        break
    ready = any(bool(profile.get("available")) for profile in profiles)
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "available": ready,
        "profiles": profiles,
        "candidate_nodes": list(NODE_CANDIDATES),
        "supported_route": dict(SUPPORTED_ROUTE),
        "safety": {
            "requires_live_object_info": True,
            "no_family_fallback": True,
            "no_loader_fallback": True,
            "source_contract": "Image 1 latent + existing inpaint mask",
        },
        "status": "available" if ready else ("missing" if available else "catalog_unavailable"),
    }


def normalize_context_latent(source: dict[str, Any] | None) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    try:
        expand = int(source.get("context_latent_expand", source.get("expand", 5)) or 0)
    except (TypeError, ValueError):
        expand = 5
    try:
        blur = float(source.get("context_latent_blur", source.get("blur", 3.0)) or 0.0)
    except (TypeError, ValueError):
        blur = 3.0
    source_mode = str(source.get("context_latent_source", "source_image") or "source_image").strip().lower()
    if source_mode not in {"source_image"}:
        source_mode = "source_image"
    return {
        "enabled": bool(source.get("context_latent_enabled", source.get("enabled", False))),
        "profile": "flux_kontext_reference_latent_mask",
        "source": source_mode,
        "expand": max(-64, min(64, expand)),
        "blur": max(0.0, min(64.0, round(blur, 2))),
        "mask_only": bool(source.get("context_latent_mask_only", source.get("mask_only", True))),
        "node_class": str(source.get("context_latent_node_class") or "").strip(),
        "node_input_names": [str(value).strip() for value in (source.get("context_latent_input_names") or []) if str(value).strip()],
    }


def resolve_context_latent(catalog: dict[str, Any] | None, selected_node: str = "") -> dict[str, Any]:
    """Resolve one live profile; never substitute another node or route."""

    catalog = catalog if isinstance(catalog, dict) else {}
    rows = [row for row in (catalog.get("profiles") or []) if isinstance(row, dict)]
    selected = str(selected_node or "").strip().casefold()
    row = next((item for item in rows if str(item.get("node_class") or "").casefold() == selected), None) if selected else None
    row = row or next((item for item in rows if item.get("available")), None)
    if not row:
        return {"ready": False, "reason": "No live Flux Kontext reference-latent node is available."}
    if not row.get("available"):
        return {"ready": False, "reason": "The live reference-latent node is missing required inputs.", "profile": row}
    return {
        "ready": True,
        "profile": row.get("id"),
        "node_class": row.get("node_class"),
        "input_names": list(row.get("input_names") or []),
        "route": dict(row.get("route") or SUPPORTED_ROUTE),
    }
