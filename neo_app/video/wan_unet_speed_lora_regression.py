from __future__ import annotations

import json
from typing import Any, Callable

from neo_app.video.wan_lora_regression import (
    SPEED_LORA,
    STANDARD_LORA,
    _assert_single_chain,
    _build_single,
    synthetic_single_object_info,
)
from neo_app.video.wan_unet_speed_lora import is_wan_speed_lora_name, wan_speed_lora_candidates


def run_wan_unet_speed_lora_regression() -> dict[str, Any]:
    info = synthetic_single_object_info()
    cases: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
        except Exception as exc:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            cases.append({"name": name, "ok": True, "details": details})

    record("classifier recognizes WAN LightX2V", lambda: _truth(is_wan_speed_lora_name(SPEED_LORA)))
    record("classifier does not promote standard WAN LoRA", lambda: _truth(not is_wan_speed_lora_name(STANDARD_LORA)))
    record("candidate catalog preserves order", lambda: _equal(wan_speed_lora_candidates([STANDARD_LORA, SPEED_LORA]), [SPEED_LORA]))

    def active(mode: str, rows: list[dict[str, Any]], names: list[str], roles: list[str]) -> dict[str, Any]:
        compiled = _build_single(mode, rows, info)
        _assert_single_chain(compiled, names)
        runtime = compiled.get("video_lora_stack") or {}
        actual_roles = [row.get("role") for row in runtime.get("applied", [])]
        assert actual_roles == roles, (actual_roles, roles)
        assert runtime.get("speed_count") == roles.count("speed")
        assert runtime.get("standard_count") == roles.count("standard")
        assert runtime.get("speed_candidates") == [SPEED_LORA]
        return {"mode": mode, "names": names, "roles": roles, "final_model_ref": runtime.get("final_model_ref")}

    for mode in ("txt2vid", "img2vid"):
        record(
            f"{mode}: LightX2V speed row",
            lambda mode=mode: active(mode, [{"name": SPEED_LORA, "strength_model": 1.0, "role": "speed", "target": "all"}], [SPEED_LORA], ["speed"]),
        )
        record(
            f"{mode}: standard rows precede speed rows",
            lambda mode=mode: active(
                mode,
                [
                    {"name": SPEED_LORA, "strength_model": 1.0, "role": "speed", "target": "all"},
                    {"name": STANDARD_LORA, "strength_model": 0.75, "role": "standard", "target": "all"},
                ],
                [STANDARD_LORA, SPEED_LORA],
                ["standard", "speed"],
            ),
        )

    failed = [case for case in cases if not case["ok"]]
    return {
        "schema_version": "neo.video.wan22.unet_speed_lora_regression.v1",
        "phase": "phase_15",
        "routes": ["wan22.unet.txt2vid", "wan22.unet.img2vid"],
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "gpu_inference_proven": False,
        "cases": cases,
    }


def _truth(value: bool) -> bool:
    assert value
    return value


def _equal(actual: Any, expected: Any) -> dict[str, Any]:
    assert actual == expected, (actual, expected)
    return {"actual": actual}


def main() -> int:
    report = run_wan_unet_speed_lora_regression()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
