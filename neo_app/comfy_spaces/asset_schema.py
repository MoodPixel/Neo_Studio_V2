from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.binding_engine import SpaceAssetReference
from neo_app.comfy_spaces.schema import StrictContractModel


SPACE_ASSET_BUS_SCHEMA_VERSION = "neo.comfy_space.asset_bus.v1"
SPACE_ASSET_RECORD_SCHEMA_VERSION = "neo.comfy_space.asset.v1"

SpaceAssetKind = Literal["image", "mask", "video", "audio", "file"]
SpaceAssetDirection = Literal["input", "output", "intermediate"]
SpaceAssetOrigin = Literal["upload", "local_import", "comfy_output", "generated_intermediate"]
SpaceAssetRetention = Literal["temporary", "durable"]
SpaceAssetState = Literal["ready", "deleted"]

_ASSET_ID_RE = re.compile(r"^asset_[a-f0-9]{32}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class SpaceAssetSource(StrictContractModel):
    runtime_profile_id: str | None = None
    provider_id: str | None = None
    prompt_id: str | None = None
    node_id: str | None = None
    filename: str | None = None
    subfolder: str = ""
    artifact_type: str | None = None
    label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpaceAssetTransportRecord(StrictContractModel):
    runtime_profile_id: str
    provider_id: str
    method: str
    comfy_name: str
    comfy_subfolder: str = ""
    comfy_type: Literal["input"] = "input"
    comfy_value: str
    verified: bool = True
    content_sha256: str
    resolved_at: str

    @field_validator("runtime_profile_id", "provider_id", "method", "comfy_name", "comfy_value", "resolved_at")
    @classmethod
    def require_text(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        return text

    @field_validator("content_sha256")
    @classmethod
    def validate_sha(cls, value: str) -> str:
        digest = str(value or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("content_sha256 must be a SHA-256 hex digest")
        return digest


class ComfySpaceAssetRecord(StrictContractModel):
    schema_version: Literal[SPACE_ASSET_RECORD_SCHEMA_VERSION] = SPACE_ASSET_RECORD_SCHEMA_VERSION
    asset_id: str
    kind: SpaceAssetKind
    direction: SpaceAssetDirection
    origin: SpaceAssetOrigin
    state: SpaceAssetState = "ready"
    original_filename: str
    stored_filename: str
    content_type: str
    size_bytes: int = Field(ge=1)
    sha256: str
    storage_relpath: str
    retention: SpaceAssetRetention
    created_at: str
    updated_at: str
    expires_at: str | None = None
    label: str = ""
    source: SpaceAssetSource | None = None
    transports: list[SpaceAssetTransportRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _ASSET_ID_RE.fullmatch(text):
            raise ValueError("asset_id must use Neo's asset_<32 hex> form")
        return text

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        digest = str(value or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("sha256 must be a 64-character SHA-256 hex digest")
        return digest

    @field_validator("original_filename", "stored_filename", "content_type", "storage_relpath", "created_at", "updated_at")
    @classmethod
    def require_non_empty(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        return text

    @field_validator("storage_relpath")
    @classmethod
    def validate_storage_relpath(cls, value: str) -> str:
        text = str(value or "").strip().replace("\\", "/")
        if text.startswith("/") or re.match(r"^[A-Za-z]:/", text):
            raise ValueError("storage_relpath must be Neo-root relative")
        parts = [part for part in text.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise ValueError("storage_relpath must not traverse outside Neo data")
        return "/".join(parts)

    @model_validator(mode="after")
    def validate_retention(self):
        if self.retention == "temporary" and not self.expires_at:
            raise ValueError("temporary assets require expires_at")
        if self.retention == "durable" and self.expires_at:
            raise ValueError("durable assets must not have expires_at")
        return self


class SpaceAssetIssue(StrictContractModel):
    code: str
    message: str
    input_id: str | None = None
    asset_id: str | None = None


class SpaceAssetIngestReport(StrictContractModel):
    schema_version: Literal[SPACE_ASSET_BUS_SCHEMA_VERSION] = SPACE_ASSET_BUS_SCHEMA_VERSION
    ok: bool = True
    asset: ComfySpaceAssetRecord
    asset_ref: SpaceAssetReference
    content_url: str
    workflow_execution_performed: Literal[False] = False


class SpaceAssetResolutionRecord(StrictContractModel):
    asset_id: str
    kind: SpaceAssetKind
    runtime_profile_id: str
    comfy_value: str
    reused_transport: bool = False
    verified: bool = True
    transport_method: str


class SpaceAssetBindingResolutionReport(StrictContractModel):
    schema_version: Literal[SPACE_ASSET_BUS_SCHEMA_VERSION] = SPACE_ASSET_BUS_SCHEMA_VERSION
    ok: bool
    ready_for_binding: bool
    runtime_profile_id: str
    resolved_values: dict[str, Any] = Field(default_factory=dict)
    resolutions: list[SpaceAssetResolutionRecord] = Field(default_factory=list)
    errors: list[SpaceAssetIssue] = Field(default_factory=list)
    warnings: list[SpaceAssetIssue] = Field(default_factory=list)
    workflow_execution_performed: Literal[False] = False


class SpaceAssetCollectionReport(StrictContractModel):
    schema_version: Literal[SPACE_ASSET_BUS_SCHEMA_VERSION] = SPACE_ASSET_BUS_SCHEMA_VERSION
    ok: bool = True
    asset: ComfySpaceAssetRecord
    asset_ref: SpaceAssetReference
    content_url: str
    workflow_execution_performed: Literal[False] = False


class SpaceAssetCleanupReport(StrictContractModel):
    schema_version: Literal[SPACE_ASSET_BUS_SCHEMA_VERSION] = SPACE_ASSET_BUS_SCHEMA_VERSION
    ok: bool = True
    scanned: int = Field(ge=0)
    deleted: int = Field(ge=0)
    skipped_durable: int = Field(ge=0)
    errors: list[SpaceAssetIssue] = Field(default_factory=list)
    workflow_execution_performed: Literal[False] = False


class SpaceAssetBusSchemaBundle(StrictContractModel):
    schema_version: Literal[SPACE_ASSET_BUS_SCHEMA_VERSION] = SPACE_ASSET_BUS_SCHEMA_VERSION
    asset_record: ComfySpaceAssetRecord
    ingest_report: SpaceAssetIngestReport
    binding_resolution_report: SpaceAssetBindingResolutionReport
    collection_report: SpaceAssetCollectionReport
    cleanup_report: SpaceAssetCleanupReport
