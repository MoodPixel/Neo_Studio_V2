from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from neo_app.video.gguf_dual_noise_mapping import classify_noise_role
from neo_app.video.gguf_loader_adapter import build_wan22_gguf_loader_plan
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

PHASE: Final[str] = "V-G6"
SCHEMA_VERSION: Final[str] = "neo.video.model_discovery.vg6"
WAN22_GGUF_DUAL_NOISE_ROUTE_ID: Final[str] = "wan22.gguf.img2vid_14b_dual_noise"
WAN22_RAPID_AIO_GGUF_ROUTE_IDS: Final[set[str]] = {"wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"}
RAPID_AIO_NONE_TEST_ID: Final[str] = "__none_test_packed__"
DEFAULT_FALLBACK_MODELS: Final[dict[str, str]] = {
    "high_noise": "wan2.2_i2v_high_noise_14B_Q4_K_M.gguf",
    "low_noise": "wan2.2_i2v_low_noise_14B_Q4_K_M.gguf",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
    "high_noise_lora": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "low_noise_lora": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
}


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    role: str = "generic"
    source: str = "comfy_object_info"
    recommended: bool = False
    visible: bool = True

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "source": self.source,
            "recommended": self.recommended,
            "visible": self.visible,
        }


def _unique(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold().replace("\\", "/")
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _option(value: str, *, role: str = "generic", recommended: bool = False) -> dict[str, Any]:
    return ModelOption(id=value, label=value, role=role, recommended=recommended).payload()


def _options(values: list[str] | tuple[str, ...], *, role: str = "generic", recommended: str | None = None) -> list[dict[str, Any]]:
    rec = str(recommended or "").strip().casefold()
    return [_option(value, role=role, recommended=bool(rec and value.casefold() == rec)) for value in _unique(values)]


def _candidate_names(mapping: dict[str, Any], role: str) -> list[str]:
    candidates = mapping.get("candidates", {}) if isinstance(mapping, dict) else {}
    rows = candidates.get(role, []) if isinstance(candidates, dict) else []
    names = [str(item.get("model_name") or "") for item in rows if isinstance(item, dict)]
    return _unique(names)


def _fallback_role_filtered(all_models: list[str], role: str) -> list[str]:
    values = [model for model in all_models if classify_noise_role(model) == role]
    return values or all_models



def _lora_role_filtered(values: list[str], role: str) -> list[str]:
    low_role = role.casefold()
    if low_role == "high_noise":
        filtered = [value for value in values if any(token in value.casefold() for token in ("high_noise", "high-noise", "highnoise"))]
    elif low_role == "low_noise":
        filtered = [value for value in values if any(token in value.casefold() for token in ("low_noise", "low-noise", "lownoise"))]
    else:
        filtered = []
    light = [value for value in filtered if "lightx2v" in value.casefold() or "4step" in value.casefold() or "4steps" in value.casefold()]
    return light or filtered

def _model_in_catalog(model_name: str, values: list[str]) -> bool:
    visible = {value.casefold() for value in values}
    return bool(model_name and model_name.casefold() in visible)



def _rapid_aio_dynamic_catalogs(object_info: dict[str, Any]) -> dict[str, list[str]]:
    gguf: list[str] = []
    clip: list[str] = []
    vae: list[str] = []
    info = object_info or {}
    for class_type, entry in info.items():
        if not isinstance(entry, dict):
            continue
        inputs = entry.get("input", {}) if isinstance(entry.get("input", {}), dict) else {}
        for group_name in ("required", "optional"):
            group = inputs.get(group_name, {}) if isinstance(inputs.get(group_name, {}), dict) else {}
            for field_name, spec in group.items():
                values: list[str] = []
                if isinstance(spec, list) and spec and isinstance(spec[0], list):
                    values = [str(item) for item in spec[0]]
                elif isinstance(spec, dict):
                    for key in ("values", "options", "choices"):
                        if isinstance(spec.get(key), list):
                            values = [str(item) for item in spec[key]]
                            break
                token = f"{class_type} {field_name}".casefold()
                for value in values:
                    low = value.casefold()
                    if low.endswith(".gguf") or "gguf" in low:
                        gguf.append(value)
                    elif "vae" in token or "vae" in low:
                        vae.append(value)
                    elif any(marker in token for marker in ("clip", "text", "encoder", "t5", "umt5")) or any(marker in low for marker in ("clip", "t5", "umt5", "encoder")):
                        clip.append(value)
    return {"gguf": _unique(gguf), "clip": _unique(clip), "vae": _unique(vae)}

def video_model_discovery_from_object_info(
    object_info: dict[str, Any] | None,
    *,
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    fallback_models: dict[str, str] | None = None,
    high_noise_model: str | None = None,
    low_noise_model: str | None = None,
    rapid_aio_model: str | None = None,
    clip_name: str | None = None,
    vae_name: str | None = None,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
) -> dict[str, Any]:
    """Discover Video model catalogs from live ComfyUI /object_info dropdowns only.

    V-G6 intentionally does not scan or assume local folder paths. ComfyUI's node schemas
    are the source of truth because custom GGUF node packs expose different loader names,
    field names, and model dropdowns.
    """
    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    nt = normalize_video_generation_type(generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    route_id = route.route_id if route else ""
    info = object_info or {}
    fallbacks = {**DEFAULT_FALLBACK_MODELS, **(fallback_models or {})}

    base_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "route_id": route_id,
        "family": nf,
        "loader": nl,
        "generation_type": nt,
        "source": "comfy_object_info",
        "filesystem_scan_used": False,
        "manual_entry_allowed": True,
        "catalog_ready": False,
        "field_sources": {},
        "selected": {},
        "catalogs": {},
        "options": {},
        "counts": {},
        "warnings": [],
        "errors": [],
    }

    if route_id in WAN22_RAPID_AIO_GGUF_ROUTE_IDS:
        catalogs_raw = _rapid_aio_dynamic_catalogs(info)
        all_gguf = catalogs_raw["gguf"]
        clip_values = catalogs_raw["clip"]
        vae_values = catalogs_raw["vae"]
        selected_model = str(rapid_aio_model or high_noise_model or "")
        warnings = ["Rapid AIO GGUF discovery is production-bound in V25.9.19-10y; model, text encoder, and VAE values come from live ComfyUI/Admin catalogs."]
        errors: list[str] = []
        if not all_gguf:
            errors.append("No GGUF model dropdown values were exposed by ComfyUI /object_info for Rapid AIO audit.")
        if selected_model and not _model_in_catalog(selected_model, all_gguf):
            warnings.append(f"Selected Rapid AIO GGUF is not visible in the live Comfy dropdown: {selected_model}")
        catalogs = {
            "rapid_aio_gguf_models": all_gguf,
            "gguf_models": all_gguf,
            "rapid_aio_text_encoders": clip_values,
            "rapid_aio_vaes": vae_values,
            "text_encoders": clip_values,
            "vaes": vae_values,
        }
        options = {
            "rapid_aio_gguf_models": _options(all_gguf, role="rapid_aio_gguf", recommended=selected_model),
            "gguf_models": _options(all_gguf, role="gguf", recommended=selected_model),
            "rapid_aio_text_encoders": _options(clip_values, role="text_encoder", recommended=clip_name),
            "rapid_aio_vaes": _options(vae_values, role="vae", recommended=vae_name),
            "text_encoders": _options(clip_values, role="text_encoder", recommended=clip_name),
            "vaes": _options(vae_values, role="vae", recommended=vae_name),
        }
        base_payload.update(
            {
                "schema_version": "neo.video.model_discovery.v25_9_19_phase10y",
                "phase": "V25.9.19-10y",
                "catalog_ready": bool(all_gguf),
                "rapid_aio_catalog_ready": bool(all_gguf),
                "audit_only": False,
                "hardcoded_model_names": False,
                "test_packed_encoder_vae_supported": False,
                "field_sources": {
                    "gguf_loader": "dynamic_object_info_scan",
                    "gguf_model_field": "dynamic_combo_scan",
                    "clip_loader": "dynamic_object_info_scan",
                    "clip_field": "dynamic_combo_scan",
                    "vae_loader": "dynamic_object_info_scan",
                    "vae_field": "dynamic_combo_scan",
                },
                "selected": {
                    "rapid_aio_model": selected_model,
                    "rapid_aio_text_encoder": clip_name or "provider_default",
                    "rapid_aio_vae": vae_name or "provider_default",
                    "clip_name": clip_name or "",
                    "vae_name": vae_name or "",
                },
                "catalogs": catalogs,
                "options": options,
                "counts": {key: len(value) for key, value in catalogs.items()},
                "production_route": {"id": "auto_encoder_auto_vae", "text_encoder": "provider_default", "vae": "provider_default", "intent": "Default Rapid AIO production route using external encoder/VAE unless live catalogs resolve selected values."},
                "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
                "errors": list(dict.fromkeys(errors)),
            }
        )
        return base_payload

    if route_id != WAN22_GGUF_DUAL_NOISE_ROUTE_ID:
        base_payload["warnings"] = ["V-G6 live model discovery is currently specialized for WAN 2.2 GGUF dual-noise Img2Vid and V25.9.19-10y Rapid AIO GGUF production routes."]
        return base_payload

    plan = build_wan22_gguf_loader_plan(
        info,
        fallback_models=fallbacks,
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        clip_name=clip_name,
        vae_name=vae_name,
        high_noise_lora=high_noise_lora,
        low_noise_lora=low_noise_lora,
    )
    adapter = plan.payload()
    all_gguf = _unique(adapter.get("available_models", {}).get("gguf", []))
    clip_values = _unique(adapter.get("available_models", {}).get("clip", []))
    vae_values = _unique(adapter.get("available_models", {}).get("vae", []))
    lora_values = _unique(adapter.get("available_models", {}).get("lora", []))
    mapping = adapter.get("adapter", {}).get("dual_noise_mapping", {})
    selected_models = mapping.get("models", {}) if isinstance(mapping, dict) else {}
    selected_high = str(selected_models.get("high_noise_model") or plan.high_noise_model or "")
    selected_low = str(selected_models.get("low_noise_model") or plan.low_noise_model or "")

    high_values = _candidate_names(mapping, "high_noise") or _fallback_role_filtered(all_gguf, "high_noise")
    low_values = _candidate_names(mapping, "low_noise") or _fallback_role_filtered(all_gguf, "low_noise")
    high_lora_values = _lora_role_filtered(lora_values, "high_noise")
    low_lora_values = _lora_role_filtered(lora_values, "low_noise")

    warnings = list(adapter.get("adapter", {}).get("diagnostics", []) or [])
    errors: list[str] = []
    if not all_gguf:
        errors.append("No GGUF model dropdown values were exposed by ComfyUI /object_info for the detected GGUF loader.")
    if all_gguf and not high_values:
        errors.append("No high-noise WAN GGUF candidates were discoverable from ComfyUI /object_info.")
    if all_gguf and not low_values:
        errors.append("No low-noise WAN GGUF candidates were discoverable from ComfyUI /object_info.")
    if all_gguf and selected_high and not _model_in_catalog(selected_high, all_gguf):
        warnings.append(f"Selected high-noise GGUF is not visible in the live Comfy dropdown: {selected_high}")
    if all_gguf and selected_low and not _model_in_catalog(selected_low, all_gguf):
        warnings.append(f"Selected low-noise GGUF is not visible in the live Comfy dropdown: {selected_low}")

    catalogs = {
        "gguf_models": all_gguf,
        "wan_high_noise_gguf": high_values,
        "wan_low_noise_gguf": low_values,
        "text_encoders": clip_values,
        "vaes": vae_values,
        "loras": lora_values,
        "wan_lightx2v_high_lora": high_lora_values,
        "wan_lightx2v_low_lora": low_lora_values,
    }
    options = {
        "gguf_models": _options(all_gguf, role="gguf", recommended=selected_high),
        "wan_high_noise_gguf": _options(high_values, role="high_noise", recommended=selected_high),
        "wan_low_noise_gguf": _options(low_values, role="low_noise", recommended=selected_low),
        "text_encoders": _options(clip_values, role="clip", recommended=plan.clip_name),
        "vaes": _options(vae_values, role="vae", recommended=plan.vae_name),
        "loras": _options(lora_values, role="lora"),
        "wan_lightx2v_high_lora": _options(high_lora_values, role="high_noise_lora", recommended=plan.high_noise_lora),
        "wan_lightx2v_low_lora": _options(low_lora_values, role="low_noise_lora", recommended=plan.low_noise_lora),
    }

    base_payload.update(
        {
            "catalog_ready": bool(all_gguf),
            "dual_noise_catalog_ready": bool(high_values and low_values),
            "selected_pair_visible": _model_in_catalog(selected_high, all_gguf) and _model_in_catalog(selected_low, all_gguf),
            "field_sources": {
                "gguf_loader": adapter.get("classes", {}).get("gguf_loader", ""),
                "gguf_model_field": adapter.get("fields", {}).get("gguf_model", ""),
                "clip_loader": adapter.get("classes", {}).get("clip_loader", ""),
                "clip_field": adapter.get("fields", {}).get("clip_name", ""),
                "vae_loader": adapter.get("classes", {}).get("vae_loader", ""),
                "vae_field": adapter.get("fields", {}).get("vae_name", ""),
                "lora_loader": adapter.get("classes", {}).get("lora_loader", ""),
                "lora_field": adapter.get("fields", {}).get("lora_name", ""),
            },
            "selected": {
                "high_noise_model": selected_high,
                "low_noise_model": selected_low,
                "clip_name": plan.clip_name,
                "vae_name": plan.vae_name,
                "high_noise_lora": plan.high_noise_lora,
                "low_noise_lora": plan.low_noise_lora,
            },
            "catalogs": catalogs,
            "options": options,
            "counts": {key: len(value) for key, value in catalogs.items()},
            "dual_noise_mapping": mapping,
            "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
            "errors": list(dict.fromkeys(errors)),
        }
    )
    return base_payload
