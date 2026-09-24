"""Optional artifact contracts layered onto the existing v1 model catalog.

Catalog declarations describe an upstream artifact, not the bytes installed on
a user's machine. File discovery, node availability and successful inference
are deliberately separate observations.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

ARTIFACT_SCHEMA_ID = "neo.models.artifact.v1"


def validate_artifact_record(record: dict[str, Any]) -> list[str]:
    if "artifact" not in record:
        return []  # Existing catalog entries do not require migration.
    artifact = record["artifact"]
    if not isinstance(artifact, dict):
        return ["artifact must be an object"]
    errors = []
    if artifact.get("schema_id") != ARTIFACT_SCHEMA_ID:
        errors.append("artifact.schema_id is invalid")
    for key in ("family_id", "architecture", "variant", "format", "precision"):
        if not isinstance(artifact.get(key), str) or not artifact[key].strip():
            errors.append(f"artifact.{key} must be a non-empty string")
    tasks = artifact.get("tasks")
    if not isinstance(tasks, list) or not tasks or any(not isinstance(x, str) or not x.strip() for x in tasks):
        errors.append("artifact.tasks must be a non-empty string array")
    runtime = artifact.get("runtime")
    if not isinstance(runtime, dict):
        errors.append("artifact.runtime must be an object")
    else:
        if not isinstance(runtime.get("id"), str) or not runtime["id"].strip():
            errors.append("artifact.runtime.id is required")
        for key in ("providers", "platforms"):
            items = runtime.get(key)
            if not isinstance(items, list) or not items or any(not isinstance(x, str) or not x.strip() for x in items):
                errors.append(f"artifact.runtime.{key} must be a non-empty string array")
        if runtime.get("platform_validation") != "not_tested":
            errors.append("artifact.runtime.platform_validation must be not_tested; runtime evidence is not a catalog declaration")
        nodes = runtime.get("node_inputs")
        if not isinstance(nodes, dict) or not nodes:
            errors.append("artifact.runtime.node_inputs must be a non-empty object")
        elif any(not isinstance(v, list) or not v or any(not isinstance(x, str) or not x for x in v) for v in nodes.values()):
            errors.append("artifact.runtime.node_inputs values must be non-empty string arrays")
    io = artifact.get("io")
    if not isinstance(io, dict):
        errors.append("artifact.io must be an object")
    else:
        scale = io.get("native_scale")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not 0 < scale <= 16:
            errors.append("artifact.io.native_scale must be finite and in (0, 16]")
        if io.get("sizing_policy") != "user_target":
            errors.append("artifact.io.sizing_policy must be user_target")
        for key in ("input", "output", "size_constraints", "tiling"):
            if not isinstance(io.get(key), str) or not io[key].strip():
                errors.append(f"artifact.io.{key} is required")
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("artifact.provenance must be an object")
    else:
        if not provenance.get("upstream_url") or not provenance.get("reviewed_at"):
            errors.append("artifact.provenance upstream_url and reviewed_at are required")
        revision_kind = provenance.get("revision_kind")
        if revision_kind not in {"commit", "release_tag", "unresolved"}:
            errors.append("artifact.provenance.revision_kind is invalid")
        if revision_kind == "commit" and not re.fullmatch(r"[0-9a-f]{40}", str(source.get("revision") or "")):
            errors.append("artifact commit revision must be a full SHA")
        if revision_kind == "release_tag" and not source.get("revision"):
            errors.append("artifact release tag is required")
        state = provenance.get("checksum_state")
        if state not in {"published", "not_published", "unresolved"}:
            errors.append("artifact.provenance.checksum_state is invalid")
        hashes = source.get("hashes") or {}
        sha = hashes.get("sha256", "") if isinstance(hashes, dict) else ""
        if (state == "published" or sha) and not re.fullmatch(r"[0-9a-fA-F]{64}", str(sha)):
            errors.append("artifact published checksum requires a valid source.hashes.sha256")
    license_info = record.get("license") if isinstance(record.get("license"), dict) else {}
    if not license_info.get("id") or license_info.get("state") not in {"upstream_declared", "unresolved"}:
        errors.append("artifact license id and state are required")
    return errors


def curated_upscale_records() -> list[dict[str, Any]]:
    # Lazy import keeps validation independent of the manifest loader.
    from .manifest_loader import load_model_catalog

    return [deepcopy(r) for r in load_model_catalog().get("records", [])
            if isinstance(r, dict) and isinstance(r.get("artifact"), dict)
            and not validate_artifact_record(r)
            and "image.upscale" in r["artifact"].get("tasks", [])]


def match_upscale_artifact(model_name: str, records: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Exact basename match is a metadata hint, never checksum verification."""
    name = str(model_name or "").replace("\\", "/").rsplit("/", 1)[-1].casefold()
    rows = curated_upscale_records() if records is None else records
    matches = [r for r in rows if name and name == str((r.get("source") or {}).get("filename") or "").casefold()]
    return deepcopy(matches[0]) if len(matches) == 1 else None


def upscale_compatibility_payload(names: list[str], object_info: dict[str, Any], *, provider_id: str) -> dict[str, Any]:
    """Annotate only this backend's discovered files; curated rows stay separate."""
    records = curated_upscale_records()

    def describe(record: dict[str, Any] | None, name: str, installed: bool) -> dict[str, Any]:
        artifact = deepcopy((record or {}).get("artifact") or {})
        requirements = artifact.get("runtime", {}).get("node_inputs") or {
            "UpscaleModelLoader": ["model_name"],
            "ImageUpscaleWithModel": ["upscale_model", "image"],
        }
        missing = []
        for node, inputs in requirements.items():
            spec = object_info.get(node)
            if not isinstance(spec, dict):
                missing.append(node)
                continue
            block = spec.get("input") or {}
            available = set(block.get("required") or {}) | set(block.get("optional") or {})
            missing.extend(f"{node}.{key}" for key in inputs if key not in available)
        declared_providers = artifact.get("runtime", {}).get("providers", ["comfyui", "comfyui_portable"])
        runtime_available = bool(object_info) and not missing and provider_id in declared_providers
        return {
            "name": name, "catalog_id": (record or {}).get("id", ""),
            "display_name": (record or {}).get("display_name", name),
            "artifact": artifact, "source": deepcopy((record or {}).get("source") or {}),
            "license": deepcopy((record or {}).get("license") or {}),
            "installed": installed, "installed_state": "backend_reported" if installed else "not_reported",
            "runtime_available": runtime_available,
            "runtime_state": "available" if runtime_available else ("unavailable" if object_info else "unknown"),
            "missing_requirements": missing,
            "execution_verified": False, "execution_state": "not_tested",
            "identity_state": "filename_match_unverified" if record and installed else "unverified",
            "native_scale": artifact.get("io", {}).get("native_scale"),
            "native_scale_source": "catalog_declaration" if artifact else "unknown",
        }

    discovered = [describe(match_upscale_artifact(name, records), name, True) for name in names]
    curated = [describe(r, r["source"]["filename"], any(match_upscale_artifact(n, records) == r for n in names)) for r in records]
    return {"upscaler_records": discovered, "curated_upscalers": curated,
            "compatibility_schema": "neo.image.upscale.compatibility.v1",
            "sizing_policy": "user_target", "selected_profile_only": True,
            "automatic_provider_fallback": False}
