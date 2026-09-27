from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Final

CONTRACT_VERSION: Final[str] = "neo.knowledge.contract.v1"
REFERENCE_VERSION: Final[str] = "neo.knowledge.reference.v1"
EVIDENCE_VERSION: Final[str] = "neo.knowledge.evidence.v1"
REVISION_VERSION: Final[str] = "neo.knowledge.revision.v1"
RELATIONSHIP_VERSION: Final[str] = "neo.knowledge.relationship.v1"
PROJECTION_VERSION: Final[str] = "neo.knowledge.projection.v1"
ADAPTER_API_VERSION: Final[str] = "neo.knowledge.adapter.v1"

VALID_INTEGRITY_STATES: Final[frozenset[str]] = frozenset({"verified", "present", "stale", "missing", "unavailable"})
VALID_KNOWN_STATES: Final[frozenset[str]] = frozenset({"established", "explicit_unknown", "not_established", "unresolved_conflict"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8", errors="ignore")
    else:
        payload = canonical_json(value).encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()


def _opaque_id(prefix: str, *parts: Any, length: int = 24) -> str:
    joined = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()[:length]
    clean = re.sub(r"[^a-z0-9]+", "_", prefix.lower()).strip("_") or "id"
    return f"{clean}_{digest}"


def stable_knowledge_id(adapter_id: str, authority_namespace: str, native_id: str) -> str:
    return _opaque_id("kn", adapter_id, authority_namespace, native_id)


def stable_revision_id(knowledge_id: str, revision_token: Any) -> str:
    token = revision_token if isinstance(revision_token, str) else canonical_json(revision_token)
    return _opaque_id("rev", knowledge_id, token)


def stable_evidence_id(
    knowledge_id: str,
    revision_id: str,
    locator: Any,
    evidence_role: str,
    derivation: str,
) -> str:
    return _opaque_id("ev", knowledge_id, revision_id, canonical_json(locator), evidence_role, derivation)


def stable_projection_id(evidence_id: str, search_text: str, *, index_version: str = PROJECTION_VERSION) -> str:
    return _opaque_id("prj", evidence_id, index_version, content_hash(search_text))


@dataclass(slots=True)
class NativeAuthorityRef:
    adapter_id: str
    authority_namespace: str
    native_id: str
    resolver_kind: str
    resolver_key: dict[str, Any] = field(default_factory=dict)
    native_schema_id: str = ""
    native_schema_version: str = ""
    native_revision_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"schema_id": REFERENCE_VERSION, **asdict(self)}


@dataclass(slots=True)
class ContextRef:
    surface_id: str = "assistant"
    scope_id: str = "general"
    project_id: str = ""
    workspace_id: str = ""
    domain_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_value(cls, value: dict[str, Any] | None = None) -> "ContextRef":
        data = value if isinstance(value, dict) else {}
        identity = data.get("identity") if isinstance(data.get("identity"), dict) else data
        return cls(
            surface_id=str(identity.get("surface_id") or identity.get("surface") or "assistant"),
            scope_id=str(identity.get("scope_id") or "general"),
            project_id=str(identity.get("project_id") or ""),
            workspace_id=str(identity.get("workspace_id") or identity.get("workspace") or ""),
            domain_refs=[str(item) for item in (identity.get("domain_refs") or []) if str(item or "").strip()],
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceLocator:
    kind: str
    path: str = ""
    label: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "label": self.label, **self.fields}


@dataclass(slots=True)
class KnowledgeRef:
    knowledge_id: str
    kind: str
    adapter_id: str
    authority_namespace: str
    native_id: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceEnvelope:
    evidence_id: str
    about_ref: dict[str, Any]
    source_ref: dict[str, Any]
    revision_id: str
    native_ref: dict[str, Any]
    source_locator: dict[str, Any]
    context: dict[str, Any]
    provenance: dict[str, Any]
    semantic: dict[str, Any]
    workflow: dict[str, Any]
    lifecycle: dict[str, Any]
    conflict: dict[str, Any]
    temporal: dict[str, Any]
    retrieval: dict[str, Any]
    retention: dict[str, Any]
    compatibility: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"schema_id": EVIDENCE_VERSION, **asdict(self)}


@dataclass(slots=True)
class KnowledgeProjection:
    projection_id: str
    knowledge_ref: dict[str, Any]
    evidence_ref: dict[str, Any]
    revision_id: str
    context: dict[str, Any]
    search_text: str
    title: str
    lexical_terms: list[str]
    entity_hints: list[str]
    relation_hints: list[str]
    source_role: str
    lifecycle_state: str
    token_estimate: int
    indexed_at: str
    index_version: str = PROJECTION_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {"schema_id": PROJECTION_VERSION, **asdict(self)}


def make_context(value: dict[str, Any] | ContextRef | None = None, **overrides: Any) -> ContextRef:
    if isinstance(value, ContextRef):
        base = value
    else:
        base = ContextRef.from_value(value)
    data = base.as_dict()
    for key, val in overrides.items():
        if val not in (None, ""):
            data[key] = val
    return ContextRef(**data)


def make_native_ref(
    *, adapter_id: str, authority_namespace: str, native_id: str, resolver_kind: str,
    resolver_key: dict[str, Any] | None = None, native_schema_id: str = "",
    native_schema_version: str = "", native_revision_id: str = "",
) -> NativeAuthorityRef:
    return NativeAuthorityRef(
        adapter_id=adapter_id,
        authority_namespace=authority_namespace,
        native_id=str(native_id),
        resolver_kind=resolver_kind,
        resolver_key=dict(resolver_key or {}),
        native_schema_id=str(native_schema_id or ""),
        native_schema_version=str(native_schema_version or ""),
        native_revision_id=str(native_revision_id or ""),
    )


def build_projection_bundle(
    *,
    adapter_id: str,
    authority_namespace: str,
    native_id: str,
    kind: str,
    title: str,
    search_text: str,
    native_ref: NativeAuthorityRef,
    revision_token: Any,
    source_locator: SourceLocator,
    context: ContextRef | dict[str, Any] | None = None,
    evidence_role: str,
    derivation: str = "direct",
    origin: str = "native_store",
    source_integrity: str = "verified",
    claim_type: str = "native_record",
    epistemic_state: str = "established",
    execution_state: str = "not_applicable",
    user_confirmation: str = "not_applicable",
    approval_state: str = "not_applicable",
    canon_state: str = "not_applicable",
    canon_domain_ref: str = "",
    lifecycle_state: str = "active",
    supersedes: list[str] | None = None,
    superseded_by: list[str] | None = None,
    conflict_state: str = "none",
    temporal: dict[str, Any] | None = None,
    importance: str = "normal",
    visibility: str = "normal",
    priority_hint: float | None = None,
    storage_class: str = "native_authority",
    compatibility: dict[str, Any] | None = None,
    entity_hints: list[str] | None = None,
    relation_hints: list[str] | None = None,
    lexical_terms: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = make_context(context)
    knowledge_id = stable_knowledge_id(adapter_id, authority_namespace, str(native_id))
    knowledge_ref = KnowledgeRef(knowledge_id, kind, adapter_id, authority_namespace, str(native_id))
    revision_id = stable_revision_id(knowledge_id, revision_token)
    ev_id = stable_evidence_id(knowledge_id, revision_id, source_locator.as_dict(), evidence_role, derivation)
    evidence = EvidenceEnvelope(
        evidence_id=ev_id,
        about_ref=knowledge_ref.as_dict(),
        source_ref=knowledge_ref.as_dict(),
        revision_id=revision_id,
        native_ref=native_ref.as_dict(),
        source_locator=source_locator.as_dict(),
        context=ctx.as_dict(),
        provenance={"origin": origin, "derivation": derivation, "source_integrity": source_integrity},
        semantic={"evidence_role": evidence_role, "claim_type": claim_type, "epistemic_state": epistemic_state},
        workflow={
            "execution_state": execution_state,
            "user_confirmation": user_confirmation,
            "approval_state": approval_state,
            "canon_state": canon_state,
            "canon_domain_ref": canon_domain_ref,
        },
        lifecycle={"state": lifecycle_state, "supersedes": list(supersedes or []), "superseded_by": list(superseded_by or [])},
        conflict={"state": conflict_state},
        temporal={**(temporal or {}), "indexed_at": utc_now_iso()},
        retrieval={"importance": importance, "visibility": visibility, "priority_hint": priority_hint},
        retention={"storage_class": storage_class},
        compatibility=dict(compatibility or {}),
    )
    normalized_text = str(search_text or title or native_id).strip()
    projection = KnowledgeProjection(
        projection_id=stable_projection_id(ev_id, normalized_text),
        knowledge_ref=knowledge_ref.as_dict(),
        evidence_ref={"evidence_id": ev_id, "adapter_id": adapter_id, "native_ref": native_ref.as_dict(), "source_locator": source_locator.as_dict()},
        revision_id=revision_id,
        context=ctx.as_dict(),
        search_text=normalized_text,
        title=str(title or native_id),
        lexical_terms=list(lexical_terms or []),
        entity_hints=list(entity_hints or []),
        relation_hints=list(relation_hints or []),
        source_role=evidence_role,
        lifecycle_state=lifecycle_state,
        token_estimate=max(1, len(normalized_text) // 4),
        indexed_at=utc_now_iso(),
    )
    return {
        "schema_id": CONTRACT_VERSION,
        "kind": kind,
        "knowledge_ref": knowledge_ref.as_dict(),
        "native_ref": native_ref.as_dict(),
        "revision": {"schema_id": REVISION_VERSION, "revision_id": revision_id, "knowledge_id": knowledge_id, "content_hash": content_hash(revision_token)},
        "evidence": evidence.as_dict(),
        "projection": projection.as_dict(),
        "payload": dict(payload or {}),
    }
