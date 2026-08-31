from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from neo_app.video.video_lora_runtime import (
    H3_MODEL_ONLY_LOADER,
    MAX_VIDEO_LORAS,
    merge_h3_legacy_turbo as _phase8_h3_merge,
    normalize_video_lora_rows,
)

PHASE = "phase_9"
SCHEMA_VERSION = "neo.video.lora_legacy_compat.v1"
GRAPH_AUTHORITY = "compiler_owned_universal_video_lora_stack"

H3_LEGACY_FIELDS: tuple[str, ...] = (
    "h3_turbo_enabled",
    "h3_turbo_lora",
    "h3_turbo_strength",
)
WAN_LEGACY_FIELDS: tuple[str, ...] = (
    "enable_video_lora",
    "video_lora_mode",
    "video_lora_model",
    "video_lora_strength",
    "video_lora_target",
    "enable_lightx2v",
    "high_noise_lora",
    "low_noise_lora",
    "high_noise_lora_strength",
    "low_noise_lora_strength",
)
LEGACY_WAN_GRAPH_NODE_IDS: frozenset[str] = frozenset({"129:101", "129:102", "9001", "9002"})
LORA_NODE_CLASSES: frozenset[str] = frozenset({H3_MODEL_ONLY_LOADER, "LoraLoader"})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "on"}


def _target_branches(target: str) -> tuple[str, ...]:
    key = str(target or "all").strip().casefold()
    if key == "all":
        return ("high", "low")
    if key in {"high", "low"}:
        return (key,)
    return ()


def _same_name(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return str(left.get("name") or "").casefold() == str(right.get("name") or "").casefold()


def _same_strength(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        float(left.get("strength_model") or 0.0) == float(right.get("strength_model") or 0.0)
        and left.get("strength_clip") == right.get("strength_clip")
    )


def _derived_uid(base: str, target: str, used: set[str]) -> str:
    stem = f"{base}__{target}" if base else f"legacy_split__{target}"
    candidate = stem
    index = 2
    while candidate in used:
        candidate = f"{stem}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def _split_all_row(
    rows: list[dict[str, Any]],
    index: int,
    *,
    used_uids: set[str],
) -> tuple[int, int]:
    original = deepcopy(rows[index])
    original_uid = str(original.get("uid") or f"video_lora_{index + 1}")
    high = deepcopy(original)
    low = deepcopy(original)
    high["target"] = "high"
    high["uid"] = original_uid
    low["target"] = "low"
    low["uid"] = _derived_uid(original_uid, "low", used_uids)
    rows[index : index + 1] = [high, low]
    return index, index + 1


def _matching_branch_indices(
    rows: list[dict[str, Any]],
    legacy: dict[str, Any],
    branch: str,
) -> list[int]:
    matches: list[int] = []
    for index, row in enumerate(rows):
        if not _same_name(row, legacy):
            continue
        if branch in _target_branches(str(row.get("target") or "all")):
            matches.append(index)
    return matches


def _preferred_match(rows: list[dict[str, Any]], indices: list[int], branch: str) -> int:
    exact = [index for index in indices if str(rows[index].get("target") or "") == branch]
    if exact:
        return exact[0]
    return indices[0]


def merge_wan_legacy_rows_hardened(
    universal_rows: list[dict[str, Any]] | None,
    legacy_rows: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge WAN legacy intent without widening branch semantics.

    Universal rows own uid and strength wherever they already cover a branch.
    Legacy rows may fill an uncovered branch or promote the same file to speed
    on the exact requested branch. If promotion touches only one half of an
    existing target=all row, that row is split rather than over-promoted.
    """
    merged = normalize_video_lora_rows(universal_rows)
    legacy = normalize_video_lora_rows(legacy_rows)
    used_uids = {str(row.get("uid") or "") for row in merged if str(row.get("uid") or "")}
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "requested": bool(legacy),
        "bridged_count": 0,
        "duplicate_branch_suppressed": 0,
        "existing_role_promoted": 0,
        "existing_row_split": 0,
        "uid_preserved": 0,
        "strength_conflict_suppressed": 0,
        "branch_exact_promotion": True,
        "universal_precedence": ["uid", "strength_model", "strength_clip"],
        "source": "legacy_wan_video_lora_fields",
        "graph_authority": GRAPH_AUTHORITY,
    }

    def ensure_capacity(extra: int = 1) -> None:
        if len(merged) + extra > MAX_VIDEO_LORAS:
            raise ValueError(
                f"Phase 9 branch-exact WAN legacy migration would exceed the Video LoRA Stack maximum "
                f"of {MAX_VIDEO_LORAS} rows. Remove or consolidate a universal row before loading this legacy state."
            )

    for legacy_row in legacy:
        wanted = _target_branches(str(legacy_row.get("target") or "all"))
        if not wanted:
            continue

        any_matches = any(_matching_branch_indices(merged, legacy_row, branch) for branch in wanted)
        if not any_matches and len(wanted) == 2:
            ensure_capacity()
            merged.append(deepcopy(legacy_row))
            used_uids.add(str(legacy_row.get("uid") or ""))
            meta["bridged_count"] += 1
            continue

        for branch in wanted:
            indices = _matching_branch_indices(merged, legacy_row, branch)
            if not indices:
                ensure_capacity()
                row = deepcopy(legacy_row)
                row["target"] = branch
                if str(row.get("uid") or "") in used_uids:
                    row["uid"] = _derived_uid(str(row.get("uid") or "legacy_wan"), branch, used_uids)
                else:
                    used_uids.add(str(row.get("uid") or ""))
                merged.append(row)
                meta["bridged_count"] += 1
                continue

            meta["duplicate_branch_suppressed"] += 1
            index = _preferred_match(merged, indices, branch)
            existing = merged[index]
            if not _same_strength(existing, legacy_row):
                meta["strength_conflict_suppressed"] += 1

            if str(legacy_row.get("role") or "standard") != "speed":
                continue
            if str(existing.get("role") or "standard") == "speed":
                continue

            if str(existing.get("target") or "all") == "all":
                ensure_capacity()
                high_index, low_index = _split_all_row(merged, index, used_uids=used_uids)
                meta["existing_row_split"] += 1
                meta["uid_preserved"] += 1
                index = high_index if branch == "high" else low_index
                existing = merged[index]

            existing["role"] = "speed"
            meta["existing_role_promoted"] += 1

    if len(merged) > MAX_VIDEO_LORAS:
        raise ValueError(f"Phase 9 WAN migration produced more than {MAX_VIDEO_LORAS} Video LoRA rows.")

    standard = [deepcopy(row) for row in merged if row.get("role") != "speed"]
    speed = [deepcopy(row) for row in merged if row.get("role") == "speed"]
    return [*standard, *speed], meta


def merge_h3_legacy_turbo_hardened(
    rows: list[dict[str, Any]] | None,
    *,
    enabled: bool,
    selected_name: str = "",
    discovered_candidates: list[str] | tuple[str, ...] | None = None,
    strength: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    before = normalize_video_lora_rows(rows)
    merged, meta = _phase8_h3_merge(
        rows,
        enabled=enabled,
        selected_name=selected_name,
        discovered_candidates=discovered_candidates,
        strength=strength,
    )
    selected = str(meta.get("selected_name") or "")
    strength_conflict = 0
    if enabled and selected:
        existing = next((row for row in before if str(row.get("name") or "").casefold() == selected.casefold()), None)
        if existing is not None and float(existing.get("strength_model") or 0.0) != float(strength):
            strength_conflict = 1

    enriched = {
        **meta,
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "strength_conflict_suppressed": strength_conflict,
        "universal_precedence": ["uid", "strength_model", "strength_clip"],
        "graph_authority": GRAPH_AUTHORITY,
        "legacy_control_status": "compatibility_only_deprecated",
    }
    return merged, enriched


def _declared_node_ids(runtime: dict[str, Any]) -> set[str]:
    declared: set[str] = set()
    for item in runtime.get("applied", []) if isinstance(runtime.get("applied"), list) else []:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "")
        if node_id:
            declared.add(node_id)
        node_ids = item.get("node_ids") if isinstance(item.get("node_ids"), dict) else {}
        for value in node_ids.values():
            text = str(value or "")
            if text:
                declared.add(text)
    return declared


def _actual_lora_node_ids(workflow: dict[str, Any]) -> set[str]:
    return {
        str(node_id)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and str(node.get("class_type") or "") in LORA_NODE_CLASSES
    }


def assert_universal_lora_graph_authority(
    workflow: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
    *,
    family: str,
) -> dict[str, Any]:
    graph = workflow if isinstance(workflow, dict) else {}
    state = runtime if isinstance(runtime, dict) else {}
    declared = _declared_node_ids(state)
    actual = _actual_lora_node_ids(graph)
    undeclared = sorted(actual - declared)
    if undeclared:
        raise ValueError(
            f"{family} Phase 9 detected LoRA graph mutation outside the universal compiler-owned Video LoRA stack: "
            + ", ".join(undeclared)
        )
    return {
        "verified": True,
        "graph_authority": GRAPH_AUTHORITY,
        "declared_lora_node_ids": sorted(declared),
        "actual_lora_node_ids": sorted(actual),
        "undeclared_lora_node_ids": [],
        "historical_wan_node_ids_present": sorted(actual & LEGACY_WAN_GRAPH_NODE_IDS),
    }


def sanitize_legacy_adapter_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    data = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    branches = data.get("branches") if isinstance(data.get("branches"), list) else []
    cleaned: list[dict[str, Any]] = []
    removed_fields = 0
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        item = dict(branch)
        for key in ("node_id", "source_model_link", "output_model_link"):
            if key in item:
                item.pop(key, None)
                removed_fields += 1
        cleaned.append(item)
    if branches:
        data["branches"] = cleaned
    data["deprecated"] = True
    data["compatibility_only"] = True
    data["graph_mutation_authority"] = "none"
    data["universal_graph_authority"] = GRAPH_AUTHORITY
    data["graph_link_fields_removed"] = removed_fields
    return data


def _legacy_field_state(payload: dict[str, Any], family: str) -> tuple[list[str], list[str]]:
    supported = H3_LEGACY_FIELDS if family == "minimax_h3" else WAN_LEGACY_FIELDS if family == "wan22" else ()
    serialized = [field for field in supported if field in payload]
    active: list[str] = []
    if family == "minimax_h3":
        if _as_bool(payload.get("h3_turbo_enabled")):
            active.append("h3_turbo_enabled")
            if str(payload.get("h3_turbo_lora") or ""):
                active.append("h3_turbo_lora")
            if "h3_turbo_strength" in payload:
                active.append("h3_turbo_strength")
    elif family == "wan22":
        mode = str(payload.get("video_lora_mode") or "").strip().casefold().replace("-", "_")
        normal_active = _as_bool(payload.get("enable_video_lora")) or mode not in {"", "off", "none", "disabled", "false"}
        speed_active = _as_bool(payload.get("enable_lightx2v")) or mode in {
            "lightx2v",
            "lightx2v_4step",
            "lightning",
            "lightning_fast",
            "4step",
            "4_step",
        }
        if normal_active:
            for field in ("enable_video_lora", "video_lora_mode", "video_lora_model", "video_lora_strength", "video_lora_target"):
                if field in payload:
                    active.append(field)
        if speed_active:
            for field in (
                "enable_lightx2v",
                "high_noise_lora",
                "low_noise_lora",
                "high_noise_lora_strength",
                "low_noise_lora_strength",
            ):
                if field in payload:
                    active.append(field)
    return serialized, list(dict.fromkeys(active))


def _compat_metadata(
    *,
    family: str,
    route_id: str,
    payload: dict[str, Any],
    runtime: dict[str, Any],
    bridge_key: str,
    authority: dict[str, Any],
) -> dict[str, Any]:
    serialized, active_fields = _legacy_field_state(payload, family)
    bridge = runtime.get(bridge_key) if isinstance(runtime.get(bridge_key), dict) else {}
    active_intent = bool(bridge.get("requested")) or bool(active_fields)
    supported = H3_LEGACY_FIELDS if family == "minimax_h3" else WAN_LEGACY_FIELDS if family == "wan22" else ()
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "family": family,
        "route_id": route_id,
        "status": "compatibility_only_deprecated",
        "legacy_intent_active": active_intent,
        "legacy_fields_supported_for_read": list(supported),
        "legacy_fields_serialized": serialized,
        "legacy_fields_active": active_fields,
        "legacy_field_writeback": False,
        "universal_stack_writeback": True,
        "next_save_action": "persist_video.lora_stack_only",
        "graph_authority": GRAPH_AUTHORITY,
        "graph_authority_verified": bool(authority.get("verified")),
        "declared_lora_node_ids": list(authority.get("declared_lora_node_ids") or []),
        "undeclared_lora_node_ids": [],
        "bridge": deepcopy(bridge),
        "removal_boundary": {
            "legacy_read_support_required": True,
            "legacy_write_support_allowed": False,
            "remove_legacy_fields_now": False,
            "remove_legacy_adapter_now": False,
            "prerequisites": [
                "saved-state writeback persists universal video.lora_stack",
                "migration telemetry/diagnostics show no unresolved legacy-only states",
                "one release boundary retains read compatibility before field removal",
            ],
        },
    }


def _wrap_build(
    module: Any,
    name: str,
    *,
    family: str,
    bridge_key: str,
    payload_getter: Callable[[], dict[str, Any] | None],
    sanitize_wan_snapshot: bool = False,
) -> None:
    original = getattr(module, name)
    if getattr(original, "_neo_phase9_legacy_compat_wrapper", False):
        return

    def wrapped(req: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        compiled = original(req, *args, **kwargs)
        runtime = compiled.get("video_lora_stack") if isinstance(compiled.get("video_lora_stack"), dict) else {}
        workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
        route_id = str(compiled.get("route_id") or "")
        authority = assert_universal_lora_graph_authority(workflow, runtime, family=family)
        payload = payload_getter() or {}
        metadata = _compat_metadata(
            family=family,
            route_id=route_id,
            payload=payload if isinstance(payload, dict) else {},
            runtime=runtime,
            bridge_key=bridge_key,
            authority=authority,
        )
        runtime["legacy_compatibility"] = metadata
        compiled["video_lora_stack"] = runtime
        compiled["legacy_lora_compatibility"] = metadata

        if sanitize_wan_snapshot:
            sanitized = sanitize_legacy_adapter_snapshot(
                compiled.get("video_lora_adapter") if isinstance(compiled.get("video_lora_adapter"), dict) else {}
            )
            compiled["video_lora_adapter"] = sanitized
            if isinstance(runtime.get("legacy_adapter_snapshot"), dict):
                runtime["legacy_adapter_snapshot"] = sanitize_legacy_adapter_snapshot(runtime["legacy_adapter_snapshot"])

        if metadata["legacy_intent_active"]:
            warnings = runtime.get("warnings") if isinstance(runtime.get("warnings"), list) else []
            warnings.append(
                "Legacy Video LoRA controls were read through the Phase 9 compatibility boundary. "
                "They are deprecated for writeback; save future state through video.lora_stack."
            )
            runtime["warnings"] = list(dict.fromkeys(str(item) for item in warnings if str(item)))

        rules = compiled.get("rules") if isinstance(compiled.get("rules"), list) else []
        rules.extend(
            [
                "Phase 9 legacy H3/WAN LoRA fields are read-compatible only; universal video.lora_stack is the only writeback target.",
                "Every LoRA graph node on a hardened H3/WAN route must be declared by the universal runtime; undeclared/legacy graph mutation fails closed.",
            ]
        )
        compiled["rules"] = list(dict.fromkeys(str(rule) for rule in rules if str(rule)))
        return compiled

    wrapped._neo_phase9_legacy_compat_wrapper = True  # type: ignore[attr-defined]
    wrapped._neo_phase9_original = original  # type: ignore[attr-defined]
    setattr(module, name, wrapped)


_INSTALLED = False


def install_video_lora_legacy_compat_hardening() -> None:
    """Install Phase-9 read-compatibility/deprecation hardening after H3/WAN integrations."""
    global _INSTALLED
    if _INSTALLED:
        return

    from neo_app.video import minimax_h3_compiler as h3
    from neo_app.video import minimax_h3_lora_integration as h3_integration
    from neo_app.video import wan_gguf_i2v14_compiler as wan_dual
    from neo_app.video import wan_lora_integration as wan_integration

    h3_integration.merge_h3_legacy_turbo = merge_h3_legacy_turbo_hardened
    wan_integration._merge_legacy_rows = merge_wan_legacy_rows_hardened

    _wrap_build(
        h3,
        "build_minimax_h3_workflow",
        family="minimax_h3",
        bridge_key="legacy_turbo_bridge",
        payload_getter=lambda: h3_integration._PHASE5_PAYLOAD.get(),
    )
    _wrap_build(
        wan_dual,
        "build_wan22_gguf_i2v14_workflow",
        family="wan22",
        bridge_key="legacy_bridge",
        payload_getter=lambda: wan_integration._PHASE8_PAYLOAD.get(),
        sanitize_wan_snapshot=True,
    )
    _INSTALLED = True
