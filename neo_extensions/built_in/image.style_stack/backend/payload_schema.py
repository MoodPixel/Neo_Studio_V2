"""Style Stack frontend payload schema helpers.

Phase F makes the browser submit a canonical ``extensions.style_stack`` block.
The backend prompt merge hook is still deferred to Phase G, but this module now
normalizes the frontend payload shape so tests and future backend code share one
contract.
"""

from __future__ import annotations

from typing import Any, Mapping

EXTENSION_ID = "style_stack"
VALID_TARGETS = ("both", "base", "finish")

DEFAULT_STYLE_STACK_PAYLOAD: dict[str, Any] = {
    "enabled": True,
    "version": 1,
    "inputs": {
        "active_styles": [],
        "manual_positive": "",
        "manual_negative": "",
    },
    "params": {
        "target": "both",
    },
    "assets": {
        "style_names": [],
        "style_records": [],
    },
    "metadata": {
        "source": "image.generations.prompt.style_stack",
        "prompt_only": True,
        "merge_policy": "append_deduped",
        "ui_phase": "G-prompt-merge-hook",
        "runtime_merge_status": "applied_phase_g",
    },
}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    clean: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        clean.append(name)
    return clean


def normalize_style_stack_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a safe canonical ``extensions.style_stack`` payload block."""
    payload = dict(payload or {})
    inputs = dict(payload.get("inputs") or {})
    params = dict(payload.get("params") or {})
    assets = dict(payload.get("assets") or {})
    metadata = dict(payload.get("metadata") or {})

    active_styles = _string_list(inputs.get("active_styles") or assets.get("style_names"))
    target = str(params.get("target") or "both").strip().lower()
    if target not in VALID_TARGETS:
        target = "both"

    style_records = assets.get("style_records") if isinstance(assets.get("style_records"), list) else []
    clean_records = []
    for style in style_records:
        if not isinstance(style, Mapping):
            continue
        name = str(style.get("name") or "").strip()
        if not name:
            continue
        clean_records.append({
            "name": name,
            "prompt": str(style.get("prompt") or ""),
            "negative_prompt": str(style.get("negative_prompt") or ""),
        })

    manual_positive = str(inputs.get("manual_positive") or "")
    manual_negative = str(inputs.get("manual_negative") or "")
    has_intent = bool(active_styles or manual_positive.strip() or manual_negative.strip())
    enabled = bool(payload.get("enabled", True) and has_intent)

    return {
        "enabled": enabled,
        "version": int(payload.get("version") or 1),
        "inputs": {
            "active_styles": active_styles,
            "manual_positive": manual_positive,
            "manual_negative": manual_negative,
        } if enabled else {},
        "params": {"target": target} if enabled else {},
        "assets": {"style_names": active_styles, "style_records": clean_records} if enabled else {},
        "metadata": {
            "source": "image.generations.prompt.style_stack",
            "prompt_only": True,
            "merge_policy": "append_deduped",
            "ui_phase": metadata.get("ui_phase") or "G-prompt-merge-hook",
            "runtime_merge_status": metadata.get("runtime_merge_status") or "applied_phase_g",
            **{k: v for k, v in metadata.items() if k not in {"source", "prompt_only", "merge_policy", "ui_phase", "runtime_merge_status"}},
        },
    }
