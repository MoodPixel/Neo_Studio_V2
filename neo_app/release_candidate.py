from __future__ import annotations

import ast
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
NEO_DATA_DIR = ROOT_DIR / "neo_data"
RC_DIR = NEO_DATA_DIR / "release_candidate"
RC_MANIFEST_PATH = RC_DIR / "phase45_release_candidate_manifest.json"

PHASE_RANGE = list(range(37, 45))
CORE_MODULES = [
    "neo_app/main.py",
    "neo_app/project_workspace.py",
    "neo_app/memory_project_qa.py",
    "neo_app/runtime_hardening.py",
    "neo_app/assistant/project_manager.py",
    "neo_app/roleplay/project_linking.py",
]
CORE_UI_FILES = [
    "neo_app/static/js/neo.js",
    "neo_app/static/css/neo.css",
]
CORE_TEST_FILES = [
    "tests/test_phase37_project_package_builder.py",
    "tests/test_phase38_cross_surface_project_actions.py",
    "tests/test_phase39_assistant_project_manager.py",
    "tests/test_phase40_roleplay_project_linking.py",
    "tests/test_phase41_memory_project_qa_regression.py",
    "tests/test_phase42_ui_modernization_sweep.py",
    "tests/test_phase43_admin_control_center_final_polish.py",
    "tests/test_phase44_packaging_runtime_hardening.py",
]
DOC_FILES = [
    "neo_system_records/05_MEMORY_SYSTEM/V2_MEMORY_ARCHITECTURE.md",
    "neo_system_records/07_CHANGELOG/V2_CHANGELOG.md",
    "neo_system_records/08_ADMIN/ADMIN_CONTROL_CENTER_FINAL_POLISH.md",
    "neo_system_records/09_VALIDATION/PACKAGING_RUNTIME_HARDENING.md",
]
REQUIRED_ROUTE_MARKERS = {
    "/api/projects/workspace/package-builder": "Phase 37 package builder preflight",
    "/api/projects/workspace/package/build": "Phase 37 package build",
    "/api/projects/workspace/surface-action": "Phase 38 cross-surface action",
    "/api/assistant/project-manager": "Phase 39 Assistant Project Manager",
    "/api/roleplay/project-link": "Phase 40 Roleplay project linking",
    "/api/projects/workspace/qa": "Phase 41 Memory/Project QA",
    "/api/runtime/hardening": "Phase 44 runtime hardening",
    "/api/release-candidate": "Phase 45 release candidate audit",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR.resolve())).replace("\\", "/")
    except Exception:
        return str(path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _file_check(rel_path: str, *, required: bool = True) -> dict[str, Any]:
    path = ROOT_DIR / rel_path
    exists = path.exists()
    status = "ready" if exists else ("missing" if required else "optional missing")
    return {
        "id": rel_path.replace("/", "_").replace(".", "_"),
        "label": rel_path,
        "status": status,
        "required": required,
        "path": rel_path,
        "size_bytes": path.stat().st_size if exists and path.is_file() else 0,
    }


def _python_compile_check(rel_path: str) -> dict[str, Any]:
    path = ROOT_DIR / rel_path
    if not path.exists():
        return {"id": f"compile_{rel_path}", "label": rel_path, "status": "missing", "detail": "file missing"}
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        return {"id": f"compile_{rel_path}", "label": rel_path, "status": "ready", "detail": "python syntax ok"}
    except SyntaxError as exc:
        return {"id": f"compile_{rel_path}", "label": rel_path, "status": "failed", "detail": f"{exc.msg} line {exc.lineno}"}
    except Exception as exc:
        return {"id": f"compile_{rel_path}", "label": rel_path, "status": "failed", "detail": str(exc)}


def _node_check_js(rel_path: str) -> dict[str, Any]:
    path = ROOT_DIR / rel_path
    if not path.exists():
        return {"id": f"node_check_{rel_path}", "label": rel_path, "status": "missing", "detail": "file missing"}
    node = subprocess.run(["node", "--check", str(path)], cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=20)
    if node.returncode == 0:
        return {"id": f"node_check_{rel_path}", "label": rel_path, "status": "ready", "detail": "node --check ok"}
    detail = (node.stderr or node.stdout or "node --check failed").strip().splitlines()[:4]
    return {"id": f"node_check_{rel_path}", "label": rel_path, "status": "failed", "detail": " | ".join(detail)}


def _route_marker_checks() -> list[dict[str, Any]]:
    main_text = _read_text(ROOT_DIR / "neo_app/main.py")
    return [
        {
            "id": f"route_{marker.strip('/').replace('/', '_').replace('-', '_')}",
            "label": label,
            "status": "ready" if marker in main_text else "missing",
            "detail": marker,
        }
        for marker, label in REQUIRED_ROUTE_MARKERS.items()
    ]


def _doc_phase_checks() -> list[dict[str, Any]]:
    changelog = _read_text(ROOT_DIR / "neo_system_records/07_CHANGELOG/V2_CHANGELOG.md")
    checks: list[dict[str, Any]] = []
    for phase in range(37, 46):
        marker = f"Phase {phase}"
        checks.append({
            "id": f"changelog_phase_{phase}",
            "label": f"Changelog marker {marker}",
            "status": "ready" if marker in changelog else "missing",
            "detail": marker,
        })
    return checks


def _phase_inventory() -> list[dict[str, Any]]:
    return [
        {"phase": 37, "name": "Project Package Builder", "status": "implemented", "test": "tests/test_phase37_project_package_builder.py"},
        {"phase": 38, "name": "Cross-Surface Project Actions", "status": "implemented", "test": "tests/test_phase38_cross_surface_project_actions.py"},
        {"phase": 39, "name": "Assistant Project Manager Mode", "status": "implemented", "test": "tests/test_phase39_assistant_project_manager.py"},
        {"phase": 40, "name": "Roleplay Project Linking Polish", "status": "implemented", "test": "tests/test_phase40_roleplay_project_linking.py"},
        {"phase": 41, "name": "Memory/Project QA + Regression Repair", "status": "implemented", "test": "tests/test_phase41_memory_project_qa_regression.py"},
        {"phase": 42, "name": "UI Modernization Sweep", "status": "implemented", "test": "tests/test_phase42_ui_modernization_sweep.py"},
        {"phase": 43, "name": "Admin Control Center Final Polish", "status": "implemented", "test": "tests/test_phase43_admin_control_center_final_polish.py"},
        {"phase": 44, "name": "Packaging/Runtime Hardening", "status": "implemented", "test": "tests/test_phase44_packaging_runtime_hardening.py"},
        {"phase": 45, "name": "Full System Audit + Release Candidate", "status": "implemented", "test": "tests/test_phase45_full_system_audit_release_candidate.py"},
    ]


def release_candidate_payload(*, include_syntax_checks: bool = True) -> dict[str, Any]:
    file_checks = [_file_check(path) for path in [*CORE_MODULES, *CORE_UI_FILES, *CORE_TEST_FILES, *DOC_FILES]]
    syntax_checks: list[dict[str, Any]] = []
    if include_syntax_checks:
        syntax_checks.extend(_python_compile_check(path) for path in CORE_MODULES)
        syntax_checks.append(_node_check_js("neo_app/static/js/neo.js"))
    route_checks = _route_marker_checks()
    doc_checks = _doc_phase_checks()
    all_checks = [*file_checks, *syntax_checks, *route_checks, *doc_checks]
    failing = [item for item in all_checks if str(item.get("status") or "").lower() in {"missing", "failed", "blocked"}]
    warnings = [item for item in all_checks if str(item.get("status") or "").lower() in {"warning", "optional missing"}]
    status = "release-candidate" if not failing else "needs attention"
    return {
        "schema_id": "neo.release_candidate.audit.v1",
        "phase": 45,
        "status": status,
        "generated_at": _now(),
        "platform": {"python": platform.python_version(), "system": platform.system(), "machine": platform.machine()},
        "summary": {
            "implemented_phase_count": 9,
            "phase_range": "37-45",
            "check_count": len(all_checks),
            "failure_count": len(failing),
            "warning_count": len(warnings),
            "release_candidate_ready": not failing,
        },
        "phase_inventory": _phase_inventory(),
        "checks": all_checks,
        "failures": failing[:20],
        "warnings": warnings[:20],
        "validation_scope": {
            "syntax_checks": include_syntax_checks,
            "targeted_tests": CORE_TEST_FILES + ["tests/test_phase45_full_system_audit_release_candidate.py"],
            "full_suite_note": "Phase 45 records release-candidate readiness from route, file, doc, and syntax checks. Run full pytest locally for final machine-specific confirmation if needed.",
        },
        "artifacts": {
            "manifest_path": _rel(RC_MANIFEST_PATH),
            "release_record": "neo_system_records/09_VALIDATION/FULL_SYSTEM_AUDIT_RELEASE_CANDIDATE.md",
            "changelog": "neo_system_records/07_CHANGELOG/V2_CHANGELOG.md",
        },
        "next_recommendation": "Freeze feature work temporarily and use this branch as the release-candidate baseline before opening a new feature arc.",
    }


def write_release_candidate_manifest(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    audit = payload or release_candidate_payload(include_syntax_checks=True)
    RC_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_id": "neo.release_candidate.manifest.v1",
        "phase": 45,
        "written_at": _now(),
        "audit_status": audit.get("status"),
        "summary": audit.get("summary", {}),
        "phase_inventory": audit.get("phase_inventory", []),
        "validation_scope": audit.get("validation_scope", {}),
        "failures": audit.get("failures", []),
        "warnings": audit.get("warnings", []),
    }
    tmp = RC_MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RC_MANIFEST_PATH)
    return {"ok": True, "schema_id": "neo.release_candidate.manifest.write.v1", "manifest_path": _rel(RC_MANIFEST_PATH), "manifest": manifest, "release_candidate": audit}
