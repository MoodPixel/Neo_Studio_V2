from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from neo_app.video.ltx_complete_route_lora_integration import ROUTES, install_ltx_complete_route_lora_integration
from neo_app.video.ltx_route_lora_capability import validate_ltx_route_lora_topology

PRIMARY = ("txt2vid", "img2vid")
EXTENDED = tuple(item[3] for item in ROUTES)
LOADERS = ("unet", "gguf")


def _support_rows() -> dict[str, dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    path = root / "neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: dict[str, dict[str, Any]] = {}
    for group in data.get("groups", []):
        if not isinstance(group, dict):
            continue
        for route_id in group.get("route_ids", []):
            rows[str(route_id)] = group
    return rows


def _info() -> dict[str, Any]:
    return {
        "UNETLoader": {"input": {"required": {"unet_name": [["ltx.safetensors"], {}]}}, "output": ["MODEL"]},
        "UnetLoaderGGUF": {"input": {"required": {"unet_name": [["ltx.gguf"], {}]}}, "output": ["MODEL"]},
        "LoraLoaderModelOnly": {"input": {"required": {"model": ["MODEL", {}], "lora_name": [["ltx_style.safetensors"], {}]}}},
    }


def run_ltx_complete_route_lora_regression() -> dict[str, Any]:
    install_ltx_complete_route_lora_integration()
    support_rows = _support_rows()
    cases: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
        except Exception as exc:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            cases.append({"name": name, "ok": True, "details": details})

    def matrix_case(route_id: str) -> dict[str, Any]:
        support = support_rows[route_id]
        assert support["state"] == "supported"
        assert support["supports_standard_lora"] is True
        assert support["supports_speed_lora"] is False
        assert support["validated_topology"] is True
        return {"route_id": route_id, "loader": support["loader"]}

    for loader in LOADERS:
        for mode in (*PRIMARY, *EXTENDED):
            record(f"matrix: ltx23.{loader}.{mode}", lambda loader=loader, mode=mode: matrix_case(f"ltx23.{loader}.{mode}"))

    def topology(loader: str) -> dict[str, Any]:
        model_loader = "UnetLoaderGGUF" if loader == "gguf" else "UNETLoader"
        return validate_ltx_route_lora_topology(
            f"ltx23.{loader}.img2vid",
            {"classes": {"model_loader": model_loader}},
            _info(),
        )

    record("UNET live ModelOnly topology", lambda: topology("unet"))
    record("GGUF live MODEL-to-ModelOnly topology", lambda: topology("gguf"))

    for native in ("ltx23.native_workflow.txt2vid", "ltx23.native_workflow.img2vid"):
        record(
            f"native remains blocked: {native}",
            lambda native=native: {"route_id": native, "blocked": support_rows[native]["state"] == "blocked"}
            if support_rows[native]["state"] == "blocked"
            else (_ for _ in ()).throw(AssertionError("unanchored native route was promoted")),
        )

    failed = [case for case in cases if not case["ok"]]
    return {
        "schema_version": "neo.video.ltx23.complete_route_lora_regression.v1",
        "phase": "phase_14",
        "compiled_route_count": 18,
        "native_routes_blocked": 2,
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "gpu_inference_proven": False,
        "cases": cases,
    }


def main() -> int:
    report = run_ltx_complete_route_lora_regression()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
