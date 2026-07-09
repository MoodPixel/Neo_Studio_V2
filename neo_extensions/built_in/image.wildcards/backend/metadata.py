"""Wildcards output metadata and replay helpers.

Phase M turns the prompt-only Wildcards runtime result into canonical Image
output metadata. The extension remains provider-neutral: this module records
source/effective prompts, resolved/missing tokens, deterministic seed/variant
state, replay UI state, assistant summaries, and memory events. It never
requests workflow graph patches.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

try:
    from .payload_schema import EXTENSION_ID, normalize_wildcards_payload
except ImportError:  # file-loaded tests without package context
    from neo_extensions.built_in.wildcards.backend.payload_schema import EXTENSION_ID, normalize_wildcards_payload

EXTENSION_METADATA_KEY = EXTENSION_ID
PROMPT_ONLY = True
PHASE_M_OUTPUT_METADATA = "M-output-metadata-replay"
REPLAY_RESTORE_POLICY = "restore_wildcard_library_payload_and_revalidate_tokens_before_enable"


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


def _wildcards_meta_from_merge(merge_result: Mapping[str, Any] | None) -> dict[str, Any]:
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    extension_metadata = merge.get("extension_metadata") if isinstance(merge.get("extension_metadata"), Mapping) else {}
    block = extension_metadata.get(EXTENSION_ID) if isinstance(extension_metadata.get(EXTENSION_ID), Mapping) else {}
    return dict(block or {})


def _dedupe_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        values = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def build_phase_f_replay_payload(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the replay-safe UI restore block from a submitted payload."""
    normalized = normalize_wildcards_payload(payload)
    inputs = normalized.get("inputs") if isinstance(normalized.get("inputs"), Mapping) else {}
    params = normalized.get("params") if isinstance(normalized.get("params"), Mapping) else {}
    assets = normalized.get("assets") if isinstance(normalized.get("assets"), Mapping) else {}
    return {
        "enabled": bool(normalized.get("enabled", True)),
        "root": str(inputs.get("root") or ""),
        "selected_token": str(inputs.get("selected_token") or ""),
        "target": str(inputs.get("target") or "positive_prompt"),
        "auto_resolve": bool(params.get("auto_resolve", True)),
        "use_seed": bool(params.get("use_seed", True)),
        "preview_count": int(params.get("preview_count") or 3),
        "queue_count": int(params.get("queue_count") or 3),
        "variant_offset": int(params.get("variant_offset") or 0),
        "max_passes": int(params.get("max_passes") or 24),
    }


def build_wildcards_replay_payload(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the Phase M replay payload with restore metadata and token labels."""
    replay = build_phase_f_replay_payload(payload)
    normalized = normalize_wildcards_payload(payload)
    assets = normalized.get("assets") if isinstance(normalized.get("assets"), Mapping) else {}
    replay.update({
        "tokens": [str(item) for item in (assets.get("tokens") or []) if str(item or "").strip()],
        "token_labels": [str(item) for item in (assets.get("token_labels") or []) if str(item or "").strip()],
        "prompt_only": True,
        "resolution_policy": "seeded_replace_tokens",
        "restore_policy": REPLAY_RESTORE_POLICY,
    })
    return replay


def build_wildcards_assistant_summary(payload: Mapping[str, Any] | None = None, merge_result: Mapping[str, Any] | None = None) -> str:
    """Build a compact Assistant-readable Wildcards summary."""
    normalized = normalize_wildcards_payload(payload)
    meta = _wildcards_meta_from_merge(merge_result)
    if not normalized.get("enabled"):
        return "Wildcards was disabled."
    resolved = _dedupe_strings(meta.get("resolved_tokens"))
    missing = _dedupe_strings(meta.get("missing_tokens"))
    inline_count = int(meta.get("inline_choice_count") or 0)
    file_count = int(meta.get("file_choice_count") or 0)
    variant = int(meta.get("variant_offset") or ((normalized.get("params") or {}).get("variant_offset") or 0))
    seed = _clean_text(meta.get("seed_used") or "")
    pieces: list[str] = []
    if resolved:
        pieces.append(f"resolved {len(resolved)} token{'s' if len(resolved) != 1 else ''}: {', '.join(resolved[:5])}{'…' if len(resolved) > 5 else ''}")
    if inline_count:
        pieces.append(f"used {inline_count} inline choice{'s' if inline_count != 1 else ''}")
    if file_count and not resolved:
        pieces.append(f"used {file_count} file choice{'s' if file_count != 1 else ''}")
    if missing:
        pieces.append(f"left {len(missing)} missing token{'s' if len(missing) != 1 else ''} visible: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}")
    if not pieces:
        pieces.append("enabled but found no resolvable wildcard tokens")
    suffix = f" Seed: {seed}." if seed else ""
    return f"Wildcards {', '.join(pieces)}. Variant offset: {variant}.{suffix}"


def _merge_runtime_metadata(block: dict[str, Any], merge_result: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(block)
    metadata = dict(merged.get("metadata") or {})
    meta = _wildcards_meta_from_merge(merge_result)
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    metadata.update({
        "phase": PHASE_M_OUTPUT_METADATA,
        "prompt_only": True,
        "provider_graph_mutation": False,
        "provider_graph_patch_path": "disabled_for_wildcards",
        "runtime_resolution_status": "recorded_phase_m",
        "resolution_policy": "seeded_replace_tokens",
        "resolution_order": "before_style_stack",
        "changed": bool(meta.get("effective_positive") != meta.get("source_positive") or meta.get("effective_negative") != meta.get("source_negative")) if meta else bool(merge.get("changed")),
        "applied": EXTENSION_ID in (merge.get("applied_extensions") or []),
        "skipped": [deepcopy(item) for item in (merge.get("skipped_extensions") or []) if isinstance(item, Mapping)],
        "source_positive": str(meta.get("source_positive") or merge.get("original_positive") or ""),
        "source_negative": str(meta.get("source_negative") or merge.get("original_negative") or ""),
        "effective_positive": str(meta.get("effective_positive") or merge.get("effective_positive") or ""),
        "effective_negative": str(meta.get("effective_negative") or merge.get("effective_negative") or ""),
        "resolved_tokens": _dedupe_strings(meta.get("resolved_tokens")),
        "missing_tokens": _dedupe_strings(meta.get("missing_tokens")),
        "inline_choice_count": int(meta.get("inline_choice_count") or 0),
        "file_choice_count": int(meta.get("file_choice_count") or 0),
        "seed_used": meta.get("seed_used", ""),
        "use_seed": bool(meta.get("use_seed", True)),
        "variant_offset": int(meta.get("variant_offset") or 0),
        "max_passes": int(meta.get("max_passes") or 24),
        "root": str(meta.get("root") or ""),
        "positive": deepcopy(meta.get("positive") if isinstance(meta.get("positive"), Mapping) else {}),
        "negative": deepcopy(meta.get("negative") if isinstance(meta.get("negative"), Mapping) else {}),
        "phase_j_style_embeddings_interop": deepcopy(((merge.get("extension_metadata") or {}).get("phase_j_style_embeddings_interop") or {}) if isinstance(merge.get("extension_metadata"), Mapping) else {}),
        "scene_director_interop": deepcopy(merge.get("scene_director_interop") if isinstance(merge.get("scene_director_interop"), Mapping) else {}),
        "execution": deepcopy(merge.get("execution") if isinstance(merge.get("execution"), Mapping) else {}),
    })
    merged["metadata"] = metadata
    return merged


def build_output_extension_metadata(extensions: Any, merge_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return canonical output metadata slots for Wildcards Phase M."""
    raw_block = _payload_block(extensions)
    if not raw_block:
        return {"used": [], "payloads": {}, "workflow_patches": [], "validation": [], "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}}
    block = normalize_wildcards_payload(raw_block)
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    meta = _wildcards_meta_from_merge(merge)
    was_applied = EXTENSION_ID in (merge.get("applied_extensions") or [])
    if not block.get("enabled") and not was_applied:
        return {"used": [], "payloads": {}, "workflow_patches": [], "validation": [], "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}}

    payload = _merge_runtime_metadata(block, merge)
    replay = build_wildcards_replay_payload(block)
    summary = build_wildcards_assistant_summary(block, merge)
    resolved_tokens = _dedupe_strings(meta.get("resolved_tokens"))
    missing_tokens = _dedupe_strings(meta.get("missing_tokens"))
    used = {
        "extension_id": EXTENSION_ID,
        "label": "Wildcards",
        "status": "applied" if was_applied else "enabled",
        "surface": "image",
        "extension_type": "workflow_extension",
        "version": block.get("version") or 1,
        "prompt_only": True,
        "merge_policy": "seeded_replace_tokens",
        "target": ((block.get("inputs") or {}).get("target") or "positive_prompt"),
        "resolved_token_count": len(resolved_tokens),
        "missing_token_count": len(missing_tokens),
        "inline_choice_count": int(meta.get("inline_choice_count") or 0),
        "file_choice_count": int(meta.get("file_choice_count") or 0),
        "variant_offset": int(meta.get("variant_offset") or ((block.get("params") or {}).get("variant_offset") or 0)),
        "workflow_patch_applied": False,
        "workflow_patch_allowed": False,
        "route": "provider_neutral_prompt_resolution",
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
            "message": "Wildcards resolved prompt text before Style Stack and provider execution.",
        }],
        "replay_payloads": {EXTENSION_ID: replay},
        "assistant_summaries": {EXTENSION_ID: summary},
        "memory_events": {
            EXTENSION_ID: {
                "event_type": "image.wildcards.resolved" if was_applied else "image.wildcards.enabled",
                "resolved_tokens": resolved_tokens,
                "missing_tokens": missing_tokens,
                "inline_choice_count": int(meta.get("inline_choice_count") or 0),
                "file_choice_count": int(meta.get("file_choice_count") or 0),
                "variant_offset": int(meta.get("variant_offset") or ((block.get("params") or {}).get("variant_offset") or 0)),
                "prompt_only": True,
            }
        },
    }


def phase_m_output_metadata_replay_status(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return Phase M metadata/replay contract status."""
    replay = build_phase_f_replay_payload(payload)
    return {
        "extension_id": EXTENSION_ID,
        "phase": "M-output-metadata-replay",
        "implemented": True,
        "depends_on": "L-queue-variants-migration",
        "output_sidecar_status": "implemented_phase_m",
        "metadata_slots": [
            "extensions.used[]",
            "extensions.payloads.wildcards",
            "extensions.replay_payloads.wildcards",
            "extensions.assistant_summaries.wildcards",
            "extensions.memory_events.wildcards",
        ],
        "replay_restores": [
            "enabled",
            "root",
            "selected_token",
            "target",
            "auto_resolve",
            "use_seed",
            "preview_count",
            "queue_count",
            "variant_offset",
            "max_passes",
        ],
        "source_effective_prompt_tracking": True,
        "resolved_missing_token_tracking": True,
        "seed_variant_tracking": True,
        "assistant_summary": True,
        "memory_event": True,
        "prompt_only": True,
        "provider_graph_mutation": False,
        "replay_payload": build_wildcards_replay_payload(payload),
        "not_in_scope": [
            "registry_default_enable",
            "scene_director_region_wildcard_resolution",
            "provider_workflow_patch_mutation",
        ],
    }


# Backward-compatible alias retained for earlier phase tests.
def phase_f_metadata_status(payload: Mapping[str, Any] | None = None) -> dict[str, object]:
    return {
        "extension_id": EXTENSION_ID,
        "phase": "F-frontend-state-payload-builder",
        "implemented": "payload_submit_plus_phase_m_output_metadata",
        "metadata_slots": phase_m_output_metadata_replay_status(payload)["metadata_slots"],
        "replay_ready": "implemented_phase_m",
        "replay_payload": build_phase_f_replay_payload(payload),
        "output_sidecar_status": "implemented_phase_m",
    }


def phase_b_metadata_status() -> dict[str, object]:
    status = phase_f_metadata_status()
    status["phase_b_compat"] = True
    status["implemented"] = False
    status["replay_ready"] = False
    return status
