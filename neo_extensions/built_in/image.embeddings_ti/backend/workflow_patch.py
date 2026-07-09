from __future__ import annotations

from copy import deepcopy
from typing import Any

from .validation import validate_and_normalize_payload

PHASE = "G"
EXTENSION_ID = "embeddings_ti"
BASE_TARGETS = {"positive_prompt", "negative_prompt"}
FINISH_TARGETS = {"finish_positive", "finish_negative"}


def _items(validation: dict[str, Any]) -> list[dict[str, Any]]:
    params = ((validation.get("block") or {}).get("params") or {})
    items = params.get("items") if isinstance(params, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _format_strength(value: Any) -> str:
    try:
        strength = float(value)
    except (TypeError, ValueError):
        strength = 1.0
    text = f"{strength:.3f}".rstrip("0").rstrip(".")
    return text or "1"


def format_embedding_token(item: dict[str, Any]) -> str:
    token = str(item.get("token") or "").strip()
    if not token:
        return ""
    try:
        strength = float(item.get("strength", 1.0))
    except (TypeError, ValueError):
        strength = 1.0
    if abs(strength - 1.0) < 0.0005:
        return token
    return f"({token}:{_format_strength(strength)})"


def _refs_equal(current: Any, expected: list[Any]) -> bool:
    return current == expected or (isinstance(current, (list, tuple)) and list(current) == expected)


def _ref_key(ref: Any) -> str:
    if isinstance(ref, (list, tuple)) and ref:
        return str(ref[0])
    return ""


def _clip_text_node_ids_from_sampler(graph: dict[str, Any], target: str) -> list[str]:
    input_name = "positive" if target == "positive_prompt" else "negative"
    ids: list[str] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        ref = inputs.get(input_name)
        node_id = _ref_key(ref)
        if node_id and node_id not in ids:
            ids.append(node_id)
    return ids


def _fallback_clip_text_node_ids(graph: dict[str, Any], target: str) -> list[str]:
    preferred = "2" if target == "positive_prompt" else "3"
    node = graph.get(preferred)
    if isinstance(node, dict) and isinstance(node.get("inputs"), dict) and "text" in node["inputs"]:
        return [preferred]
    text_nodes = [str(node_id) for node_id, node in graph.items() if isinstance(node, dict) and isinstance(node.get("inputs"), dict) and "text" in node["inputs"]]
    if len(text_nodes) >= 2:
        return [text_nodes[0] if target == "positive_prompt" else text_nodes[1]]
    return text_nodes[:1]


def _target_node_ids(graph: dict[str, Any], target: str) -> list[str]:
    ids = _clip_text_node_ids_from_sampler(graph, target)
    if not ids:
        ids = _fallback_clip_text_node_ids(graph, target)
    clean: list[str] = []
    for node_id in ids:
        node = graph.get(str(node_id))
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and "text" in inputs and str(node_id) not in clean:
            clean.append(str(node_id))
    return clean


def _append_prompt_token(text: Any, token: str) -> tuple[str, bool, str]:
    current = str(text or "").strip()
    needle = token.casefold()
    # Also detect unweighted duplicates when the new token is weighted.
    raw_token = token
    if token.startswith("(") and token.endswith(")") and ":" in token:
        raw_token = token[1:-1].rsplit(":", 1)[0]
    if needle in current.casefold() or raw_token.casefold() in current.casefold():
        return current, False, "duplicate"
    if not current:
        return token, True, "appended"
    separator = ", " if not current.endswith((",", "\n")) else " "
    return f"{current}{separator}{token}", True, "appended"


def build_workflow_patch_summary(
    *,
    validation_result: dict[str, Any],
    applied_items: list[dict[str, Any]] | None = None,
    deferred_items: list[dict[str, Any]] | None = None,
    duplicate_items: list[dict[str, Any]] | None = None,
    patched_prompt_nodes: dict[str, list[str]] | None = None,
    reason: str = "",
    mutated: bool = False,
) -> dict[str, Any]:
    route = validation_result.get("route") if isinstance(validation_result, dict) else {}
    applied_items = deepcopy(applied_items or [])
    deferred_items = deepcopy(deferred_items or [])
    duplicate_items = deepcopy(duplicate_items or [])
    patched_prompt_nodes = deepcopy(patched_prompt_nodes or {"positive_prompt": [], "negative_prompt": []})
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "strategy": "classic_ti_prompt_token_append",
        "applied": bool(mutated),
        "mutated": bool(mutated),
        "graph_patch": "prompt_text_only",
        "node": "none",
        "node_class": "none",
        "prompt_patch": (validation_result.get("support") or {}).get("prompt_patch", "none"),
        "item_count": len(_items(validation_result)),
        "applied_item_count": len(applied_items),
        "deferred_item_count": len(deferred_items),
        "duplicate_item_count": len(duplicate_items),
        "applied_tokens": [format_embedding_token(item) for item in applied_items],
        "deferred_targets": [str(item.get("target") or "") for item in deferred_items],
        "patched_prompt_nodes": patched_prompt_nodes,
        "route": deepcopy(route or {}),
        "reason": reason,
    }


def apply_embeddings_ti_patch(
    workflow: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    available_nodes: Any = None,
    **_: Any,
) -> dict[str, Any]:
    """Patch validated Embeddings/TI chips into Comfy CLIPTextEncode prompt text.

    Embeddings/TI is non-node-based in Comfy. Phase G appends validated
    textual-inversion prompt tokens to the existing positive/negative text
    encoder nodes on available/experimental checkpoint routes. Gated routes,
    disabled payloads, finish-only targets, and duplicates do not mutate the
    graph.
    """
    graph = deepcopy(workflow or {})
    validation = validate_and_normalize_payload(payload, route=route, available_nodes=available_nodes)
    items = _items(validation)

    def no_patch(reason: str) -> dict[str, Any]:
        patch = build_workflow_patch_summary(validation_result=validation, reason=reason, mutated=False)
        return {
            "workflow": graph,
            "workflow_patch": patch,
            "validation": validation,
            "mutated": False,
            "changed": False,
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "route_state": (validation.get("route") or {}).get("route_state"),
            "gated_reason": reason,
        }

    if not validation.get("workflow_patch_allowed"):
        metadata = (validation.get("block") or {}).get("metadata") or {}
        return no_patch(str(metadata.get("reason") or validation.get("reason") or "disabled_or_route_gated"))
    if not items:
        return no_patch("no_valid_embedding_items")

    applied: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    patched_nodes: dict[str, list[str]] = {"positive_prompt": [], "negative_prompt": []}
    missing_targets: list[str] = []

    for item in items:
        target = str(item.get("target") or "negative_prompt")
        if target in FINISH_TARGETS:
            deferred.append(item)
            continue
        if target not in BASE_TARGETS:
            deferred.append(item)
            continue
        token = format_embedding_token(item)
        if not token:
            deferred.append(item)
            continue
        target_nodes = _target_node_ids(graph, target)
        if not target_nodes:
            missing_targets.append(target)
            deferred.append(item)
            continue
        item_changed = False
        item_duplicate = True
        for node_id in target_nodes:
            node = graph.get(node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            new_text, changed, status = _append_prompt_token(inputs.get("text"), token)
            if changed:
                inputs["text"] = new_text
                item_changed = True
                item_duplicate = False
                if node_id not in patched_nodes[target]:
                    patched_nodes[target].append(node_id)
            elif status == "duplicate":
                continue
        if item_changed:
            applied.append(item)
        elif item_duplicate:
            duplicates.append(item)

    mutated = bool(applied)
    reason_parts: list[str] = []
    if applied:
        reason_parts.append(f"appended {len(applied)} Embeddings/TI token(s) to prompt text nodes")
    if duplicates:
        reason_parts.append(f"skipped {len(duplicates)} duplicate token(s)")
    if deferred:
        reason_parts.append(f"deferred {len(deferred)} non-base or unresolved target item(s)")
    if missing_targets:
        reason_parts.append(f"missing prompt node target(s): {', '.join(sorted(set(missing_targets)))}")
    reason = "; ".join(reason_parts) if reason_parts else "no prompt text changes required"
    patch = build_workflow_patch_summary(
        validation_result=validation,
        applied_items=applied,
        deferred_items=deferred,
        duplicate_items=duplicates,
        patched_prompt_nodes=patched_nodes,
        reason=reason,
        mutated=mutated,
    )
    return {
        "workflow": graph,
        "workflow_patch": patch,
        "validation": validation,
        "mutated": mutated,
        "changed": mutated,
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "route_state": (validation.get("route") or {}).get("route_state"),
        "gated_reason": "" if mutated else reason,
    }


def workflow_patch_not_implemented() -> dict[str, Any]:
    return {
        "extension_id": EXTENSION_ID,
        "implemented": True,
        "phase": PHASE,
        "node": "none",
        "graph_patch": "prompt_text_only",
        "reason": "Phase G appends validated Embeddings/TI prompt-token chips to existing positive/negative Comfy text encoder nodes on active checkpoint routes.",
    }
