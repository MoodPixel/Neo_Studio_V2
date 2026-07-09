from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "neo_app" / "static"
CSS_PATH = STATIC_DIR / "css" / "neo.css"
JS_PATH = STATIC_DIR / "js" / "neo.js"
SURFACE_MODULES_DIR = STATIC_DIR / "js" / "surfaces"
AUDIT_DIR = ROOT / "neo_data" / "memory" / "audits"

REQUIRED_CSS_MARKERS = [
    "--neo-modern-radius-card",
    ".neo-modern-system-card",
    ".neo-modern-readable-panel",
    ".neo-modern-kpi-grid",
    ".neo-modern-focus-ring",
]

REQUIRED_JS_MARKERS = [
    "adminModernUiSystemHtml",
    "reloadModernUiSystem",
    "runModernUiSystemAudit",
]

MODERN_PRINCIPLES = [
    "Local-first power should feel clean, compact, and intentional.",
    "Every async action needs visible state: idle, running, complete, failed.",
    "Diagnostics should be available without turning the primary workflow into an admin console.",
    "Surfaces should migrate toward dedicated modules instead of expanding monolith files.",
    "No user-facing developer-note language, vague buttons, or silent failures.",
]


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except Exception:
        return 0


def _contains(path: Path, marker: str) -> bool:
    if not path.exists():
        return False
    try:
        return marker in path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def modern_ui_system_status() -> dict[str, Any]:
    css_lines = _line_count(CSS_PATH)
    js_lines = _line_count(JS_PATH)
    module_count = len(list(SURFACE_MODULES_DIR.glob("*.js"))) if SURFACE_MODULES_DIR.exists() else 0
    css_markers = {marker: _contains(CSS_PATH, marker) for marker in REQUIRED_CSS_MARKERS}
    js_markers = {marker: _contains(JS_PATH, marker) for marker in REQUIRED_JS_MARKERS}
    ready_checks = [*css_markers.values(), *js_markers.values(), module_count >= 1]
    ready_count = sum(1 for item in ready_checks if item)
    return {
        "status": "ready" if ready_count == len(ready_checks) else "needs_review",
        "phase": "M17",
        "name": "Modern UI System Pass",
        "summary": {
            "css_lines": css_lines,
            "neo_js_lines": js_lines,
            "surface_module_count": module_count,
            "ready_checks": ready_count,
            "total_checks": len(ready_checks),
        },
        "principles": MODERN_PRINCIPLES,
        "css_markers": css_markers,
        "js_markers": js_markers,
        "design_tokens": {
            "radius_card": "var(--neo-modern-radius-card)",
            "radius_control": "var(--neo-modern-radius-control)",
            "surface_glass": "var(--neo-modern-surface-glass)",
            "shadow_soft": "var(--neo-modern-shadow-soft)",
            "focus": "var(--neo-modern-focus)",
        },
        "notes": [
            "M17 is a shared polish layer, not a full surface rewrite.",
            "M16 module stubs remain the safe migration targets for future UI extraction.",
            "Modern styling is additive and should not remove existing controls or diagnostics.",
        ],
    }


def modern_ui_system_audit(write: bool = True) -> dict[str, Any]:
    status = modern_ui_system_status()
    findings: list[dict[str, Any]] = []
    summary = status.get("summary", {})
    if summary.get("neo_js_lines", 0) > 20000:
        findings.append({
            "severity": "high",
            "area": "frontend_modularity",
            "title": "neo.js remains a monolith",
            "detail": f"neo.js has {summary.get('neo_js_lines')} lines. Continue M16 surface module migration instead of adding permanent large features here.",
        })
    if summary.get("css_lines", 0) > 9000:
        findings.append({
            "severity": "medium",
            "area": "css_modularity",
            "title": "neo.css needs future design-token extraction",
            "detail": f"neo.css has {summary.get('css_lines')} lines. M17 adds tokens; future passes should split surface-specific CSS safely.",
        })
    for marker, ok in (status.get("css_markers") or {}).items():
        if not ok:
            findings.append({"severity": "medium", "area": "modern_css", "title": f"Missing CSS marker {marker}", "detail": "Modern UI token/style marker was not found."})
    for marker, ok in (status.get("js_markers") or {}).items():
        if not ok:
            findings.append({"severity": "medium", "area": "modern_ui_panel", "title": f"Missing JS marker {marker}", "detail": "Modern UI admin cockpit marker was not found."})
    result = {
        "status": "completed",
        "phase": "M17",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "modern_ui": status,
        "findings": findings,
        "recommendations": [
            "Keep M17 as the shared visual system baseline.",
            "Use M16 surface module contracts before moving large UI features out of neo.js.",
            "Prioritize status/progress/diagnostic clarity on every workflow button before visual decoration.",
            "Keep Admin cockpit panels compact: summary first, trace/detail on demand.",
        ],
    }
    if write:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        (AUDIT_DIR / "m17_modern_ui_system_pass.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        (AUDIT_DIR / "m17_modern_ui_system_pass.md").write_text(_audit_markdown(result), encoding="utf-8")
    return result


def _audit_markdown(result: dict[str, Any]) -> str:
    summary = ((result.get("modern_ui") or {}).get("summary") or {})
    findings = result.get("findings") or []
    lines = [
        "# Phase M17 — Modern UI System Pass Audit",
        "",
        f"Generated: {result.get('generated_at')}",
        "",
        "## Summary",
        "",
        f"- Status: {(result.get('modern_ui') or {}).get('status')}",
        f"- CSS lines: {summary.get('css_lines', 0)}",
        f"- neo.js lines: {summary.get('neo_js_lines', 0)}",
        f"- Surface frontend modules: {summary.get('surface_module_count', 0)}",
        f"- Ready checks: {summary.get('ready_checks', 0)}/{summary.get('total_checks', 0)}",
        "",
        "## Findings",
        "",
    ]
    if findings:
        for item in findings:
            lines.append(f"- **{item.get('severity', 'info')}** — {item.get('title', '')}: {item.get('detail', '')}")
    else:
        lines.append("- No findings.")
    lines.extend(["", "## Recommendations", ""])
    for item in result.get("recommendations") or []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
