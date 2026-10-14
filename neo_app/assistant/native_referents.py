from __future__ import annotations

from copy import deepcopy
from typing import Any

NATIVE_REFERENTS_SCHEMA_ID = "neo.assistant.native_referents.v1"


def _clean(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit and len(text) > limit else text


def _bounded_image_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "attachment_id": _clean(item.get("attachment_id"), 120),
        "filename": _clean(item.get("filename"), 400),
        "proof_level": _clean(item.get("proof_level"), 120) or "unidentified",
        "known_state": _clean(item.get("known_state"), 120) or "not_established",
        "identity": deepcopy(item.get("identity")) if isinstance(item.get("identity"), dict) else {},
        "model": deepcopy(item.get("model")) if isinstance(item.get("model"), dict) else {},
        "sampling": deepcopy(item.get("sampling")) if isinstance(item.get("sampling"), dict) else {},
        "external_metadata": deepcopy(item.get("external_metadata")) if isinstance(item.get("external_metadata"), dict) else {},
    }


def session_native_referents(session: dict[str, Any] | None) -> dict[str, Any]:
    data = session if isinstance(session, dict) else {}
    refs = data.get("native_referents") if isinstance(data.get("native_referents"), dict) else {}
    return deepcopy(refs)


def update_image_referent(
    session: dict[str, Any],
    image_context: list[dict[str, Any]] | None,
    *,
    updated_at: str,
) -> dict[str, Any]:
    """Update the ephemeral/session Image referent from explicit current-turn attachments.

    One image becomes the active referent. Multiple images intentionally replace the
    previous single target with an ambiguous referent so a later deictic request cannot
    silently bleed back to an older image. No-image turns leave the referent unchanged.
    """
    rows = [item for item in (image_context or []) if isinstance(item, dict)]
    refs = session_native_referents(session)
    if not rows:
        return refs
    if len(rows) == 1:
        item = _bounded_image_candidate(rows[0])
        refs["image"] = {
            "schema_id": NATIVE_REFERENTS_SCHEMA_ID,
            "scope": "image",
            "kind": "asset",
            "status": "active",
            "source": "assistant_attachment",
            "updated_at": updated_at,
            **item,
        }
    else:
        refs["image"] = {
            "schema_id": NATIVE_REFERENTS_SCHEMA_ID,
            "scope": "image",
            "kind": "asset",
            "status": "ambiguous",
            "source": "assistant_attachments",
            "updated_at": updated_at,
            "candidates": [_bounded_image_candidate(item) for item in rows[:6]],
        }
    session["native_referents"] = refs
    return refs


def active_image_referent(session: dict[str, Any] | None) -> dict[str, Any]:
    refs = session_native_referents(session)
    image = refs.get("image") if isinstance(refs.get("image"), dict) else {}
    return deepcopy(image)
