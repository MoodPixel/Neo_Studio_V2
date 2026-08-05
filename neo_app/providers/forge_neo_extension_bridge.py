from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID = "neo.provider.forge_generic_extension_bridge.v1"
FORGE_GENERIC_EXTENSION_BRIDGE_VERSION = "1.0.0"

# These scripts already have dedicated Neo adapters. E3 must never expose a second,
# generic control path for them because positional script args are provider-owned.
_KNOWN_MAPPED_SCRIPT_TOKENS: dict[str, str] = {
    "controlnet": "image.controlnet",
    "adetailer": "image.adetailer",
    "imagestitch": "image_stitch",
    "pidintegrated": "image.pid_integrated",
    "spectrumintegrated": "image.spectrum",
    "multidiffusionintegrated": "image.multidiffusion",
    "forgecouple": "image.forge_couple",
}

# /script-info does not expose component classes. Labels are therefore the only
# safe signal that an argument is not a primitive browser control. Reject rather
# than guessing whenever a script appears to need file/image/object semantics.
_RISKY_LABEL_TOKENS = (
    "image",
    "mask",
    "upload",
    "file",
    "filepath",
    "file path",
    "folder",
    "directory",
    "canvas",
    "gallery",
    "batch input",
    "input directory",
    "output directory",
    "json",
    "metadata object",
)

_MAX_GENERIC_ARGS = 24


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return text or "script"


def _script_mode(record: dict[str, Any]) -> str:
    return "img2img" if bool(record.get("is_img2img")) else "txt2img"


def _invocation(record: dict[str, Any]) -> str:
    return "alwayson" if bool(record.get("alwayson")) else "selectable"


def _known_mapping(name: str) -> str:
    normalized = _normalized(name)
    for token, owner in _KNOWN_MAPPED_SCRIPT_TOKENS.items():
        if token in normalized:
            return owner
    return ""


def _infer_arg_type(arg: dict[str, Any]) -> tuple[str, str]:
    choices = arg.get("choices") if isinstance(arg.get("choices"), list) else []
    value = arg.get("value")
    if choices:
        if not all(isinstance(item, str) for item in choices):
            return "unsupported", "Choice lists must contain strings only."
        return "select", ""
    if isinstance(value, bool):
        return "boolean", ""
    # bool is an int subclass; keep bool above int.
    if isinstance(value, int):
        return "integer", ""
    if isinstance(value, float):
        return "number", ""
    if isinstance(value, str):
        return "text", ""
    if value is None:
        return "unsupported", "Argument type cannot be inferred from a null default."
    return "unsupported", f"Argument default uses unsupported {type(value).__name__} semantics."


def _normalize_arg(arg: dict[str, Any], index: int) -> tuple[dict[str, Any], list[str]]:
    label = str(arg.get("label") or "").strip()
    arg_type, type_reason = _infer_arg_type(arg)
    blockers: list[str] = []
    lowered = label.casefold()
    if not label:
        blockers.append(f"Argument {index} has no stable label.")
    if any(token in lowered for token in _RISKY_LABEL_TOKENS):
        blockers.append(f"Argument {index} ({label or 'unlabelled'}) appears to require file/image/object semantics.")
    if type_reason:
        blockers.append(f"Argument {index} ({label or 'unlabelled'}): {type_reason}")
    record: dict[str, Any] = {
        "index": int(index),
        "label": label,
        "type": arg_type,
        "default": deepcopy(arg.get("value")),
    }
    for key in ("minimum", "maximum", "step"):
        value = arg.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            record[key] = value
    choices = arg.get("choices")
    if isinstance(choices, list):
        record["choices"] = [str(item) for item in choices if isinstance(item, str)][:200]
    return record, blockers


def _fingerprint_payload(record: dict[str, Any], args: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": str(record.get("name") or "").strip(),
        "mode": _script_mode(record),
        "invocation": _invocation(record),
        "args": [
            {
                key: arg.get(key)
                for key in ("index", "label", "type", "default", "minimum", "maximum", "step", "choices")
                if key in arg
            }
            for arg in args
        ],
    }


def _schema_fingerprint(record: dict[str, Any], args: list[dict[str, Any]]) -> str:
    raw = json.dumps(_fingerprint_payload(record, args), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _external_extension_match(script_name: str, extensions: list[dict[str, Any]]) -> str:
    target = _normalized(script_name)
    if not target:
        return ""
    best = ""
    best_score = 0
    for item in extensions:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        name = str(item.get("name") or "").strip()
        normalized = _normalized(name)
        if not normalized:
            continue
        score = 0
        if normalized in target or target in normalized:
            score = min(len(normalized), len(target)) + 20
        else:
            script_tokens = {token for token in re.split(r"[^a-z0-9]+", str(script_name).casefold()) if len(token) >= 4}
            ext_tokens = {token for token in re.split(r"[^a-z0-9]+", name.casefold()) if len(token) >= 4}
            score = len(script_tokens & ext_tokens) * 5
        if score > best_score:
            best = name
            best_score = score
    return best if best_score >= 5 else ""


def _classify_script(record: dict[str, Any], extensions: list[dict[str, Any]]) -> dict[str, Any]:
    name = str(record.get("name") or "").strip()
    mode = _script_mode(record)
    invocation = _invocation(record)
    known_owner = _known_mapping(name)
    raw_args = [item for item in _as_list(record.get("args")) if isinstance(item, dict)]
    normalized_args: list[dict[str, Any]] = []
    blockers: list[str] = []
    for index, arg in enumerate(raw_args):
        normalized_arg, arg_blockers = _normalize_arg(arg, int(arg.get("index") if isinstance(arg.get("index"), int) else index))
        normalized_args.append(normalized_arg)
        blockers.extend(arg_blockers)
    if int(record.get("argument_count") or len(raw_args)) != len(raw_args):
        blockers.append("Forge script argument metadata is incomplete.")
    if len(raw_args) > _MAX_GENERIC_ARGS:
        blockers.append(f"Script exposes {len(raw_args)} arguments; generic bridge limit is {_MAX_GENERIC_ARGS}.")
    fingerprint = _schema_fingerprint(record, normalized_args)
    external_name = _external_extension_match(name, extensions)
    source = "external_extension" if external_name else "built_in_or_unknown"
    if known_owner:
        status = "neo_mapped"
        reason = f"Dedicated Neo adapter owns this Forge script ({known_owner})."
    elif not external_name:
        status = "adapter_required"
        reason = "Forge built-in or unattributed script requires an explicit Neo adapter; E3 generic execution is limited to scripts attributable to enabled external Forge extensions."
    elif blockers:
        status = "adapter_required"
        reason = blockers[0]
    else:
        status = "generic_bridge_ready"
        reason = "Enabled external Forge extension exposes only primitive API arguments and can use the generic bridge."
    key = f"{_slug(name)}:{mode}:{invocation}:{fingerprint[:8]}"
    return {
        "script_key": key,
        "name": name,
        "mode": mode,
        "invocation": invocation,
        "alwayson": invocation == "alwayson",
        "argument_count": len(normalized_args),
        "args": normalized_args,
        "schema_fingerprint": fingerprint,
        "status": status,
        "bridgeable": status == "generic_bridge_ready",
        "known_neo_owner": known_owner,
        "source": source,
        "source_extension": external_name,
        "reason": reason,
        "blockers": blockers,
        "route_policy": "sd15_sdxl_only" if status == "generic_bridge_ready" else "not_executable",
        "physical_validation": "required" if status == "generic_bridge_ready" else "not_applicable",
    }


def build_forge_generic_extension_bridge(
    *,
    extensions: list[dict[str, Any]] | None,
    scripts: dict[str, Any] | None,
    script_info: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a path-safe provider-owned Forge extension/script catalog.

    Discovery is generic. Execution remains conservative: only primitive
    /script-info schemas are bridgeable, and E3 limits generic execution to
    SD1.5/SDXL routes because Forge does not publish architecture compatibility
    metadata for third-party scripts.
    """
    extension_records = [
        {
            "name": str(item.get("name") or "").strip(),
            "branch": str(item.get("branch") or "")[:120],
            "commit_hash": str(item.get("commit_hash") or "")[:12],
            "version": str(item.get("version") or "")[:120],
            "enabled": bool(item.get("enabled", False)),
        }
        for item in (extensions or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    info_records = [item for item in (script_info or []) if isinstance(item, dict) and str(item.get("name") or "").strip()]
    classified = [_classify_script(item, extension_records) for item in info_records]
    classified.sort(key=lambda item: (item.get("status") != "generic_bridge_ready", item.get("name", "").casefold(), item.get("mode", ""), item.get("invocation", "")))

    info_names = {(str(item.get("name") or "").strip(), _script_mode(item)) for item in info_records}
    unresolved: list[dict[str, Any]] = []
    for mode in ("txt2img", "img2img"):
        for name in _as_list(_as_dict(scripts).get(mode)):
            title = str(name or "").strip()
            if not title or (title, mode) in info_names:
                continue
            unresolved.append({
                "name": title,
                "mode": mode,
                "status": "adapter_required",
                "bridgeable": False,
                "reason": "Forge lists this script but does not publish an API argument schema for it.",
                "source": "built_in_or_unknown",
            })

    bridgeable = [item for item in classified if item.get("bridgeable")]
    adapter_required = [item for item in classified if item.get("status") == "adapter_required"] + unresolved
    neo_mapped = [item for item in classified if item.get("status") == "neo_mapped"]
    external_enabled = [item for item in extension_records if item.get("enabled") is not False]
    return {
        "schema_id": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID,
        "version": FORGE_GENERIC_EXTENSION_BRIDGE_VERSION,
        "provider_id": "forge",
        "execution_policy": "external_primitive_scripts_sd15_sdxl_only",
        "scripts": classified,
        "unresolved_scripts": unresolved,
        "extensions": extension_records,
        "summary": {
            "installed_extensions": len(extension_records),
            "enabled_extensions": len(external_enabled),
            "script_info_records": len(classified),
            "generic_bridge_ready": len(bridgeable),
            "adapter_required": len(adapter_required),
            "neo_mapped": len(neo_mapped),
        },
        "capability": {
            "available": bool(bridgeable),
            "mode": "generic_script_bridge",
            "contract": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID,
            "bridgeable_count": len(bridgeable),
            "adapter_required_count": len(adapter_required),
            "reason": (
                f"{len(bridgeable)} Forge script schema(s) are safe for the generic primitive bridge."
                if bridgeable
                else "No Forge scripts with a safe primitive /script-info schema were discovered."
            ),
            "supported_families": ["sd15", "sdxl"],
            "supported_modes": ["txt2img", "img2img", "inpaint", "outpaint"],
        },
    }


def _mode_for_script(mode: str) -> str:
    return "img2img" if str(mode) in {"img2img", "inpaint", "outpaint", "edit"} else "txt2img"


def _bridge_block_scripts(block: Any) -> list[dict[str, Any]]:
    source = _as_dict(block)
    params = _as_dict(source.get("params"))
    scripts = params.get("scripts") if isinstance(params.get("scripts"), list) else source.get("scripts")
    return [item for item in _as_list(scripts) if isinstance(item, dict) and item.get("enabled") is not False]


def _coerce_arg_value(value: Any, spec: dict[str, Any]) -> tuple[Any, str]:
    arg_type = str(spec.get("type") or "unsupported")
    label = str(spec.get("label") or f"arg {spec.get('index')}")
    if value is None:
        value = deepcopy(spec.get("default"))
    try:
        if arg_type == "boolean":
            if isinstance(value, bool):
                normalized = value
            elif str(value).strip().casefold() in {"true", "1", "yes", "on"}:
                normalized = True
            elif str(value).strip().casefold() in {"false", "0", "no", "off"}:
                normalized = False
            else:
                return None, f"{label}: expected boolean."
        elif arg_type == "integer":
            normalized = int(value)
        elif arg_type == "number":
            normalized = float(value)
        elif arg_type in {"text", "select"}:
            normalized = str(value if value is not None else "")
        else:
            return None, f"{label}: unsupported generic argument type {arg_type}."
    except (TypeError, ValueError):
        return None, f"{label}: value does not match {arg_type}."

    choices = [str(item) for item in _as_list(spec.get("choices"))]
    if arg_type == "select" and choices and normalized not in choices:
        return None, f"{label}: value is not one of the current Forge choices."
    if arg_type in {"integer", "number"}:
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        if isinstance(minimum, (int, float)) and normalized < minimum:
            return None, f"{label}: value is below Forge minimum {minimum}."
        if isinstance(maximum, (int, float)) and normalized > maximum:
            return None, f"{label}: value is above Forge maximum {maximum}."
    return normalized, ""


def compile_forge_generic_extension_bridge(
    block: Any,
    *,
    snapshot: dict[str, Any] | None,
    mode: str,
    family: str,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    source = _as_dict(block)
    if not source or source.get("enabled") is False:
        return {}, {"enabled": False, "schema_id": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID}, []
    if family not in {"sd15", "sdxl"}:
        return {}, {"enabled": False, "schema_id": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID}, [
            f"Forge generic script bridge is conservatively limited to SD 1.5/SDXL in E3; {family or 'unknown family'} requires a dedicated compatibility adapter."
        ]
    catalog = _as_dict(_as_dict(snapshot).get("generic_extension_bridge"))
    records = {str(item.get("script_key") or ""): item for item in _as_list(catalog.get("scripts")) if isinstance(item, dict)}
    selected = _bridge_block_scripts(source)
    if not selected:
        return {}, {"enabled": False, "schema_id": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID}, ["Forge Script Bridge has no enabled scripts."]
    target_mode = _mode_for_script(mode)
    update: dict[str, Any] = {}
    alwayson: dict[str, Any] = {}
    selectable: list[tuple[str, list[Any], str]] = []
    applied: list[dict[str, Any]] = []
    errors: list[str] = []

    for selected_item in selected:
        key = str(selected_item.get("script_key") or "").strip()
        record = _as_dict(records.get(key))
        if not record:
            errors.append(f"Forge Script Bridge selection is stale or unavailable: {key or '(missing key)' }.")
            continue
        if record.get("status") != "generic_bridge_ready":
            errors.append(f"Forge script {record.get('name') or key} requires a dedicated Neo adapter.")
            continue
        if str(record.get("mode")) != target_mode:
            errors.append(f"Forge script {record.get('name') or key} is not published for {target_mode}.")
            continue
        submitted_fingerprint = str(selected_item.get("schema_fingerprint") or "")
        current_fingerprint = str(record.get("schema_fingerprint") or "")
        if not submitted_fingerprint or submitted_fingerprint != current_fingerprint:
            errors.append(f"Forge script {record.get('name') or key} API schema changed; refresh the Forge profile before using it.")
            continue
        raw_values = selected_item.get("args")
        if isinstance(raw_values, dict):
            raw_map = {str(k): v for k, v in raw_values.items()}
        elif isinstance(raw_values, list):
            raw_map = {str(index): value for index, value in enumerate(raw_values)}
        else:
            raw_map = {}
        args: list[Any] = []
        script_errors: list[str] = []
        for spec in _as_list(record.get("args")):
            if not isinstance(spec, dict):
                continue
            index = int(spec.get("index") or 0)
            value = raw_map.get(str(index), deepcopy(spec.get("default")))
            coerced, error = _coerce_arg_value(value, spec)
            if error:
                script_errors.append(error)
            args.append(coerced)
        if script_errors:
            errors.extend(f"{record.get('name')}: {message}" for message in script_errors)
            continue
        name = str(record.get("name") or "").strip()
        if record.get("invocation") == "alwayson":
            alwayson[name] = {"args": args}
        else:
            selectable.append((name, args, key))
        applied.append({
            "script_key": key,
            "name": name,
            "mode": target_mode,
            "invocation": record.get("invocation"),
            "schema_fingerprint": current_fingerprint,
            "argument_count": len(args),
        })

    if len(selectable) > 1:
        errors.append("Forge supports one selectable script per generation request; enable only one generic selectable script.")
    if selectable and _as_dict(payload).get("script_name"):
        errors.append("A Forge selectable script is already assigned by the active workflow; generic selectable script cannot be stacked.")
    if errors:
        return {}, {
            "enabled": False,
            "schema_id": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID,
            "applied": applied,
        }, errors
    if alwayson:
        update["alwayson_scripts"] = alwayson
    if selectable:
        name, args, _key = selectable[0]
        update["script_name"] = name
        update["script_args"] = args
    return update, {
        "enabled": bool(applied),
        "schema_id": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID,
        "version": FORGE_GENERIC_EXTENSION_BRIDGE_VERSION,
        "applied": applied,
    }, []


def validate_forge_generic_extension_bridge(
    block: Any,
    *,
    snapshot: dict[str, Any] | None,
    mode: str,
    family: str,
) -> list[str]:
    source = _as_dict(block)
    if not source or source.get("enabled") is False:
        return []
    # Reuse compilation without a payload: it performs schema, mode, family and
    # primitive-value validation but does not mutate the generation request.
    _update, _meta, errors = compile_forge_generic_extension_bridge(
        source,
        snapshot=snapshot,
        mode=mode,
        family=family,
        payload={},
    )
    return errors


def forge_generic_extension_bridge_contract_payload() -> dict[str, Any]:
    return {
        "schema_id": FORGE_GENERIC_EXTENSION_BRIDGE_SCHEMA_ID,
        "version": FORGE_GENERIC_EXTENSION_BRIDGE_VERSION,
        "discovery": "forge_extensions_plus_script_info",
        "execution": "enabled_external_extension_primitive_script_args_only",
        "supported_families": ["sd15", "sdxl"],
        "mode_mapping": {"txt2img": "txt2img", "img2img": "img2img", "inpaint": "img2img", "outpaint": "img2img"},
        "schema_fingerprint_required": True,
        "complex_scripts": "adapter_required",
        "provider_owns_installation": True,
    }
