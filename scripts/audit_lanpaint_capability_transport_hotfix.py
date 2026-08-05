from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _check(check_id: str, passed: bool, detail: str) -> dict:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def run_audit() -> dict:
    profiles = (ROOT / "neo_app/providers/profiles.py").read_text(encoding="utf-8")
    main = (ROOT / "neo_app/main.py").read_text(encoding="utf-8")
    overlays = (ROOT / "neo_app/image/capability_overlays.py").read_text(encoding="utf-8")
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")

    checks = [
        _check(
            "connect_probe_discovers_comfy_capabilities",
            "profile_provider.discover_backend_capabilities()" in profiles,
            "Comfy Connect/Test performs live object_info-backed capability discovery.",
        ),
        _check(
            "discovery_is_selected_profile_bound",
            "base_url=base_url" in profiles and "timeout=timeout" in profiles,
            "The discovery adapter uses the selected profile URL and timeout rather than provider defaults.",
        ),
        _check(
            "connect_result_transports_snapshot",
            '"backend_capabilities": backend_capabilities' in profiles and '"capability_source": "selected_profile_object_info"' in profiles,
            "The Connect/Test runtime carries the capability snapshot and provenance.",
        ),
        _check(
            "stale_snapshots_are_not_live",
            'runtime_payload.pop("backend_capabilities", None)' in profiles,
            "Passive profile listings strip saved capability snapshots after disconnect or restart.",
        ),
        _check(
            "overlay_uses_live_task_profile",
            "is_backend_profile_connected_for_task(profile_id)" in main and "get_backend_profile_for_live_task(profile_id) or profile" in main,
            "The Image overlay resolves only the explicitly connected selected profile.",
        ),
        _check(
            "comfy_overlay_transports_snapshot",
            '"backend_capabilities": backend_capabilities' in overlays and '"capability_snapshot_ready": snapshot_ready' in overlays,
            "The Comfy Image overlay exposes backend capabilities and readiness.",
        ),
        _check(
            "frontend_falls_back_to_selected_overlay",
            "const overlay = imageCapabilityOverlayForProfile(profile);" in js and "overlay?.backend_capabilities || overlay?.capabilities?.backend_capabilities" in js,
            "LanPaint gating reads the selected profile overlay when the profile object has not yet been refreshed.",
        ),
        _check(
            "no_lora_bypass_added",
            "force_lanpaint_lora" not in js.lower() and "bypass_lanpaint" not in js.lower(),
            "LoRA remains engine- and capability-aware; the fix does not force-enable unsupported routes.",
        ),
        _check(
            "cache_revision_advanced",
            "hotfix=lanpaint_lora_independence_20260804" in index and any(marker in js for marker in ("lanpaint_lora_independence_hotfix_20260804", "global_lora_engine_decoupling_20260805", "lanpaint_family_adapter_v2_20260805", "lanpaint_route_parity_phase14_20260805", "lanpaint_sd_family_phase15_20260805", "lanpaint_flux1_family_phase16_20260805", "lanpaint_flux2_family_phase17_20260805")),
            "HTML and JavaScript use the capability-transport hotfix cache identity.",
        ),
        _check(
            "public_path_hygiene",
            re.search(r"(?:[A-Za-z]:[\\/](?:Users|Documents)[\\/]|/(?:home|mnt)/[^/\s]+/)", "\n".join((profiles, main, overlays, js, index)), re.IGNORECASE) is None,
            "The implementation contains no personal or packaging-environment paths.",
        ),
    ]
    return {
        "schema_id": "neo.validation.lanpaint_capability_transport_hotfix.v1",
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "passed": sum(1 for item in checks if item["passed"]),
        "total": len(checks),
        "checks": checks,
        "physical_validation": "not_run",
    }


def main() -> int:
    report = run_audit()
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
