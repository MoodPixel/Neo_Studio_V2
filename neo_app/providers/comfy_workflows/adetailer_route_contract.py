from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from neo_extensions.built_in.adetailer.backend.route_contract import (
    build_adetailer_route_contract,
    normalize_adetailer_route_contract,
)


def publish_adetailer_route_contract(
    *,
    actual_params: dict[str, Any],
    workflow: Mapping[str, Any],
    route: Any,
    image_ref: Any,
    model_ref: Any,
    clip_ref: Any,
    vae_ref: Any,
    positive_ref: Any,
    negative_ref: Any,
    sampler_node_id: str | int,
    source: str,
    compiler_id: str,
    model_sampling_state: str = "passthrough",
    model_sampling_ref: Any = None,
    model_sampling_nodes: list[str] | tuple[str, ...] | None = None,
    notes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Publish and self-check the compiler-owned ADetailer graph contract.

    Phase 5 compilers must expose the exact image/model/CLIP/VAE/conditioning
    anchors that their own KSampler consumes. The extension may rebase those
    anchors after earlier extensions, but it must never infer checkpoint-style
    node IDs for component, GGUF, AIO, or model-sampling-patched workflows.
    """

    route_payload = dict(route.as_dict()) if hasattr(route, "as_dict") else dict(route or {})
    mode = str(route_payload.get("mode") or "generate").strip().lower()
    route_payload["workflow_mode"] = "generate" if mode in {"txt2img", "generate"} else ("img2img" if mode == "edit" else mode)
    route_payload["compiler_id"] = compiler_id

    contract = build_adetailer_route_contract(
        route=route_payload,
        image_ref=image_ref,
        model_ref=model_ref,
        clip_ref=clip_ref,
        vae_ref=vae_ref,
        positive_ref=positive_ref,
        negative_ref=negative_ref,
        sampler_node_id=sampler_node_id,
        model_sampling_ref=model_sampling_ref if model_sampling_ref is not None else model_ref,
        model_sampling_state=model_sampling_state,
        model_sampling_nodes=model_sampling_nodes,
        source=source,
        compiler_id=compiler_id,
        validated=True,
        notes=notes,
    )
    normalized = normalize_adetailer_route_contract(
        contract,
        route=route_payload,
        workflow=workflow,
        require_validated=True,
    )
    actual_params["_neo_adetailer_route_contract"] = deepcopy(contract)
    actual_params["_neo_adetailer_route_contract_validation"] = {
        "valid": bool(normalized.get("valid")),
        "reason": str(normalized.get("reason") or ""),
        "errors": list(normalized.get("errors") or []),
        "source": source,
        "compiler_id": compiler_id,
        "fallback_policy": "none",
    }
    return contract
