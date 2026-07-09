from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.surfaces.blueprint import BLUEPRINT_AREAS, surface_blueprint_payload
from neo_app.surfaces.registry import list_surfaces

ROOT_DIR = Path(__file__).resolve().parents[2]
NEO_APP_DIR = ROOT_DIR / "neo_app"
STATIC_JS = NEO_APP_DIR / "static" / "js" / "neo.js"
MAIN_PY = NEO_APP_DIR / "main.py"
FRONTEND_MODULE_ROOT = NEO_APP_DIR / "static" / "js" / "surfaces"

MODULAR_ARCHITECTURE_SCHEMA_ID = "neo.surface.module_architecture.v1"

SURFACE_BACKEND_DIRS: dict[str, list[str]] = {
    "admin": ["admin"],
    "assistant": ["assistant"],
    "image": ["image"],
    "prompt_captioning": ["prompt_captioning"],
    "roleplay": ["roleplay"],
    "voice": ["voice"],
    "video": ["video"],
    "music": ["music"],
    "board": ["surfaces"],
}

SURFACE_FRONTEND_MODULES: dict[str, list[str]] = {
    "admin": ["admin.js"],
    "assistant": ["assistant.js"],
    "image": ["image.js"],
    "prompt_captioning": ["prompt_captioning.js"],
    "roleplay": ["roleplay.js"],
    "voice": ["voice.js"],
    "video": ["video.js"],
    "music": ["music.js"],
    "board": ["board.js"],
}

CANONICAL_BACKEND_AREAS = [
    "routes",
    "service",
    "storage",
    "memory_ingestion",
    "control_center",
    "observability",
    "tests_scripts",
]

CANONICAL_FRONTEND_AREAS = [
    "state",
    "render",
    "actions",
    "api_client",
    "components",
    "diagnostics",
]


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _line_count(path: Path) -> int:
    text = _safe_read_text(path)
    return text.count("\n") + (1 if text else 0)


def _python_route_count(path: Path) -> int:
    text = _safe_read_text(path)
    if not text:
        return 0
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return len([line for line in text.splitlines() if line.strip().startswith("@app.")])
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    value = decorator.func.value
                    if isinstance(value, ast.Name) and value.id == "app":
                        count += 1
    return count


def _count_files(paths: list[Path]) -> dict[str, Any]:
    files: list[Path] = []
    for path in paths:
        if path.exists() and path.is_dir():
            files.extend([item for item in path.rglob("*.py") if item.is_file()])
        elif path.exists() and path.is_file():
            files.append(path)
    return {
        "file_count": len(files),
        "python_line_count": sum(_line_count(item) for item in files),
        "files": [str(item.relative_to(ROOT_DIR)) for item in files[:40]],
    }


def _surface_backend_paths(surface_id: str) -> list[Path]:
    return [NEO_APP_DIR / part for part in SURFACE_BACKEND_DIRS.get(surface_id, [surface_id])]


def _surface_frontend_paths(surface_id: str) -> list[Path]:
    return [FRONTEND_MODULE_ROOT / name for name in SURFACE_FRONTEND_MODULES.get(surface_id, [f"{surface_id}.js"])]


def _status_from_ratio(value: int, threshold: int) -> str:
    if value <= threshold:
        return "ready"
    if value <= threshold * 2:
        return "needs_modularization"
    return "monolith_risk"


def modular_surface_contract(surface_id: str) -> dict[str, Any] | None:
    surfaces = {surface.surface_id: model_to_dict(surface) for surface in list_surfaces(include_disabled=True)}
    surface = surfaces.get(surface_id)
    if not surface:
        return None
    blueprint = surface_blueprint_payload(include_disabled=True)
    surface_blueprint = next((item for item in blueprint.get("surfaces", []) if item.get("surface_id") == surface_id), None)
    backend_paths = _surface_backend_paths(surface_id)
    frontend_paths = _surface_frontend_paths(surface_id)
    backend_stats = _count_files(backend_paths)
    frontend_stats = {
        "module_count": sum(1 for path in frontend_paths if path.exists()),
        "line_count": sum(_line_count(path) for path in frontend_paths),
        "modules": [str(path.relative_to(ROOT_DIR)) for path in frontend_paths if path.exists()],
        "expected_modules": [str(path.relative_to(ROOT_DIR)) for path in frontend_paths],
    }
    return {
        "schema_id": "neo.surface.module_contract.v1",
        "surface_id": surface_id,
        "display_name": surface.get("display_name") or surface_id.title(),
        "status": surface.get("status") or "planned",
        "backend": {
            "owned_dirs": [str(path.relative_to(ROOT_DIR)) for path in backend_paths],
            "canonical_areas": CANONICAL_BACKEND_AREAS,
            **backend_stats,
        },
        "frontend": {
            "module_root": str(FRONTEND_MODULE_ROOT.relative_to(ROOT_DIR)),
            "canonical_areas": CANONICAL_FRONTEND_AREAS,
            **frontend_stats,
        },
        "memory_policy": surface.get("memory_policy") or {},
        "extension_targets": surface.get("extension_targets") or [],
        "blueprint": surface_blueprint or {},
        "policy": "Each surface should own its backend service files and frontend module while consuming shared memory, control-center, provider, and UI contracts.",
    }


def module_architecture_status() -> dict[str, Any]:
    surfaces = list_surfaces(include_disabled=True)
    js_lines = _line_count(STATIC_JS)
    main_lines = _line_count(MAIN_PY)
    route_count = _python_route_count(MAIN_PY)
    module_root_exists = FRONTEND_MODULE_ROOT.exists()
    frontend_modules = sorted(str(path.relative_to(ROOT_DIR)) for path in FRONTEND_MODULE_ROOT.glob("*.js")) if module_root_exists else []
    active_surfaces = [surface for surface in surfaces if surface.status == "active"]
    contracts = [modular_surface_contract(surface.surface_id) for surface in surfaces]
    contracts = [item for item in contracts if item]
    surface_status_counts = Counter(surface.status for surface in surfaces)
    return {
        "schema_id": MODULAR_ARCHITECTURE_SCHEMA_ID,
        "release_stage": "public_preview",
        "status": "ready" if module_root_exists else "needs_setup",
        "summary": {
            "surface_count": len(surfaces),
            "active_surface_count": len(active_surfaces),
            "surface_status_counts": dict(surface_status_counts),
            "frontend_module_count": len(frontend_modules),
            "canonical_area_count": len(BLUEPRINT_AREAS),
            "main_py_lines": main_lines,
            "main_py_route_count": route_count,
            "neo_js_lines": js_lines,
            "main_py_status": _status_from_ratio(main_lines, 4500),
            "neo_js_status": _status_from_ratio(js_lines, 12000),
        },
        "frontend": {
            "module_root": str(FRONTEND_MODULE_ROOT.relative_to(ROOT_DIR)),
            "modules": frontend_modules,
            "monolith": {"path": "neo_app/static/js/neo.js", "line_count": js_lines, "status": _status_from_ratio(js_lines, 12000)},
        },
        "backend": {
            "main": {"path": "neo_app/main.py", "line_count": main_lines, "route_count": route_count, "status": _status_from_ratio(main_lines, 4500)},
            "surface_contracts": contracts,
        },
        "policy": {
            "modular": "New surface work should add surface-owned backend modules, API route groups, frontend modules, memory ingestion hooks, observability hooks, and prompt/control contracts instead of adding permanent one-off code to main.py or neo.js.",
            "modern": "Surface UI should use shared cards, panels, badges, progress states, trace views, and clear status/diagnostics instead of silent actions or legacy utility layouts.",
        },
    }


def module_architecture_audit() -> dict[str, Any]:
    status = module_architecture_status()
    findings: list[dict[str, Any]] = []
    summary = status.get("summary", {})
    if summary.get("neo_js_lines", 0) > 12000:
        findings.append({
            "severity": "high",
            "area": "frontend",
            "title": "neo.js remains a large monolith",
            "detail": f"neo.js has {summary.get('neo_js_lines')} lines. public preview adds module boundaries, but follow-up phases should migrate surfaces incrementally.",
            "recommendation": "Move one surface at a time into neo_app/static/js/surfaces/<surface>.js with a stable action/render API.",
        })
    if summary.get("main_py_lines", 0) > 4500:
        findings.append({
            "severity": "high",
            "area": "backend",
            "title": "main.py remains a large route monolith",
            "detail": f"main.py has {summary.get('main_py_lines')} lines and {summary.get('main_py_route_count')} route handlers.",
            "recommendation": "Move future endpoints into APIRouter modules per surface/service, then include them from main.py.",
        })
    contracts = status.get("backend", {}).get("surface_contracts", [])
    missing_frontend = [item.get("surface_id") for item in contracts if not item.get("frontend", {}).get("module_count")]
    if missing_frontend:
        findings.append({
            "severity": "medium",
            "area": "frontend",
            "title": "Some surfaces do not yet have frontend module stubs",
            "detail": ", ".join(missing_frontend),
            "recommendation": "Create surface module stubs before migrating render/action code.",
        })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "architecture",
            "title": "public preview module contracts are ready",
            "detail": "Surface manifest, blueprint, module contracts, and audit endpoints are available.",
            "recommendation": "Use the audit as a status checklist for public preview+.",
        })
    return {
        "schema_id": "neo.surface.module_architecture_audit.v1",
        "release_stage": "public_preview",
        "status": "completed",
        "summary": summary,
        "findings": findings,
        "architecture": status,
    }


def module_architecture_manifest() -> dict[str, Any]:
    status = module_architecture_status()
    return {
        "schema_id": "neo.surface.module_manifest.v1",
        "release_stage": "public_preview",
        "status": status.get("status", "ready"),
        "surfaces": status.get("backend", {}).get("surface_contracts", []),
        "frontend_modules": status.get("frontend", {}).get("modules", []),
        "policy": status.get("policy", {}),
    }


def write_module_architecture_report(output_dir: Path | None = None) -> dict[str, Any]:
    report = module_architecture_audit()
    output = output_dir or (ROOT_DIR / "neo_data" / "memory" / "audits")
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "m16_modular_surface_architecture.json"
    md_path = output / "m16_modular_surface_architecture.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    findings = report.get("findings", [])
    lines = [
        "# Surface module status",
        "",
        f"Status: `{report.get('status')}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in (report.get("summary") or {}).items():
        lines.append(f"- **{key}**: {value}")
    lines += ["", "## Findings", ""]
    for item in findings:
        lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
    lines += ["", "## Policy", "", report.get("architecture", {}).get("policy", {}).get("modular", ""), "", report.get("architecture", {}).get("policy", {}).get("modern", "")]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "completed", "json_path": str(json_path), "markdown_path": str(md_path), "report": report}
