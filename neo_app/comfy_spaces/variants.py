from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.contract import load_space_manifest
from neo_app.comfy_spaces.graph_addons import SpaceGraphAddonError, SpaceGraphAddonRequest, parse_graph_addon_requests
from neo_app.comfy_spaces.paths import (
    comfy_space_manifest_path,
    comfy_space_publication_receipt_path,
    comfy_space_stages_path,
    comfy_spaces_variants_root,
    comfy_space_variant_path,
)
from neo_app.comfy_spaces.publish_schema import ComfySpacePublicationReceipt
from neo_app.comfy_spaces.schema import StrictContractModel
from neo_app.comfy_spaces.stages import parse_stage_sidecar
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_VARIANT_SCHEMA_VERSION = "neo.comfy_space.variant.v1"
SPACE_VARIANT_COLLECTION_SCHEMA_VERSION = "neo.comfy_space.variant_collection.v1"
_SPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_KINDS = {"image", "mask", "video", "audio", "file"}


class SpaceVariantError(ValueError):
    """Raised when a local Space Variant is invalid, stale, or unavailable."""

    def __init__(self, message: str, *, code: str = "space_variant_invalid") -> None:
        super().__init__(message)
        self.code = str(code or "space_variant_invalid")


class SpaceVariantStageDefaults(StrictContractModel):
    enabled_stage_ids: list[str] = Field(default_factory=list)
    preferred_run_mode: Literal["all", "stage"] = "all"
    preferred_stage_id: str = ""

    @field_validator("enabled_stage_ids")
    @classmethod
    def normalize_stage_ids(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            token = str(item or "").strip().lower()
            if not token or not _SPACE_ID_RE.fullmatch(token):
                raise ValueError("enabled_stage_ids must contain safe stage IDs")
            if token not in result:
                result.append(token)
        return result

    @field_validator("preferred_stage_id")
    @classmethod
    def validate_preferred_stage_id(cls, value: str) -> str:
        token = str(value or "").strip().lower()
        if token and not _SPACE_ID_RE.fullmatch(token):
            raise ValueError("preferred_stage_id must be a safe stage ID")
        return token

    @model_validator(mode="after")
    def validate_preferred_mode(self):
        if self.preferred_run_mode == "stage" and not self.preferred_stage_id:
            raise ValueError("preferred_stage_id is required when preferred_run_mode='stage'")
        if self.preferred_run_mode == "all" and self.preferred_stage_id:
            raise ValueError("preferred_stage_id is only valid when preferred_run_mode='stage'")
        return self


class SpaceVariantPresentation(StrictContractModel):
    badge_label: str = ""
    description_override: str = ""

    @field_validator("badge_label")
    @classmethod
    def validate_badge_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if len(text) > 48:
            raise ValueError("badge_label must be 48 characters or fewer")
        return text

    @field_validator("description_override")
    @classmethod
    def validate_description_override(cls, value: str) -> str:
        text = str(value or "").strip()
        if len(text) > 500:
            raise ValueError("description_override must be 500 characters or fewer")
        return text


class ComfySpaceVariant(StrictContractModel):
    schema_version: Literal[SPACE_VARIANT_SCHEMA_VERSION] = SPACE_VARIANT_SCHEMA_VERSION
    kind: Literal["comfy_space_variant"] = "comfy_space_variant"
    variant_id: str
    name: str
    description: str = ""
    base_space_id: str
    base_version: str
    base_publication_fingerprint: str
    created_at: str
    updated_at: str
    graph_addons: list[SpaceGraphAddonRequest] = Field(default_factory=list)
    stage_defaults: SpaceVariantStageDefaults = Field(default_factory=SpaceVariantStageDefaults)
    presentation: SpaceVariantPresentation = Field(default_factory=SpaceVariantPresentation)
    saved_values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("variant_id", "base_space_id")
    @classmethod
    def validate_safe_id(cls, value: str, info) -> str:
        token = str(value or "").strip().lower()
        if not _SPACE_ID_RE.fullmatch(token):
            raise ValueError(f"{info.field_name} must use lowercase letters, numbers, dot, underscore, or hyphen")
        return token

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Variant name must not be empty")
        if len(text) > 120:
            raise ValueError("Variant name must be 120 characters or fewer")
        return text

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        text = str(value or "").strip()
        if len(text) > 1000:
            raise ValueError("Variant description must be 1000 characters or fewer")
        return text

    @field_validator("base_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not _VERSION_RE.fullmatch(text):
            raise ValueError("base_version must be semantic version text")
        return text

    @field_validator("base_publication_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _SHA256_RE.fullmatch(text):
            raise ValueError("base_publication_fingerprint must be a SHA-256 digest")
        return text


class SpaceVariantCollection(StrictContractModel):
    schema_version: Literal[SPACE_VARIANT_COLLECTION_SCHEMA_VERSION] = SPACE_VARIANT_COLLECTION_SCHEMA_VERSION
    ok: Literal[True] = True
    variants: list[dict[str, Any]] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)


def _canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SpaceVariantError(f"{label} is unavailable.", code="space_variant_base_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceVariantError(f"Unable to read {label}.", code="space_variant_base_invalid") from exc
    if not isinstance(payload, dict):
        raise SpaceVariantError(f"{label} must be a JSON object.", code="space_variant_base_invalid")
    return payload


def _publication_receipt(space_id: str, version: str, *, root_dir: Path | str | None = None) -> tuple[ComfySpacePublicationReceipt, str]:
    raw = _read_json(comfy_space_publication_receipt_path(space_id, version, root_dir), label="base publication receipt")
    try:
        receipt = model_from_dict(ComfySpacePublicationReceipt, raw)
    except Exception as exc:
        raise SpaceVariantError(f"Invalid base publication receipt: {exc}", code="space_variant_base_invalid") from exc
    if receipt.space_id != str(space_id or "").strip().lower() or receipt.version != str(version or "").strip():
        raise SpaceVariantError("Base publication identity does not match the requested Space/version.", code="space_variant_base_invalid")
    return receipt, _canonical_json_sha256(model_to_dict(receipt))


def _variant_status(variant: ComfySpaceVariant, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        _, fingerprint = _publication_receipt(variant.base_space_id, variant.base_version, root_dir=root_dir)
    except SpaceVariantError as exc:
        return {"status": "stale", "ready": False, "reason": str(exc), "code": exc.code}
    if fingerprint != variant.base_publication_fingerprint:
        return {
            "status": "stale",
            "ready": False,
            "reason": "The exact published base changed after this Variant was saved.",
            "code": "space_variant_base_fingerprint_mismatch",
        }
    return {"status": "ready", "ready": True, "reason": "", "code": ""}


def _sanitize_saved_values(space_id: str, version: str, values: Mapping[str, Any] | None, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    manifest = load_space_manifest(comfy_space_manifest_path(space_id, version, root_dir))
    inputs = {item.input_id: item for item in manifest.inputs}
    result: dict[str, Any] = {}
    for key, value in dict(values or {}).items():
        input_id = str(key or "").strip().lower()
        if input_id not in inputs:
            raise SpaceVariantError(f"Variant saved_values contains unknown input_id: {input_id}", code="space_variant_value_unknown")
        item = inputs[input_id]
        if item.kind in _MEDIA_KINDS:
            raise SpaceVariantError(
                f"Variant cannot persist media/file input '{input_id}'. Re-select that asset per run.",
                code="space_variant_media_value_forbidden",
            )
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SpaceVariantError(f"Variant value for '{input_id}' is not JSON-serializable.", code="space_variant_value_invalid") from exc
        result[input_id] = value
    return result


def _sanitize_graph_addons(payload: Sequence[Mapping[str, Any]] | Sequence[SpaceGraphAddonRequest] | None) -> list[SpaceGraphAddonRequest]:
    rows: list[Mapping[str, Any]] = []
    for item in list(payload or []):
        rows.append(model_to_dict(item) if isinstance(item, SpaceGraphAddonRequest) else dict(item))
    try:
        parsed = parse_graph_addon_requests(rows)
    except (SpaceGraphAddonError, ValueError) as exc:
        raise SpaceVariantError(f"Invalid Variant Graph Add-on configuration: {exc}", code="space_variant_graph_addon_invalid") from exc
    for addon in parsed:
        for field, value in addon.values.items():
            if isinstance(value, (dict, list, tuple, set)):
                raise SpaceVariantError(
                    f"Variant cannot persist asset/structured Graph Add-on value '{field}'. Re-select per-run assets after applying the Variant.",
                    code="space_variant_addon_asset_forbidden",
                )
    return parsed


def _validate_stage_defaults(space_id: str, version: str, defaults: SpaceVariantStageDefaults, *, root_dir: Path | str | None = None) -> None:
    path = comfy_space_stages_path(space_id, version, root_dir)
    if not path.is_file():
        if defaults.enabled_stage_ids or defaults.preferred_run_mode != "all" or defaults.preferred_stage_id:
            raise SpaceVariantError("This base Space does not define execution stages.", code="space_variant_stages_unavailable")
        return
    sidecar = parse_stage_sidecar(_read_json(path, label="base Stage sidecar"))
    known = {item.stage_id for item in sidecar.stages}
    missing = [item for item in defaults.enabled_stage_ids if item not in known]
    if defaults.preferred_stage_id and defaults.preferred_stage_id not in known:
        missing.append(defaults.preferred_stage_id)
    if missing:
        raise SpaceVariantError(f"Variant references unknown stage(s): {', '.join(sorted(set(missing)))}", code="space_variant_stage_unknown")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def variant_payload(variant: ComfySpaceVariant, *, root_dir: Path | str | None = None, include_values: bool = True) -> dict[str, Any]:
    payload = model_to_dict(variant)
    status = _variant_status(variant, root_dir=root_dir)
    payload.update(status)
    if not include_values:
        payload["saved_values"] = {}
        payload["saved_value_count"] = len(variant.saved_values)
    else:
        payload["saved_value_count"] = len(variant.saved_values)
    payload["graph_addon_count"] = len(variant.graph_addons)
    return payload


def create_space_variant(
    *,
    name: str,
    base_space_id: str,
    base_version: str,
    description: str = "",
    graph_addons: Sequence[Mapping[str, Any]] | None = None,
    enabled_stage_ids: Sequence[str] | None = None,
    preferred_run_mode: str = "all",
    preferred_stage_id: str = "",
    saved_values: Mapping[str, Any] | None = None,
    badge_label: str = "",
    description_override: str = "",
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    space_id = str(base_space_id or "").strip().lower()
    version = str(base_version or "").strip()
    if not _SPACE_ID_RE.fullmatch(space_id):
        raise SpaceVariantError("Invalid base_space_id.")
    if not _VERSION_RE.fullmatch(version):
        raise SpaceVariantError("Invalid base_version.")
    # Requiring both manifest and receipt prevents Variants from pointing at draft or
    # arbitrary local workflow folders.
    try:
        manifest = load_space_manifest(comfy_space_manifest_path(space_id, version, root_dir))
    except Exception as exc:
        raise SpaceVariantError(f"Published base Space is unavailable: {exc}", code="space_variant_base_missing") from exc
    if manifest.status != "published":
        raise SpaceVariantError("Space Variants can only reference a published base Space.", code="space_variant_base_not_published")
    _, fingerprint = _publication_receipt(space_id, version, root_dir=root_dir)

    try:
        defaults = SpaceVariantStageDefaults(
            enabled_stage_ids=[str(item) for item in (enabled_stage_ids or [])],
            preferred_run_mode=str(preferred_run_mode or "all").strip().lower(),
            preferred_stage_id=str(preferred_stage_id or "").strip().lower(),
        )
    except ValueError as exc:
        raise SpaceVariantError(f"Invalid Variant stage defaults: {exc}", code="space_variant_stage_defaults_invalid") from exc
    _validate_stage_defaults(space_id, version, defaults, root_dir=root_dir)
    addons = _sanitize_graph_addons(graph_addons)
    values = _sanitize_saved_values(space_id, version, saved_values, root_dir=root_dir)
    now = _now_iso()
    try:
        variant = ComfySpaceVariant(
            variant_id=f"variant_{uuid4().hex}",
            name=name,
            description=description,
            base_space_id=space_id,
            base_version=version,
            base_publication_fingerprint=fingerprint,
            created_at=now,
            updated_at=now,
            graph_addons=addons,
            stage_defaults=defaults,
            presentation=SpaceVariantPresentation(badge_label=badge_label, description_override=description_override),
            saved_values=values,
        )
    except ValueError as exc:
        raise SpaceVariantError(f"Invalid Space Variant: {exc}", code="space_variant_invalid") from exc
    root = comfy_spaces_variants_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = comfy_space_variant_path(variant.variant_id, root_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model_to_dict(variant), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return variant_payload(variant, root_dir=root_dir)


def load_space_variant(variant_id: str, *, root_dir: Path | str | None = None, require_ready: bool = False) -> ComfySpaceVariant:
    path = comfy_space_variant_path(variant_id, root_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SpaceVariantError(f"Unknown Space Variant: {variant_id}", code="space_variant_not_found") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceVariantError(f"Unable to read Space Variant: {variant_id}", code="space_variant_invalid") from exc
    try:
        variant = model_from_dict(ComfySpaceVariant, raw)
    except Exception as exc:
        raise SpaceVariantError(f"Invalid Space Variant: {exc}", code="space_variant_invalid") from exc
    if require_ready:
        status = _variant_status(variant, root_dir=root_dir)
        if not status["ready"]:
            raise SpaceVariantError(str(status["reason"]), code=str(status["code"] or "space_variant_stale"))
    return variant


def list_space_variants(
    *,
    space_id: str = "",
    version: str = "",
    root_dir: Path | str | None = None,
    include_values: bool = True,
) -> dict[str, Any]:
    clean_space = str(space_id or "").strip().lower()
    clean_version = str(version or "").strip()
    items: list[dict[str, Any]] = []
    root = comfy_spaces_variants_root(root_dir)
    if root.is_dir():
        for path in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                variant = model_from_dict(ComfySpaceVariant, raw)
            except Exception:
                continue
            if clean_space and variant.base_space_id != clean_space:
                continue
            if clean_version and variant.base_version != clean_version:
                continue
            items.append(variant_payload(variant, root_dir=root_dir, include_values=include_values))
    items.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("variant_id") or "")))
    return model_to_dict(SpaceVariantCollection(variants=items, count=len(items)))


def delete_space_variant(variant_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    variant = load_space_variant(variant_id, root_dir=root_dir)
    path = comfy_space_variant_path(variant.variant_id, root_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SpaceVariantError(f"Unable to delete Space Variant: {variant.variant_id}", code="space_variant_delete_failed") from exc
    return {"ok": True, "variant_id": variant.variant_id, "deleted": True}


def resolve_space_variant(
    variant_id: str,
    *,
    space_id: str,
    version: str,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    variant = load_space_variant(variant_id, root_dir=root_dir, require_ready=True)
    clean_space = str(space_id or "").strip().lower()
    clean_version = str(version or "").strip()
    if variant.base_space_id != clean_space or variant.base_version != clean_version:
        raise SpaceVariantError(
            "Variant belongs to a different published Space/version and cannot be silently retargeted.",
            code="space_variant_base_mismatch",
        )
    return variant_payload(variant, root_dir=root_dir)


def variant_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_VARIANT_SCHEMA_VERSION,
        "space_variants": True,
        "immutable_base_reference": True,
        "exact_version_pinning": True,
        "publication_fingerprint_pinning": True,
        "graph_addons_supported": True,
        "stage_defaults_supported": True,
        "saved_non_media_values_supported": True,
        "media_values_persisted": False,
        "silent_retargeting": False,
        "automatic_migration": False,
        "preset_semantics": "separate_future_value_only_layer",
    }
