from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_MODULE_ROOT = ROOT_DIR / "neo_app" / "static" / "js" / "surfaces"
INDEX_HTML = ROOT_DIR / "neo_app" / "static" / "index.html"
NEO_JS = ROOT_DIR / "neo_app" / "static" / "js" / "neo.js"
REPORT_DIR = ROOT_DIR / "neo_data" / "memory" / "audits"

SURFACE_MODULE_STATUS_SCHEMA_ID = "neo.surface.module_status_runtime.v1"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_manifest() -> dict[str, Any]:
    path = FRONTEND_MODULE_ROOT / "manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"schema_id": "neo.frontend.surface_modules.v1", "status": "missing_or_invalid", "error": str(exc), "modules": []}


def _js_line_count(path: Path) -> int:
    text = _read_text(path)
    return text.count("\n") + (1 if text else 0)


def _module_file_status(module_name: str) -> dict[str, Any]:
    path = FRONTEND_MODULE_ROOT / module_name
    text = _read_text(path)
    return {
        "module": module_name,
        "exists": path.exists(),
        "line_count": _js_line_count(path),
        "registers_runtime": "NeoSurfaceRuntime" in text and ".register" in text,
        "has_renderers": "renderers" in text,
        "has_actions": "actions" in text,
    }


def surface_status_runtime_status() -> dict[str, Any]:
    manifest = _read_manifest()
    modules = manifest.get("modules") if isinstance(manifest.get("modules"), list) else []
    runtime_file = _module_file_status("runtime.js")
    module_statuses = []
    migrated_area_count = 0
    partial_count = 0
    shell_count = 0
    for item in modules:
        module_name = item.get("module") or f"{item.get('surface_id', 'unknown')}.js"
        file_status = _module_file_status(module_name)
        migrated_areas = item.get("migrated_areas") or item.get("migratedAreas") or []
        if migrated_areas:
            partial_count += 1
            migrated_area_count += len(migrated_areas)
        if item.get("status") in {"status_shell", "stub"}:
            shell_count += 1
        module_statuses.append({**item, **file_status, "migrated_areas": migrated_areas})
    index_text = _read_text(INDEX_HTML)
    scripts_loaded = ["/static/js/surfaces/runtime.js" in index_text]
    for item in module_statuses:
        scripts_loaded.append(f"/static/js/surfaces/{item.get('module')}" in index_text)
    return {
        "schema_id": SURFACE_MODULE_STATUS_SCHEMA_ID,
        "release_stage": manifest.get("release_stage") or "public_preview",
        "status": "ready" if runtime_file.get("exists") and all(scripts_loaded) else "needs_attention",
        "summary": {
            "surface_module_count": len(module_statuses),
            "partial_migrated_surface_count": partial_count,
            "status_shell_surface_count": shell_count,
            "migrated_area_count": migrated_area_count,
            "neo_js_lines": _js_line_count(NEO_JS),
            "runtime_loaded_in_index": scripts_loaded[0] if scripts_loaded else False,
            "all_surface_scripts_loaded": all(scripts_loaded) if scripts_loaded else False,
        },
        "runtime": runtime_file,
        "manifest": manifest,
        "modules": module_statuses,
        "policy": {
            "safe_status": "Move one surface slice at a time into neo_app/static/js/surfaces/<surface>.js, keep legacy wrappers as fallback until each slice is proven stable.",
            "no_big_bang_rewrite": "public preview starts with Admin read-only renderers. It must not rewrite full surfaces or remove stable legacy behavior in one jump.",
            "module_contract": "Each surface module should declare migratedAreas, renderers/actions, diagnostics, and fallback assumptions.",
        },
    }


def surface_status_runtime_audit(write: bool = True) -> dict[str, Any]:
    status = surface_status_runtime_status()
    findings: list[dict[str, Any]] = []
    summary = status.get("summary", {})
    if not summary.get("runtime_loaded_in_index"):
        findings.append({"severity": "high", "area": "frontend", "title": "Surface runtime is not loaded", "detail": "runtime.js is not included in index.html before Neo bootstraps.", "recommendation": "Load /static/js/surfaces/runtime.js before surface modules and neo.js."})
    if not summary.get("all_surface_scripts_loaded"):
        findings.append({"severity": "medium", "area": "frontend", "title": "Not all surface modules are loaded", "detail": "One or more surface module scripts are missing from index.html.", "recommendation": "Load all declared surface modules or mark them disabled in manifest."})
    if summary.get("partial_migrated_surface_count", 0) < 1:
        findings.append({"severity": "medium", "area": "status", "title": "No migrated surface slices yet", "detail": "public preview should migrate at least one safe read-only slice into a surface module.", "recommendation": "Migrate one Admin read-only panel first, then continue per-surface."})
    if summary.get("neo_js_lines", 0) > 24000:
        findings.append({"severity": "info", "area": "frontend", "title": "neo.js remains a monolith", "detail": f"neo.js has {summary.get('neo_js_lines')} lines after public preview. This is expected at status start.", "recommendation": "Continue incremental surface status in follow-up phases."})
    if not findings:
        findings.append({"severity": "info", "area": "status", "title": "public preview status runtime is ready", "detail": "Runtime registry, module scripts, and first Admin migrated renderers are available.", "recommendation": "Continue with targeted surface status updates."})
    report = {"schema_id": "neo.surface.module_status_audit.v1", "release_stage": "public_preview", "status": "completed", "summary": summary, "findings": findings, "status_runtime": status}
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_surface_module_status.json"
        md_path = REPORT_DIR / "m18_surface_module_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in summary.items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def admin_memory_cockpit_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    admin = next((item for item in modules if item.get("surface_id") == "admin"), {})
    migrated = admin.get("migrated_areas") or []
    expected = [
        "render.memory_observability",
        "render.control_center_review",
        "render.assistant_brain_workspace",
        "render.surface_module_status",
    ]
    admin_js = _read_text(FRONTEND_MODULE_ROOT / "admin.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "admin_module_exists": (FRONTEND_MODULE_ROOT / "admin.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_admin_memory_cockpit": all(area in migrated for area in expected),
        "admin_module_has_memory_observability_renderer": "memoryObservabilityHtml" in admin_js,
        "admin_module_has_control_center_review_renderer": "controlCenterReviewHtml" in admin_js,
        "admin_module_has_assistant_brain_workspace_renderer": "assistantBrainWorkspaceHtml" in admin_js,
        "admin_module_has_surface_status_renderer": "surfaceModuleStatusHtml" in admin_js,
        "legacy_wrappers_call_runtime": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('admin', 'memoryObservabilityHtml'",
            "neoInvokeSurfaceModule('admin', 'controlCenterReviewHtml'",
            "neoInvokeSurfaceModule('admin', 'assistantBrainWorkspaceHtml'",
            "neoInvokeSurfaceModule('admin', 'surfaceModuleStatusHtml'",
        ]),
    }
    return {
        "schema_id": "neo.surface.admin_memory_cockpit_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "admin_migrated_area_count": len(migrated),
            "admin_memory_cockpit_expected_count": len(expected),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
        },
        "checks": checks,
        "admin_module": admin,
        "status_runtime": status,
        "policy": {
            "safe_extraction": "public preview extracts Admin Memory Cockpit read-only renderers into admin.js while keeping legacy neo.js wrappers as fallback.",
            "action_handlers": "Interactive actions remain legacy global functions until each action slice is migrated and tested separately.",
            "no_big_bang_rewrite": "Do not remove legacy wrappers until module renderers and actions have stable tests and trace coverage.",
        },
    }


def admin_memory_cockpit_status_audit(write: bool = True) -> dict[str, Any]:
    status = admin_memory_cockpit_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({"severity": "medium", "area": "admin_memory_cockpit", "title": f"Check failed: {key}", "detail": "Admin Memory Cockpit module extraction is incomplete.", "recommendation": "Keep legacy fallback active and finish the missing renderer/wrapper before migrating more panels."})
    if not findings:
        findings.append({"severity": "info", "area": "admin_memory_cockpit", "title": "Admin Memory Cockpit extraction is ready", "detail": "Admin module owns public preview cockpit renderers and legacy wrappers are intact.", "recommendation": "Next migrate action handlers only after renderer stability is confirmed."})
    report = {
        "schema_id": "neo.surface.admin_memory_cockpit_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "admin_memory_cockpit": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_1_admin_memory_cockpit_status.json"
        md_path = REPORT_DIR / "m18_1_admin_memory_cockpit_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report



def admin_memory_cockpit_action_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    admin = next((item for item in modules if item.get("surface_id") == "admin"), {})
    migrated = admin.get("migrated_areas") or []
    expected_actions = [
        "action.memory_observability.refresh",
        "action.surface_module_status.refresh",
        "action.surface_module_status.audit",
        "action.surface_module_architecture.refresh",
        "action.surface_module_architecture.audit",
        "action.modern_ui.refresh",
        "action.modern_ui.audit",
        "action.assistant_brain.refresh",
        "action.assistant_brain.select",
        "action.assistant_brain.activate",
        "action.assistant_brain.context",
        "action.control_center_review.refresh",
        "action.control_center_review.select_trace",
        "action.control_center_review.review",
    ]
    expected_renderers = [
        "render.memory_observability",
        "render.control_center_review",
        "render.assistant_brain_workspace",
        "render.surface_module_status",
        "render.surface_module_architecture",
        "render.modern_ui_system",
    ]
    admin_js = _read_text(FRONTEND_MODULE_ROOT / "admin.js")
    neo_js = _read_text(NEO_JS)
    action_function_names = [
        "reloadMemoryObservability",
        "reloadSurfaceModuleStatus",
        "runSurfaceModuleStatusAudit",
        "reloadSurfaceModuleArchitecture",
        "runSurfaceModuleArchitectureAudit",
        "reloadModernUiSystem",
        "runModernUiSystemAudit",
        "reloadAssistantBrainWorkspace",
        "selectAssistantBrainWorkspace",
        "activateAssistantBrainWorkspace",
        "buildAssistantBrainContext",
        "reloadControlCenterReview",
        "selectControlCenterReviewTrace",
        "reviewControlCenterTrace",
    ]
    checks = {
        "admin_module_exists": (FRONTEND_MODULE_ROOT / "admin.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_renderers": all(area in migrated for area in expected_renderers),
        "manifest_declares_actions": all(area in migrated for area in expected_actions),
        "admin_module_has_actions_block": "actions: {" in admin_js,
        "admin_module_has_action_handlers": all(f"async {name}" in admin_js for name in action_function_names),
        "legacy_wrappers_call_action_runtime": all(f"neoTryAdminAction('{name}" in neo_js for name in action_function_names),
        "runtime_tracks_async_actions": "status: 'pending'" in _read_text(FRONTEND_MODULE_ROOT / "runtime.js"),
        "neo_invoke_passes_load_json_render": "loadJson," in neo_js and "render," in neo_js,
    }
    return {
        "schema_id": "neo.surface.admin_memory_cockpit_action_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "admin_migrated_area_count": len(migrated),
            "admin_expected_renderer_count": len(expected_renderers),
            "admin_expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
        },
        "checks": checks,
        "expected_actions": expected_actions,
        "expected_renderers": expected_renderers,
        "admin_module": admin,
        "status_runtime": status,
        "policy": {
            "safe_action_status": "public preview migrates Admin Memory Cockpit action handlers into admin.js while keeping global neo.js wrappers as fallback dispatchers.",
            "no_big_bang_rewrite": "Only Admin Memory Cockpit actions are migrated here. Other surfaces and legacy panels keep stable behavior.",
            "async_visibility": "Surface runtime now records pending/ok/failed async action calls so Admin can inspect migrated action health.",
        },
    }


def admin_memory_cockpit_action_status_audit(write: bool = True) -> dict[str, Any]:
    status = admin_memory_cockpit_action_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "admin_memory_cockpit_actions",
                "title": f"Check failed: {key}",
                "detail": "Admin Memory Cockpit action handler status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish missing action handler/runtime wiring before migrating more UI actions.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "admin_memory_cockpit_actions",
            "title": "Admin Memory Cockpit action handlers are migrated",
            "detail": "Admin module owns public preview cockpit actions with legacy wrappers still available as safe fallback.",
            "recommendation": "Next migrate a small Assistant or Roleplay surface slice with the same renderer/action pattern.",
        })
    report = {
        "schema_id": "neo.surface.admin_memory_cockpit_action_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "admin_memory_cockpit_actions": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_2_admin_memory_cockpit_actions.json"
        md_path = REPORT_DIR / "m18_2_admin_memory_cockpit_actions.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report

def assistant_surface_slice_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    assistant = next((item for item in modules if item.get("surface_id") == "assistant"), {})
    migrated = assistant.get("migrated_areas") or []
    expected_renderers = [
        "render.assistant_chat_panel",
        "render.assistant_rail",
        "render.assistant_side_proof",
    ]
    expected_actions = [
        "action.assistant.refresh",
        "action.assistant.create_session",
        "action.assistant.load_session",
        "action.assistant.save_session",
        "action.assistant.send_local_message",
        "action.assistant.set_grounded_mode",
        "action.assistant.use_starter",
        "action.assistant.clear_draft",
        "action.assistant.set_search",
        "action.assistant.set_project_filter",
        "action.assistant.create_project",
        "action.assistant.rename_project",
        "action.assistant.save_project_editor",
        "action.assistant.rename_session",
        "action.assistant.delete_session",
        "action.assistant.capture_memory",
        "action.assistant.preview_context_pack",
    ]
    assistant_js = _read_text(FRONTEND_MODULE_ROOT / "assistant.js")
    neo_js = _read_text(NEO_JS)
    renderer_names = ["chatPanelHtml", "railHtml", "sideProofHtml"]
    action_names = [
        "assistantRefresh",
        "assistantCreateSession",
        "assistantLoadSession",
        "assistantSaveActiveSession",
        "assistantSendLocalMessage",
        "assistantSetGroundedMode",
        "assistantUseStarter",
        "assistantClearDraft",
        "assistantSetSearch",
        "assistantSetProjectFilter",
        "assistantCreateProject",
        "assistantRenameProject",
        "assistantSaveProjectEditor",
        "assistantRenameActiveSession",
        "assistantDeleteActiveSession",
        "assistantCaptureSelectionAsMemory",
        "assistantPreviewContextPack",
    ]
    checks = {
        "assistant_module_exists": (FRONTEND_MODULE_ROOT / "assistant.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_assistant_renderers": all(area in migrated for area in expected_renderers),
        "manifest_declares_assistant_actions": all(area in migrated for area in expected_actions),
        "assistant_module_has_renderers": all(name in assistant_js for name in renderer_names),
        "assistant_module_has_actions": all(name in assistant_js for name in action_names),
        "legacy_render_wrappers_call_runtime": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('assistant', 'chatPanelHtml'",
            "neoInvokeSurfaceModule('assistant', 'railHtml'",
            "neoInvokeSurfaceModule('assistant', 'sideProofHtml'",
        ]),
        "legacy_action_wrappers_call_runtime": all(snippet in neo_js for snippet in [
            "neoTryAssistantAction('assistantRefresh'",
            "neoTryAssistantAction('assistantCreateSession'",
            "neoTryAssistantAction('assistantLoadSession'",
            "neoTryAssistantAction('assistantSendLocalMessage'",
            "neoTryAssistantAction('assistantPreviewContextPack'",
        ]),
        "shared_module_invocation_has_assistant_context": all(snippet in neo_js for snippet in [
            "assistantState: typeof assistantState",
            "assistantFetchJson: typeof assistantFetchJson",
            "assistantActiveProject: typeof assistantActiveProject",
        ]),
    }
    return {
        "schema_id": "neo.surface.assistant_slice_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "assistant_migrated_area_count": len(migrated),
            "assistant_expected_renderer_count": len(expected_renderers),
            "assistant_expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
        },
        "checks": checks,
        "assistant_module": assistant,
        "status_runtime": status,
        "policy": {
            "safe_extraction": "public preview migrates the first Assistant chat render/action slice into assistant.js while keeping legacy wrappers as fallback.",
            "assistant_scope": "Chat panel, rail, side proof, and common chat/session/project actions are module-owned first; deeper Assistant panels remain legacy until later slices.",
            "no_big_bang_rewrite": "Do not remove legacy Assistant behavior until the module slice is stable in daily use and diagnostics remain clean.",
        },
    }


def assistant_surface_slice_status_audit(write: bool = True) -> dict[str, Any]:
    status = assistant_surface_slice_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({"severity": "medium", "area": "assistant_surface_status", "title": f"Check failed: {key}", "detail": "Assistant surface slice status is incomplete.", "recommendation": "Keep legacy fallback active and finish the missing renderer/action/wrapper before migrating more Assistant panels."})
    if not findings:
        findings.append({"severity": "info", "area": "assistant_surface_status", "title": "Assistant surface slice status is ready", "detail": "Assistant module owns first chat renderer/action lanes with legacy wrappers intact.", "recommendation": "Next migrate deeper Assistant panels only after smoke testing chat/session behavior."})
    report = {
        "schema_id": "neo.surface.assistant_slice_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "assistant_surface_slice": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_3_assistant_surface_slice_status.json"
        md_path = REPORT_DIR / "m18_3_assistant_surface_slice_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def assistant_deep_panel_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    assistant = next((item for item in modules if item.get("surface_id") == "assistant"), {})
    migrated = assistant.get("migrated_areas") or []
    expected_renderers = [
        "render.assistant_chat_panel",
        "render.assistant_rail",
        "render.assistant_side_proof",
        "render.assistant_project_manager",
        "render.assistant_source_grounding",
        "render.assistant_action_review",
        "render.assistant_citation_viewer",
        "render.assistant_project_workspace",
        "render.assistant_memory_lens",
        "render.assistant_context_knowledge",
        "render.assistant_safe_tools",
        "render.assistant_guide",
        "render.assistant_validation",
        "render.assistant_inspector",
        "render.assistant_deep_panel_layout",
    ]
    expected_actions = [
        "action.assistant.refresh",
        "action.assistant.create_session",
        "action.assistant.load_session",
        "action.assistant.save_session",
        "action.assistant.send_local_message",
        "action.assistant.set_grounded_mode",
        "action.assistant.use_starter",
        "action.assistant.clear_draft",
        "action.assistant.set_search",
        "action.assistant.set_project_filter",
        "action.assistant.create_project",
        "action.assistant.rename_project",
        "action.assistant.save_project_editor",
        "action.assistant.rename_session",
        "action.assistant.delete_session",
        "action.assistant.capture_memory",
        "action.assistant.preview_context_pack",
        "action.assistant.project_manager.load",
        "action.assistant.project_manager.ask",
        "action.assistant.action_review.plan",
        "action.assistant.action_review.run",
        "action.assistant.citation.open",
        "action.assistant.project_knowledge.save",
        "action.assistant.surface_context.attach",
        "action.assistant.safe_tool.run",
    ]
    assistant_js = _read_text(FRONTEND_MODULE_ROOT / "assistant.js")
    neo_js = _read_text(NEO_JS)
    renderer_names = [
        "chatPanelHtml",
        "railHtml",
        "sideProofHtml",
        "projectManagerPanelHtml",
        "sourceGroundingPanelHtml",
        "actionReviewPanelHtml",
        "citationViewerHtml",
        "projectWorkspaceMainHtml",
        "memoryMainHtml",
        "contextMainHtml",
        "toolsMainHtml",
        "guideMainHtml",
        "validationMainHtml",
        "inspectorMainHtml",
        "deepPanelLayout",
    ]
    action_names = [
        "assistantRefresh",
        "assistantCreateSession",
        "assistantLoadSession",
        "assistantSaveActiveSession",
        "assistantSendLocalMessage",
        "assistantSetGroundedMode",
        "assistantUseStarter",
        "assistantClearDraft",
        "assistantSetSearch",
        "assistantSetProjectFilter",
        "assistantCreateProject",
        "assistantRenameProject",
        "assistantSaveProjectEditor",
        "assistantRenameActiveSession",
        "assistantDeleteActiveSession",
        "assistantCaptureSelectionAsMemory",
        "assistantPreviewContextPack",
        "assistantLoadProjectManager",
        "assistantAskProjectManager",
        "assistantPlanActionReview",
        "assistantRunActionReview",
        "assistantOpenCitationViewer",
        "assistantSaveProjectKnowledge",
        "assistantAttachCurrentSurfaceContext",
        "assistantRunSafeTool",
    ]
    wrapper_names = [
        "assistantLoadProjectManager",
        "assistantAskProjectManager",
        "assistantPlanActionReview",
        "assistantRunActionReview",
        "assistantOpenCitationViewer",
        "assistantSaveProjectKnowledge",
        "assistantAttachCurrentSurfaceContext",
        "assistantRunSafeTool",
    ]
    checks = {
        "assistant_module_exists": (FRONTEND_MODULE_ROOT / "assistant.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_deep_renderers": all(area in migrated for area in expected_renderers),
        "manifest_declares_deep_actions": all(area in migrated for area in expected_actions),
        "assistant_module_has_deep_renderers": all(name in assistant_js for name in renderer_names),
        "assistant_module_has_deep_actions": all(name in assistant_js for name in action_names),
        "legacy_layout_wrapper_calls_runtime": "neoInvokeSurfaceModule('assistant', 'deepPanelLayout'" in neo_js,
        "legacy_deep_action_wrappers_call_runtime": all(f"neoTryAssistantAction('{name}" in neo_js for name in wrapper_names),
        "shared_module_invocation_has_deep_context": all(snippet in neo_js for snippet in [
            "assistantEvidenceCardsHtml",
            "assistantFetchJson: typeof assistantFetchJson",
            "assistantActiveProject: typeof assistantActiveProject",
        ]),
    }
    return {
        "schema_id": "neo.surface.assistant_deep_panel_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "assistant_migrated_area_count": len(migrated),
            "assistant_expected_renderer_count": len(expected_renderers),
            "assistant_expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "assistant_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "assistant.js"),
        },
        "checks": checks,
        "expected_renderers": expected_renderers,
        "expected_actions": expected_actions,
        "assistant_module": assistant,
        "status_runtime": status,
        "policy": {
            "safe_deep_panel_status": "public preview migrates Assistant project/context/memory/tools/guide/validation/inspector panels into assistant.js while keeping legacy wrappers as fallback dispatchers.",
            "no_big_bang_rewrite": "The Assistant surface still uses neo.js bridge wrappers; the module now owns deeper panels and actions but legacy code is not removed yet.",
            "next_slice": "After public preview, migrate Assistant backend route grouping or start Roleplay surface module slices only after smoke testing Assistant deep panels.",
        },
    }


def assistant_deep_panel_status_audit(write: bool = True) -> dict[str, Any]:
    status = assistant_deep_panel_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "assistant_deep_panel_status",
                "title": f"Check failed: {key}",
                "detail": "Assistant deep panel status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish the missing renderer/action/wrapper before migrating another surface slice.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "assistant_deep_panel_status",
            "title": "Assistant deep panel status is ready",
            "detail": "Assistant module owns chat, project, memory, context, tools, guide, validation, inspector, and supporting action lanes with legacy wrappers intact.",
            "recommendation": "Smoke test Assistant project/context/tools panels, then continue with a targeted Roleplay or Assistant route modularization update.",
        })
    report = {
        "schema_id": "neo.surface.assistant_deep_panel_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "assistant_deep_panel_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_4_assistant_deep_panel_status.json"
        md_path = REPORT_DIR / "m18_4_assistant_deep_panel_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report



def roleplay_surface_slice_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected_renderers = [
        "render.roleplay_scene_status",
        "render.roleplay_runtime_status",
        "render.roleplay_compile_status",
    ]
    expected_actions = [
        "action.roleplay.scope_build.refresh",
        "action.roleplay.scope_build.preview",
        "action.roleplay.scope_build.build",
        "action.roleplay.runtime.refresh_presets",
        "action.roleplay.runtime.select_preset",
        "action.roleplay.runtime.preview_packet",
        "action.roleplay.runtime.build_packet",
        "action.roleplay.runtime.open_scene_chat",
        "action.roleplay.scene.refresh_state",
        "action.roleplay.scene.save_setup",
        "action.roleplay.scene.stop_stream",
        "action.roleplay.scene.reset_transcript",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    wrapper_names = [
        "reloadRoleplayScopeBuildState",
        "previewRoleplayScopeBuildFromUi",
        "buildRoleplayScopeMemoryRuntimeFromUi",
        "reloadRoleplayRuntimePresets",
        "setRoleplayRuntimePresetFromUi",
        "prepareRoleplayPresetScenePacketFromUi",
        "buildRoleplayPresetScenePacketFromUi",
        "openRoleplaySceneChatFromRuntime",
        "refreshRoleplaySceneStateFromUi",
        "saveRoleplaySceneSetupFromUi",
        "resetRoleplaySceneTranscriptFromUi",
    ]
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_roleplay_slice": all(area in migrated for area in expected_renderers + expected_actions),
        "roleplay_module_registers_runtime": "NeoSurfaceRuntime" in roleplay_js and ".register('roleplay'" in roleplay_js,
        "roleplay_module_has_renderers": all(name.split(".")[-1].replace("roleplay_", "roleplay")[:8] or name for name in expected_renderers) and all(token in roleplay_js for token in ["roleplaySceneStatusHtml", "roleplayRuntimeStatusHtml", "roleplayCompileStatusHtml"]),
        "roleplay_module_has_actions": all(token in roleplay_js for token in ["reloadRoleplayScopeBuildState", "previewRoleplayScopeBuildFromUi", "buildRoleplayScopeMemoryRuntimeFromUi", "reloadRoleplayRuntimePresets", "prepareRoleplayPresetScenePacketFromUi", "buildRoleplayPresetScenePacketFromUi", "refreshRoleplaySceneStateFromUi", "resetRoleplaySceneTranscriptFromUi"]),
        "legacy_roleplay_wrappers_call_runtime": "neoTryRoleplayAction" in neo_js and all(name in neo_js for name in wrapper_names),
    }
    return {
        "schema_id": "neo.surface.roleplay_slice_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "roleplay_expected_renderer_count": len(expected_renderers),
            "roleplay_expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
        },
        "checks": checks,
        "expected_renderers": expected_renderers,
        "expected_actions": expected_actions,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "safe_roleplay_slice": "public preview migrates the first Roleplay Compile/Runtime/Scene state action lane into roleplay.js with legacy neo.js wrappers intact.",
            "no_scene_generation_rewrite_yet": "Scene generation/streaming itself remains legacy-owned until the Scene Director runtime is smoke-tested under the module bridge.",
            "next_slice": "After public preview, migrate Roleplay Scene Director/Control Center cockpit panels or split Roleplay backend routes by workspace.",
        },
    }


def roleplay_surface_slice_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_surface_slice_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_surface_slice_status",
                "title": f"Check failed: {key}",
                "detail": "Roleplay surface slice status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish the missing renderer/action/wrapper before migrating deeper Roleplay behavior.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_surface_slice_status",
            "title": "Roleplay surface slice status is ready",
            "detail": "Roleplay module owns selected Compile, Runtime, and Scene state actions with legacy wrappers intact.",
            "recommendation": "Smoke test Compile scope build, Runtime packet build, and Scene setup/reset before migrating Scene Director generation handlers.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_slice_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_surface_slice_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_5_roleplay_surface_slice_status.json"
        md_path = REPORT_DIR / "m18_5_roleplay_surface_slice_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def roleplay_scene_director_cockpit_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected = [
        "render.roleplay_scene_director_cockpit",
        "action.roleplay.scene_director.status",
        "action.roleplay.scene_director.preflight",
        "action.roleplay.scene_director.validate",
        "action.roleplay.scene_director.traces",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    main_py = _read_text(ROOT_DIR / "neo_app" / "main.py")
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_scene_director_cockpit": all(area in migrated for area in expected),
        "roleplay_module_has_scene_director_renderer": "roleplaySceneDirectorCockpitHtml" in roleplay_js and "sceneDirectorCockpitHtml" in roleplay_js,
        "roleplay_module_has_scene_director_actions": all(token in roleplay_js for token in ["refreshRoleplaySceneDirectorStatus", "runRoleplaySceneDirectorPreflight", "validateRoleplaySceneDirectorLastResponse", "refreshRoleplaySceneDirectorTraces"]),
        "scene_director_api_routes_exist": all(route in main_py for route in ["/api/roleplay/scene-director/status", "/api/roleplay/scene-director/preflight", "/api/roleplay/scene-director/validate", "/api/roleplay/scene-director/traces"]),
        "generation_streaming_not_migrated": "Scene generation/streaming itself remains legacy-owned" in roleplay_js or "legacy neo.js still owns" in roleplay_js,
    }
    return {
        "schema_id": "neo.surface.roleplay_scene_director_cockpit_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "scene_director_expected_area_count": len(expected),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
        },
        "checks": checks,
        "expected_areas": expected,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "scene_director_cockpit": "public preview migrates the Scene Director cockpit, preflight, validation, trace refresh, and readiness diagnostics into roleplay.js.",
            "no_streaming_rewrite": "Scene generation/streaming handlers stay legacy-owned until the cockpit proves the Control Center/Director chain is stable.",
            "right_path": "Do not bypass advanced memory, prompt contracts, or validation to make Roleplay look working. Use cockpit traces to diagnose first.",
        },
    }


def roleplay_scene_director_cockpit_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_scene_director_cockpit_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_scene_director_cockpit",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Scene Director cockpit module status is incomplete.",
                "recommendation": "Keep legacy generation fallback active and finish the missing cockpit renderer/action/API route before migrating streaming handlers.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_scene_director_cockpit",
            "title": "Roleplay Scene Director cockpit status is ready",
            "detail": "Roleplay module owns Scene Director preflight/validation/trace cockpit actions while generation/streaming remains fallback-safe.",
            "recommendation": "Smoke test preflight, inspect trace output, then decide whether to migrate Scene Chat dispatch in a later targeted update.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_scene_director_cockpit_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_scene_director_cockpit_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_6_roleplay_scene_director_cockpit_status.json"
        md_path = REPORT_DIR / "m18_6_roleplay_scene_director_cockpit_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def roleplay_scene_chat_dispatch_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected = [
        "action.roleplay.scene_chat.stream_turn",
        "action.roleplay.scene_chat.send_non_stream",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_scene_chat_dispatch": all(area in migrated for area in expected),
        "roleplay_module_has_stream_dispatch": "executeSceneTurnStream" in roleplay_js and "/api/roleplay/scene/turn-stream" in roleplay_js,
        "roleplay_module_has_non_stream_dispatch": "executeSceneTurnNonStream" in roleplay_js and "/api/roleplay/scene/turn'" in roleplay_js,
        "roleplay_module_preserves_scene_director_payload": "dispatch_owner: 'roleplay_surface_module_m18_7'" in roleplay_js,
        "legacy_wrappers_call_roleplay_module": all(snippet in neo_js for snippet in [
            "neoTryRoleplayAction('executeRoleplaySceneTurnStreamFromUi'",
            "neoTryRoleplayAction('executeRoleplaySceneTurnFromUi'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_scene_chat_dispatch_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "scene_chat_dispatch_expected_area_count": len(expected),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
        },
        "checks": checks,
        "expected_areas": expected,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "scene_chat_dispatch": "public preview migrates Roleplay Scene Chat stream and non-stream dispatch into roleplay.js while keeping neo.js wrappers as fallback.",
            "generation_backend": "Backend Scene Director, Control Center, transcript save, and writeback routes remain server-owned; this update only moves frontend dispatch orchestration.",
            "safe_status": "Do not remove legacy dispatch bodies until stream/non-stream dispatch is smoke-tested in the module path.",
        },
    }


def roleplay_scene_chat_dispatch_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_scene_chat_dispatch_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_scene_chat_dispatch",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Scene Chat dispatch module status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish the missing stream/non-stream module bridge before further status.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_scene_chat_dispatch",
            "title": "Roleplay Scene Chat dispatch status is ready",
            "detail": "Roleplay module owns stream/non-stream frontend dispatch while backend generation, Control Center, Scene Director validation, and writeback remain intact.",
            "recommendation": "Smoke test Stream scene turn and Send non-stream, then migrate transcript/checkpoint helpers if stable.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_scene_chat_dispatch_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_scene_chat_dispatch_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_7_roleplay_scene_chat_dispatch_status.json"
        md_path = REPORT_DIR / "m18_7_roleplay_scene_chat_dispatch_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report



def roleplay_transcript_checkpoint_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected = [
        "action.roleplay.scene.transcript_placeholder",
        "action.roleplay.scene.continue_placeholder",
        "action.roleplay.scene.save_checkpoint",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_transcript_checkpoint_helpers": all(area in migrated for area in expected),
        "roleplay_module_has_placeholder_helper": "appendTranscriptPlaceholder" in roleplay_js and "/api/roleplay/scene/transcript/append-placeholder" in roleplay_js,
        "roleplay_module_has_continue_placeholder_helper": "continueTranscriptPlaceholder" in roleplay_js,
        "roleplay_module_has_checkpoint_helper": "createSceneCheckpoint" in roleplay_js and "/api/roleplay/story-checkpoint/capture-active" in roleplay_js,
        "roleplay_module_marks_checkpoint_owner": "roleplay_surface_module_m18_8" in roleplay_js,
        "legacy_wrappers_call_roleplay_module": all(snippet in neo_js for snippet in [
            "neoTryRoleplayAction('appendRoleplaySceneTranscriptPlaceholderFromUi'",
            "neoTryRoleplayAction('continueRoleplayScenePlaceholderFromUi'",
            "neoTryRoleplayAction('createRoleplaySceneCheckpointFromUi'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_transcript_checkpoint_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "transcript_checkpoint_expected_area_count": len(expected),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
        },
        "checks": checks,
        "expected_areas": expected,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "transcript_helpers": "public preview migrates transcript placeholder and continue-placeholder helpers into roleplay.js while keeping legacy wrappers as fallback.",
            "checkpoint_helpers": "public preview migrates Scene checkpoint capture orchestration into roleplay.js; backend checkpoint persistence remains server-owned.",
            "safe_status": "Generation, Control Center, Scene Director, transcript persistence, and memory writeback remain backend-owned. This update only moves frontend helper orchestration.",
        },
    }


def roleplay_transcript_checkpoint_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_transcript_checkpoint_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_transcript_checkpoint_helpers",
                "title": f"Check failed: {key}",
                "detail": "Roleplay transcript/checkpoint helper status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish the missing helper/module bridge before migrating deeper Scene state lanes.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_transcript_checkpoint_helpers",
            "title": "Roleplay transcript/checkpoint helper status is ready",
            "detail": "Roleplay module owns placeholder, continue-placeholder, and checkpoint helper orchestration while backend persistence remains intact.",
            "recommendation": "Smoke test placeholder append, continue placeholder, Save checkpoint, then continue with roleplay scene state/checkpoint inspector status.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_transcript_checkpoint_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_transcript_checkpoint_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_8_roleplay_transcript_checkpoint_status.json"
        md_path = REPORT_DIR / "m18_8_roleplay_transcript_checkpoint_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def roleplay_scene_state_checkpoint_inspector_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected = [
        "render.roleplay_scene_state_inspector",
        "render.roleplay_checkpoint_inspector",
        "action.roleplay.scene_state_inspector.refresh",
        "action.roleplay.checkpoint.diff_select",
        "action.roleplay.checkpoint.diff_compare",
        "action.roleplay.checkpoint.branch",
        "action.roleplay.checkpoint.restore",
        "action.roleplay.checkpoint.resume_selected",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_scene_state_checkpoint_inspector": all(area in migrated for area in expected),
        "roleplay_module_has_scene_state_inspector_renderer": "sceneStateInspectorHtml" in roleplay_js and "roleplaySceneStateInspectorHtml" in roleplay_js,
        "roleplay_module_has_checkpoint_inspector_renderer": "checkpointInspectorHtml" in roleplay_js and "roleplayCheckpointInspectorHtml" in roleplay_js,
        "roleplay_module_has_checkpoint_diff_action": "compareRoleplayCheckpointsFromUi" in roleplay_js and "/api/roleplay/story-checkpoint/diff" in roleplay_js,
        "roleplay_module_has_checkpoint_branch_action": "branchRoleplayCheckpointFromUi" in roleplay_js and "/api/roleplay/story-checkpoint/branch" in roleplay_js,
        "roleplay_module_has_checkpoint_restore_action": "restoreRoleplayStoryToSceneFromUi" in roleplay_js and "/api/roleplay/story-checkpoint/restore-snapshot" in roleplay_js,
        "legacy_wrappers_call_roleplay_module": all(snippet in neo_js for snippet in [
            "roleplaySceneStateInspectorRefresh",
            "neoTryRoleplayAction('compareRoleplayCheckpointsFromUi'",
            "neoTryRoleplayAction('branchRoleplayCheckpointFromUi'",
            "neoTryRoleplayAction('restoreRoleplayStoryToSceneFromUi'",
            "neoTryRoleplayAction('resumeRoleplayStoryFromSelection'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_scene_state_checkpoint_inspector_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "scene_state_checkpoint_expected_area_count": len(expected),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
        },
        "checks": checks,
        "expected_areas": expected,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "scene_state_inspector": "public preview migrates the Scene State Inspector renderer and refresh action into roleplay.js so live packet, transcript, backend, director, and checkpoint readiness can be inspected from the module.",
            "checkpoint_inspector": "public preview migrates checkpoint diff/branch/restore/resume frontend orchestration into roleplay.js while backend persistence and restore routes remain server-owned.",
            "safe_status": "Generation, Control Center, Scene Director runtime, transcript persistence, and memory writeback remain backend-owned. This update only moves inspector/readiness cockpit and checkpoint frontend helpers.",
        },
    }


def roleplay_scene_state_checkpoint_inspector_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_scene_state_checkpoint_inspector_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_scene_state_checkpoint_inspector",
                "title": f"Check failed: {key}",
                "detail": "Roleplay scene state/checkpoint inspector status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish the missing renderer/action bridge before migrating deeper Stories/Roleplay state lanes.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_scene_state_checkpoint_inspector",
            "title": "Roleplay scene state/checkpoint inspector status is ready",
            "detail": "Roleplay module owns Scene State Inspector and Checkpoint Inspector cockpit lanes while backend state, restore, diff, and checkpoint persistence remain intact.",
            "recommendation": "Smoke test Refresh inspector, checkpoint diff, branch, restore, and resume selected before migrating broader Stories panels.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_scene_state_checkpoint_inspector_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_scene_state_checkpoint_inspector_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_9_roleplay_scene_state_checkpoint_inspector_status.json"
        md_path = REPORT_DIR / "m18_9_roleplay_scene_state_checkpoint_inspector_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report



def roleplay_stories_workspace_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    roleplay = next((module for module in status.get("modules", []) if module.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas", []) or []
    expected = [
        "render.roleplay_stories_workspace",
        "action.roleplay.stories.refresh",
        "action.roleplay.stories.set_view",
        "action.roleplay.stories.set_archive_view",
        "action.roleplay.stories.set_inspector_view",
        "action.roleplay.stories.fill_from_scene",
        "action.roleplay.stories.clear_storyline_form",
        "action.roleplay.stories.clear_session_form",
        "action.roleplay.stories.create_storyline",
        "action.roleplay.stories.create_session",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_stories_workspace": all(area in migrated for area in expected),
        "roleplay_module_has_stories_renderer": "storiesWorkspaceShellHtml" in roleplay_js and "roleplayStoriesWorkspaceShellHtml" in roleplay_js,
        "roleplay_module_has_stories_create_actions": "createRoleplayStorylineFromUi" in roleplay_js and "/api/roleplay/storyline/create" in roleplay_js and "createRoleplayStorySessionFromUi" in roleplay_js,
        "roleplay_module_has_stories_view_actions": "setRoleplayStoriesView" in roleplay_js and "setRoleplayStoriesArchiveView" in roleplay_js and "setRoleplayStoriesInspectorView" in roleplay_js,
        "legacy_wrappers_call_roleplay_module": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('roleplay', 'roleplayStoriesWorkspaceShellHtml'",
            "neoTryRoleplayAction('refreshRoleplayStoriesFromUi'",
            "neoTryRoleplayAction('createRoleplayStorylineFromUi'",
            "neoTryRoleplayAction('createRoleplayStorySessionFromUi'",
            "neoInvokeSurfaceModule('roleplay', 'setRoleplayStoriesView'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_stories_workspace_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "stories_workspace_expected_area_count": len(expected),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
        },
        "checks": checks,
        "expected_areas": expected,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "stories_workspace": "public preview migrates the Roleplay Stories Workspace shell, story/session creation, view switching, archive switching, inspector switching, and fill/clear helpers into roleplay.js.",
            "server_owned": "Storyline, session, checkpoint, branch, restore, and persistence routes remain backend-owned. The module only owns frontend orchestration and rendering.",
            "safe_status": "Legacy neo.js wrappers remain as fallback while the module renderer/action bridge is smoke-tested.",
        },
    }


def roleplay_stories_workspace_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_stories_workspace_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_stories_workspace",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Stories Workspace module status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish the missing renderer/action bridge before migrating archive/provenance deeper lanes.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_stories_workspace",
            "title": "Roleplay Stories Workspace status is ready",
            "detail": "Roleplay module owns the Stories Workspace shell, view switching, story/session creation, and basic archive/inspector rendering while backend persistence remains intact.",
            "recommendation": "Smoke test refresh, create storyline, create session, archive switch, inspector switch, checkpoint restore, and resume selected before extracting deeper provenance graph controls.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_stories_workspace_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_stories_workspace_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_10_roleplay_stories_workspace_status.json"
        md_path = REPORT_DIR / "m18_10_roleplay_stories_workspace_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def roleplay_archive_provenance_graph_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    roleplay = next((module for module in status.get("modules", []) if module.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas", []) or []
    expected = [
        "render.roleplay_stories_archive",
        "render.roleplay_stories_inspector_provenance",
        "render.roleplay_provenance_graph",
        "action.roleplay.provenance.reload_state",
        "action.roleplay.provenance.refresh_graph",
        "action.roleplay.provenance.trace_node",
        "action.roleplay.provenance.zoom",
        "action.roleplay.provenance.pan",
        "action.roleplay.provenance.reset_view",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_archive_provenance": all(area in migrated for area in expected),
        "roleplay_module_has_archive_renderer": "storiesArchiveHtml" in roleplay_js and "roleplayStoriesArchiveHtml" in roleplay_js,
        "roleplay_module_has_provenance_renderer": "roleplayProvenanceGraphHtml" in roleplay_js and "provenanceVisualGraphHtml" in roleplay_js,
        "roleplay_module_has_graph_actions": all(snippet in roleplay_js for snippet in [
            "reloadRoleplayProvenanceState",
            "refreshRoleplayProvenanceGraph",
            "traceRoleplayProvenanceNode",
            "roleplayProvenanceZoom",
            "roleplayProvenancePan",
            "roleplayProvenanceResetView",
        ]),
        "legacy_wrappers_call_roleplay_module": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('roleplay', 'roleplayStoriesArchiveHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayStoriesInspectorHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayProvenanceGraphHtml'",
            "neoTryRoleplayAction('refreshRoleplayProvenanceGraph'",
            "neoTryRoleplayAction('traceRoleplayProvenanceNode'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_archive_provenance_graph_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "archive_provenance_expected_area_count": len(expected),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
        },
        "checks": checks,
        "expected_areas": expected,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "archive_lanes": "public preview keeps Stories, Roleplay, and Canon archive lanes separate in the roleplay module to prevent cross-scope memory soup.",
            "provenance_graph": "public preview migrates the canvas-style provenance graph renderer and trace/zoom/pan/refresh actions into roleplay.js while backend graph/trace routes remain server-owned.",
            "safe_status": "Legacy neo.js wrappers remain as fallback. Persistence, graph construction, provenance trace, and contradiction resolution stay backend-owned.",
        },
    }


def roleplay_archive_provenance_graph_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_archive_provenance_graph_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_archive_provenance_graph",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Archive + Provenance Graph module status is incomplete.",
                "recommendation": "Keep legacy fallback active and finish missing renderer/action bridge before moving more Roleplay panes into the module.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_archive_provenance_graph",
            "title": "Roleplay Archive + Provenance Graph status is ready",
            "detail": "Roleplay module owns Archive lane rendering plus provenance graph rendering/controls while backend graph state, trace, and persistence remain intact.",
            "recommendation": "Smoke test Archive child switching, provenance refresh, node trace, zoom/pan/reset, and legacy fallback before extracting additional Studio/Forge lanes.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_archive_provenance_graph_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_archive_provenance_graph_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_11_roleplay_archive_provenance_graph_status.json"
        md_path = REPORT_DIR / "m18_11_roleplay_archive_provenance_graph_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def roleplay_compile_runtime_deep_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected_renderers = [
        "render.roleplay_studio_compile_deep",
        "render.roleplay_studio_runtime_deep",
        "render.roleplay_scoped_compile_deep",
        "render.roleplay_scope_build_deep",
    ]
    expected_actions = [
        "action.roleplay.scoped_compile.refresh",
        "action.roleplay.scoped_compile.preview",
        "action.roleplay.scoped_compile.execute",
        "action.roleplay.runtime_retrieval.index_vectors",
        "action.roleplay.runtime_retrieval.run",
        "action.roleplay.runtime_retrieval.semantic_only",
        "action.roleplay.runtime_retrieval.keyword_only",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_deep_renderers": all(area in migrated for area in expected_renderers),
        "manifest_declares_deep_actions": all(area in migrated for area in expected_actions),
        "roleplay_module_has_compile_renderer": "studioCompileDeepHtml" in roleplay_js,
        "roleplay_module_has_runtime_renderer": "runtimeDeepHtml" in roleplay_js,
        "roleplay_module_has_scoped_compile_actions": all(snippet in roleplay_js for snippet in ["previewRoleplayScopedCompilePlanFromUi", "executeRoleplayScopedCompilePlanFromUi", "reloadRoleplayScopedCompileState"]),
        "roleplay_module_has_runtime_retrieval_actions": all(snippet in roleplay_js for snippet in ["runRoleplayRuntimeRetrievalLaneFromUi", "indexRoleplaySemanticMemoryFromUi", "searchRoleplaySemanticRetrievalFromUi", "searchRoleplayRetrievalFoundationFromUi"]),
        "legacy_render_wrappers_call_runtime": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('roleplay', 'roleplayStudioCompileDeepHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayStudioRuntimeDeepHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayScopeBuildDeepHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayScopedCompileDeepHtml'",
        ]),
        "legacy_action_wrappers_call_runtime": all(snippet in neo_js for snippet in [
            "neoTryRoleplayAction('previewRoleplayScopedCompilePlanFromUi'",
            "neoTryRoleplayAction('executeRoleplayScopedCompilePlanFromUi'",
            "neoTryRoleplayAction('runRoleplayRuntimeRetrievalLaneFromUi'",
            "neoTryRoleplayAction('indexRoleplaySemanticMemoryFromUi'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_compile_runtime_deep_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "expected_renderer_count": len(expected_renderers),
            "expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
        },
        "checks": checks,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "safe_extraction": "public preview migrates Roleplay Studio Compile/Runtime deep renderers and advanced retrieval handlers into roleplay.js while keeping backend persistence authoritative and legacy wrappers as fallback.",
            "compile_runtime_boundary": "The module owns UI rendering/action orchestration. Backend compile, retrieval, reranking, Chroma mirror, and packet persistence remain server-owned.",
            "no_degradation": "This status does not reduce retrieval/rerank details or bypass the Control Center architecture; it only moves surface UI ownership out of the neo.js monolith.",
        },
    }


def roleplay_compile_runtime_deep_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_compile_runtime_deep_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_compile_runtime_deep",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Studio Compile/Runtime deep module status is incomplete.",
                "recommendation": "Keep legacy fallback active and complete the missing renderer/action/wrapper before moving deeper Roleplay lanes.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_compile_runtime_deep",
            "title": "Roleplay Compile/Runtime deep status is ready",
            "detail": "Roleplay module owns deep Compile/Runtime UI lanes and advanced retrieval handlers with legacy fallback intact.",
            "recommendation": "Smoke-test Compile preview/build, Runtime preset packet build, and advanced retrieval before further Roleplay module extraction.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_compile_runtime_deep_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_compile_runtime_deep_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_12_roleplay_compile_runtime_deep_status.json"
        md_path = REPORT_DIR / "m18_12_roleplay_compile_runtime_deep_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report

def roleplay_forge_builder_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected_renderers = [
        "render.roleplay_forge_builder_rail",
        "render.roleplay_forge_builder",
        "render.roleplay_forge_records",
        "render.roleplay_forge_inspector",
    ]
    expected_actions = [
        "action.roleplay.forge.refresh_state",
        "action.roleplay.forge.select_kind",
        "action.roleplay.forge.load_record",
        "action.roleplay.forge.reset_payload",
        "action.roleplay.forge.save_record",
        "action.roleplay.forge.delete_record",
        "action.roleplay.forge.import_preview",
        "action.roleplay.forge.import_run",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_forge_renderers": all(area in migrated for area in expected_renderers),
        "manifest_declares_forge_actions": all(area in migrated for area in expected_actions),
        "roleplay_module_has_forge_renderers": all(snippet in roleplay_js for snippet in ["forgeBuilderRailModuleHtml", "forgeBuilderModuleHtml", "forgeRecordsModuleHtml", "forgeInspectorModuleHtml"]),
        "roleplay_module_has_forge_actions": all(snippet in roleplay_js for snippet in ["saveRoleplayForgeRecordFromUi", "deleteRoleplayForgeRecordFromUi", "roleplayForgeImportPreview", "roleplayForgeImportRun"]),
        "legacy_forge_panel_uses_runtime": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeBuilderRailHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeBuilderHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeRecordsHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeInspectorHtml'",
        ]),
        "legacy_forge_actions_use_runtime": all(snippet in neo_js for snippet in [
            "neoTryRoleplayAction('saveRoleplayForgeRecordFromUi'",
            "neoTryRoleplayAction('deleteRoleplayForgeRecordFromUi'",
            "neoTryRoleplayAction('roleplayForgeImportPreview'",
            "neoTryRoleplayAction('roleplayForgeImportRun'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_forge_builder_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "expected_renderer_count": len(expected_renderers),
            "expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
        },
        "checks": checks,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "safe_extraction": "public preview migrates Roleplay Forge Builder rail, builder, records, inspector, save/delete, and import preview/run action handlers into roleplay.js while keeping legacy wrappers as fallback.",
            "backend_boundary": "The module owns UI render/action orchestration. Backend Forge state, file-backed records, SQLite sync, import parsing, and save/delete persistence remain server-owned.",
            "no_degradation": "This status does not alter Forge record schema, compile behavior, or memory pipeline. It only moves surface UI ownership out of the neo.js monolith.",
        },
    }


def roleplay_forge_builder_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_forge_builder_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_forge_builder",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Forge Builder module status is incomplete.",
                "recommendation": "Keep legacy fallback active and complete the missing Forge renderer/action/wrapper before moving deeper Forge lanes.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_forge_builder",
            "title": "Roleplay Forge Builder status is ready",
            "detail": "Roleplay module owns Forge Builder rail, builder, records, inspector, save/delete, and import preview/run actions with legacy fallback intact.",
            "recommendation": "Smoke-test Forge kind switching, record load/save/delete, scope inheritance, import preview/import, and Project link buttons before migrating Forge SQLite/provenance subpanes.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_forge_builder_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_forge_builder_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_13_roleplay_forge_builder_status.json"
        md_path = REPORT_DIR / "m18_13_roleplay_forge_builder_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def roleplay_forge_sqlite_template_inspector_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected_renderers = [
        "render.roleplay_forge_sqlite_inspector",
        "render.roleplay_forge_template_inspector",
    ]
    expected_actions = [
        "action.roleplay.forge.refresh_sqlite_inspector",
        "action.roleplay.forge.refresh_template_inspector",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_sqlite_template_renderers": all(area in migrated for area in expected_renderers),
        "manifest_declares_sqlite_template_actions": all(area in migrated for area in expected_actions),
        "roleplay_module_has_sqlite_inspector": "forgeSqliteInspectorHtml" in roleplay_js and "roleplayForgeSqliteInspectorHtml" in roleplay_js,
        "roleplay_module_has_template_inspector": "forgeTemplateInspectorHtml" in roleplay_js and "roleplayForgeTemplateInspectorHtml" in roleplay_js,
        "roleplay_module_has_refresh_actions": all(snippet in roleplay_js for snippet in ["roleplayRefreshForgeSqliteInspector", "roleplayRefreshForgeTemplateInspector"]),
        "legacy_wrappers_use_runtime_actions": all(snippet in neo_js for snippet in [
            "neoTryRoleplayAction('roleplayRefreshForgeSqliteInspector'",
            "neoTryRoleplayAction('roleplayRefreshForgeTemplateInspector'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_forge_sqlite_template_inspector_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "expected_renderer_count": len(expected_renderers),
            "expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
        },
        "checks": checks,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "safe_extraction": "public preview migrates Roleplay Forge SQLite and Template Inspector lanes into roleplay.js while keeping legacy wrappers as fallback.",
            "backend_boundary": "The module owns inspector UI and refresh orchestration. Backend remains authoritative for Forge state, SQLite sync, template registry, save/delete/import, and compile/runtime memory pipeline.",
            "no_degradation": "This status does not alter Forge templates, SQLite schema, record persistence, or memory compile behavior. It only makes inspector lanes module-owned and auditable.",
        },
    }


def roleplay_forge_sqlite_template_inspector_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_forge_sqlite_template_inspector_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_forge_sqlite_template_inspector",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Forge SQLite + Template Inspector module status is incomplete.",
                "recommendation": "Keep legacy fallback active and complete the missing renderer/action/wrapper before moving deeper Forge lanes.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_forge_sqlite_template_inspector",
            "title": "Roleplay Forge SQLite + Template Inspector status is ready",
            "detail": "Roleplay module owns separate SQLite and Template Inspector lanes with refresh actions and legacy fallback intact.",
            "recommendation": "Smoke-test template preview, field paths, SQLite status/table list, Forge reload, and record template reset before migrating deeper Forge SQLite sync tooling.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_forge_sqlite_template_inspector_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_forge_sqlite_template_inspector_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_14_roleplay_forge_sqlite_template_inspector_status.json"
        md_path = REPORT_DIR / "m18_14_roleplay_forge_sqlite_template_inspector_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


def roleplay_forge_advanced_import_scope_status_status() -> dict[str, Any]:
    status = surface_status_runtime_status()
    modules = status.get("modules") if isinstance(status.get("modules"), list) else []
    roleplay = next((item for item in modules if item.get("surface_id") == "roleplay"), {})
    migrated = roleplay.get("migrated_areas") or []
    expected_renderers = [
        "render.roleplay_forge_advanced_import",
        "render.roleplay_forge_advanced_scope",
    ]
    expected_actions = [
        "action.roleplay.forge.import_choose_file",
        "action.roleplay.forge.import_update_option",
        "action.roleplay.forge.import_clear",
        "action.roleplay.forge.scope_set_mode",
        "action.roleplay.forge.scope_search",
        "action.roleplay.forge.scope_use_record",
    ]
    roleplay_js = _read_text(FRONTEND_MODULE_ROOT / "roleplay.js")
    neo_js = _read_text(NEO_JS)
    checks = {
        "roleplay_module_exists": (FRONTEND_MODULE_ROOT / "roleplay.js").exists(),
        "runtime_loaded": bool(status.get("summary", {}).get("runtime_loaded_in_index")),
        "manifest_declares_advanced_import_renderers": all(area in migrated for area in expected_renderers),
        "manifest_declares_advanced_import_actions": all(area in migrated for area in expected_actions),
        "roleplay_module_has_advanced_import_renderer": "forgeAdvancedImportHtml" in roleplay_js and "roleplayForgeAdvancedImportHtml" in roleplay_js,
        "roleplay_module_has_scope_picker_renderer": "forgeScopePickerHtml" in roleplay_js and "roleplayForgeAdvancedScopeHtml" in roleplay_js,
        "roleplay_module_has_import_state_actions": all(snippet in roleplay_js for snippet in ["roleplayForgeImportChooseFile", "roleplayForgeImportUpdateOption", "roleplayForgeImportClear"]),
        "roleplay_module_has_scope_actions": all(snippet in roleplay_js for snippet in ["roleplayForgeSetScopeMode", "roleplayForgeSetScopeSearch", "roleplayForgeUseRecordAsScope"]),
        "legacy_wrappers_use_module_renderers": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeAdvancedImportHtml'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeAdvancedScopeHtml'",
        ]),
        "legacy_wrappers_use_module_actions": all(snippet in neo_js for snippet in [
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeImportChooseFile'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeImportUpdateOption'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeImportClear'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeSetScopeMode'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeSetScopeSearch'",
            "neoInvokeSurfaceModule('roleplay', 'roleplayForgeUseRecordAsScope'",
        ]),
    }
    return {
        "schema_id": "neo.surface.roleplay_forge_advanced_import_scope_status_status.v1",
        "release_stage": "public_preview",
        "status": "ready" if all(checks.values()) else "needs_attention",
        "summary": {
            "roleplay_migrated_area_count": len(migrated),
            "expected_renderer_count": len(expected_renderers),
            "expected_action_count": len(expected_actions),
            "checks_ready": sum(1 for value in checks.values() if value),
            "checks_total": len(checks),
            "roleplay_js_lines": _js_line_count(FRONTEND_MODULE_ROOT / "roleplay.js"),
            "neo_js_lines": status.get("summary", {}).get("neo_js_lines", 0),
        },
        "checks": checks,
        "roleplay_module": roleplay,
        "status_runtime": status,
        "policy": {
            "safe_extraction": "public preview migrates Roleplay Forge advanced import and active scope picker lanes into roleplay.js while keeping legacy wrappers as fallback.",
            "backend_boundary": "The module owns file selection state, scope picker UI state, and import option UI state. Backend remains authoritative for import parsing, conflict handling, save path, SQLite sync, and compile/runtime memory effects.",
            "sandbox_guard": "Active scope must remain explicit because imported records, child creation, Compile discovery, Runtime packets, and Assistant/Roleplay memory routing depend on scope boundaries.",
        },
    }


def roleplay_forge_advanced_import_scope_status_audit(write: bool = True) -> dict[str, Any]:
    status = roleplay_forge_advanced_import_scope_status_status()
    checks = status.get("checks", {})
    findings: list[dict[str, Any]] = []
    for key, ok in checks.items():
        if not ok:
            findings.append({
                "severity": "medium",
                "area": "roleplay_forge_advanced_import_scope",
                "title": f"Check failed: {key}",
                "detail": "Roleplay Forge advanced import/scope module status is incomplete.",
                "recommendation": "Keep legacy fallback active and complete the missing renderer/action/wrapper before migrating more Forge lanes.",
            })
    if not findings:
        findings.append({
            "severity": "info",
            "area": "roleplay_forge_advanced_import_scope",
            "title": "Roleplay Forge advanced import/scope status is ready",
            "detail": "Roleplay module owns active scope picker, import file state, import option state, clear/preview/import orchestration, and legacy fallback remains intact.",
            "recommendation": "Smoke-test active scope selection, scope search, import preview, import run, conflict modes, and Compile scope discovery after import.",
        })
    report = {
        "schema_id": "neo.surface.roleplay_forge_advanced_import_scope_status_audit.v1",
        "release_stage": "public_preview",
        "status": "completed" if status.get("status") == "ready" else "needs_attention",
        "summary": status.get("summary", {}),
        "findings": findings,
        "roleplay_forge_advanced_import_scope_status": status,
    }
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = REPORT_DIR / "m18_15_roleplay_forge_advanced_import_scope_status.json"
        md_path = REPORT_DIR / "m18_15_roleplay_forge_advanced_import_scope_status.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# Surface module status", "", f"Status: `{report['status']}`", "", "## Summary", ""]
        for key, value in report.get("summary", {}).items():
            lines.append(f"- **{key}**: {value}")
        lines += ["", "## Findings", ""]
        for item in findings:
            lines.append(f"- **{item.get('severity')} / {item.get('area')}** — {item.get('title')}: {item.get('detail')} Recommendation: {item.get('recommendation')}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(md_path)
    return report


# ---------------------------------------------------------------------------
# Public-preview compatibility aliases
# ---------------------------------------------------------------------------
# Phase 2 removed user-facing migration wording from runtime/status payloads, but
# neo_app.main and existing route names still import the older symbol names.
# Keep these aliases until the backend route names are migrated in a controlled
# follow-up pass. This preserves startup/runtime compatibility without bringing
# dev-facing text back into the UI.
surface_migration_runtime_status = surface_status_runtime_status
surface_migration_runtime_audit = surface_status_runtime_audit
admin_memory_cockpit_migration_status = admin_memory_cockpit_status_status
admin_memory_cockpit_migration_audit = admin_memory_cockpit_status_audit
admin_memory_cockpit_action_migration_status = admin_memory_cockpit_action_status_status
admin_memory_cockpit_action_migration_audit = admin_memory_cockpit_action_status_audit
assistant_surface_slice_migration_status = assistant_surface_slice_status_status
assistant_surface_slice_migration_audit = assistant_surface_slice_status_audit
assistant_deep_panel_migration_status = assistant_deep_panel_status_status
assistant_deep_panel_migration_audit = assistant_deep_panel_status_audit
roleplay_surface_slice_migration_status = roleplay_surface_slice_status_status
roleplay_surface_slice_migration_audit = roleplay_surface_slice_status_audit
roleplay_scene_director_cockpit_migration_status = roleplay_scene_director_cockpit_status_status
roleplay_scene_director_cockpit_migration_audit = roleplay_scene_director_cockpit_status_audit
roleplay_scene_chat_dispatch_migration_status = roleplay_scene_chat_dispatch_status_status
roleplay_scene_chat_dispatch_migration_audit = roleplay_scene_chat_dispatch_status_audit
roleplay_transcript_checkpoint_migration_status = roleplay_transcript_checkpoint_status_status
roleplay_transcript_checkpoint_migration_audit = roleplay_transcript_checkpoint_status_audit
roleplay_scene_state_checkpoint_inspector_migration_status = roleplay_scene_state_checkpoint_inspector_status_status
roleplay_scene_state_checkpoint_inspector_migration_audit = roleplay_scene_state_checkpoint_inspector_status_audit
roleplay_stories_workspace_migration_status = roleplay_stories_workspace_status_status
roleplay_stories_workspace_migration_audit = roleplay_stories_workspace_status_audit
roleplay_archive_provenance_graph_migration_status = roleplay_archive_provenance_graph_status_status
roleplay_archive_provenance_graph_migration_audit = roleplay_archive_provenance_graph_status_audit
roleplay_compile_runtime_deep_migration_status = roleplay_compile_runtime_deep_status_status
roleplay_compile_runtime_deep_migration_audit = roleplay_compile_runtime_deep_status_audit
roleplay_forge_builder_migration_status = roleplay_forge_builder_status_status
roleplay_forge_builder_migration_audit = roleplay_forge_builder_status_audit
roleplay_forge_sqlite_template_inspector_migration_status = roleplay_forge_sqlite_template_inspector_status_status
roleplay_forge_sqlite_template_inspector_migration_audit = roleplay_forge_sqlite_template_inspector_status_audit
roleplay_forge_advanced_import_scope_migration_status = roleplay_forge_advanced_import_scope_status_status
roleplay_forge_advanced_import_scope_migration_audit = roleplay_forge_advanced_import_scope_status_audit
