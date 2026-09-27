from __future__ import annotations

from typing import Any


def map_legacy_policy_fields(record: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conservative NKB-4/NKB-5 compatibility mapping.

    Ambiguous legacy values stay compatibility metadata. This helper deliberately
    refuses to promote ``confirmed`` to universal truth/canon authority.
    """
    data = record if isinstance(record, dict) else {}
    trust = str(data.get("trust_level") or "").strip().lower()
    state = str(data.get("memory_state") or data.get("status") or "").strip().lower()
    retention = str(data.get("retention_scope") or "").strip().lower()
    approval = str(data.get("approval_state") or "").strip().lower()
    confidence = data.get("confidence")

    lifecycle = "active"
    if state in {"archived", "deprecated", "superseded"}:
        lifecycle = state
    elif state in {"draft", "candidate", "candidate_canon"}:
        lifecycle = "draft"

    canon_state = "not_applicable"
    if state == "canon" or retention == "canon":
        canon_state = "accepted_legacy"

    approval_state = approval or "unknown"
    epistemic_state = "unknown"
    mapping_status = "partial"
    if trust == "inferred":
        epistemic_state = "inferred"
    elif trust in {"conflicting", "mixed"}:
        epistemic_state = "contested"
    elif trust in {"draft", "deprecated", "system", "confirmed", "external", ""}:
        epistemic_state = "legacy_unresolved"
        mapping_status = "ambiguous"

    return {
        "mapping_status": mapping_status,
        "lifecycle_state": lifecycle,
        "canon_state": canon_state,
        "approval_state": approval_state,
        "epistemic_state": epistemic_state,
        "confidence": confidence,
        "legacy": {
            "trust_level": data.get("trust_level"),
            "memory_state": data.get("memory_state"),
            "retention_scope": data.get("retention_scope"),
            "approval_state": data.get("approval_state"),
            "importance": data.get("importance"),
            "confidence": confidence,
            "status": data.get("status"),
        },
        "policy": "Legacy trust/status fields are compatibility evidence only. confirmed does not imply universal truth or canon.",
    }
