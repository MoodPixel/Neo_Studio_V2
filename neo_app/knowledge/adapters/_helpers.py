from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from neo_app.knowledge.contracts import ContextRef, SourceLocator, build_projection_bundle, make_native_ref, content_hash
from neo_app.knowledge.io import safe_relative, read_json


def compact_text(*parts: Any, limit: int = 12000) -> str:
    text = "\n".join(str(part or "").strip() for part in parts if str(part or "").strip())
    return text[:limit]


def record_revision_token(record: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    return {
        "record_hash": content_hash(record),
        "updated_at": record.get("updated_at") or record.get("created_at") or "",
        "path": path.as_posix() if path else "",
    }


def json_sidecar_projection(
    *, root_dir: Path, adapter_id: str, authority_namespace: str, native_id: str,
    kind: str, title: str, record: dict[str, Any], path: Path, search_text: str,
    evidence_role: str, context: ContextRef, native_schema_id: str = "",
    execution_state: str = "not_applicable", lifecycle_state: str = "active",
    canon_state: str = "not_applicable", canon_domain_ref: str = "",
    claim_type: str = "native_record", entity_hints: list[str] | None = None,
    relation_hints: list[str] | None = None, compatibility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rel = safe_relative(root_dir, path)
    native_ref = make_native_ref(
        adapter_id=adapter_id,
        authority_namespace=authority_namespace,
        native_id=native_id,
        resolver_kind="sidecar",
        resolver_key={"path": rel},
        native_schema_id=native_schema_id,
    )
    return build_projection_bundle(
        adapter_id=adapter_id,
        authority_namespace=authority_namespace,
        native_id=native_id,
        kind=kind,
        title=title,
        search_text=search_text,
        native_ref=native_ref,
        revision_token=record_revision_token(record, path=path),
        source_locator=SourceLocator(kind="record_key", path=rel, fields={"record_key": native_id}),
        context=context,
        evidence_role=evidence_role,
        derivation="direct",
        origin="native_store",
        source_integrity="verified",
        claim_type=claim_type,
        epistemic_state="established",
        execution_state=execution_state,
        canon_state=canon_state,
        canon_domain_ref=canon_domain_ref,
        lifecycle_state=lifecycle_state,
        entity_hints=entity_hints or [],
        relation_hints=relation_hints or [],
        payload=record,
        compatibility=compatibility or {},
    )


def read_json_dict(path: Path) -> dict[str, Any] | None:
    data = read_json(path, default=None)
    return data if isinstance(data, dict) else None


def status_to_lifecycle(status: Any) -> str:
    value = str(status or "").strip().lower()
    if value in {"archived", "deprecated", "superseded", "deleted"}:
        return value
    if value in {"draft", "candidate", "candidate_canon", "foundation_stub", "draft_breakdown"}:
        return "draft"
    return "active"
