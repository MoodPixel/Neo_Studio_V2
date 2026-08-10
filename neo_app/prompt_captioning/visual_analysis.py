from __future__ import annotations

from copy import deepcopy
from typing import Any

from .profile_contract import build_profile_instruction_blocks, normalize_profile

VISUAL_ANALYSIS_SCHEMA_VERSION = "prompt_captioning.visual_analysis.v1"

# Canonical image-understanding fields. These are intentionally descriptive and
# model-neutral so Caption Studio, Dataset, Library, image editing, and image-to-
# video prompt generation can consume the same analysis without sharing provider
# implementation details.
VISUAL_ANALYSIS_FIELDS = (
    "subjects",
    "appearance",
    "pose",
    "expression",
    "clothing",
    "environment",
    "composition",
    "camera",
    "lighting",
    "visual_style",
    "visible_text",
    "actions_interactions",
    "objects",
    "uncertainties",
)


def empty_visual_analysis() -> dict[str, Any]:
    return {
        "schema_version": VISUAL_ANALYSIS_SCHEMA_VERSION,
        "subjects": [],
        "appearance": {},
        "pose": {},
        "expression": {},
        "clothing": {},
        "environment": {},
        "composition": {},
        "camera": {},
        "lighting": {},
        "visual_style": {},
        "visible_text": [],
        "actions_interactions": [],
        "objects": [],
        "uncertainties": [],
    }


def _list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def normalize_visual_analysis(value: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize provider/model visual analysis into the shared P23 schema."""
    source = value if isinstance(value, dict) else {}
    result = empty_visual_analysis()
    for field in VISUAL_ANALYSIS_FIELDS:
        if field in {"subjects", "visible_text", "actions_interactions", "objects", "uncertainties"}:
            result[field] = _list(source.get(field))
        else:
            result[field] = _dict(source.get(field))
    return result


def build_visual_analysis_request(
    *,
    profile: dict[str, Any] | None = None,
    user_instruction: str = "",
    task: str = "caption_image",
) -> dict[str, Any]:
    """Build a provider-neutral, grounded image-analysis request.

    This is the shared P23 visual-analysis contract. It deliberately does not
    choose a local VLM provider. P23.2 Caption Studio consumes the same request
    semantics while the selected vision backend remains provider-configurable.
    """
    requested = profile if isinstance(profile, dict) else {}
    surface = str(requested.get("surface") or "caption_studio").strip()
    normalized = normalize_profile(requested, surface=surface).get("profile") or {}
    instruction_contract = build_profile_instruction_blocks(normalized, user_instruction, surface=surface)
    return {
        "schema_version": "prompt_captioning.visual_analysis_request.v1",
        "task": str(task or "caption_image"),
        "profile": normalized,
        "instruction_blocks": instruction_contract.get("blocks") or [],
        "response_schema": {
            "schema_version": VISUAL_ANALYSIS_SCHEMA_VERSION,
            "fields": list(VISUAL_ANALYSIS_FIELDS),
            "rules": [
                "Return only observations supported by the image or explicit user context.",
                "Use uncertainties for visually ambiguous claims instead of converting guesses into facts.",
                "Keep source facts separate from requested visual transformation language.",
            ],
        },
    }
