from __future__ import annotations

import json
from typing import Any, Callable
from neo_app.video.video_lora_legacy_retirement import LEGACY_FIELDS, assert_no_legacy_writeback, retire_legacy_payload


def run_phase20_gate() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    def record(name: str, fn: Callable[[], None]) -> None:
        try: fn(); cases.append({"name": name, "ok": True})
        except Exception as exc: cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    def check(condition: bool) -> None:
        if not condition: raise AssertionError("contract failed")
    h3 = {"h3_turbo_enabled": True, "h3_turbo_lora": "h3_turbo.safetensors", "h3_turbo_strength": .7}
    wan = {"enable_video_lora": True, "video_lora_model": "wan_style.safetensors", "video_lora_strength": .8, "video_lora_target": "both"}
    speed = {"enable_lightx2v": True, "high_noise_lora": "high.safetensors", "low_noise_lora": "low.safetensors"}
    record("H3 Turbo migrates to speed row", lambda: check(retire_legacy_payload(h3)[0]["video_lora_stack"]["rows"][0]["role"] == "speed"))
    record("WAN standard migrates to all", lambda: check(retire_legacy_payload(wan)[0]["video_lora_stack"]["rows"][0]["target"] == "all"))
    record("WAN high and low migrate separately", lambda: check([r["target"] for r in retire_legacy_payload(speed)[0]["video_lora_stack"]["rows"]] == ["high", "low"]))
    record("legacy keys removed", lambda: check(not any(k in retire_legacy_payload({**h3, **wan, **speed})[0] for k in LEGACY_FIELDS)))
    record("canonical rows take precedence", lambda: check(retire_legacy_payload({**wan, "video_lora_stack": {"enabled": True, "rows": [{"uid":"c","name":"canonical.safetensors","target":"all"}]}})[0]["video_lora_stack"]["rows"][0]["uid"] == "c"))
    record("canonical conflict is reported", lambda: check(retire_legacy_payload({**wan, "video_lora_stack": {"enabled": True, "rows": [{"uid":"c","name":"canonical.safetensors"}]}})[1]["canonical_precedence"]))
    record("disabled legacy fields add no rows", lambda: check("video_lora_stack" not in retire_legacy_payload({"h3_turbo_enabled": False})[0]))
    record("missing H3 filename remains unresolved", lambda: check(retire_legacy_payload({"h3_turbo_enabled": True})[1]["unresolved"][0]["code"] == "legacy_h3_file_missing"))
    record("missing WAN filename remains unresolved", lambda: check(retire_legacy_payload({"enable_video_lora": True})[1]["unresolved"][0]["code"] == "legacy_wan_file_missing"))
    record("missing speed files remain unresolved", lambda: check(retire_legacy_payload({"enable_lightx2v": True})[1]["unresolved"][0]["code"] == "legacy_wan_speed_files_missing"))
    record("row ceiling reports overflow", lambda: check(retire_legacy_payload(speed, max_rows=1)[1]["overflow_count"] == 1))
    record("migration marker is persisted canonically", lambda: check(retire_legacy_payload(h3)[0]["video_lora_stack"]["migration"]["status"] == "retired"))
    record("safe write report passes", lambda: check(retire_legacy_payload({**h3, **wan})[1]["safe_to_persist"]))
    record("legacy write assertion rejects", lambda: _expect_reject(h3))
    record("canonical-only write assertion passes", lambda: assert_no_legacy_writeback({"video_lora_stack": {"rows": []}}))
    failed = sum(not item["ok"] for item in cases)
    return {"schema_version":"neo.video.lora_stack.legacy_retirement_regression.v1","phase":"phase_20","ok":failed == 0,"gate":"pass" if failed == 0 else "fail","case_count":len(cases),"passed":len(cases)-failed,"failed":failed,"cases":cases}


def _expect_reject(payload: dict[str, Any]) -> None:
    try: assert_no_legacy_writeback(payload)
    except ValueError: return
    raise AssertionError("legacy writeback was accepted")


if __name__ == "__main__":
    report=run_phase20_gate(); print(json.dumps(report, indent=2)); raise SystemExit(0 if report["ok"] else 1)
