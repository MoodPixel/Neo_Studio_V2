from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from neo_app.image.lanpaint_family_policies import (
    COMPLETE_POLICY_STATE,
    PLACEHOLDER_POLICY_STATE,
    get_lanpaint_family_policy,
)
from neo_app.image.lanpaint_route_contract import (
    ENGINE_ID,
    MODE_ID,
    ROUTE_FAMILY_ID,
    normalize_family_id,
    normalize_loader_id,
    normalize_provider_id,
)

SCHEMA_ID = "neo.image.lanpaint_family_expansion_profile.v1"
SCHEMA_VERSION = 1
REGISTRY_SCHEMA_ID = "neo.image.lanpaint_family_expansion_registry.v1"
AUTHORITY = "neo_app.image.lanpaint_family_expansion"
PHASE_STATE = "family_expansion_with_phase22_anima_ideogram4_onboarding"
EXECUTION_STATE = "scaffold_only"
SUPPORTED_PROVIDERS = ("comfyui", "comfyui_portable")
SUPPORTED_LOADERS = ("checkpoint", "diffusion_model", "gguf")
ONBOARDING_STATES = {
    "onboarded_phase10",
    "onboarded_phase14",
    "onboarded_phase15",
    "onboarded_phase16",
    "onboarded_phase17",
    "onboarded_phase18",
    "onboarded_phase20",
    "onboarded_phase21",
    "onboarded_phase22",
    "ready_for_phase10",
    "blocked_family_policy",
    "blocked_variant_identity",
    "blocked_loader_ecosystem",
}
PROFILE_DATA_PATH = Path(__file__).with_name("lanpaint_family_expansion_profiles.json")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _fingerprint(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop("profile_fingerprint", None)
    payload.pop("registry_fingerprint", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _route_keys(provider_ids: list[str], family: str, loader: str) -> list[str]:
    return [f"{provider}:{family}:{loader}:{MODE_ID}:{ENGINE_ID}" for provider in provider_ids]


def load_lanpaint_family_expansion_profiles(path: Path | None = None) -> list[dict[str, Any]]:
    source = path or PROFILE_DATA_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, list):
        raise ValueError("LanPaint family expansion data must contain a profiles array.")
    return [deepcopy(item) for item in profiles if isinstance(item, dict)]


def validate_lanpaint_family_expansion_profile(raw: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    profile = _mapping(raw)
    issues: list[dict[str, Any]] = []

    def error(field: str, message: str) -> None:
        issues.append({"level": "error", "field": field, "message": message})

    def warning(field: str, message: str) -> None:
        issues.append({"level": "warning", "field": field, "message": message})

    if profile.get("schema_id") != SCHEMA_ID:
        error("schema_id", f"Expected {SCHEMA_ID}.")
    if profile.get("schema_version") != SCHEMA_VERSION:
        error("schema_version", f"Expected {SCHEMA_VERSION}.")
    if profile.get("authority") != AUTHORITY:
        error("authority", f"Expected {AUTHORITY}.")

    identity = _mapping(profile.get("identity"))
    if not identity.get("profile_id"):
        error("identity.profile_id", "Profile id is required.")
    if identity.get("route_family_id") != ROUTE_FAMILY_ID:
        error("identity.route_family_id", f"Expected {ROUTE_FAMILY_ID}.")
    family = normalize_family_id(identity.get("family"))
    loader = normalize_loader_id(identity.get("loader"))
    providers = [normalize_provider_id(item) for item in identity.get("provider_ids", [])]
    if not family:
        error("identity.family", "Family is required.")
    if loader not in SUPPORTED_LOADERS:
        error("identity.loader", "Expansion scaffolds support checkpoint, diffusion_model or gguf loaders.")
    if not providers or any(item not in SUPPORTED_PROVIDERS for item in providers):
        error("identity.provider_ids", "Profiles may target ComfyUI and ComfyUI Portable only.")
    if identity.get("mode") != MODE_ID or identity.get("engine") != ENGINE_ID:
        error("identity", "Expansion profiles must target image.inpaint.lanpaint.")
    expected_keys = _route_keys(providers, family, loader)
    if identity.get("route_keys") != expected_keys:
        error("identity.route_keys", "Route keys must be deterministic provider/family/loader/inpaint/lanpaint keys.")

    onboarding = _mapping(profile.get("onboarding"))
    onboarding_state = str(onboarding.get("state") or "")
    if onboarding_state not in ONBOARDING_STATES:
        error("onboarding.state", "Invalid Phase 9 onboarding state.")
    if not isinstance(onboarding.get("required_work"), list) or not onboarding.get("required_work"):
        error("onboarding.required_work", "At least one explicit onboarding task is required.")

    family_policy = _mapping(profile.get("family_policy"))
    expected_policy = get_lanpaint_family_policy(family, loader=loader, provider_id=providers[0] if providers else "")
    if expected_policy is None:
        if family_policy.get("resolution_state") != "missing_policy":
            error("family_policy.resolution_state", "Routes without a policy must declare missing_policy.")
    else:
        expected_identity = expected_policy.get("identity") or {}
        if family_policy.get("policy_id") != expected_identity.get("policy_id"):
            error("family_policy.policy_id", "Expansion profile must point to the matching family policy.")
        if family_policy.get("policy_status") != expected_identity.get("status"):
            error("family_policy.policy_status", "Expansion policy status does not match the family-policy registry.")

    loader_policy = _mapping(profile.get("loader_policy"))
    if not loader_policy.get("model_loader_role"):
        error("loader_policy.model_loader_role", "Model loader role is required.")
    if not loader_policy.get("accepted_node_classes"):
        error("loader_policy.accepted_node_classes", "At least one candidate loader node is required.")

    for section in ("conditioning_policy", "negative_policy", "lora_policy", "node_requirements", "model_requirements", "test_status"):
        if not isinstance(profile.get(section), dict):
            error(section, "Required expansion scaffold section is missing.")

    execution = _mapping(profile.get("execution"))
    onboarded = onboarding_state in {"onboarded_phase10", "onboarded_phase14", "onboarded_phase15", "onboarded_phase16", "onboarded_phase17", "onboarded_phase18", "onboarded_phase20", "onboarded_phase21", "onboarded_phase22"}
    if onboarded:
        if not all(bool(execution.get(key)) for key in ("enabled", "selectable", "executable")):
            error("execution", "Phase 10 onboarded profiles must be enabled, selectable and executable.")
        expected_execution_state = ({"onboarded_phase10": "phase10_onboarded", "onboarded_phase14": "phase14_stabilized", "onboarded_phase15": "phase15_onboarded", "onboarded_phase16": "phase16_onboarded", "onboarded_phase17": "phase17_onboarded", "onboarded_phase18": "phase18_onboarded", "onboarded_phase20": "phase20_onboarded", "onboarded_phase21": "phase21_onboarded", "onboarded_phase22": "phase22_onboarded"})[onboarding_state]
        if execution.get("state") != expected_execution_state:
            error("execution.state", f"Onboarded profiles require state={expected_execution_state}.")
        if execution.get("compiler_id") != "comfy.lanpaint.family_aware.v1":
            error("execution.compiler_id", "Onboarded routes must bind the family-aware LanPaint compiler.")
        if str(execution.get("route_status") or "") != "experimental_available":
            error("execution.route_status", "Onboarded routes remain experimental_available until physical validation.")
    else:
        if execution.get("enabled") is not False or execution.get("selectable") is not False or execution.get("executable") is not False:
            error("execution", "Non-onboarded expansion scaffolds must remain disabled.")
        if execution.get("state") != EXECUTION_STATE:
            error("execution.state", f"Expected {EXECUTION_STATE}.")
        if execution.get("compiler_id") is not None:
            error("execution.compiler_id", "Non-onboarded scaffolds must not bind a compiler.")
        if str(execution.get("route_status") or "") != "unsupported":
            error("execution.route_status", "Non-onboarded routes remain unsupported.")

    tests = _mapping(profile.get("test_status"))
    if tests.get("physical_validation") != "pending":
        error("test_status.physical_validation", "All Phase 9 expansion routes require physical validation.")
    if onboarding_state == "ready_for_phase10" and family_policy.get("policy_status") != COMPLETE_POLICY_STATE:
        error("onboarding.state", "Only routes with a complete family policy may be ready_for_phase10.")
    if onboarding_state == "blocked_family_policy" and family_policy.get("policy_status") == COMPLETE_POLICY_STATE:
        warning("onboarding.state", "A complete policy route normally should be ready_for_phase10.")
    if onboarding_state == "blocked_variant_identity" and family != "z_image_base":
        error("onboarding.state", "blocked_variant_identity is reserved for the future z_image_base identity scaffold.")

    return issues


def normalize_lanpaint_family_expansion_profile(raw: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile = deepcopy(_mapping(raw))
    identity = _mapping(profile.get("identity"))
    identity["family"] = normalize_family_id(identity.get("family"))
    identity["loader"] = normalize_loader_id(identity.get("loader"))
    identity["provider_ids"] = [
        item for item in SUPPORTED_PROVIDERS
        if item in {normalize_provider_id(value) for value in identity.get("provider_ids", [])}
    ]
    identity["mode"] = MODE_ID
    identity["engine"] = ENGINE_ID
    identity["route_family_id"] = ROUTE_FAMILY_ID
    identity["route_keys"] = _route_keys(identity["provider_ids"], identity["family"], identity["loader"])
    profile["identity"] = identity
    profile["schema_id"] = SCHEMA_ID
    profile["schema_version"] = SCHEMA_VERSION
    profile["authority"] = AUTHORITY
    issues = validate_lanpaint_family_expansion_profile(profile)
    profile["validation"] = {
        "ok": not any(item["level"] == "error" for item in issues),
        "issues": deepcopy(issues),
    }
    profile["profile_fingerprint"] = _fingerprint(profile)
    return profile, issues


def lanpaint_family_expansion_registry() -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_routes: set[tuple[str, str]] = set()
    for raw in load_lanpaint_family_expansion_profiles():
        profile, profile_issues = normalize_lanpaint_family_expansion_profile(raw)
        profile_id = str(profile.get("identity", {}).get("profile_id") or "")
        route_identity = (
            str(profile.get("identity", {}).get("family") or ""),
            str(profile.get("identity", {}).get("loader") or ""),
        )
        if profile_id in seen_ids:
            profile_issues.append({"level": "error", "field": "identity.profile_id", "message": "Duplicate expansion profile id."})
        if route_identity in seen_routes:
            profile_issues.append({"level": "error", "field": "identity", "message": "Duplicate family/loader expansion profile."})
        seen_ids.add(profile_id)
        seen_routes.add(route_identity)
        if profile_issues:
            profile["validation"] = {
                "ok": not any(item["level"] == "error" for item in profile_issues),
                "issues": deepcopy(profile_issues),
            }
            profile["profile_fingerprint"] = _fingerprint(profile)
        profiles.append(profile)
        issues.extend({**item, "profile_id": profile_id} for item in profile_issues)

    profiles.sort(key=lambda item: item["identity"]["profile_id"])
    matrix = [
        {
            "profile_id": item["identity"]["profile_id"],
            "family": item["identity"]["family"],
            "loader": item["identity"]["loader"],
            "display_name": item["identity"]["display_name"],
            "onboarding_state": item["onboarding"]["state"],
            "policy_status": item["family_policy"]["policy_status"],
            "lora_strategy": item["lora_policy"]["strategy"],
            "test_state": item["test_status"]["overall"],
            "route_status": item["execution"]["route_status"],
            "selectable": bool(item["execution"].get("selectable")),
            "executable": bool(item["execution"].get("executable")),
        }
        for item in profiles
    ]
    registry: dict[str, Any] = {
        "schema_id": REGISTRY_SCHEMA_ID,
        "schema_version": 1,
        "authority": AUTHORITY,
        "phase_state": PHASE_STATE,
        "route_family_id": ROUTE_FAMILY_ID,
        "profiles": profiles,
        "compatibility_matrix": matrix,
        "validation": {
            "ok": not any(item["level"] == "error" for item in issues),
            "issues": issues,
        },
        "execution": {
            "enabled": any(bool(item["execution"].get("enabled")) for item in profiles),
            "selectable": any(bool(item["execution"].get("selectable")) for item in profiles),
            "executable": any(bool(item["execution"].get("executable")) for item in profiles),
            "state": "registry_metadata",
            "compiler_id": "comfy.lanpaint.family_aware.v1",
            "reason": "Registry metadata summarizes Phase 9 scaffolds and the Phase 10 through Phase 22 onboarded bindings; execution authority remains the compile router.",
        },
    }
    registry["registry_fingerprint"] = _fingerprint(registry)
    return registry


def get_lanpaint_family_expansion_profile(
    family: Any,
    *,
    loader: Any,
    provider_id: Any | None = None,
) -> dict[str, Any] | None:
    family_id = normalize_family_id(family)
    loader_id = normalize_loader_id(loader)
    provider = normalize_provider_id(provider_id) if provider_id not in (None, "") else ""
    for profile in lanpaint_family_expansion_registry()["profiles"]:
        identity = profile["identity"]
        if identity["family"] != family_id or identity["loader"] != loader_id:
            continue
        if provider and provider not in identity["provider_ids"]:
            return None
        return deepcopy(profile)
    return None


def lanpaint_family_expansion_summary() -> dict[str, Any]:
    registry = lanpaint_family_expansion_registry()
    return {
        "schema_id": registry["schema_id"],
        "schema_version": registry["schema_version"],
        "authority": registry["authority"],
        "phase_state": registry["phase_state"],
        "route_family_id": registry["route_family_id"],
        "compatibility_matrix": deepcopy(registry["compatibility_matrix"]),
        "validation": deepcopy(registry["validation"]),
        "execution": deepcopy(registry["execution"]),
        "registry_fingerprint": registry["registry_fingerprint"],
    }


__all__ = [
    "AUTHORITY",
    "EXECUTION_STATE",
    "ONBOARDING_STATES",
    "PHASE_STATE",
    "REGISTRY_SCHEMA_ID",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "get_lanpaint_family_expansion_profile",
    "lanpaint_family_expansion_registry",
    "lanpaint_family_expansion_summary",
    "load_lanpaint_family_expansion_profiles",
    "normalize_lanpaint_family_expansion_profile",
    "validate_lanpaint_family_expansion_profile",
]
