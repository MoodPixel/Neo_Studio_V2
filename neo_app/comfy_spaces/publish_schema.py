from __future__ import annotations

import re
from typing import Literal

from pydantic import field_validator

from neo_app.comfy_spaces.schema import StrictContractModel


SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION = "neo.comfy_space.publication_receipt.v1"
_SPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ComfySpacePublicationReceipt(StrictContractModel):
    schema_version: Literal[SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION] = SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION
    kind: Literal["comfy_space_publication_receipt"] = "comfy_space_publication_receipt"
    space_id: str
    version: str
    draft_id: str
    draft_revision: int
    published_at: str
    manifest_sha256: str
    workflow_sha256: str
    ui_sha256: str
    graph_anchors_sha256: str = ""
    stages_sha256: str = ""
    dynamic_controls_sha256: str = ""
    migration_sha256: str = ""
    replaces_version: str = ""
    source_workflow_sha256: str
    source_analysis_sha256: str
    dependency_preflight_state: str
    dependency_preflight_checked: bool
    portability_ready: Literal[True] = True
    trust_validated: Literal[True] = True
    package_validated: Literal[True] = True
    execution_performed: Literal[False] = False
    generated_neo_code: Literal[False] = False
    automatic_dependency_install: Literal[False] = False

    @field_validator("space_id", "draft_id")
    @classmethod
    def validate_id(cls, value: str, info) -> str:
        text = str(value or "").strip().lower()
        if not _SPACE_ID_RE.fullmatch(text):
            raise ValueError(f"{info.field_name} is invalid")
        return text

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not _VERSION_RE.fullmatch(text):
            raise ValueError("version must be semantic version text")
        return text


    @field_validator("replaces_version")
    @classmethod
    def validate_replaces_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if not _VERSION_RE.fullmatch(text):
            raise ValueError("replaces_version must be semantic version text")
        return text

    @field_validator(
        "manifest_sha256",
        "workflow_sha256",
        "ui_sha256",
        "graph_anchors_sha256",
        "stages_sha256",
        "dynamic_controls_sha256",
        "migration_sha256",
        "source_workflow_sha256",
        "source_analysis_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str, info) -> str:
        text = str(value or "").strip().lower()
        if info.field_name in {"graph_anchors_sha256", "stages_sha256", "dynamic_controls_sha256", "migration_sha256"} and not text:
            return ""
        if not _SHA256_RE.fullmatch(text):
            raise ValueError(f"{info.field_name} must be a SHA-256 hex digest")
        return text
