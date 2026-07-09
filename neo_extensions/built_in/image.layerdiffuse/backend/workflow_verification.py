"""LayerDiffuse workflow export verification helpers.

Phase B scope:
- inspect local workflow_templates without executing ComfyUI
- distinguish verified executable exports from mapping-only placeholders
- expose a transparent report for adapter payloads, UI/docs, and tests
- keep blocked/background modes blocked until a real exported graph exists
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
import json

try:
    from .capability_registry import EXTENSION_ID, mode_config_map, normalize_mode_id
except Exception:  # pragma: no cover - direct script execution fallback
    from capability_registry import EXTENSION_ID, mode_config_map, normalize_mode_id

WORKFLOW_EXPORT_VERIFICATION_VERSION = "layerdiffuse-workflow-export-verification-v1"
TEMPLATE_DIR_NAME = "workflow_templates"
VERIFIED_STATUSES = {"verified_executable", "executable"}
BLOCKED_STATUSES = {"requires_verified_workflow_export", "blocked_until_exported_workflow_mapping", "mapping_only"}


def _extension_root_from_file() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"_missing": str(path)}
    except json.JSONDecodeError as exc:
        return {"_invalid_json": str(path), "error": str(exc)}


def _node_classes(template: Mapping[str, Any]) -> list[str]:
    graph = template.get("graph") if isinstance(template.get("graph"), Mapping) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), Mapping) else {}
    classes: list[str] = []
    for node in nodes.values():
        if isinstance(node, Mapping):
            class_type = str(node.get("class_type") or "").strip()
            if class_type:
                classes.append(class_type)
    return classes


def _template_path(template_id: str | None, extension_root: str | Path | None = None) -> Path | None:
    if not template_id:
        return None
    root = Path(extension_root) if extension_root else _extension_root_from_file()
    return root / TEMPLATE_DIR_NAME / template_id


def verify_template_export(
    template_id: str | None,
    *,
    mode: str | None = None,
    extension_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return a non-executing verification report for one workflow template."""
    path = _template_path(template_id, extension_root)
    mode_id = normalize_mode_id(mode or "transparent_asset") if mode else None
    report: dict[str, Any] = {
        "contract_version": WORKFLOW_EXPORT_VERIFICATION_VERSION,
        "extension_id": EXTENSION_ID,
        "mode": mode_id,
        "template_id": template_id,
        "exists": False,
        "valid_json": False,
        "status": "missing_template",
        "verified_export": False,
        "executable_graph": False,
        "graph_has_nodes": False,
        "node_count": 0,
        "required_nodes": [],
        "graph_node_classes": [],
        "missing_required_nodes_in_graph": [],
        "output_roles": [],
        "primary_output": None,
        "blocked_reason": None,
        "warnings": [],
        "errors": [],
        "source": "local_extension_template",
    }
    if path is None:
        report["blocked_reason"] = "no_template_id_declared"
        report["errors"].append("no_template_id_declared")
        return report

    template = _load_json(path)
    report["exists"] = bool(path.exists())
    if template.get("_missing"):
        report["blocked_reason"] = "workflow_template_file_missing"
        report["errors"].append(f"workflow_template_file_missing:{template_id}")
        return report
    if template.get("_invalid_json"):
        report["status"] = "invalid_json"
        report["blocked_reason"] = "workflow_template_invalid_json"
        report["errors"].append(f"workflow_template_invalid_json:{template_id}")
        return report

    report["valid_json"] = True
    status = str(template.get("status") or "unknown").strip() or "unknown"
    report["status"] = status
    graph = template.get("graph") if isinstance(template.get("graph"), Mapping) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), Mapping) else {}
    node_classes = _node_classes(template)
    required_nodes = [str(item) for item in (template.get("required_nodes") or [])]
    output_roles = [str(item.get("type")) for item in (template.get("outputs") or []) if isinstance(item, Mapping) and item.get("type")]
    primary_output = graph.get("primary_output") or template.get("primary_output")
    missing_required = sorted(set(required_nodes) - set(node_classes))

    report.update({
        "graph_has_nodes": bool(nodes),
        "node_count": len(nodes),
        "required_nodes": required_nodes,
        "graph_node_classes": sorted(set(node_classes)),
        "missing_required_nodes_in_graph": missing_required,
        "output_roles": output_roles,
        "primary_output": deepcopy(primary_output),
        "executable_graph": bool(nodes) and status in VERIFIED_STATUSES and not missing_required,
        "verified_export": bool(nodes) and status in VERIFIED_STATUSES and not missing_required,
    })

    if status in BLOCKED_STATUSES or not report["verified_export"]:
        if not nodes:
            report["blocked_reason"] = "requires_verified_exported_graph_nodes"
            report["errors"].append("requires_verified_exported_graph_nodes")
        elif missing_required:
            report["blocked_reason"] = "exported_graph_missing_required_layerdiffuse_nodes"
            report["errors"].append("exported_graph_missing_required_layerdiffuse_nodes:" + ",".join(missing_required))
        elif status not in VERIFIED_STATUSES:
            report["blocked_reason"] = f"template_status_not_verified:{status}"
            report["errors"].append(f"template_status_not_verified:{status}")
    else:
        report["warnings"].append("verified_export_available_local_template_only_not_runtime_executed")

    return report


def verify_mode_export(mode: str, *, model_family: str = "sdxl", extension_root: str | Path | None = None) -> dict[str, Any]:
    mode_id = normalize_mode_id(mode)
    cfg = mode_config_map().get(mode_id) or {}
    template_map = cfg.get("template") or {}
    template_id = template_map.get(model_family) or cfg.get("fallback_template")
    report = verify_template_export(template_id, mode=mode_id, extension_root=extension_root)
    report.update({
        "mode": mode_id,
        "model_family": model_family,
        "capability_status": cfg.get("status"),
        "capability_executable": bool(cfg.get("executable")),
        "capability_blocked_reason": cfg.get("blocked_reason"),
    })
    return report


def build_workflow_export_verification_report(*, extension_root: str | Path | None = None) -> dict[str, Any]:
    modes = mode_config_map()
    mode_reports: dict[str, Any] = {}
    for mode_id, cfg in modes.items():
        families = list(cfg.get("model_families") or ["sdxl"])
        family_reports = {}
        for family in families:
            family_reports[family] = verify_mode_export(mode_id, model_family=family, extension_root=extension_root)
        # The primary report is the first declared model family. Family reports keep all details.
        primary_family = families[0] if families else "sdxl"
        primary = deepcopy(family_reports.get(primary_family) or {})
        primary["family_reports"] = family_reports
        mode_reports[mode_id] = primary

    verified_modes = sorted([mode for mode, report in mode_reports.items() if report.get("verified_export")])
    blocked_modes = sorted([mode for mode, report in mode_reports.items() if not report.get("verified_export")])
    return {
        "contract_version": WORKFLOW_EXPORT_VERIFICATION_VERSION,
        "extension_id": EXTENSION_ID,
        "source": "local_extension_templates",
        "runtime_execution_performed": False,
        "hidden_enablement_allowed": False,
        "verified_modes": verified_modes,
        "blocked_modes": blocked_modes,
        "modes": mode_reports,
    }
