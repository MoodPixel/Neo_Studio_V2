"""Provider-neutral prompt extension merge hooks for Image generation.

Phase I formally closes provider-neutral execution for prompt-only Image
extensions after Phase H locked the prompt-transform order. Prompt-only
extensions mutate strings only; they do not request ComfyUI/Forge graph patches.

Current ordered pipeline:
1. Wildcards resolve V1-compatible tokens/inline choices.
2. Style Stack appends/dedupes selected styles after wildcard expansion.
3. Embeddings/TI and LoRA visibility metadata records tokens after Style Stack.
4. Scene Director receives the resolved global prompt only; regions, identity,
   IPAdapter, and LoRA bindings are preserved by default.
5. Queue Variants submits separate jobs with deterministic wildcard offsets.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import re
from typing import Any, Mapping

from neo_extensions.built_in.style_stack.backend.payload_schema import normalize_style_stack_payload
from neo_extensions.built_in.wildcards.backend.resolver import apply_wildcard_prompt_extension

STYLE_STACK_EXTENSION_ID = "style_stack"
WILDCARDS_EXTENSION_ID = "wildcards"
EMBEDDINGS_TI_EXTENSION_ID = "embeddings_ti"
LORA_STACK_EXTENSION_ID = "lora_stack"
SCENE_DIRECTOR_EXTENSION_ID = "image.scene_director"
STYLE_STACK_MERGE_POLICY = "append_deduped"
STYLE_STACK_PROMPT_PLACEHOLDER_RE = re.compile(r"\{\s*prompt\s*\}", re.IGNORECASE)
STYLE_STACK_VALID_TARGETS = {"both", "base", "finish"}
FINISH_WORKFLOW_MODES = {"finish", "refine", "redraw", "hires", "high_res", "high_res_fix"}
PROMPT_ONLY_EXTENSION_IDS = {WILDCARDS_EXTENSION_ID, STYLE_STACK_EXTENSION_ID}
PROVIDER_NEUTRAL_BACKENDS = {"comfyui", "comfyui_portable", "forge"}
PROMPT_EXTENSION_ORDER = [WILDCARDS_EXTENSION_ID, STYLE_STACK_EXTENSION_ID, EMBEDDINGS_TI_EXTENSION_ID, SCENE_DIRECTOR_EXTENSION_ID]
ACTIVE_PROMPT_MUTATION_ORDER = [WILDCARDS_EXTENSION_ID, STYLE_STACK_EXTENSION_ID]



def is_prompt_only_extension(extension_id: Any) -> bool:
    """Return True for extensions that must never patch provider workflow graphs."""
    return str(extension_id or "").strip() in PROMPT_ONLY_EXTENSION_IDS




def _payload_container(extensions: Any) -> tuple[dict[str, Any] | None, str]:
    """Return the dict that owns extension payload blocks plus its shape label."""
    if not isinstance(extensions, dict):
        return None, "none"
    payloads = extensions.get("payloads")
    if isinstance(payloads, dict):
        return payloads, "payloads"
    nested = extensions.get("extensions")
    if isinstance(nested, dict):
        return nested, "extensions"
    return extensions, "direct"


def _scene_director_payload_from_extensions(extensions: Any) -> dict[str, Any] | None:
    if not isinstance(extensions, Mapping):
        return None
    for source in (
        extensions.get("payloads") if isinstance(extensions.get("payloads"), Mapping) else None,
        extensions.get("extensions") if isinstance(extensions.get("extensions"), Mapping) else None,
        extensions,
    ):
        if isinstance(source, Mapping) and isinstance(source.get(SCENE_DIRECTOR_EXTENSION_ID), Mapping):
            return dict(source.get(SCENE_DIRECTOR_EXTENSION_ID) or {})
    return None


def _scene_director_region_fingerprints(block: Mapping[str, Any]) -> list[dict[str, str]]:
    inputs = block.get("inputs") if isinstance(block.get("inputs"), Mapping) else {}
    regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    fingerprints: list[dict[str, str]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, Mapping):
            continue
        fingerprints.append({
            "index": str(index),
            "id": _clean_text(region.get("id") or region.get("uid") or f"scene_region_{index + 1}"),
            "prompt": str(region.get("prompt") or region.get("positive") or region.get("text") or ""),
            "negative_prompt": str(region.get("negative_prompt") or region.get("negative") or ""),
        })
    return fingerprints


def _scene_director_identity_fingerprints(block: Mapping[str, Any]) -> list[dict[str, str]]:
    assets = block.get("assets") if isinstance(block.get("assets"), Mapping) else {}
    units = assets.get("identity_units") if isinstance(assets.get("identity_units"), list) else []
    fingerprints: list[dict[str, str]] = []
    for index, unit in enumerate(units):
        if not isinstance(unit, Mapping):
            continue
        fingerprints.append({
            "index": str(index),
            "profile_id": _clean_text(unit.get("profile_id") or unit.get("id")),
            "profile_name": _clean_text(unit.get("profile_name") or unit.get("name") or unit.get("label")),
            "reference_image": _clean_text(unit.get("reference_image") or unit.get("image_name")),
        })
    return fingerprints


def _scene_director_binding_fingerprints(block: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact preservation snapshot for Scene Director-owned areas."""
    inputs = block.get("inputs") if isinstance(block.get("inputs"), Mapping) else {}
    assets = block.get("assets") if isinstance(block.get("assets"), Mapping) else {}
    return {
        "regions": deepcopy(inputs.get("regions") if isinstance(inputs.get("regions"), list) else []),
        "identity_units": deepcopy(assets.get("identity_units") if isinstance(assets.get("identity_units"), list) else []),
        "ipadapter_bindings": deepcopy(assets.get("ipadapter_bindings") if isinstance(assets.get("ipadapter_bindings"), list) else []),
        "lora_bindings": deepcopy(assets.get("lora_bindings") if isinstance(assets.get("lora_bindings"), list) else []),
    }


def _scene_director_interop_triggered(merge: Mapping[str, Any], *, require_extension: str | None = None) -> tuple[bool, str]:
    applied = set(str(item) for item in (merge.get("applied_extensions") or []))
    if require_extension:
        return require_extension in applied, f"{require_extension}_not_applied"
    if WILDCARDS_EXTENSION_ID in applied:
        return True, "wildcards_applied"
    if STYLE_STACK_EXTENSION_ID in applied:
        return True, "style_stack_applied"
    return False, "no_prompt_extension_applied"


def apply_scene_director_prompt_extension_interop(
    extensions: Any,
    merge_result: Mapping[str, Any] | None,
    *,
    require_extension: str | None = None,
) -> dict[str, Any]:
    """Route resolved prompt-extension output into Scene Director's global prompt only.

    Phase K closes Wildcards → Scene Director interop. The effective global
    prompt produced by Wildcards and Style Stack can be passed to Scene Director,
    but regional prompts, identity units, IPAdapter bindings, and LoRA bindings
    are copied untouched. Region wildcard resolution is deliberately not in
    scope for Phase K.
    """
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    triggered, reason = _scene_director_interop_triggered(merge, require_extension=require_extension)
    if not triggered:
        return {"extensions": extensions, "changed": False, "metadata": {"reason": reason}}
    source_block = _scene_director_payload_from_extensions(extensions)
    if not source_block or source_block.get("enabled") is False:
        return {"extensions": extensions, "changed": False, "metadata": {"reason": "scene_director_not_enabled"}}

    updated = deepcopy(extensions) if isinstance(extensions, dict) else {}
    container, shape = _payload_container(updated)
    if container is None:
        return {"extensions": extensions, "changed": False, "metadata": {"reason": "extensions_not_mapping"}}

    block = deepcopy(container.get(SCENE_DIRECTOR_EXTENSION_ID) or source_block)
    inputs = block.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        inputs = {}
        block["inputs"] = inputs
    global_inputs = inputs.setdefault("global", {})
    if not isinstance(global_inputs, dict):
        global_inputs = {}
        inputs["global"] = global_inputs
    params = block.setdefault("params", {})
    if not isinstance(params, dict):
        params = {}
        block["params"] = params
    metadata = block.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        block["metadata"] = metadata

    original_positive = str(merge.get("original_positive") or global_inputs.get("positive_prompt") or global_inputs.get("prompt") or "")
    original_negative = str(merge.get("original_negative") or global_inputs.get("negative_prompt") or "")
    effective_positive = str(merge.get("effective_positive") or original_positive)
    effective_negative = str(merge.get("effective_negative") or original_negative)
    applied = list(merge.get("applied_extensions") or [])
    wildcard_applied = WILDCARDS_EXTENSION_ID in applied
    style_stack_applied = STYLE_STACK_EXTENSION_ID in applied

    before_regions = _scene_director_region_fingerprints(block)
    before_identity = _scene_director_identity_fingerprints(block)
    before_bindings = _scene_director_binding_fingerprints(block)

    global_inputs.setdefault("prompt_extensions_original_positive_prompt", original_positive)
    global_inputs.setdefault("prompt_extensions_original_negative_prompt", original_negative)
    if wildcard_applied:
        global_inputs.setdefault("wildcards_original_positive_prompt", original_positive)
        global_inputs.setdefault("wildcards_original_negative_prompt", original_negative)
    if style_stack_applied:
        global_inputs.setdefault("style_stack_original_positive_prompt", original_positive)
        global_inputs.setdefault("style_stack_original_negative_prompt", original_negative)

    global_inputs["positive_prompt"] = effective_positive
    global_inputs["negative_prompt"] = effective_negative
    global_inputs["prompt_extensions_global_only"] = True
    global_inputs["wildcards_global_only"] = True if wildcard_applied else bool(global_inputs.get("wildcards_global_only", False))
    global_inputs["style_stack_global_only"] = True if style_stack_applied else bool(global_inputs.get("style_stack_global_only", False))
    global_inputs["prompt_extension_interop_source"] = "prompt_extension_order_phase_k_scene_director_global_only"

    params["prompt_extension_interop"] = {
        "phase": "K-scene-director-interop",
        "scope": "scene_director_global_prompt_only",
        "wildcards_applied": wildcard_applied,
        "style_stack_applied": style_stack_applied,
        "regions_mutated": False,
        "region_wildcards_resolved": False,
        "identity_units_mutated": False,
        "ipadapter_bindings_mutated": False,
        "lora_bindings_mutated": False,
        "region_context_style_injection": "blocked_use_original_global_for_region_context",
        "region_wildcard_policy": "preserve_region_prompts_by_default",
    }
    # Preserve the historical Style Stack metadata key so existing Style Stack
    # output/replay code remains compatible while Phase K adds Wildcards details.
    metadata["style_stack_interop"] = {
        "phase": "K-scene-director-interop" if style_stack_applied else "style_stack_not_applied",
        "scope": "global_prompt_only",
        "source": "neo_app.image.prompt_extensions.apply_scene_director_prompt_extension_interop",
        "style_stack_applied": style_stack_applied,
        "region_prompts_preserved": True,
        "identity_units_preserved": True,
        "global_positive_changed": effective_positive != original_positive,
        "global_negative_changed": effective_negative != original_negative,
        "style_names": ((merge.get("extension_metadata") or {}).get(STYLE_STACK_EXTENSION_ID) or {}).get("style_names", []),
    }
    metadata["wildcards_interop"] = {
        "phase": "K-scene-director-interop",
        "scope": "global_prompt_only",
        "source": "neo_app.image.prompt_extensions.apply_scene_director_prompt_extension_interop",
        "wildcards_applied": wildcard_applied,
        "region_prompts_preserved": True,
        "region_wildcards_preserved_by_default": True,
        "region_wildcards_resolved": False,
        "identity_units_preserved": True,
        "ipadapter_bindings_preserved": True,
        "lora_bindings_preserved": True,
        "global_positive_changed": effective_positive != original_positive,
        "global_negative_changed": effective_negative != original_negative,
        "resolved_tokens": ((merge.get("extension_metadata") or {}).get(WILDCARDS_EXTENSION_ID) or {}).get("resolved_tokens", []),
        "missing_tokens": ((merge.get("extension_metadata") or {}).get(WILDCARDS_EXTENSION_ID) or {}).get("missing_tokens", []),
    }

    after_regions = _scene_director_region_fingerprints(block)
    after_identity = _scene_director_identity_fingerprints(block)
    after_bindings = _scene_director_binding_fingerprints(block)
    container[SCENE_DIRECTOR_EXTENSION_ID] = block
    audit = {
        "phase": "K-scene-director-interop",
        "changed": True,
        "container_shape": shape,
        "scene_director_extension_id": SCENE_DIRECTOR_EXTENSION_ID,
        "wildcards_extension_id": WILDCARDS_EXTENSION_ID,
        "style_stack_extension_id": STYLE_STACK_EXTENSION_ID,
        "scope": "global_prompt_only",
        "wildcards_applied": wildcard_applied,
        "style_stack_applied": style_stack_applied,
        "region_prompts_preserved": before_regions == after_regions,
        "region_wildcards_preserved_by_default": True,
        "region_wildcards_resolved": False,
        "identity_units_preserved": before_identity == after_identity,
        "ipadapter_bindings_preserved": before_bindings.get("ipadapter_bindings") == after_bindings.get("ipadapter_bindings"),
        "lora_bindings_preserved": before_bindings.get("lora_bindings") == after_bindings.get("lora_bindings"),
        "global_positive_changed": effective_positive != original_positive,
        "global_negative_changed": effective_negative != original_negative,
        "region_count": len(after_regions),
        "identity_unit_count": len(after_identity),
        "not_in_scope": ["scene_director_region_wildcard_resolution", "identity_unit_mutation", "ipadapter_binding_mutation", "lora_binding_mutation"],
    }
    return {"extensions": updated, "changed": True, "metadata": audit}


def apply_scene_director_wildcards_interop(extensions: Any, merge_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Phase K helper: require Wildcards to have contributed before interop."""
    return apply_scene_director_prompt_extension_interop(extensions, merge_result, require_extension=WILDCARDS_EXTENSION_ID)


def apply_scene_director_style_stack_interop(extensions: Any, merge_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Backward-compatible Style Stack interop wrapper.

    Existing Style Stack phases call this function directly. It now delegates to
    the Phase K generic prompt-extension interop while still requiring Style
    Stack to be applied.
    """
    return apply_scene_director_prompt_extension_interop(extensions, merge_result, require_extension=STYLE_STACK_EXTENSION_ID)

def build_prompt_extension_execution_snapshot(*, provider_id: Any = "", backend_id: Any = "", workflow_mode: Any = "", pass_target: Any = "base", merge_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build provider-neutral diagnostics for output/runtime metadata.

    This intentionally contains no Comfy/Forge node names. Providers receive the
    already-merged prompt strings through the normal job.prompt fields.
    """
    merge = merge_result if isinstance(merge_result, Mapping) else {}
    return {
        "phase": "I-provider-neutral-execution",
        "legacy_phase": "H-provider-neutral-execution",
        "prompt_order_phase": "H-prompt-extension-order",
        "provider_neutral_phase": "I-provider-neutral-execution",
        "provider_id": str(provider_id or ""),
        "backend_id": str(backend_id or provider_id or ""),
        "workflow_mode": str(workflow_mode or ""),
        "pass_target": str(pass_target or "base"),
        "prompt_only_extensions": sorted(PROMPT_ONLY_EXTENSION_IDS),
        "style_stack_prompt_only": STYLE_STACK_EXTENSION_ID in PROMPT_ONLY_EXTENSION_IDS,
        "wildcards_prompt_only": WILDCARDS_EXTENSION_ID in PROMPT_ONLY_EXTENSION_IDS,
        "provider_graph_mutation": False,
        "provider_graph_patch_path": "disabled_for_prompt_only_extensions",
        "prompt_only_graph_patch_path": "disabled_for_prompt_only_extensions",
        "workflow_patch_detection": "excluded_for_prompt_only_extensions",
        "wildcards_workflow_patch_excluded": True,
        "style_stack_workflow_patch_excluded": True,
        "effective_prompt_source": "job.prompt/job.positive_prompt after apply_prompt_extensions",
        "prompt_extension_order": list(PROMPT_EXTENSION_ORDER),
        "active_prompt_mutation_order": list(ACTIVE_PROMPT_MUTATION_ORDER),
        "wildcards_before_style_stack": True,
        "embeddings_ti_visibility_stage": "after_style_stack_metadata_only_phase_j",
        "scene_director_interop_stage": "after_wildcards_and_style_stack_global_context_phase_k",
        "phase_j_style_embeddings_interop_status": "implemented",
        "phase_k_scene_director_interop_status": "implemented",
        "phase_l_queue_variants_status": "implemented",
        "wildcard_queue_variants_execution": "separate_jobs_from_ui",
        "wildcard_queue_offset_policy": "positive=variant_offset*2, negative=variant_offset*2+1",
        "providers_receive_only_prompt_fields": ["job.prompt", "job.positive_prompt", "job.negative_prompt"],
        "effective_negative_source": "job.negative_prompt after apply_prompt_extensions",
        "supported_backends": sorted(PROVIDER_NEUTRAL_BACKENDS),
        "changed": bool(merge.get("changed")),
        "applied_extensions": list(merge.get("applied_extensions") or []),
        "skipped_extensions": list(merge.get("skipped_extensions") or []),
    }


@dataclass(frozen=True)
class PromptExtensionMergeResult:
    """Result snapshot for replay/output metadata and tests."""

    original_positive: str
    original_negative: str
    effective_positive: str
    effective_negative: str
    changed: bool
    pass_target: str
    workflow_mode: str
    applied_extensions: list[str] = field(default_factory=list)
    skipped_extensions: list[dict[str, Any]] = field(default_factory=list)
    extension_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _style_stack_payload_from_extensions(extensions: Any) -> dict[str, Any] | None:
    """Extract ``extensions.payloads.style_stack`` or direct ``style_stack`` blocks."""
    if not isinstance(extensions, Mapping):
        return None
    payloads = extensions.get("payloads") if isinstance(extensions.get("payloads"), Mapping) else extensions
    block = payloads.get(STYLE_STACK_EXTENSION_ID) if isinstance(payloads, Mapping) else None
    return dict(block) if isinstance(block, Mapping) else None


def _extension_payload_from_extensions(extensions: Any, extension_id: str) -> dict[str, Any] | None:
    """Extract an extension block from direct, payloads, or nested extension shapes."""
    if not isinstance(extensions, Mapping):
        return None
    for source in (
        extensions.get("payloads") if isinstance(extensions.get("payloads"), Mapping) else None,
        extensions.get("extensions") if isinstance(extensions.get("extensions"), Mapping) else None,
        extensions,
    ):
        if isinstance(source, Mapping) and isinstance(source.get(extension_id), Mapping):
            return dict(source.get(extension_id) or {})
    return None


def _embeddings_ti_payload_from_extensions(extensions: Any) -> dict[str, Any] | None:
    return _extension_payload_from_extensions(extensions, EMBEDDINGS_TI_EXTENSION_ID)


def _lora_stack_payload_from_extensions(extensions: Any) -> dict[str, Any] | None:
    return _extension_payload_from_extensions(extensions, LORA_STACK_EXTENSION_ID)


def _dedupe_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _embedding_tokens_in_text(text: Any) -> list[str]:
    import re
    found = re.findall(r"embedding:[A-Za-z0-9_. -]+", str(text or ""))
    return _dedupe_strings([item.strip().rstrip(",.;") for item in found])


def _lora_triggers_in_text(text: Any) -> list[str]:
    import re
    found = re.findall(r"<lora:[^>]+>", str(text or ""), flags=re.IGNORECASE)
    return _dedupe_strings(found)


def _embedding_items_from_payload(block: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(block, Mapping) or not block.get("enabled"):
        return []
    params = block.get("params") if isinstance(block.get("params"), Mapping) else {}
    items = params.get("items") if isinstance(params.get("items"), list) else []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        token = str(item.get("token") or item.get("name") or "").strip()
        if token and not token.startswith("embedding:"):
            token = f"embedding:{token}"
        if token:
            result.append({
                "token": token,
                "target": str(item.get("target") or "").strip(),
                "strength": item.get("strength", 1.0),
            })
    return result


def _lora_rows_from_payload(block: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(block, Mapping) or not block.get("enabled"):
        return []
    params = block.get("params") if isinstance(block.get("params"), Mapping) else {}
    rows = params.get("loras") if isinstance(params.get("loras"), list) else []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        if name:
            result.append({
                "name": name,
                "target": str(row.get("target") or "both").strip(),
                "apply_to": str(row.get("apply_to") or "global").strip(),
                "strength": row.get("strength", 0.8),
            })
    return result


def build_phase_j_style_embeddings_interop_metadata(
    *,
    effective_positive: Any,
    effective_negative: Any,
    extensions: Any,
    wildcard_metadata: Mapping[str, Any] | None = None,
    style_stack_metadata: Mapping[str, Any] | None = None,
    seed: Any = "",
) -> dict[str, Any]:
    """Build Phase J visibility metadata without mutating prompts or provider graphs.

    This makes tokens produced by Wildcards/Style Stack visible to downstream
    Embeddings/TI and LoRA systems while avoiding duplicate extension metadata.
    """
    positive_embedding_tokens = _embedding_tokens_in_text(effective_positive)
    negative_embedding_tokens = _embedding_tokens_in_text(effective_negative)
    visible_embedding_tokens = _dedupe_strings([*positive_embedding_tokens, *negative_embedding_tokens])
    visible_lora_triggers = _dedupe_strings([*_lora_triggers_in_text(effective_positive), *_lora_triggers_in_text(effective_negative)])

    embeddings_block = _embeddings_ti_payload_from_extensions(extensions)
    lora_block = _lora_stack_payload_from_extensions(extensions)
    embedding_items = _embedding_items_from_payload(embeddings_block)
    configured_embedding_tokens = _dedupe_strings([item.get("token") for item in embedding_items])
    duplicate_embedding_metadata_tokens = [
        token for token in configured_embedding_tokens
        if token.casefold() in {found.casefold() for found in visible_embedding_tokens}
    ]
    lora_rows = _lora_rows_from_payload(lora_block)

    wildcard_meta = wildcard_metadata if isinstance(wildcard_metadata, Mapping) else {}
    style_meta = style_stack_metadata if isinstance(style_stack_metadata, Mapping) else {}
    return {
        "phase": "J-style-stack-embeddings-ti-interop",
        "implemented": True,
        "prompt_only": True,
        "provider_graph_mutation": False,
        "wildcards_before_style_stack": True,
        "style_stack_after_wildcards": True,
        "visibility_scope": "metadata_only_after_style_stack",
        "prompt_mutated_by_phase_j": False,
        "embeddings_ti_stage": "visibility_after_wildcards_and_style_stack",
        "lora_trigger_stage": "visibility_after_wildcards_and_style_stack",
        "effective_positive": str(effective_positive or ""),
        "effective_negative": str(effective_negative or ""),
        "visible_embedding_tokens": visible_embedding_tokens,
        "positive_embedding_tokens": positive_embedding_tokens,
        "negative_embedding_tokens": negative_embedding_tokens,
        "embedding_payload_tokens": configured_embedding_tokens,
        "embedding_duplicate_metadata_tokens": duplicate_embedding_metadata_tokens,
        "embedding_duplicate_metadata_avoided": bool(duplicate_embedding_metadata_tokens),
        "embedding_token_visible_in_effective_prompt": bool(visible_embedding_tokens),
        "visible_lora_triggers": visible_lora_triggers,
        "lora_payload_names": _dedupe_strings([row.get("name") for row in lora_rows]),
        "lora_trigger_visible_in_effective_prompt": bool(visible_lora_triggers),
        "wildcard_resolved_tokens": list(wildcard_meta.get("resolved_tokens") or []),
        "wildcard_missing_tokens": list(wildcard_meta.get("missing_tokens") or []),
        "wildcard_inline_choice_count": int(wildcard_meta.get("inline_choice_count") or 0),
        "wildcard_file_choice_count": int(wildcard_meta.get("file_choice_count") or 0),
        "style_names": list(style_meta.get("style_names") or []),
        "style_positive_prompt": str(style_meta.get("style_positive_prompt") or ""),
        "style_negative_prompt": str(style_meta.get("style_negative_prompt") or ""),
        "seed_used": str(seed or wildcard_meta.get("seed_used") or ""),
        "same_seed_same_variant_contract": "delegated_to_phase_g_resolver_seed_variant_determinism",
        "different_variant_offset_contract": "delegated_to_phase_g_resolver_seed_variant_determinism",
    }

def _style_stack_should_apply(target: str, pass_target: str, workflow_mode: str) -> bool:
    normalized_target = str(target or "both").strip().lower()
    if normalized_target not in STYLE_STACK_VALID_TARGETS:
        normalized_target = "both"
    normalized_pass = str(pass_target or "base").strip().lower()
    normalized_mode = str(workflow_mode or "").strip().lower()
    is_finish = normalized_pass == "finish" or normalized_mode in FINISH_WORKFLOW_MODES
    if normalized_target == "both":
        return True
    if normalized_target == "finish":
        return is_finish
    if normalized_target == "base":
        return not is_finish
    return True


def _append_deduped(base: Any, additions: list[Any]) -> str:
    """Append comma-style prompt parts while preserving order and exact first text."""
    parts: list[str] = []
    seen: set[str] = set()
    for candidate in [base, *additions]:
        text = _clean_text(candidate).strip(" ,")
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        parts.append(text)
    return ", ".join(parts)


def _contains_prompt_placeholder(value: Any) -> bool:
    return bool(STYLE_STACK_PROMPT_PLACEHOLDER_RE.search(str(value or "")))


def _render_style_prompt_template(template: Any, prompt: Any) -> str:
    """Replace Style Stack's ``{prompt}`` marker with the current effective prompt.

    The marker is an insertion slot, not literal prompt text. The replacement is
    intentionally case-insensitive and allows light whitespace such as
    ``{ prompt }`` because legacy CSVs and user libraries are not guaranteed to
    be perfectly normalized.
    """
    style_text = _clean_text(template).strip(" ,")
    if not style_text:
        return ""
    current_prompt = _clean_text(prompt).strip(" ,")
    return STYLE_STACK_PROMPT_PLACEHOLDER_RE.sub(current_prompt, style_text).strip(" ,")


def _merge_style_prompt_parts(base: Any, additions: list[Any]) -> tuple[str, int]:
    """Merge Style Stack positive/negative parts using V1-style ``{prompt}`` slots.

    Style rows that contain ``{prompt}`` wrap the current prompt. Style rows
    without a placeholder keep the old append/dedupe behavior. Multiple template
    styles apply in order, so later templates can wrap earlier style output.
    """
    current = _clean_text(base).strip(" ,")
    template_count = 0
    for addition in additions:
        text = _clean_text(addition).strip(" ,")
        if not text:
            continue
        if _contains_prompt_placeholder(text):
            rendered = _render_style_prompt_template(text, current)
            if rendered:
                current = rendered
                template_count += 1
            continue
        current = _append_deduped(current, [text])
    return current, template_count


def _style_records_by_name(payload: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    assets = payload.get("assets") if isinstance(payload.get("assets"), Mapping) else {}
    records = assets.get("style_records") if isinstance(assets, Mapping) and isinstance(assets.get("style_records"), list) else []
    by_name: dict[str, dict[str, str]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = _clean_text(record.get("name"))
        if not name or name in by_name:
            continue
        by_name[name] = {
            "name": name,
            "prompt": str(record.get("prompt") or ""),
            "negative_prompt": str(record.get("negative_prompt") or ""),
        }
    return by_name


def apply_prompt_extensions(
    positive_prompt: Any,
    negative_prompt: Any,
    extensions: Any,
    workflow_mode: Any = "txt2img",
    pass_target: Any = "base",
    seed: Any = "",
    repo_root: Any = None,
) -> dict[str, Any]:
    """Apply provider-neutral prompt-only extensions before prompt conditioning.

    Phase I preserves the Phase H active prompt mutation order: Wildcards first,
    then Style Stack. Providers receive only the resulting effective prompt fields.
    The return value is a plain dict so it can be stored directly in job
    params/output context metadata.
    """
    original_positive = str(positive_prompt or "")
    original_negative = str(negative_prompt or "")
    effective_positive = original_positive
    effective_negative = original_negative
    applied: list[str] = []
    skipped: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        "prompt_extension_order": list(PROMPT_EXTENSION_ORDER),
        "active_prompt_mutation_order": list(ACTIVE_PROMPT_MUTATION_ORDER),
        "phase": "H-prompt-extension-order",
        "provider_neutral_phase": "I-provider-neutral-execution",
        "provider_graph_mutation": False,
        "workflow_patch_detection": "excluded_for_prompt_only_extensions",
        "phase_j_status": "implemented",
        "phase_k_status": "implemented",
    }

    wildcard_result = apply_wildcard_prompt_extension(
        effective_positive,
        effective_negative,
        extensions,
        seed=seed,
        repo_root=repo_root,
    )
    if wildcard_result.get("enabled") and wildcard_result.get("applied"):
        effective_positive = str(wildcard_result.get("effective_positive") or effective_positive)
        effective_negative = str(wildcard_result.get("effective_negative") or effective_negative)
        applied.append(WILDCARDS_EXTENSION_ID)
        metadata[WILDCARDS_EXTENSION_ID] = {
            **dict(wildcard_result.get("metadata") or {}),
            "phase_h_order_index": ACTIVE_PROMPT_MUTATION_ORDER.index(WILDCARDS_EXTENSION_ID),
            "runs_before": STYLE_STACK_EXTENSION_ID,
        }
    elif wildcard_result.get("reason") not in {"payload_missing", None}:
        skipped.append({"extension_id": WILDCARDS_EXTENSION_ID, "reason": wildcard_result.get("reason") or "not_applied"})

    raw_style_stack = _style_stack_payload_from_extensions(extensions)
    if raw_style_stack is not None:
        style_stack = normalize_style_stack_payload(raw_style_stack)
        target = ((style_stack.get("params") or {}).get("target") or "both") if isinstance(style_stack.get("params"), Mapping) else "both"
        if not style_stack.get("enabled"):
            skipped.append({"extension_id": STYLE_STACK_EXTENSION_ID, "reason": "disabled_or_empty"})
        elif not _style_stack_should_apply(str(target), str(pass_target or "base"), str(workflow_mode or "txt2img")):
            skipped.append({"extension_id": STYLE_STACK_EXTENSION_ID, "reason": "target_not_for_pass", "target": target, "pass_target": pass_target})
        else:
            inputs = style_stack.get("inputs") if isinstance(style_stack.get("inputs"), Mapping) else {}
            active_names = [str(name or "").strip() for name in (inputs.get("active_styles") or []) if str(name or "").strip()]
            records = _style_records_by_name(style_stack)
            positive_parts: list[str] = []
            negative_parts: list[str] = []
            used_names: list[str] = []
            missing_names: list[str] = []
            for name in active_names:
                record = records.get(name)
                if not record:
                    missing_names.append(name)
                    continue
                used_names.append(name)
                positive_parts.append(record.get("prompt") or "")
                negative_parts.append(record.get("negative_prompt") or "")
            manual_positive = str(inputs.get("manual_positive") or "")
            manual_negative = str(inputs.get("manual_negative") or "")
            if manual_positive.strip():
                positive_parts.append(manual_positive)
            if manual_negative.strip():
                negative_parts.append(manual_negative)
            effective_positive, positive_template_count = _merge_style_prompt_parts(effective_positive, positive_parts)
            effective_negative, negative_template_count = _merge_style_prompt_parts(effective_negative, negative_parts)
            applied.append(STYLE_STACK_EXTENSION_ID)
            metadata[STYLE_STACK_EXTENSION_ID] = {
                "enabled": True,
                "prompt_only": True,
                "merge_policy": STYLE_STACK_MERGE_POLICY,
                "target": target,
                "pass_target": str(pass_target or "base"),
                "workflow_mode": str(workflow_mode or "txt2img"),
                "style_names": used_names,
                "missing_style_names": missing_names,
                "manual_positive": bool(manual_positive.strip()),
                "manual_negative": bool(manual_negative.strip()),
                "positive_parts_added": sum(1 for item in positive_parts if str(item or "").strip()),
                "negative_parts_added": sum(1 for item in negative_parts if str(item or "").strip()),
                "positive_template_count": positive_template_count,
                "negative_template_count": negative_template_count,
                "prompt_placeholder_interpolation": "enabled",
                "literal_prompt_placeholder_removed": True,
                "runtime_merge_status": "applied_phase_h_provider_neutral",
                "phase_i_provider_neutral_status": "implemented",
                "prompt_extension_order_status": "implemented_phase_h_prompt_extension_order",
                "wildcards_order_status": "style_stack_runs_after_wildcards_phase_h",
                "phase_h_order_index": ACTIVE_PROMPT_MUTATION_ORDER.index(STYLE_STACK_EXTENSION_ID),
                "runs_after": WILDCARDS_EXTENSION_ID,
                "scene_director_interop_ready": True,
                "style_positive_prompt": _merge_style_prompt_parts("", positive_parts)[0],
                "style_negative_prompt": _merge_style_prompt_parts("", negative_parts)[0],
                "provider_graph_mutation": False,
                "provider_graph_patch_path": "disabled_for_prompt_only_extensions",
                "workflow_patch_detection": "excluded_for_prompt_only_extensions",
            }

    phase_j_metadata = build_phase_j_style_embeddings_interop_metadata(
        effective_positive=effective_positive,
        effective_negative=effective_negative,
        extensions=extensions,
        wildcard_metadata=metadata.get(WILDCARDS_EXTENSION_ID),
        style_stack_metadata=metadata.get(STYLE_STACK_EXTENSION_ID),
        seed=seed,
    )
    metadata[EMBEDDINGS_TI_EXTENSION_ID] = {
        "phase": "J-style-stack-embeddings-ti-interop",
        "visibility_status": "implemented_phase_j",
        "prompt_mutation": False,
        "visible_embedding_tokens": phase_j_metadata.get("visible_embedding_tokens", []),
        "embedding_payload_tokens": phase_j_metadata.get("embedding_payload_tokens", []),
        "duplicate_metadata_tokens": phase_j_metadata.get("embedding_duplicate_metadata_tokens", []),
        "duplicate_metadata_avoided": phase_j_metadata.get("embedding_duplicate_metadata_avoided", False),
        "runs_after": [WILDCARDS_EXTENSION_ID, STYLE_STACK_EXTENSION_ID],
    }
    metadata[LORA_STACK_EXTENSION_ID] = {
        "phase": "J-style-stack-embeddings-ti-interop",
        "visibility_status": "implemented_phase_j",
        "prompt_mutation": False,
        "visible_lora_triggers": phase_j_metadata.get("visible_lora_triggers", []),
        "lora_payload_names": phase_j_metadata.get("lora_payload_names", []),
        "runs_after": [WILDCARDS_EXTENSION_ID, STYLE_STACK_EXTENSION_ID],
    }
    metadata["phase_j_style_embeddings_interop"] = phase_j_metadata
    metadata[SCENE_DIRECTOR_EXTENSION_ID] = {
        "phase": "K-scene-director-interop",
        "interop_status": "implemented_phase_k",
        "scope": "global_prompt_only_after_wildcards_and_style_stack",
        "prompt_mutation": False,
        "wildcards_applied": WILDCARDS_EXTENSION_ID in applied,
        "style_stack_applied": STYLE_STACK_EXTENSION_ID in applied,
        "regions_preserved_by_default": True,
        "region_wildcard_resolution": "not_in_scope_phase_k",
        "identity_units_preserved": True,
        "ipadapter_bindings_preserved": True,
        "lora_bindings_preserved": True,
        "runs_after": [WILDCARDS_EXTENSION_ID, STYLE_STACK_EXTENSION_ID],
    }

    return PromptExtensionMergeResult(
        original_positive=original_positive,
        original_negative=original_negative,
        effective_positive=effective_positive,
        effective_negative=effective_negative,
        changed=(effective_positive != original_positive or effective_negative != original_negative),
        pass_target=str(pass_target or "base"),
        workflow_mode=str(workflow_mode or "txt2img"),
        applied_extensions=applied,
        skipped_extensions=skipped,
        extension_metadata=metadata,
    ).to_dict()


def phase_h_prompt_extension_order_status() -> dict[str, Any]:
    """Return the Phase H prompt pipeline contract for diagnostics/tests."""

    return {
        "phase": "H-prompt-extension-order",
        "implemented": True,
        "prompt_extension_order": list(PROMPT_EXTENSION_ORDER),
        "active_prompt_mutation_order": list(ACTIVE_PROMPT_MUTATION_ORDER),
        "wildcards_before_style_stack": True,
        "provider_graph_mutation": False,
        "prompt_only_extensions": sorted(PROMPT_ONLY_EXTENSION_IDS),
        "conditioning_boundary": "apply_prompt_extensions_before_condition_prompt_pair_before_provider_run",
    }


def phase_i_provider_neutral_execution_status() -> dict[str, Any]:
    """Return the Phase I provider-neutral execution contract for diagnostics/tests."""

    return {
        "phase": "I-provider-neutral-execution",
        "implemented": True,
        "prompt_order_phase": "H-prompt-extension-order",
        "prompt_extension_order": list(PROMPT_EXTENSION_ORDER),
        "active_prompt_mutation_order": list(ACTIVE_PROMPT_MUTATION_ORDER),
        "prompt_only_extensions": sorted(PROMPT_ONLY_EXTENSION_IDS),
        "supported_provider_backends": sorted(PROVIDER_NEUTRAL_BACKENDS),
        "provider_graph_mutation": False,
        "workflow_patch_detection": "excluded_for_prompt_only_extensions",
        "wildcards_workflow_patch_excluded": True,
        "style_stack_workflow_patch_excluded": True,
        "providers_receive_only_prompt_fields": ["job.prompt", "job.positive_prompt", "job.negative_prompt"],
        "no_comfy_graph_mutation": True,
        "no_forge_patch": True,
        "no_object_info_request": True,
    }



def phase_j_style_embeddings_interop_status() -> dict[str, Any]:
    """Return the Phase J Style Stack + Embeddings/TI interop contract."""
    return {
        "phase": "J-style-stack-embeddings-ti-interop",
        "implemented": True,
        "depends_on": "I-provider-neutral-execution",
        "prompt_order_phase": "H-prompt-extension-order",
        "prompt_extension_order": list(PROMPT_EXTENSION_ORDER),
        "active_prompt_mutation_order": list(ACTIVE_PROMPT_MUTATION_ORDER),
        "wildcards_before_style_stack": True,
        "style_stack_runs_after_wildcards": True,
        "embeddings_ti_visibility_stage": "after_wildcards_and_style_stack_metadata_only",
        "lora_trigger_visibility_stage": "after_wildcards_and_style_stack_metadata_only",
        "prompt_mutation": False,
        "provider_graph_mutation": False,
        "embedding_token_from_wildcard_remains_visible": True,
        "lora_trigger_from_wildcard_remains_visible": True,
        "duplicate_embedding_metadata_policy": "record_and_avoid_duplicate_metadata_claims",
        "same_seed_same_variant_contract": "same_expansion",
        "same_seed_different_variant_offset_contract": "different_deterministic_expansion_when_choices_allow",
        "not_in_scope": [
            "scene_director_region_wildcard_resolution",
            "output_metadata_replay",
            "provider_workflow_patch_mutation",
        ],
    }


def phase_k_scene_director_interop_status() -> dict[str, Any]:
    """Return the Phase K Scene Director interop contract."""
    return {
        "phase": "K-scene-director-interop",
        "implemented": True,
        "depends_on": "J-style-stack-embeddings-ti-interop",
        "prompt_order_phase": "H-prompt-extension-order",
        "prompt_extension_order": list(PROMPT_EXTENSION_ORDER),
        "active_prompt_mutation_order": list(ACTIVE_PROMPT_MUTATION_ORDER),
        "scene_director_stage": "after_wildcards_and_style_stack_global_prompt_only",
        "wildcards_affect_scene_director_global_prompt": True,
        "style_stack_affects_scene_director_global_prompt": True,
        "region_prompts_preserved_by_default": True,
        "region_wildcard_resolution": "not_in_scope_phase_k",
        "identity_units_preserved": True,
        "ipadapter_bindings_preserved": True,
        "lora_bindings_preserved": True,
        "prompt_only": True,
        "provider_graph_mutation": False,
        "not_in_scope": [
            "scene_director_region_wildcard_resolution",
            "output_metadata_replay",
            "registry_default_enable",
        ],
    }


def phase_l_queue_variants_status() -> dict[str, Any]:
    """Return the Phase L Queue Variants migration contract.

    The queue submission loop lives in the Image UI because it must create
    separate jobs. The backend resolver already consumes variant_offset and
    applies the V1 offset contract: positive=variant_offset*2 and
    negative=variant_offset*2+1.
    """
    return {
        "phase": "L-queue-variants-migration",
        "implemented": True,
        "depends_on": "K-scene-director-interop",
        "submission_owner": "neo_app.static.js.neo:runWildcardVariantQueue",
        "separate_jobs": True,
        "queue_count_source": "extensions.payloads.wildcards.params.queue_count",
        "variant_offset_source": "extensions.payloads.wildcards.params.variant_offset",
        "offset_policy": "positive=variant_offset*2, negative=variant_offset*2+1",
        "stable_seed_policy": "one_base_seed_per_queue",
        "large_queue_warning_threshold": 20,
        "max_queue_count": 50,
        "provider_graph_mutation": False,
        "prompt_only": True,
        "not_in_scope": [
            "output_metadata_replay",
            "registry_default_enable",
            "scene_director_region_wildcard_resolution",
        ],
    }


def phase_m_output_metadata_replay_status() -> dict[str, Any]:
    """Return the Phase M Wildcards output metadata/replay contract."""
    return {
        "phase": "M-output-metadata-replay",
        "implemented": True,
        "depends_on": "L-queue-variants-migration",
        "metadata_builder": "neo_extensions.built_in.wildcards.backend.metadata.build_output_extension_metadata",
        "output_sidecar_status": "implemented_phase_m",
        "replay_payloads": "extensions.replay_payloads.wildcards",
        "assistant_summaries": "extensions.assistant_summaries.wildcards",
        "memory_events": "extensions.memory_events.wildcards",
        "source_effective_prompt_tracking": True,
        "resolved_missing_token_tracking": True,
        "seed_variant_tracking": True,
        "prompt_only": True,
        "provider_graph_mutation": False,
        "not_in_scope": [
            "registry_default_enable",
            "scene_director_region_wildcard_resolution",
            "provider_workflow_patch_mutation",
        ],
    }



def phase_o_final_v1_parity_closeout_status() -> dict[str, Any]:
    """Return the Phase O final Wildcards V1 parity closeout contract."""
    return {
        "phase": "O-final-tests-v1-parity-closeout",
        "implemented": True,
        "depends_on": "N-registry-integration",
        "v1_parity_closeout": True,
        "final_regression_wall": True,
        "prompt_extension_order": list(PROMPT_EXTENSION_ORDER),
        "active_prompt_mutation_order": list(ACTIVE_PROMPT_MUTATION_ORDER),
        "wildcards_before_style_stack": True,
        "provider_neutral": True,
        "prompt_only": True,
        "provider_graph_mutation": False,
        "queue_variants": "implemented_phase_l",
        "output_metadata_replay": "implemented_phase_m",
        "registry_integration": "implemented_phase_n",
        "scene_director_global_interop": "implemented_phase_k",
        "scene_director_region_wildcards": "not_enabled_by_default",
        "metadata_slots": [
            "extensions.used[]",
            "extensions.payloads.wildcards",
            "extensions.replay_payloads.wildcards",
            "extensions.assistant_summaries.wildcards",
            "extensions.memory_events.wildcards",
        ],
    }
