from __future__ import annotations

import json
from typing import Any, Callable

from neo_app.video import minimax_h3_lora_regression as h3reg
from neo_app.video import video_lora_legacy_compat as compat
from neo_app.video import wan_lora_regression as wanreg
from neo_app.video.video_lora_runtime import normalize_video_lora_rows

SCHEMA_VERSION = "neo.video.lora_legacy_compat.regression.v1"
PHASE = "phase_9"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _expect_error(fn: Callable[[], Any], contains: str) -> str:
    try:
        fn()
    except ValueError as exc:
        text = str(exc)
        _assert(contains.casefold() in text.casefold(), f"Expected {contains!r} in {text!r}")
        return text
    raise AssertionError(f"Expected ValueError containing {contains!r}")


def _row(rows: list[dict[str, Any]], target: str) -> dict[str, Any]:
    matches = [item for item in rows if item.get("target") == target]
    _assert(len(matches) == 1, f"Expected one {target} row, found {len(matches)}: {rows}")
    return matches[0]


def _case_wan_high_speed_split() -> dict[str, Any]:
    universal = [{"uid": "universal_all", "name": wanreg.LEGACY_HIGH, "strength_model": 0.72, "role": "standard", "target": "all"}]
    legacy = [{"uid": "legacy_high", "name": wanreg.LEGACY_HIGH, "strength_model": 1.0, "role": "speed", "target": "high"}]
    rows, meta = compat.merge_wan_legacy_rows_hardened(universal, legacy)
    high = _row(rows, "high")
    low = _row(rows, "low")
    _assert(high["role"] == "speed", "High branch was not promoted to speed")
    _assert(low["role"] == "standard", "Low branch was incorrectly promoted by high-only legacy intent")
    _assert(float(high["strength_model"]) == 0.72 and float(low["strength_model"]) == 0.72, "Universal strength did not win")
    _assert(high["uid"] == "universal_all", "Original universal uid was not preserved on deterministic high split")
    _assert(meta["existing_row_split"] == 1 and meta["existing_role_promoted"] == 1, "Split/promotion metadata mismatch")
    _assert(meta["strength_conflict_suppressed"] == 1, "Legacy strength conflict was not recorded")
    return {"rows": rows, "meta": meta}


def _case_wan_low_speed_split() -> dict[str, Any]:
    universal = [{"uid": "universal_all", "name": wanreg.LEGACY_LOW, "strength_model": 0.64, "role": "standard", "target": "all"}]
    legacy = [{"uid": "legacy_low", "name": wanreg.LEGACY_LOW, "strength_model": 1.0, "role": "speed", "target": "low"}]
    rows, meta = compat.merge_wan_legacy_rows_hardened(universal, legacy)
    high = _row(rows, "high")
    low = _row(rows, "low")
    _assert(high["role"] == "standard", "High branch was incorrectly promoted by low-only legacy intent")
    _assert(low["role"] == "speed", "Low branch was not promoted to speed")
    _assert(float(low["strength_model"]) == 0.64, "Universal low strength was not preserved after split")
    _assert(meta["existing_row_split"] == 1 and meta["existing_role_promoted"] == 1, "Low split metadata mismatch")
    return {"rows": rows, "meta": meta}


def _case_wan_all_speed_exact() -> dict[str, Any]:
    universal = [{"uid": "universal_all", "name": wanreg.SPEED_LORA, "strength_model": 0.83, "role": "standard", "target": "all"}]
    legacy = [{"uid": "legacy_all", "name": wanreg.SPEED_LORA, "strength_model": 1.0, "role": "speed", "target": "all"}]
    rows, meta = compat.merge_wan_legacy_rows_hardened(universal, legacy)
    _assert(len(rows) == 2, f"Expected branch-exact split rows, got {rows}")
    _assert(_row(rows, "high")["role"] == "speed" and _row(rows, "low")["role"] == "speed", "Both branches were not promoted")
    _assert(all(float(item["strength_model"]) == 0.83 for item in rows), "Universal all strength was not retained")
    _assert(meta["existing_row_split"] == 1 and meta["existing_role_promoted"] == 2, "All-target promotion metadata mismatch")
    return {"rows": rows, "meta": meta}


def _case_wan_partial_fill() -> dict[str, Any]:
    universal = [{"uid": "universal_high", "name": wanreg.STANDARD_LORA, "strength_model": 0.55, "role": "standard", "target": "high"}]
    legacy = [{"uid": "legacy_all", "name": wanreg.STANDARD_LORA, "strength_model": 0.9, "role": "standard", "target": "all"}]
    rows, meta = compat.merge_wan_legacy_rows_hardened(universal, legacy)
    high = _row(rows, "high")
    low = _row(rows, "low")
    _assert(float(high["strength_model"]) == 0.55, "Universal covered branch strength was overwritten")
    _assert(float(low["strength_model"]) == 0.9, "Legacy missing branch strength was not bridged")
    _assert(meta["bridged_count"] == 1 and meta["duplicate_branch_suppressed"] == 1, "Partial coverage merge metadata mismatch")
    _assert(meta["strength_conflict_suppressed"] == 1, "Covered branch strength conflict was not reported")
    return {"rows": rows, "meta": meta}


def _case_wan_deterministic() -> dict[str, Any]:
    universal = [
        {"uid": "u1", "name": wanreg.LEGACY_HIGH, "strength_model": 0.7, "role": "standard", "target": "all"},
        {"uid": "u2", "name": wanreg.STANDARD_LORA_2, "strength_model": 0.5, "role": "standard", "target": "low"},
    ]
    legacy = [
        {"uid": "l1", "name": wanreg.LEGACY_HIGH, "strength_model": 1.0, "role": "speed", "target": "high"},
        {"uid": "l2", "name": wanreg.LEGACY_LOW, "strength_model": 1.0, "role": "speed", "target": "low"},
    ]
    first = compat.merge_wan_legacy_rows_hardened(universal, legacy)
    second = compat.merge_wan_legacy_rows_hardened(universal, legacy)
    _assert(json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True), "WAN legacy merge is not deterministic")
    return {"rows": first[0], "meta": first[1]}


def _case_wan_uid_collision() -> dict[str, Any]:
    universal = [
        {"uid": "u1", "name": wanreg.LEGACY_HIGH, "strength_model": 0.7, "role": "standard", "target": "all"},
        {"uid": "u1__low", "name": wanreg.STANDARD_LORA_2, "strength_model": 0.4, "role": "standard", "target": "high"},
    ]
    legacy = [{"uid": "legacy", "name": wanreg.LEGACY_HIGH, "strength_model": 1.0, "role": "speed", "target": "high"}]
    rows, _ = compat.merge_wan_legacy_rows_hardened(universal, legacy)
    split_low = [item for item in rows if item["name"] == wanreg.LEGACY_HIGH and item["target"] == "low"][0]
    _assert(split_low["uid"] == "u1__low_2", f"Derived split uid did not avoid collision: {split_low['uid']}")
    return {"derived_uid": split_low["uid"]}


def _case_wan_max_split_fails() -> str:
    universal = [{"uid": "root", "name": wanreg.LEGACY_HIGH, "strength_model": 0.7, "role": "standard", "target": "all"}]
    universal.extend(
        {"uid": f"u{index}", "name": f"filler_{index}.safetensors", "strength_model": 0.5, "role": "standard", "target": "high"}
        for index in range(1, 12)
    )
    legacy = [{"name": wanreg.LEGACY_HIGH, "strength_model": 1.0, "role": "speed", "target": "high"}]
    return _expect_error(lambda: compat.merge_wan_legacy_rows_hardened(universal, legacy), "would exceed")


def _case_h3_universal_precedence() -> dict[str, Any]:
    rows = [{"uid": "h3_universal", "name": h3reg.SPEED_LORA, "strength_model": 0.91, "role": "standard", "target": "high"}]
    merged, meta = compat.merge_h3_legacy_turbo_hardened(
        rows,
        enabled=True,
        selected_name=h3reg.SPEED_LORA,
        discovered_candidates=[h3reg.SPEED_LORA],
        strength=1.0,
    )
    _assert(len(merged) == 1, "H3 duplicate legacy Turbo was not suppressed")
    _assert(merged[0]["uid"] == "h3_universal", "H3 universal uid was not preserved")
    _assert(float(merged[0]["strength_model"]) == 0.91, "H3 universal strength was not preserved")
    _assert(merged[0]["role"] == "speed" and merged[0]["target"] == "all", "H3 legacy semantics were not normalized")
    _assert(meta["strength_conflict_suppressed"] == 1, "H3 strength conflict was not reported")
    _assert(meta["legacy_control_status"] == "compatibility_only_deprecated", "H3 deprecation status missing")
    return {"rows": merged, "meta": meta}


def _case_h3_disabled_unchanged() -> dict[str, Any]:
    rows = [{"uid": "h3_standard", "name": h3reg.STANDARD_LORA, "strength_model": 0.77, "role": "standard", "target": "all"}]
    merged, meta = compat.merge_h3_legacy_turbo_hardened(rows, enabled=False)
    expected = normalize_video_lora_rows(rows)
    _assert(merged == expected, "Disabled H3 legacy bridge changed canonical universal state")
    _assert(not meta["requested"], "Disabled H3 bridge reported requested")
    return {"rows": merged, "meta": meta}


def _h3_compiled(*, legacy: bool) -> dict[str, Any]:
    info = h3reg.synthetic_h3_object_info()
    req = h3reg._request("img2vid", turbo=legacy, turbo_name=h3reg.SPEED_LORA if legacy else "")
    rows = [] if legacy else [{"uid": "h3_ui", "name": h3reg.STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}]
    return h3reg._build_with_rows(req, rows, info)


def _wan_compiled(*, legacy: str | None) -> dict[str, Any]:
    info = wanreg.synthetic_dual_object_info()
    if legacy == "normal":
        return wanreg._build_dual(
            [],
            info,
            enable_video_lora=True,
            video_lora_mode="normal",
            video_lora_model=wanreg.STANDARD_LORA,
            video_lora_strength=0.7,
            video_lora_target="both",
        )
    if legacy == "speed":
        return wanreg._build_dual(
            [],
            info,
            enable_lightx2v=True,
            high_noise_lora=wanreg.LEGACY_HIGH,
            low_noise_lora=wanreg.LEGACY_LOW,
        )
    return wanreg._build_dual(
        [{"uid": "wan_ui", "name": wanreg.STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}],
        info,
    )


def _assert_compat_metadata(compiled: dict[str, Any], *, family: str, active: bool) -> dict[str, Any]:
    meta = compiled.get("legacy_lora_compatibility") or {}
    runtime_meta = (compiled.get("video_lora_stack") or {}).get("legacy_compatibility") or {}
    _assert(meta == runtime_meta, "Top-level/runtime compatibility metadata diverged")
    _assert(meta.get("schema_version") == compat.SCHEMA_VERSION and meta.get("phase") == compat.PHASE, "Compatibility schema/phase mismatch")
    _assert(meta.get("family") == family, "Compatibility family mismatch")
    _assert(bool(meta.get("legacy_intent_active")) is active, f"Legacy active state mismatch: {meta}")
    _assert(meta.get("legacy_field_writeback") is False and meta.get("universal_stack_writeback") is True, "Writeback boundary mismatch")
    _assert(meta.get("next_save_action") == "persist_video.lora_stack_only", "Next-save migration action missing")
    _assert(meta.get("graph_authority_verified") is True and not meta.get("undeclared_lora_node_ids"), "Graph authority was not verified")
    boundary = meta.get("removal_boundary") or {}
    _assert(boundary.get("legacy_read_support_required") is True, "Legacy read compatibility was removed too early")
    _assert(boundary.get("legacy_write_support_allowed") is False, "Legacy writeback remains allowed")
    _assert(boundary.get("remove_legacy_fields_now") is False and boundary.get("remove_legacy_adapter_now") is False, "Deprecation boundary permits premature removal")
    return meta


def _case_h3_metadata_active() -> dict[str, Any]:
    compiled = _h3_compiled(legacy=True)
    return _assert_compat_metadata(compiled, family="minimax_h3", active=True)


def _case_h3_metadata_universal_only() -> dict[str, Any]:
    compiled = _h3_compiled(legacy=False)
    return _assert_compat_metadata(compiled, family="minimax_h3", active=False)


def _case_wan_metadata_normal() -> dict[str, Any]:
    compiled = _wan_compiled(legacy="normal")
    return _assert_compat_metadata(compiled, family="wan22", active=True)


def _case_wan_metadata_speed() -> dict[str, Any]:
    compiled = _wan_compiled(legacy="speed")
    return _assert_compat_metadata(compiled, family="wan22", active=True)


def _case_wan_metadata_universal_only() -> dict[str, Any]:
    compiled = _wan_compiled(legacy=None)
    return _assert_compat_metadata(compiled, family="wan22", active=False)


def _case_wan_snapshot_sanitized() -> dict[str, Any]:
    compiled = _wan_compiled(legacy="speed")
    snapshot = compiled.get("video_lora_adapter") or {}
    text = json.dumps(snapshot, sort_keys=True)
    _assert(snapshot.get("deprecated") is True and snapshot.get("compatibility_only") is True, "WAN adapter snapshot is not marked deprecated")
    _assert(snapshot.get("graph_mutation_authority") == "none", "WAN legacy snapshot still claims graph authority")
    for forbidden in ("129:101", "129:102", "9001", "9002", "source_model_link", "output_model_link", '"node_id"'):
        _assert(forbidden not in text, f"WAN legacy graph detail leaked through sanitized snapshot: {forbidden}")
    runtime_snapshot = (compiled.get("video_lora_stack") or {}).get("legacy_adapter_snapshot") or {}
    runtime_text = json.dumps(runtime_snapshot, sort_keys=True)
    _assert("129:101" not in runtime_text and '"node_id"' not in runtime_text, "Runtime legacy adapter snapshot was not sanitized")
    return {"snapshot": snapshot, "runtime_snapshot": runtime_snapshot}


def _case_graph_accepts_h3() -> dict[str, Any]:
    compiled = _h3_compiled(legacy=True)
    return compat.assert_universal_lora_graph_authority(compiled["workflow"], compiled["video_lora_stack"], family="minimax_h3")


def _case_graph_accepts_wan() -> dict[str, Any]:
    compiled = _wan_compiled(legacy="normal")
    return compat.assert_universal_lora_graph_authority(compiled["workflow"], compiled["video_lora_stack"], family="wan22")


def _case_graph_rejects_undeclared() -> str:
    workflow = {"77": {"class_type": "LoraLoaderModelOnly", "inputs": {}}}
    return _expect_error(lambda: compat.assert_universal_lora_graph_authority(workflow, {"applied": []}, family="wan22"), "outside the universal")


def _case_graph_rejects_historical_wan() -> str:
    workflow = {"9001": {"class_type": "LoraLoaderModelOnly", "inputs": {}}}
    return _expect_error(lambda: compat.assert_universal_lora_graph_authority(workflow, {"applied": []}, family="wan22"), "9001")


def _case_graph_rejects_generic_loader() -> str:
    workflow = {"88": {"class_type": "LoraLoader", "inputs": {}}}
    return _expect_error(lambda: compat.assert_universal_lora_graph_authority(workflow, {"applied": []}, family="minimax_h3"), "outside the universal")


def _case_declared_node_contract() -> dict[str, Any]:
    workflow = {
        "10": {"class_type": "LoraLoaderModelOnly", "inputs": {}},
        "11": {"class_type": "LoraLoaderModelOnly", "inputs": {}},
    }
    runtime = {"applied": [{"node_ids": {"high": "10", "low": "11"}}]}
    result = compat.assert_universal_lora_graph_authority(workflow, runtime, family="wan22")
    _assert(result["declared_lora_node_ids"] == ["10", "11"], "Declared branch node ids were not recognized")
    return result


def run_phase9_gate() -> dict[str, Any]:
    compat.install_video_lora_legacy_compat_hardening()
    cases: list[dict[str, Any]] = []

    def run(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
            cases.append({"name": name, "ok": True, "details": details})
        except Exception as exc:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "error": f"{exc.__class__.__name__}: {exc}"})

    run("WAN all-row + legacy high speed is branch-exact", _case_wan_high_speed_split)
    run("WAN all-row + legacy low speed is branch-exact", _case_wan_low_speed_split)
    run("WAN all-row + legacy all speed promotes both exact branches", _case_wan_all_speed_exact)
    run("WAN legacy all fills only missing branch and preserves universal strength", _case_wan_partial_fill)
    run("WAN mixed legacy/universal merge is deterministic", _case_wan_deterministic)
    run("WAN split uid derivation avoids collision", _case_wan_uid_collision)
    run("WAN branch-exact split fails closed at max stack size", _case_wan_max_split_fails)
    run("H3 duplicate legacy Turbo preserves universal uid/strength", _case_h3_universal_precedence)
    run("H3 disabled legacy bridge leaves universal state unchanged", _case_h3_disabled_unchanged)
    run("H3 active legacy state emits deprecation/writeback metadata", _case_h3_metadata_active)
    run("H3 universal-only state keeps legacy compatibility inactive", _case_h3_metadata_universal_only)
    run("WAN legacy Normal emits deprecation/writeback metadata", _case_wan_metadata_normal)
    run("WAN legacy LightX2V emits deprecation/writeback metadata", _case_wan_metadata_speed)
    run("WAN universal-only state keeps legacy compatibility inactive", _case_wan_metadata_universal_only)
    run("WAN legacy adapter diagnostics remove graph links/node ids", _case_wan_snapshot_sanitized)
    run("Graph authority accepts declared H3 universal nodes", _case_graph_accepts_h3)
    run("Graph authority accepts declared WAN universal nodes", _case_graph_accepts_wan)
    run("Graph authority rejects undeclared ModelOnly node", _case_graph_rejects_undeclared)
    run("Graph authority rejects historical WAN hardcoded node", _case_graph_rejects_historical_wan)
    run("Graph authority rejects undeclared generic LoraLoader", _case_graph_rejects_generic_loader)
    run("Graph authority recognizes declared dual-branch node map", _case_declared_node_contract)

    failed = [case for case in cases if not case.get("ok")]
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "families": ["minimax_h3", "wan22"],
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "cases": cases,
        "previous_gate_case_count": 90,
        "combined_case_count": 90 + len(cases),
        "next_phase_allowed": not failed,
        "run_command": "python -m neo_app.video.video_lora_legacy_compat_regression",
    }


def main() -> int:
    result = run_phase9_gate()
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
