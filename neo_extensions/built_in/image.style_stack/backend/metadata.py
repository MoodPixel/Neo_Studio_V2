"""Style Stack output metadata and replay helpers.

Phase J turns the prompt-only Style Stack runtime result into canonical Image
output metadata. The extension remains provider-neutral: this module records the
style prompt effect, but never requests workflow graph patches.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .payload_schema import EXTENSION_ID, normalize_style_stack_payload

EXTENSION_METADATA_KEY = EXTENSION_ID
PROMPT_MERGE_POLICY = "append_deduped"
PROMPT_ONLY = True
PHASE_J_OUTPUT_METADATA = "J-output-metadata-replay"
REPLAY_FIELDS = (
    "enabled",
    "target",
    "active_styles",
    "manual_positive",
    "manual_negative",
    "selected_name",
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _payload_block(source: Any) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    if isinstance(source.get(EXTENSION_ID), Mapping):
        return dict(source.get(EXTENSION_ID) or {})
    payloads = source.get("payloads")
    if isinstance(payloads, Mapping) and isinstance(payloads.get(EXTENSION_ID), Mapping):
        return dict(payloads.get(EXTENSION_ID) or {})
    extensions = source.get("extensions")
    if isinstance(extensions, Mapping) and isinstance(extensions.get(EXTENSION_ID), Mapping):
        return dict(extensions.get(EXTENSION_ID) or {})
    return {}


def _style_meta_from_merge(merge_result: Mapping[str, Any] | None) -> dict[str, Any]:
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    extension_metadata = merge.get("extension_metadata") if isinstance(merge.get("extension_metadata"), Mapping) else {}
    block = extension_metadata.get(EXTENSION_ID) if isinstance(extension_metadata.get(EXTENSION_ID), Mapping) else {}
    return dict(block or {})


def _merge_runtime_metadata(block: dict[str, Any], merge_result: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(block)
    metadata = dict(merged.get("metadata") or {})
    style_meta = _style_meta_from_merge(merge_result)
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    style_names = [str(item) for item in (style_meta.get("style_names") or []) if str(item or "").strip()]
    missing_style_names = [str(item) for item in (style_meta.get("missing_style_names") or []) if str(item or "").strip()]
    metadata.update({
        "phase": PHASE_J_OUTPUT_METADATA,
        "prompt_only": True,
        "merge_policy": PROMPT_MERGE_POLICY,
        "runtime_merge_status": "recorded_phase_j",
        "provider_graph_mutation": False,
        "provider_graph_patch_path": "disabled_for_style_stack",
        "changed": bool(merge.get("changed")),
        "applied": EXTENSION_ID in (merge.get("applied_extensions") or []),
        "skipped": [deepcopy(item) for item in (merge.get("skipped_extensions") or []) if isinstance(item, Mapping)],
        "target": style_meta.get("target", ""),
        "style_names": style_names,
        "style_count": len(style_names),
        "missing_style_names": missing_style_names,
        "manual_positive": bool(style_meta.get("manual_positive")),
        "manual_negative": bool(style_meta.get("manual_negative")),
        "positive_parts_added": style_meta.get("positive_parts_added", 0),
        "negative_parts_added": style_meta.get("negative_parts_added", 0),
        "source_positive_prompt": merge.get("original_positive", ""),
        "source_negative_prompt": merge.get("original_negative", ""),
        "effective_positive_prompt": merge.get("effective_positive", ""),
        "effective_negative_prompt": merge.get("effective_negative", ""),
        "style_positive_prompt": style_meta.get("style_positive_prompt", ""),
        "style_negative_prompt": style_meta.get("style_negative_prompt", ""),
        "output_inspector_status": "style_stack_slot_details_ready_phase_o",
        "scene_director_interop": deepcopy(merge.get("scene_director_interop") or {}),
        "execution": deepcopy(merge.get("execution") or {}),
    })
    merged["metadata"] = metadata
    return merged


def build_style_stack_replay_payload(payload: Mapping[str, Any] | None, merge_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the compact replay state the Image workspace can restore later."""
    normalized = normalize_style_stack_payload(payload)
    inputs = normalized.get("inputs") if isinstance(normalized.get("inputs"), Mapping) else {}
    params = normalized.get("params") if isinstance(normalized.get("params"), Mapping) else {}
    assets = normalized.get("assets") if isinstance(normalized.get("assets"), Mapping) else {}
    style_meta = _style_meta_from_merge(merge_result)
    style_names = list(style_meta.get("style_names") or assets.get("style_names") or inputs.get("active_styles") or [])
    return {
        "enabled": bool(normalized.get("enabled")),
        "target": str(params.get("target") or "both"),
        "active_styles": [str(item) for item in style_names if str(item or "").strip()],
        "manual_positive": str(inputs.get("manual_positive") or ""),
        "manual_negative": str(inputs.get("manual_negative") or ""),
        "selected_name": str((payload or {}).get("selected_name") or "") if isinstance(payload, Mapping) else "",
        "style_names": [str(item) for item in style_names if str(item or "").strip()],
        "style_records": deepcopy(assets.get("style_records") if isinstance(assets.get("style_records"), list) else []),
        "prompt_only": True,
        "merge_policy": PROMPT_MERGE_POLICY,
        "restore_policy": "restore_style_chips_and_revalidate_style_library_before_enable",
    }


def build_style_stack_assistant_summary(payload: Mapping[str, Any] | None, merge_result: Mapping[str, Any] | None = None) -> str:
    normalized = normalize_style_stack_payload(payload)
    style_meta = _style_meta_from_merge(merge_result)
    params = normalized.get("params") if isinstance(normalized.get("params"), Mapping) else {}
    inputs = normalized.get("inputs") if isinstance(normalized.get("inputs"), Mapping) else {}
    style_names = [str(item) for item in (style_meta.get("style_names") or inputs.get("active_styles") or []) if str(item or "").strip()]
    target = str(style_meta.get("target") or params.get("target") or "both")
    manual_bits = []
    if style_meta.get("manual_positive") or _clean_text(inputs.get("manual_positive")):
        manual_bits.append("manual positive")
    if style_meta.get("manual_negative") or _clean_text(inputs.get("manual_negative")):
        manual_bits.append("manual negative")
    count = len(style_names)
    names = ", ".join(style_names[:5])
    if len(style_names) > 5:
        names += f", +{len(style_names) - 5} more"
    if not normalized.get("enabled"):
        return "Style Stack was disabled or empty."
    if count and manual_bits:
        return f"Style Stack applied {count} style{'s' if count != 1 else ''} ({names}) plus {' and '.join(manual_bits)} to the {target} prompt path."
    if count:
        return f"Style Stack applied {count} style{'s' if count != 1 else ''}: {names}. Target: {target}."
    if manual_bits:
        return f"Style Stack applied {' and '.join(manual_bits)} style text. Target: {target}."
    return "Style Stack was enabled but did not add prompt text."


def build_output_extension_metadata(extensions: Any, merge_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return canonical output metadata slots for Style Stack."""
    raw_block = _payload_block(extensions)
    if not raw_block:
        return {"used": [], "payloads": {}, "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}}
    block = normalize_style_stack_payload(raw_block)
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    style_meta = _style_meta_from_merge(merge)
    was_applied = EXTENSION_ID in (merge.get("applied_extensions") or [])
    if not block.get("enabled") and not was_applied:
        return {"used": [], "payloads": {}, "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}}

    payload = _merge_runtime_metadata(block, merge)
    replay = build_style_stack_replay_payload(block, merge)
    summary = build_style_stack_assistant_summary(block, merge)
    style_names = replay.get("style_names") or []
    inputs = block.get("inputs") if isinstance(block.get("inputs"), Mapping) else {}
    params = block.get("params") if isinstance(block.get("params"), Mapping) else {}
    used = {
        "extension_id": EXTENSION_ID,
        "label": "Style Stack",
        "status": "applied" if was_applied else "enabled",
        "surface": "image",
        "extension_type": "workflow_extension",
        "version": block.get("version") or 1,
        "prompt_only": True,
        "merge_policy": PROMPT_MERGE_POLICY,
        "target": style_meta.get("target") or params.get("target") or "both",
        "style_count": len(style_names),
        "style_names": style_names,
        "manual_positive": bool(_clean_text(inputs.get("manual_positive")) or style_meta.get("manual_positive")),
        "manual_negative": bool(_clean_text(inputs.get("manual_negative")) or style_meta.get("manual_negative")),
        "positive_count": style_meta.get("positive_parts_added", 0),
        "negative_count": style_meta.get("negative_parts_added", 0),
        "workflow_patch_applied": False,
        "workflow_patch_allowed": False,
        "route": "provider_neutral_prompt_merge",
        "assistant_summary": summary,
    }
    return {
        "used": [used],
        "payloads": {EXTENSION_ID: payload},
        "workflow_patches": [],
        "validation": [{
            "extension_id": EXTENSION_ID,
            "status": "passed",
            "prompt_only": True,
            "provider_graph_mutation": False,
            "message": "Style Stack merged through prompt text before provider execution.",
        }],
        "replay_payloads": {EXTENSION_ID: replay},
        "assistant_summaries": {EXTENSION_ID: summary},
        "memory_events": {
            EXTENSION_ID: {
                "event_type": "image.style_stack.applied" if was_applied else "image.style_stack.enabled",
                "style_names": style_names,
                "target": used["target"],
                "prompt_only": True,
            }
        },
    }
