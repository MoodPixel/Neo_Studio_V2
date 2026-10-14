from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Mapping

from pydantic import Field

from neo_app.comfy_spaces.schema import SpaceValueKind, StrictContractModel
from neo_app.core.pydantic_compat import model_to_dict


SPACE_TYPE_SYSTEM_SCHEMA_VERSION = "neo.comfy_space.type_system.v1"

SpaceInputControl = Literal[
    "text_input",
    "text_area",
    "integer_input",
    "number_input",
    "slider",
    "seed_input",
    "toggle",
    "select",
    "search_select",
    "model_select",
    "json_editor",
    "image_upload",
    "mask_upload",
    "video_upload",
    "audio_upload",
    "file_upload",
]

SpaceOutputRenderer = Literal[
    "text_viewer",
    "numeric_value",
    "boolean_value",
    "choice_value",
    "json_viewer",
    "image_preview",
    "mask_preview",
    "video_player",
    "audio_player",
    "file_download",
]

SpaceValueShape = Literal["scalar", "structured", "asset_ref"]
SpaceTypeConfidence = Literal["exact", "heuristic", "fallback"]
SpaceOptionSource = Literal["none", "inline_catalog", "runtime_catalog"]
SpaceCollectionMode = Literal["first", "all"]

_ASSET_KINDS = {"image", "mask", "video", "audio", "file"}
_MEDIA_ACCEPT = {
    "image": ["image/*"],
    "mask": ["image/*"],
    "video": ["video/*"],
    "audio": ["audio/*"],
    "file": ["*/*"],
}
_PROMPTISH_FIELDS = {
    "prompt",
    "text",
    "positive",
    "positive_prompt",
    "negative",
    "negative_prompt",
    "instruction",
    "edit_prompt",
}


class SpaceTypeConstraint(StrictContractModel):
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None
    default: Any = None
    multiline: bool | None = None
    tooltip: str = ""


class SpaceOptionContract(StrictContractModel):
    source: SpaceOptionSource = "none"
    count: int = Field(default=0, ge=0)
    preview: list[Any] = Field(default_factory=list)
    truncated: bool = False
    searchable: bool = False


class SpaceInputTypeDescriptor(StrictContractModel):
    schema_version: Literal[SPACE_TYPE_SYSTEM_SCHEMA_VERSION] = SPACE_TYPE_SYSTEM_SCHEMA_VERSION
    kind: SpaceValueKind
    control: SpaceInputControl
    semantic_role: str = "generic"
    value_shape: SpaceValueShape = "scalar"
    confidence: SpaceTypeConfidence = "exact"
    required: bool | None = None
    model_type: str | None = None
    asset_kind: str | None = None
    accepted_mime_types: list[str] = Field(default_factory=list)
    asset_bus_required: bool = False
    supports_multiple: bool = False
    constraints: SpaceTypeConstraint = Field(default_factory=SpaceTypeConstraint)
    options: SpaceOptionContract = Field(default_factory=SpaceOptionContract)
    manifest_renderer_hint: str
    reason: str


class SpaceOutputTypeDescriptor(StrictContractModel):
    schema_version: Literal[SPACE_TYPE_SYSTEM_SCHEMA_VERSION] = SPACE_TYPE_SYSTEM_SCHEMA_VERSION
    kind: SpaceValueKind
    renderer: SpaceOutputRenderer
    value_shape: SpaceValueShape = "scalar"
    confidence: SpaceTypeConfidence = "exact"
    artifact: bool = False
    asset_kind: str | None = None
    previewable: bool = False
    downloadable: bool = False
    default_collection: SpaceCollectionMode = "first"
    supported_collections: list[SpaceCollectionMode] = Field(default_factory=lambda: ["first", "all"])
    asset_bus_required: bool = False
    manifest_renderer_hint: str
    reason: str


class SpaceTypeSystemSchemaBundle(StrictContractModel):
    """Schema-only bundle used to publish one coherent JSON Schema document."""

    schema_version: Literal[SPACE_TYPE_SYSTEM_SCHEMA_VERSION] = SPACE_TYPE_SYSTEM_SCHEMA_VERSION
    input_descriptor: SpaceInputTypeDescriptor
    output_descriptor: SpaceOutputTypeDescriptor


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clean_constraints(candidate: Mapping[str, Any]) -> SpaceTypeConstraint:
    raw = _mapping(candidate.get("constraints"))
    minimum = raw.get("min")
    maximum = raw.get("max")
    step = raw.get("step")
    default = deepcopy(raw.get("default")) if "default" in raw else None
    multiline = raw.get("multiline") if isinstance(raw.get("multiline"), bool) else None
    tooltip = str(raw.get("tooltip") or "").strip()
    return SpaceTypeConstraint(
        minimum=minimum if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) else None,
        maximum=maximum if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) else None,
        step=step if isinstance(step, (int, float)) and not isinstance(step, bool) else None,
        default=default,
        multiline=multiline,
        tooltip=tooltip,
    )


def _semantic_role(candidate: Mapping[str, Any]) -> str:
    field = str(candidate.get("field") or "").strip().casefold()
    model_type = str(candidate.get("model_type") or "").strip()
    kind = str(candidate.get("kind") or "").strip()
    if model_type:
        return f"model.{model_type}"
    if field in {"negative", "negative_prompt"}:
        return "prompt.negative"
    if field in _PROMPTISH_FIELDS or "prompt" in field:
        return "prompt"
    if field in {"seed", "noise_seed"}:
        return "generation.seed"
    if field in {"steps", "cfg", "cfg_scale", "denoise", "strength"}:
        return f"generation.{field}"
    if field in {"width", "height", "aspect_ratio"}:
        return f"canvas.{field}"
    if field in {"duration", "frames", "frame_rate", "fps"}:
        return f"timeline.{field}"
    if field in {"sampler_name", "sampler", "scheduler"}:
        return f"generation.{field}"
    if kind in _ASSET_KINDS:
        return f"asset.{kind}"
    return "generic"


def _numeric_slider_is_sensible(constraints: SpaceTypeConstraint, field: str) -> bool:
    if field in {"seed", "noise_seed", "width", "height", "batch_size", "frames"}:
        return False
    if constraints.minimum is None or constraints.maximum is None:
        return False
    try:
        span = float(constraints.maximum) - float(constraints.minimum)
    except (TypeError, ValueError):
        return False
    if span <= 0:
        return False
    # Avoid giant sliders (for example uint64 seed ranges). Small/medium bounded
    # values are usable as sliders; Builder can still override the hint later.
    return span <= 10000


def resolve_input_type_descriptor(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one NCS-2 input candidate into a Neo-native control descriptor.

    This is declarative only. Asset controls use a future ``neo_asset_ref`` shape;
    they do not upload files or create runtime paths in NCS-3.
    """

    kind = str(candidate.get("kind") or "json").strip().lower()
    if kind not in {
        "text", "integer", "number", "boolean", "choice", "json",
        "image", "mask", "video", "audio", "file",
    }:
        kind = "json"
        confidence: SpaceTypeConfidence = "fallback"
    else:
        confidence = "exact"

    field = str(candidate.get("field") or "").strip().casefold()
    model_type = str(candidate.get("model_type") or "").strip() or None
    constraints = _clean_constraints(candidate)
    choice_count = int(candidate.get("choice_count") or 0)
    choices_preview = list(candidate.get("choices_preview") or [])

    option_source: SpaceOptionSource = "none"
    if choice_count:
        # NCS-2 choices are populated from live object_info when available. If a
        # caller supplies a literal candidate without a live catalog, inline is
        # still a valid declarative source.
        option_source = "runtime_catalog" if str(candidate.get("comfy_type") or "").strip() == "COMBO" else "inline_catalog"

    options = SpaceOptionContract(
        source=option_source,
        count=choice_count,
        preview=deepcopy(choices_preview),
        truncated=choice_count > len(choices_preview),
        searchable=bool(model_type or choice_count > 12),
    )

    value_shape: SpaceValueShape = "scalar"
    control: SpaceInputControl
    reason = ""
    accepted_mime_types: list[str] = []
    asset_kind: str | None = None
    asset_bus_required = False
    supports_multiple = False

    if kind == "text":
        multiline = constraints.multiline is True or field in _PROMPTISH_FIELDS or "prompt" in field
        control = "text_area" if multiline else "text_input"
        reason = "text input; multiline/prompt semantics choose textarea" if multiline else "plain text input"
    elif kind == "integer":
        if field in {"seed", "noise_seed"}:
            control = "seed_input"
            reason = "seed-like integer uses a dedicated seed control"
        elif _numeric_slider_is_sensible(constraints, field):
            control = "slider"
            reason = "bounded integer range is suitable for a slider"
        else:
            control = "integer_input"
            reason = "integer value uses a numeric input"
    elif kind == "number":
        if _numeric_slider_is_sensible(constraints, field):
            control = "slider"
            reason = "bounded numeric range is suitable for a slider"
        else:
            control = "number_input"
            reason = "numeric value uses a number input"
    elif kind == "boolean":
        control = "toggle"
        reason = "boolean value maps directly to a toggle"
    elif kind == "choice":
        if model_type:
            control = "model_select"
            reason = f"model selector ({model_type}) uses a searchable model control"
        elif choice_count > 12:
            control = "search_select"
            reason = "large choice catalog uses searchable selection"
        else:
            control = "select"
            reason = "bounded choice catalog uses selection control"
    elif kind == "json":
        control = "json_editor"
        value_shape = "structured"
        supports_multiple = True
        reason = "structured/unknown Comfy value falls back to a JSON editor; author-reviewed multiple JSON values may use an NCS-11 repeater/stack without changing graph topology"
        if confidence != "fallback":
            confidence = "heuristic"
    else:
        control = {
            "image": "image_upload",
            "mask": "mask_upload",
            "video": "video_upload",
            "audio": "audio_upload",
            "file": "file_upload",
        }[kind]  # type: ignore[index]
        value_shape = "asset_ref"
        asset_kind = kind
        accepted_mime_types = list(_MEDIA_ACCEPT[kind])
        asset_bus_required = True
        supports_multiple = True
        reason = f"{kind} input maps to a Neo asset selector/uploader backed by the NCS-6 neutral asset bus; NCS-7 resolves it through the selected Comfy runtime at execution time"

    descriptor = SpaceInputTypeDescriptor(
        kind=kind,  # type: ignore[arg-type]
        control=control,
        semantic_role=_semantic_role({**dict(candidate), "kind": kind}),
        value_shape=value_shape,
        confidence=confidence,
        required=candidate.get("required_by_node") if isinstance(candidate.get("required_by_node"), bool) else None,
        model_type=model_type,
        asset_kind=asset_kind,
        accepted_mime_types=accepted_mime_types,
        asset_bus_required=asset_bus_required,
        supports_multiple=supports_multiple,
        constraints=constraints,
        options=options,
        manifest_renderer_hint=control,
        reason=reason,
    )
    return model_to_dict(descriptor)


def resolve_output_type_descriptor(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one NCS-2 output candidate into a native Neo result renderer."""

    kind = str(candidate.get("kind") or "json").strip().lower()
    supported = {
        "text", "integer", "number", "boolean", "choice", "json",
        "image", "mask", "video", "audio", "file",
    }
    confidence: SpaceTypeConfidence = "exact"
    if kind not in supported:
        kind = "json"
        confidence = "fallback"

    renderer: SpaceOutputRenderer
    value_shape: SpaceValueShape = "scalar"
    artifact = kind in _ASSET_KINDS
    previewable = False
    downloadable = False
    asset_kind: str | None = None
    asset_bus_required = False

    if kind == "text":
        renderer = "text_viewer"
        reason = "text output uses the native text result viewer"
    elif kind in {"integer", "number"}:
        renderer = "numeric_value"
        reason = "numeric output uses a value result"
    elif kind == "boolean":
        renderer = "boolean_value"
        reason = "boolean output uses a boolean/status value result"
    elif kind == "choice":
        renderer = "choice_value"
        reason = "choice output uses a compact value result"
    elif kind == "json":
        renderer = "json_viewer"
        value_shape = "structured"
        reason = "structured/unknown output uses a JSON result viewer"
        if confidence != "fallback":
            confidence = "heuristic"
    else:
        renderer = {
            "image": "image_preview",
            "mask": "mask_preview",
            "video": "video_player",
            "audio": "audio_player",
            "file": "file_download",
        }[kind]  # type: ignore[index]
        value_shape = "asset_ref"
        asset_kind = kind
        asset_bus_required = True
        previewable = kind in {"image", "mask", "video", "audio"}
        downloadable = True
        reason = f"{kind} output maps to Neo's native artifact renderer; NCS-7 supplies trusted runtime output coordinates and NCS-6 persists the collected artifact into Neo-owned storage"

    descriptor = SpaceOutputTypeDescriptor(
        kind=kind,  # type: ignore[arg-type]
        renderer=renderer,
        value_shape=value_shape,
        confidence=confidence,
        artifact=artifact,
        asset_kind=asset_kind,
        previewable=previewable,
        downloadable=downloadable,
        asset_bus_required=asset_bus_required,
        manifest_renderer_hint=renderer,
        reason=reason,
    )
    return model_to_dict(descriptor)


def type_system_capabilities() -> dict[str, Any]:
    """Return the stable NCS-3 registry advertised to later Builder/runtime phases."""

    return {
        "schema_version": SPACE_TYPE_SYSTEM_SCHEMA_VERSION,
        "media_agnostic": True,
        "value_kinds": [
            "text", "integer", "number", "boolean", "choice", "json",
            "image", "mask", "video", "audio", "file",
        ],
        "input_controls": [
            "text_input", "text_area", "integer_input", "number_input", "slider",
            "seed_input", "toggle", "select", "search_select", "model_select",
            "json_editor", "image_upload", "mask_upload", "video_upload",
            "audio_upload", "file_upload",
        ],
        "output_renderers": [
            "text_viewer", "numeric_value", "boolean_value", "choice_value",
            "json_viewer", "image_preview", "mask_preview", "video_player",
            "audio_player", "file_download",
        ],
        "asset_reference_shape": "neo_asset_ref",
        "asset_transport_implemented": False,
        "workflow_binding_implemented": False,
        "workflow_execution_implemented": False,
    }
