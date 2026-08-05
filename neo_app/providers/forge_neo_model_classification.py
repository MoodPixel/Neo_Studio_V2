from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any, Iterable

from neo_app.models.forge_neo_route_catalog import FORGE_FAMILY_POLICIES, resolve_forge_route

FORGE_MODEL_CLASSIFICATION_SCHEMA_ID = "neo.provider.forge_live_model_classification.v1"
FORGE_MODEL_CLASSIFICATION_VERSION = "1.0.0"
FORGE_LIVE_ROUTE_INTERSECTION_SCHEMA_ID = "neo.provider.forge_live_route_intersection.v1"
FORGE_LIVE_ROUTE_INTERSECTION_VERSION = "1.0.0"

_SELECTABLE_ROUTE_STATES = {"available", "experimental_available"}
_IMAGE_MODES = ("txt2img", "img2img", "inpaint", "outpaint", "edit")
_KNOWN_MODEL_FORMATS = {
    ".safetensors": "safetensors",
    ".ckpt": "ckpt",
    ".gguf": "gguf",
    ".pt": "pt",
    ".pth": "pth",
    ".bin": "bin",
}
_PRECISION_TAGS = (
    "bf16",
    "fp16",
    "fp8_scaled",
    "fp8mixed",
    "mxfp8",
    "nvfp4",
    "fp4mixed",
    "fp8",
    "nf4",
    "int8_convrot",
    "convrot_w4a4",
    "q2_k",
    "q3_k",
    "q4_k",
    "q5_k",
    "q6_k",
    "q8_0",
)


def _portable_name(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1] if text else ""


def _identity_parts(record: dict[str, Any], fields: Iterable[str]) -> list[str]:
    parts: list[str] = []
    for field in fields:
        value = _portable_name(record.get(field))
        if value and value not in parts:
            parts.append(value)
    return parts


def _normalized_text(parts: Iterable[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "_", " ".join(parts).casefold()).strip("_")


def _contains(text: str, *tokens: str) -> bool:
    return all(re.search(rf"(?:^|_){re.escape(token)}(?:_|$)", text) is not None for token in tokens)


def _contains_any(text: str, tokens: Iterable[str]) -> bool:
    return any(token in text for token in tokens)


def _model_format(parts: list[str]) -> str:
    for value in reversed(parts):
        suffix = PurePath(value).suffix.casefold()
        if suffix in _KNOWN_MODEL_FORMATS:
            return _KNOWN_MODEL_FORMATS[suffix]
    return "unknown"


def _precision_tags(text: str) -> list[str]:
    return [tag for tag in _PRECISION_TAGS if tag in text]


def _candidate(family: str, score: int, confidence: str, signals: list[str], *, variant: str = "") -> dict[str, Any]:
    return {
        "family": family,
        "score": score,
        "confidence": confidence,
        "signals": list(signals),
        "variant": variant,
    }


def _family_candidates(text: str, model_format: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    qwen = "qwen" in text
    if qwen and "rapid" in text and "aio" in text:
        candidates.append(_candidate("qwen_rapid_aio", 100, "high", ["qwen", "rapid", "aio"], variant="rapid_aio"))
        return candidates

    if qwen and "edit" in text:
        version = "2509" if "2509" in text else ""
        confidence = "high" if version else "medium"
        score = 100 if version else 82
        signals = ["qwen", "edit"] + (["2509"] if version else [])
        exact_qwen_edit_family = "qwen_image_edit_2511" if version == "2511" else "qwen_image_edit_2509"
        candidates.append(_candidate(exact_qwen_edit_family, score, confidence, signals, variant=version or "version_unverified"))
        return candidates

    if ("flux_2" in text or "flux2" in text) and "klein" in text:
        size = "9b" if _contains(text, "9b") else "4b" if _contains(text, "4b") else ""
        candidates.append(_candidate("flux2_klein", 100, "high", ["flux2", "klein"] + ([size] if size else []), variant=size))
        return candidates

    has_krea2 = "krea_2" in text or "krea2" in text
    if has_krea2 and "turbo" in text:
        candidates.append(_candidate("krea2_turbo", 100, "high", ["krea2", "turbo"], variant="turbo"))
        return candidates
    if has_krea2:
        signals = ["krea2"] + (["raw"] if "raw" in text else [])
        candidates.append(_candidate("krea2", 96, "high", signals, variant="raw" if "raw" in text else ""))
        return candidates

    has_z_image = "z_image" in text or "zimage" in text
    if has_z_image and "turbo" in text:
        candidates.append(_candidate("z_image_turbo", 100, "high", ["z_image", "turbo"], variant="turbo"))
        return candidates
    if has_z_image:
        candidates.append(_candidate("z_image", 96, "high", ["z_image"]))
        return candidates

    if qwen and "image" in text:
        candidates.append(_candidate("qwen_image", 96, "high", ["qwen", "image"]))
        return candidates

    if "wan" in text and _contains_any(text, ("wan_2_2", "wan22", "wan_2_1", "t2v", "i2v")):
        variant = "i2v" if "i2v" in text else "t2v" if "t2v" in text else "wan"
        candidates.append(_candidate("wan_image", 94, "high", ["wan"], variant=variant))
        return candidates

    if "hunyuan" in text and "image" in text:
        candidates.append(_candidate("hunyuan_image", 92, "high", ["hunyuan", "image"]))
        return candidates

    if "hidream" in text or "hi_dream" in text:
        candidates.append(_candidate("hidream", 92, "high", ["hidream"]))
        return candidates

    if "flux" in text and not ("flux_2" in text or "flux2" in text):
        variant = "fill" if "fill" in text else "kontext" if "kontext" in text else "krea" if "krea" in text else "dev" if "dev" in text else ""
        candidates.append(_candidate("flux", 92, "high", ["flux"] + ([variant] if variant else []), variant=variant))
        return candidates

    if "sdxl" in text:
        candidates.append(_candidate("sdxl", 98, "high", ["sdxl"]))
        return candidates
    if _contains_any(text, ("juggernautxl", "realvisxl", "animaginexl", "ponyxl", "illustriousxl")) or re.search(r"(?:^|_)xl(?:_|$)", text):
        candidates.append(_candidate("sdxl", 74, "medium", ["xl_name_hint"]))
        return candidates

    if _contains_any(text, ("sd_1_5", "sd15", "sd1_5", "v1_5", "stable_diffusion_1_5")):
        candidates.append(_candidate("sd15", 96, "high", ["sd15"]))
        return candidates

    if model_format in {"safetensors", "ckpt"}:
        # Forge's standard model endpoint does not publish a reliable architecture
        # field for classic checkpoints. Preserve both candidates instead of
        # guessing between SD 1.5 and SDXL from a generic filename.
        candidates.extend(
            [
                _candidate("sd15", 30, "low", ["classic_checkpoint_ambiguous"]),
                _candidate("sdxl", 30, "low", ["classic_checkpoint_ambiguous"]),
            ]
        )
    return candidates


def _model_packaging(text: str, model_format: str) -> str:
    if "nunchaku" in text or "svdq" in text:
        return "nunchaku_svdq"
    if model_format == "gguf":
        return "gguf"
    if model_format in {"safetensors", "ckpt"}:
        return "forge_primary_model"
    return "unknown"


def _loader_candidates(family: str, model_format: str, packaging: str) -> list[str]:
    if packaging == "nunchaku_svdq":
        return ["nunchaku"]
    if family == "qwen_rapid_aio" and model_format == "safetensors":
        return ["checkpoint_aio"]
    if family in {"sd15", "sdxl"}:
        return ["checkpoint"] if model_format in {"safetensors", "ckpt"} else []
    if model_format == "gguf":
        return ["gguf"]
    if model_format == "safetensors":
        return ["diffusion_model"]
    return []


def classify_forge_primary_model(record: dict[str, Any]) -> dict[str, Any]:
    parts = _identity_parts(record, ("title", "model_name", "filename", "config", "name"))
    text = _normalized_text(parts)
    model_format = _model_format(parts)
    packaging = _model_packaging(text, model_format)
    candidates = _family_candidates(text, model_format)
    top = candidates[0] if candidates else None
    top_score = int((top or {}).get("score") or 0)
    exact_family = str((top or {}).get("family") or "") if top_score >= 70 else ""
    status = "classified" if exact_family else "ambiguous" if candidates else "unclassified"
    if packaging == "nunchaku_svdq":
        status = "classified_provider_format_gated" if exact_family else "unclassified_provider_format_gated"

    candidate_families = {str(item.get("family") or "") for item in candidates if isinstance(item, dict)}
    family_for_loader = exact_family or (str(candidates[0]["family"]) if len(candidates) == 1 else "")
    if not exact_family and candidate_families and candidate_families <= {"sd15", "sdxl"} and model_format in {"safetensors", "ckpt"}:
        loaders = ["checkpoint"]
    else:
        loaders = _loader_candidates(family_for_loader, model_format, packaging)
    route_eligible = bool(
        exact_family
        and packaging != "nunchaku_svdq"
        and any(loader in (FORGE_FAMILY_POLICIES.get(exact_family).loaders if FORGE_FAMILY_POLICIES.get(exact_family) else {}) for loader in loaders)
    )
    if exact_family == "qwen_image_edit_2509" and (top or {}).get("variant") == "version_unverified":
        route_eligible = False

    display_name = parts[0] if parts else ""
    stable_id = _portable_name(record.get("filename") or record.get("title") or record.get("model_name") or display_name)
    return {
        "id": stable_id or display_name,
        "name": display_name or stable_id,
        "title": _portable_name(record.get("title") or display_name),
        "model_name": _portable_name(record.get("model_name") or display_name),
        "filename": _portable_name(record.get("filename")),
        "format": model_format,
        "packaging": packaging,
        "precision_tags": _precision_tags(text),
        "classification_status": status,
        "family": exact_family,
        "family_candidates": candidates,
        "loader_candidates": loaders,
        "variant": str((top or {}).get("variant") or ""),
        "confidence": str((top or {}).get("confidence") or "none"),
        "signals": list((top or {}).get("signals") or []),
        "route_eligible": route_eligible,
        "source": "forge_sd_models",
    }


def _module_roles(text: str) -> tuple[str, list[str], list[str]]:
    roles: list[str] = []
    signals: list[str] = []
    kind = "module"

    def add(*items: str) -> None:
        for item in items:
            if item not in roles:
                roles.append(item)

    if "mmproj" in text:
        kind = "mmproj"
        add("mmproj")
        signals.append("mmproj")
        return kind, roles, signals

    qwen2d_vae = "qwen2d" in text and "vae" in text
    qwen_image_vae = "qwen_image_vae" in text or qwen2d_vae
    flux2_decoder = ("flux2" in text and ("vae" in text or "decoder" in text)) or "small_decoder" in text
    generic_vae = "vae" in text
    generic_ae = re.search(r"(?:^|_)ae(?:_|$)", text) is not None or text.endswith("_ae")
    if qwen_image_vae:
        kind = "vae"
        add("qwen_image_vae", "vae", "vae_or_ae", "ae_or_vae")
        signals.append("qwen_image_vae")
    elif flux2_decoder:
        kind = "vae"
        add("vae", "vae_or_ae", "ae_or_vae")
        signals.append("flux2_decoder")
    elif generic_vae:
        kind = "vae"
        add("vae", "vae_or_ae", "ae_or_vae")
        signals.append("vae")
    elif generic_ae:
        kind = "vae"
        add("vae_or_ae", "ae_or_vae")
        signals.append("ae")

    if "qwen3vl" in text and "4b" in text:
        kind = "text_encoder"
        add("qwen3vl_4b_text_encoder", "qwen3_text_encoder")
        signals.extend(["qwen3vl", "4b"])
    elif ("qwen_3" in text or "qwen3" in text) and _contains_any(text, ("4b", "8b", "0_6b")):
        kind = "text_encoder"
        add("qwen3_text_encoder")
        signals.append("qwen3")
    elif ("qwen_2_5" in text or "qwen2_5" in text or "qwen2_5vl" in text) and "vl" in text:
        kind = "text_encoder"
        add("qwen_text_encoder")
        signals.append("qwen2_5_vl")
    elif "qwen" in text and not roles:
        kind = "text_encoder"
        add("qwen_text_encoder")
        signals.append("qwen_text")

    if "clip_l" in text or "clipl" in text:
        kind = "text_encoder"
        add("text_encoder_primary")
        signals.append("clip_l")
    if "t5" in text:
        kind = "text_encoder"
        add("text_encoder_secondary")
        signals.append("t5")
    if any(token in text for token in ("text_encoder", "clip", "gemma", "ministral", "umt5")) and kind == "module":
        kind = "text_encoder"
        signals.append("generic_text_encoder")

    return kind, roles, list(dict.fromkeys(signals))


def classify_forge_module(record: dict[str, Any]) -> dict[str, Any]:
    parts = _identity_parts(record, ("model_name", "filename", "name"))
    text = _normalized_text(parts)
    module_format = _model_format(parts)
    kind, roles, signals = _module_roles(text)
    name = _portable_name(record.get("model_name") or record.get("name") or (parts[0] if parts else ""))
    filename = _portable_name(record.get("filename"))
    return {
        "id": filename or name,
        "name": name or filename,
        "filename": filename,
        "format": module_format,
        "module_kind": kind,
        "roles": roles,
        "signals": signals,
        "precision_tags": _precision_tags(text),
        "source": "forge_sd_modules",
    }


def _truthy_setting(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _setting_capabilities(settings_catalog: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    settings = (settings_catalog or {}).get("settings") if isinstance(settings_catalog, dict) else []
    settings = settings if isinstance(settings, list) else []
    capabilities: dict[str, dict[str, Any]] = {
        "flux2_klein_regular_img2img": {
            "capability_id": "flux2_klein_regular_img2img",
            "status": "not_discovered",
            "available": False,
            "enabled": False,
            "backend_key": "",
            "signals": [],
        }
    }
    for item in settings:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        label = str(item.get("label") or "")
        description = str(item.get("description") or "")
        text = _normalized_text((key, label, description))
        if "flux" in text and "klein" in text and "img2img" in text:
            capabilities["flux2_klein_regular_img2img"] = {
                "capability_id": "flux2_klein_regular_img2img",
                "status": "discovered",
                "available": True,
                "enabled": _truthy_setting(item.get("current_value")),
                "backend_key": key,
                "signals": ["flux", "klein", "img2img"],
            }
            break
    return capabilities


def _walk_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append(str(key))
            found.extend(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_keys(child))
    return found


def _script_capabilities(
    scripts: dict[str, Any] | None,
    script_info: list[dict[str, Any]] | None,
    extensions: list[dict[str, Any]] | None,
    bridge: dict[str, Any] | None,
    openapi_feature_keys: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    evidence: list[str] = []
    scripts = scripts if isinstance(scripts, dict) else {}
    for mode in ("txt2img", "img2img"):
        evidence.extend(str(item) for item in scripts.get(mode) or [])
    for item in script_info or []:
        if not isinstance(item, dict):
            continue
        evidence.append(str(item.get("name") or ""))
        for arg in item.get("args") or []:
            if isinstance(arg, dict):
                evidence.append(str(arg.get("label") or ""))
    for item in extensions or []:
        if isinstance(item, dict):
            evidence.append(str(item.get("name") or ""))
    evidence.extend(str(item) for item in (openapi_feature_keys or []))
    evidence.extend(_walk_keys((bridge or {}).get("capabilities") if isinstance(bridge, dict) else {}))

    matches = []
    for item in evidence:
        normalized = _normalized_text((item,))
        if "imagestitch" in normalized or ("image" in normalized and "stitch" in normalized):
            matches.append(_portable_name(item))
    matches = sorted({item for item in matches if item})
    return {
        "image_stitch_integrated": {
            "capability_id": "image_stitch_integrated",
            "status": "discovered" if matches else "not_exposed",
            "available": bool(matches),
            "signals": matches,
        }
    }


def _module_inventory(modules: list[dict[str, Any]]) -> dict[str, list[str]]:
    inventory: dict[str, list[str]] = {}
    for module in modules:
        name = str(module.get("name") or "").strip()
        for role in module.get("roles") or []:
            bucket = inventory.setdefault(str(role), [])
            if name and name not in bucket:
                bucket.append(name)
    return {role: sorted(names, key=str.casefold) for role, names in sorted(inventory.items())}


def build_forge_live_model_classification(
    *,
    models: list[dict[str, Any]] | None = None,
    modules: list[dict[str, Any]] | None = None,
    settings_catalog: dict[str, Any] | None = None,
    scripts: dict[str, Any] | None = None,
    script_info: list[dict[str, Any]] | None = None,
    extensions: list[dict[str, Any]] | None = None,
    identity: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    bridge: dict[str, Any] | None = None,
    openapi_feature_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    classified_models = [classify_forge_primary_model(item) for item in (models or []) if isinstance(item, dict)]
    classified_modules = [classify_forge_module(item) for item in (modules or []) if isinstance(item, dict)]
    family_counts: dict[str, int] = {}
    ambiguous_count = 0
    unclassified_count = 0
    for item in classified_models:
        family = str(item.get("family") or "")
        if family:
            family_counts[family] = family_counts.get(family, 0) + 1
        elif item.get("classification_status") == "ambiguous":
            ambiguous_count += 1
        else:
            unclassified_count += 1
    model_formats: dict[str, int] = {}
    packaging_counts: dict[str, int] = {}
    for item in classified_models:
        model_format = str(item.get("format") or "unknown")
        packaging = str(item.get("packaging") or "unknown")
        model_formats[model_format] = model_formats.get(model_format, 0) + 1
        packaging_counts[packaging] = packaging_counts.get(packaging, 0) + 1

    return {
        "schema_id": FORGE_MODEL_CLASSIFICATION_SCHEMA_ID,
        "version": FORGE_MODEL_CLASSIFICATION_VERSION,
        "provider_id": "forge",
        "backend_identity": dict(identity or {}),
        "models": classified_models,
        "modules": classified_modules,
        "module_inventory": _module_inventory(classified_modules),
        "setting_capabilities": _setting_capabilities(settings_catalog),
        "script_capabilities": _script_capabilities(scripts, script_info, extensions, bridge, openapi_feature_keys),
        "endpoint_capabilities": {
            "txt2img": bool((capabilities or {}).get("txt2img_api", (capabilities or {}).get("neo_execution_adapter", False))),
            "img2img": bool((capabilities or {}).get("img2img_api", (capabilities or {}).get("neo_execution_adapter", False))),
            "progress": bool((capabilities or {}).get("progress_api", (capabilities or {}).get("neo_execution_adapter", False))),
            "interrupt": bool((capabilities or {}).get("interrupt_api", (capabilities or {}).get("neo_execution_adapter", False))),
        },
        "summary": {
            "models": len(classified_models),
            "modules": len(classified_modules),
            "classified_models": sum(1 for item in classified_models if str(item.get("family") or "")),
            "ambiguous_models": ambiguous_count,
            "unclassified_models": unclassified_count,
            "route_eligible_models": sum(1 for item in classified_models if item.get("route_eligible")),
            "families": dict(sorted(family_counts.items())),
            "formats": dict(sorted(model_formats.items())),
            "packaging": dict(sorted(packaging_counts.items())),
        },
        "policy": {
            "filename_classification_is_advisory": True,
            "ambiguous_classic_checkpoints_are_not_forced_to_sd15_or_sdxl": True,
            "nunchaku_is_not_gguf": True,
            "absolute_paths_are_not_retained": True,
            "live_route_intersection_is_required": True,
        },
    }


def _model_matches_route(model: dict[str, Any], family: str, loader: str) -> tuple[bool, bool]:
    loaders = {str(item) for item in model.get("loader_candidates") or []}
    if loader not in loaders:
        return False, False
    exact = str(model.get("family") or "") == family and bool(model.get("route_eligible"))
    candidate_families = {str(item.get("family") or "") for item in model.get("family_candidates") or [] if isinstance(item, dict)}
    ambiguous = not exact and family in candidate_families and model.get("classification_status") == "ambiguous"
    return exact, ambiguous


def _route_endpoint_available(route_endpoint: str | None, endpoint_capabilities: dict[str, Any]) -> bool:
    if route_endpoint == "/sdapi/v1/txt2img":
        return bool(endpoint_capabilities.get("txt2img"))
    if route_endpoint == "/sdapi/v1/img2img":
        return bool(endpoint_capabilities.get("img2img"))
    return True


def build_forge_live_route_intersection(
    classification: dict[str, Any],
    *,
    enabled_modes: set[str] | None = None,
) -> dict[str, Any]:
    enabled_modes = set(_IMAGE_MODES) if enabled_modes is None else set(enabled_modes)
    models = classification.get("models") if isinstance(classification.get("models"), list) else []
    module_inventory = classification.get("module_inventory") if isinstance(classification.get("module_inventory"), dict) else {}
    setting_caps = classification.get("setting_capabilities") if isinstance(classification.get("setting_capabilities"), dict) else {}
    script_caps = classification.get("script_capabilities") if isinstance(classification.get("script_capabilities"), dict) else {}
    endpoint_caps = classification.get("endpoint_capabilities") if isinstance(classification.get("endpoint_capabilities"), dict) else {}

    routes: list[dict[str, Any]] = []
    for family_id in sorted(FORGE_FAMILY_POLICIES):
        family = FORGE_FAMILY_POLICIES[family_id]
        for loader_id in sorted(family.loaders):
            loader = family.loaders[loader_id]
            for mode in _IMAGE_MODES:
                if mode not in loader.workflows:
                    continue
                authority = resolve_forge_route(family_id, loader_id, mode)
                exact_models: list[str] = []
                ambiguous_models: list[str] = []
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    exact, ambiguous = _model_matches_route(model, family_id, loader_id)
                    if exact:
                        exact_models.append(str(model.get("name") or model.get("id") or ""))
                    elif ambiguous:
                        ambiguous_models.append(str(model.get("name") or model.get("id") or ""))
                exact_models = [name for name in exact_models if name]
                ambiguous_models = [name for name in ambiguous_models if name]

                missing_modules = [role for role in authority.required_module_roles if not module_inventory.get(role)]
                missing_settings: list[str] = []
                disabled_settings: list[str] = []
                for capability_id in authority.required_settings:
                    detail = setting_caps.get(capability_id) if isinstance(setting_caps.get(capability_id), dict) else {}
                    if not detail.get("available"):
                        missing_settings.append(capability_id)
                    elif not detail.get("enabled"):
                        disabled_settings.append(capability_id)
                missing_scripts = [
                    capability_id
                    for capability_id in authority.required_scripts
                    if not bool((script_caps.get(capability_id) or {}).get("available"))
                ]
                endpoint_available = _route_endpoint_available(authority.endpoint, endpoint_caps)
                mode_enabled = mode in enabled_modes

                blockers: list[str] = []
                if authority.state not in _SELECTABLE_ROUTE_STATES:
                    blockers.append(f"authority_state:{authority.state}")
                if not exact_models and not ambiguous_models:
                    blockers.append("missing_primary_model")
                if missing_modules:
                    blockers.append("missing_modules")
                if missing_settings:
                    blockers.append("missing_settings")
                if disabled_settings:
                    blockers.append("disabled_settings")
                if missing_scripts:
                    blockers.append("missing_scripts")
                if not endpoint_available:
                    blockers.append("endpoint_unavailable")
                if not mode_enabled:
                    blockers.append("mode_disabled_by_profile")

                selectable = not blockers
                assets_ready = bool((exact_models or ambiguous_models) and not missing_modules and not missing_settings and not disabled_settings and not missing_scripts and endpoint_available)
                if selectable:
                    live_state = "ready"
                elif authority.state == "implementation_target" and assets_ready and mode_enabled:
                    live_state = "compiler_gated_assets_ready"
                elif authority.state not in _SELECTABLE_ROUTE_STATES:
                    live_state = "authority_gated"
                elif "missing_primary_model" in blockers:
                    live_state = "missing_primary_model"
                elif any(item in blockers for item in ("missing_modules", "missing_settings", "disabled_settings", "missing_scripts")):
                    live_state = "missing_requirements"
                elif "endpoint_unavailable" in blockers:
                    live_state = "endpoint_unavailable"
                else:
                    live_state = "profile_gated"

                routes.append(
                    {
                        "family": family_id,
                        "architecture_id": authority.architecture_id,
                        "loader": loader_id,
                        "provider_loader_id": authority.provider_loader_id,
                        "mode": mode,
                        "authority_state": authority.state,
                        "live_state": live_state,
                        "selectable": selectable,
                        "assets_ready": assets_ready,
                        "mode_enabled": mode_enabled,
                        "endpoint": authority.endpoint,
                        "endpoint_available": endpoint_available,
                        "exact_models": sorted(set(exact_models), key=str.casefold),
                        "ambiguous_models": sorted(set(ambiguous_models), key=str.casefold),
                        "required_module_roles": list(authority.required_module_roles),
                        "missing_module_roles": missing_modules,
                        "required_settings": list(authority.required_settings),
                        "missing_settings": missing_settings,
                        "disabled_settings": disabled_settings,
                        "required_scripts": list(authority.required_scripts),
                        "missing_scripts": missing_scripts,
                        "blockers": blockers,
                        "reason": authority.reason,
                    }
                )

    selectable_routes = [item for item in routes if item.get("selectable")]
    families = sorted({str(item["family"]) for item in selectable_routes})
    loaders = sorted({str(item["loader"]) for item in selectable_routes})
    modes = [mode for mode in _IMAGE_MODES if any(item.get("mode") == mode for item in selectable_routes)]
    discovered_families = sorted(
        {
            str(item.get("family") or "")
            for item in models
            if isinstance(item, dict) and str(item.get("family") or "")
        }
    )
    compiler_targets_assets_ready = [
        {"family": item["family"], "loader": item["loader"], "mode": item["mode"]}
        for item in routes
        if item.get("live_state") == "compiler_gated_assets_ready"
    ]
    return {
        "schema_id": FORGE_LIVE_ROUTE_INTERSECTION_SCHEMA_ID,
        "version": FORGE_LIVE_ROUTE_INTERSECTION_VERSION,
        "provider_id": "forge",
        "classification_schema_id": classification.get("schema_id"),
        "routes": routes,
        "selectable_summary": {
            "families": families,
            "loaders": loaders,
            "modes": modes,
            "routes": [
                {
                    "family": item["family"],
                    "loader": item["loader"],
                    "mode": item["mode"],
                    "state": item["authority_state"],
                }
                for item in selectable_routes
            ],
        },
        "diagnostics": {
            "discovered_families": discovered_families,
            "compiler_targets_assets_ready": compiler_targets_assets_ready,
            "unclassified_models": [
                str(item.get("name") or item.get("id") or "")
                for item in models
                if isinstance(item, dict) and not item.get("family") and item.get("classification_status") != "ambiguous"
            ],
            "ambiguous_models": [
                str(item.get("name") or item.get("id") or "")
                for item in models
                if isinstance(item, dict) and item.get("classification_status") == "ambiguous"
            ],
        },
        "policy": {
            "selected_profile_only": True,
            "authority_and_live_assets_must_agree": True,
            "implementation_targets_never_become_selectable_without_compilers": True,
            "ambiguous_classic_checkpoints_may_satisfy_sd_candidate_routes": True,
        },
    }


def ensure_forge_live_discovery(snapshot: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    classification = snapshot.get("model_classification") if isinstance(snapshot.get("model_classification"), dict) else None
    if not classification or classification.get("schema_id") != FORGE_MODEL_CLASSIFICATION_SCHEMA_ID:
        capabilities = dict(snapshot.get("capabilities") or {}) if isinstance(snapshot.get("capabilities"), dict) else {}
        connected_legacy_snapshot = bool(
            snapshot.get("reachable")
            and snapshot.get("api_enabled", True)
            and str(snapshot.get("status") or "") in {"connected", "connected_with_warnings", "online", "ready", "available"}
        )
        if connected_legacy_snapshot:
            capabilities.setdefault("neo_execution_adapter", True)
            capabilities.setdefault("txt2img_api", True)
            capabilities.setdefault("img2img_api", True)
            capabilities.setdefault("progress_api", True)
            capabilities.setdefault("interrupt_api", True)
        classification = build_forge_live_model_classification(
            models=snapshot.get("models") if isinstance(snapshot.get("models"), list) else [],
            modules=snapshot.get("modules") if isinstance(snapshot.get("modules"), list) else [],
            settings_catalog=snapshot.get("settings_catalog") if isinstance(snapshot.get("settings_catalog"), dict) else {},
            scripts=snapshot.get("scripts") if isinstance(snapshot.get("scripts"), dict) else {},
            script_info=snapshot.get("script_info") if isinstance(snapshot.get("script_info"), list) else [],
            extensions=snapshot.get("extensions") if isinstance(snapshot.get("extensions"), list) else [],
            identity=snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {},
            capabilities=capabilities,
            bridge=snapshot.get("bridge") if isinstance(snapshot.get("bridge"), dict) else {},
            openapi_feature_keys=snapshot.get("openapi_feature_keys") if isinstance(snapshot.get("openapi_feature_keys"), list) else [],
        )
    intersection = snapshot.get("live_route_intersection") if isinstance(snapshot.get("live_route_intersection"), dict) else None
    if not intersection or intersection.get("schema_id") != FORGE_LIVE_ROUTE_INTERSECTION_SCHEMA_ID:
        intersection = build_forge_live_route_intersection(classification)
    return classification, intersection
