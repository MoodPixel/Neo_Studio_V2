from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.lora_stack.compatibility_recovery.v1"


def _suggestions(name: str, catalog: list[str], limit: int = 3) -> list[dict[str, Any]]:
    wanted = str(name or "").casefold()
    scored: list[tuple[float, str]] = []
    for candidate in catalog:
        value = str(candidate or "")
        if not value: continue
        ratio = SequenceMatcher(None, wanted, value.casefold()).ratio()
        if wanted and (wanted in value.casefold() or value.casefold() in wanted): ratio += 0.25
        scored.append((min(ratio, 1.0), value))
    return [{"name": name, "confidence": round(score, 3)} for score, name in sorted(scored, key=lambda item: (-item[0], item[1].casefold()))[:limit] if score >= 0.35]


def reconcile_video_lora_rows(
    rows: list[dict[str, Any]] | None,
    *,
    catalog: list[str] | None,
    support: dict[str, Any] | None,
    catalog_ready: bool,
    stack_enabled: bool,
    route_id: str = "",
    profile_id: str = "",
) -> dict[str, Any]:
    available = [str(value) for value in (catalog or []) if str(value)]
    folded = {value.casefold(): value for value in available}
    supports_standard = bool((support or {}).get("supports_standard_lora"))
    supports_speed = bool((support or {}).get("supports_speed_lora"))
    targets = [str(value) for value in ((support or {}).get("allowed_targets") or ["all"])]
    result_rows: list[dict[str, Any]] = []
    for index, source in enumerate(rows or []):
        row = deepcopy(source) if isinstance(source, dict) else {}
        uid = str(row.get("uid") or f"video_lora_{index + 1}")
        name = str(row.get("name") or row.get("lora_name") or "").strip()
        role = "speed" if str(row.get("role") or "standard") == "speed" else "standard"
        target = str(row.get("target") or "all")
        enabled = row.get("enabled", True) is not False
        issues: list[dict[str, str]] = []
        canonical_name = folded.get(name.casefold(), "") if name else ""
        if not catalog_ready:
            issues.append({"code": "catalog_unavailable", "message": "Refresh the selected backend profile's live LoRA catalog before generating."})
        elif not name or not canonical_name:
            issues.append({"code": "missing_file", "message": f"{name or 'This saved LoRA'} is not available on the selected backend profile."})
        if role == "speed" and not supports_speed:
            issues.append({"code": "role_blocked", "message": "Speed/Turbo is not supported on the selected route."})
        if role == "standard" and not supports_standard:
            issues.append({"code": "role_blocked", "message": "Standard LoRA is not supported on the selected route."})
        if target not in targets:
            issues.append({"code": "target_blocked", "message": f"Target {target!r} is not supported on the selected route."})
        blocking = bool(stack_enabled and enabled and issues)
        result_rows.append({
            "uid": uid, "index": index, "enabled": enabled, "name": name,
            "canonical_catalog_name": canonical_name, "role": role, "target": target,
            "status": "blocked" if blocking else "attention" if issues else "ready",
            "blocking": blocking, "issues": issues,
            "suggestions": _suggestions(name, available) if catalog_ready and not canonical_name else [],
            "actions": ["replace", "disable", "remove"] if issues else [],
        })
    blocked = [row for row in result_rows if row["blocking"]]
    issue_counts: dict[str, int] = {}
    for row in result_rows:
        for issue in row["issues"]: issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "route_id": route_id,
        "profile_id": profile_id,
        "catalog_ready": bool(catalog_ready),
        "stack_enabled": bool(stack_enabled),
        "rows": result_rows,
        "blocking": bool(blocked),
        "blocking_row_uids": [row["uid"] for row in blocked],
        "issue_counts": issue_counts,
        "generation_allowed": not blocked,
        "recovery_policy": "preserve_intent_require_explicit_replace_disable_or_remove",
    }


def apply_recovery_action(rows: list[dict[str, Any]], *, uid: str, action: str, replacement: str = "") -> list[dict[str, Any]]:
    updated = [deepcopy(row) for row in rows]
    index = next((i for i, row in enumerate(updated) if str(row.get("uid") or "") == str(uid)), -1)
    if index < 0: raise ValueError(f"Video LoRA recovery row {uid!r} was not found.")
    if action == "replace":
        name = str(replacement or "").strip()
        if not name: raise ValueError("Video LoRA replacement filename is required.")
        updated[index]["name"] = name
    elif action == "disable": updated[index]["enabled"] = False
    elif action == "remove": updated.pop(index)
    else: raise ValueError(f"Unknown Video LoRA recovery action {action!r}.")
    return updated
