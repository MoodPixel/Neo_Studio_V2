from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator

from neo_app.comfy_spaces.schema import StrictContractModel, SpaceValueKind


SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION = "neo.comfy_space.execution_runtime.v1"
SPACE_RUN_PROGRESS_SCHEMA_VERSION = "neo.comfy_space.run_progress.v1"

SpaceExecutionStage = Literal[
    "package",
    "runtime",
    "dependencies",
    "assets",
    "binding",
    "graph_addons",
    "stages",
    "variants",
    "resource_lease",
    "queue",
    "running",
    "outputs",
    "complete",
    "cancel",
]
SpaceExecutionStatus = Literal[
    "ready",
    "queued",
    "running",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
]

_RUN_ID_RE = re.compile(r"^csrun_[a-f0-9]{32}$")


SpaceRunProgressPhase = Literal[
    "preparing",
    "queued",
    "running",
    "finalizing",
    "cancel_requested",
    "completed",
    "failed",
    "cancelled",
]
SpaceRunProgressMode = Literal["phase", "graph_estimate", "node_step", "terminal"]


class SpaceRunProgressSnapshot(StrictContractModel):
    schema_version: Literal[SPACE_RUN_PROGRESS_SCHEMA_VERSION] = SPACE_RUN_PROGRESS_SCHEMA_VERSION
    sequence: int = Field(default=0, ge=0)
    phase: SpaceRunProgressPhase = "preparing"
    percent: float = Field(default=0.0, ge=0.0, le=100.0)
    percent_mode: SpaceRunProgressMode = "phase"
    message: str = ""
    event_type: str = ""
    source: str = "neo_runtime"
    updated_at: str = ""
    current_node_id: str = ""
    current_display_node_id: str = ""
    current_node_class: str = ""
    current_node_title: str = ""
    current_node_index: int = Field(default=0, ge=0)
    node_count: int = Field(default=0, ge=0)
    node_step: int = Field(default=0, ge=0)
    node_step_max: int = Field(default=0, ge=0)
    completed_node_ids: list[str] = Field(default_factory=list)
    cached_node_ids: list[str] = Field(default_factory=list)
    queue_remaining: int | None = Field(default=None, ge=0)
    websocket_connected: bool = False
    monitor_state: str = "idle"
    recoverable: bool = True


class SpaceRunProgressEvent(StrictContractModel):
    sequence: int = Field(ge=1)
    event_type: str
    at: str
    phase: str
    percent: float = Field(ge=0.0, le=100.0)
    message: str = ""
    current_node_id: str = ""
    source: str = "neo_runtime"
    data: dict[str, Any] = Field(default_factory=dict)


class SpaceExecutionIssue(StrictContractModel):
    code: str
    message: str
    stage: SpaceExecutionStage
    node_id: str | None = None
    input_id: str | None = None
    output_id: str | None = None


class SpaceExecutionPreflightReport(StrictContractModel):
    schema_version: Literal[SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION] = SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION
    ok: bool
    ready_for_queue: bool
    space_id: str
    version: str
    runtime_profile_id: str
    provider_id: str = ""
    package_valid: bool = False
    runtime_reachable: bool = False
    object_info_available: bool = False
    dependency_preflight_ready: bool = False
    asset_resolution_ready: bool = False
    binding_ready: bool = False
    graph_addons_ready: bool = True
    graph_addon_count: int = Field(default=0, ge=0)
    graph_addons: list[dict[str, Any]] = Field(default_factory=list)
    graph_addon_added_node_count: int = Field(default=0, ge=0)
    variant_id: str = ""
    variant_modified: bool = False
    stage_plan: dict[str, Any] = Field(default_factory=dict)
    model_reconciliation_count: int = Field(default=0, ge=0)
    runtime_model_reconciliations: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[SpaceExecutionIssue] = Field(default_factory=list)
    warnings: list[SpaceExecutionIssue] = Field(default_factory=list)
    execution_performed: Literal[False] = False


class SpaceExecutionOutputRecord(StrictContractModel):
    output_id: str
    kind: SpaceValueKind
    label: str
    primary: bool = False
    collection: Literal["first", "all"] = "first"
    source_node_id: str
    value: Any = None
    collected_asset_ids: list[str] = Field(default_factory=list)


class SpaceExecutionRunResult(StrictContractModel):
    schema_version: Literal[SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION] = SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION
    ok: bool
    run_id: str
    status: SpaceExecutionStatus
    space_id: str
    version: str
    runtime_profile_id: str
    provider_id: str
    prompt_id: str = ""
    client_id: str = ""
    message: str = ""
    outputs: list[SpaceExecutionOutputRecord] = Field(default_factory=list)
    results: dict[str, Any] = Field(default_factory=dict)
    errors: list[SpaceExecutionIssue] = Field(default_factory=list)
    warnings: list[SpaceExecutionIssue] = Field(default_factory=list)
    graph_addons: list[dict[str, Any]] = Field(default_factory=list)
    graph_addon_added_nodes: list[dict[str, Any]] = Field(default_factory=list)
    variant_id: str = ""
    variant_modified: bool = False
    stage_plan: dict[str, Any] = Field(default_factory=dict)
    continue_from_run_id: str = ""
    backend_completed: bool = False
    output_collection_complete: bool = False
    progress: SpaceRunProgressSnapshot = Field(default_factory=SpaceRunProgressSnapshot)
    recent_progress_events: list[SpaceRunProgressEvent] = Field(default_factory=list)
    public_runner_mounted: Literal[False] = False

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _RUN_ID_RE.fullmatch(text):
            raise ValueError("run_id must use Neo's csrun_<32 hex> form")
        return text


class SpaceExecutionSchemaBundle(StrictContractModel):
    schema_version: Literal[SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION] = SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION
    preflight: SpaceExecutionPreflightReport
    run_result: SpaceExecutionRunResult
