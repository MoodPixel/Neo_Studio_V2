"""Provider-aware preview-action capability evaluation.

This module evaluates the canonical preview-action definitions against exactly
one selected Image backend profile. It does not execute actions and it never
searches for a different provider to make an action available.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List

from neo_app.extensions.registry import resolve_extension_manifest_route_state
from neo_app.image.preview_actions import (
    ACTION_GROUPS,
    ALLOWED_ROUTE_STATES,
    get_preview_action_registry,
)

PREVIEW_ACTION_EVALUATION_SCHEMA_ID = "neo.image.preview_action_provider_evaluation.v1"
PREVIEW_ACTION_EVALUATION_SCHEMA_VERSION = 1

COMFY_PROVIDER_IDS = {"comfyui", "comfyui_portable"}
CLOUD_PROVIDER_IDS = {"xai_grok"}
CONNECTED_STATES = {"connected", "connected_with_warnings", "online", "ready", "available"}

# Phase 2 intentionally separates capability truth from execution readiness.
# Later phases turn the planned Forge dispatches on without changing the action
# inventory or allowing a Comfy profile to satisfy Forge requirements.
_PROVIDER_ROUTES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "comfy": {
        "core.img2img": {"execution_mode": "comfy_img2img", "required_capability": "img2img", "dispatch_ready": True},
        "core.inpaint": {"execution_mode": "comfy_inpaint", "required_capability": "inpaint", "dispatch_ready": True},
        "core.outpaint": {"execution_mode": "comfy_outpaint", "required_capability": "outpaint", "dispatch_ready": True},
        "extension.controlnet": {"execution_mode": "comfy_controlnet", "required_capability": "controlnet", "dispatch_ready": True},
        "extension.ip_adapter": {"execution_mode": "comfy_ip_adapter", "required_capability": "ip_adapter", "dispatch_ready": True},
        "extension.layerdiffuse.source": {"execution_mode": "comfy_layerdiffuse", "required_capability": "layerdiffuse_inline", "dispatch_ready": True},
        "extension.layerdiffuse.background": {"execution_mode": "comfy_layerdiffuse", "required_capability": "layerdiffuse_inline", "dispatch_ready": True},
        "extension.layerdiffuse.foreground": {"execution_mode": "comfy_layerdiffuse", "required_capability": "layerdiffuse_inline", "dispatch_ready": True},
        "extension.layerdiffuse.replace_target": {"execution_mode": "comfy_layerdiffuse", "required_capability": "layerdiffuse_inline", "dispatch_ready": True},
        "extension.high_res_lab": {"execution_mode": "comfy_high_res_finish", "required_capability": "highres_inline", "dispatch_ready": True, "runtime_required": True},
        "extension.adetailer": {"execution_mode": "comfy_adetailer_finish", "required_capability": "adetailer_inline", "dispatch_ready": True, "runtime_required": True},
        "extension.identity_rescue": {"execution_mode": "comfy_faceid_finish", "required_capability": "face_id", "dispatch_ready": True, "runtime_required": True},
        "extension.image_upscale": {"execution_mode": "comfy_image_upscale", "required_capability": "image_upscale", "dispatch_ready": True, "runtime_required": True},
    },
    "forge": {
        "core.img2img": {"execution_mode": "forge_img2img", "required_capability": "img2img", "dispatch_ready": True},
        "core.inpaint": {"execution_mode": "forge_inpaint", "required_capability": "inpaint", "dispatch_ready": True},
        "core.outpaint": {"execution_mode": "forge_outpaint", "required_capability": "outpaint", "dispatch_ready": True},
        "extension.controlnet": {"execution_mode": "forge_integrated_controlnet", "required_capability": "controlnet", "dispatch_ready": True},
        "extension.ip_adapter": {"execution_mode": "forge_integrated_ip_adapter", "required_capability": "ip_adapter", "dispatch_ready": True},
        "extension.layerdiffuse.source": {"execution_mode": "none", "required_capability": "layerdiffuse_inline", "dispatch_ready": False, "provider_supported": False},
        "extension.layerdiffuse.background": {"execution_mode": "none", "required_capability": "layerdiffuse_inline", "dispatch_ready": False, "provider_supported": False},
        "extension.layerdiffuse.foreground": {"execution_mode": "none", "required_capability": "layerdiffuse_inline", "dispatch_ready": False, "provider_supported": False},
        "extension.layerdiffuse.replace_target": {"execution_mode": "none", "required_capability": "layerdiffuse_inline", "dispatch_ready": False, "provider_supported": False},
        "extension.high_res_lab": {"execution_mode": "forge_native_txt2img_upscale", "required_capability": "highres_inline", "required_bridge_capability": "native_post_hires", "requires_bridge": True, "dispatch_ready": True, "implementation_phase": 6, "runtime_required": True},
        "extension.adetailer": {"execution_mode": "forge_adetailer_finish", "required_capability": "adetailer_inline", "dispatch_ready": True, "implementation_phase": 7, "runtime_required": True},
        "extension.identity_rescue": {"execution_mode": "forge_faceid_finish", "required_capability": "face_id", "dispatch_ready": True, "implementation_phase": 7, "runtime_required": True},
        "extension.image_upscale": {"execution_mode": "forge_extra_single_image", "required_capability": "image_upscale", "dispatch_ready": True, "implementation_phase": 10, "runtime_required": True},
    },
    "cloud": {
        "core.img2img": {"execution_mode": "provider_img2img", "required_capability": "img2img", "dispatch_ready": True},
        "core.inpaint": {"execution_mode": "provider_inpaint", "required_capability": "inpaint", "dispatch_ready": True},
        "core.outpaint": {"execution_mode": "provider_outpaint", "required_capability": "outpaint", "dispatch_ready": True},
    },
    "generic": {
        "core.img2img": {"execution_mode": "provider_img2img", "required_capability": "img2img", "dispatch_ready": True},
        "core.inpaint": {"execution_mode": "provider_inpaint", "required_capability": "inpaint", "dispatch_ready": True},
        "core.outpaint": {"execution_mode": "provider_outpaint", "required_capability": "outpaint", "dispatch_ready": True},
    },
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _provider_class(provider_id: str) -> str:
    value = str(provider_id or "").strip().casefold()
    if value in COMFY_PROVIDER_IDS:
        return "comfy"
    if value == "forge":
        return "forge"
    if value in CLOUD_PROVIDER_IDS:
        return "cloud"
    return "generic"


def _capabilities(profile: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **_as_dict(profile.get("capability_flags")),
        **_as_dict(profile.get("capabilities")),
        **_as_dict(profile.get("feature_capabilities")),
        **_as_dict(overlay.get("capabilities")),
    }


def _profile_connected(profile: Dict[str, Any], overlay: Dict[str, Any]) -> bool:
    if overlay.get("connected") is not None:
        return bool(overlay.get("connected"))
    runtime = _as_dict(profile.get("runtime"))
    status = str(runtime.get("status") or profile.get("runtime_status") or "").casefold()
    return bool(runtime.get("reachable")) or status in CONNECTED_STATES


def _extension_map(extension_payload: Dict[str, Any] | Iterable[Dict[str, Any]] | None) -> Dict[str, Dict[str, Any]]:
    if isinstance(extension_payload, dict):
        records = _as_list(extension_payload.get("extensions"))
    else:
        records = list(extension_payload or [])
    output: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        manifest = _as_dict(record.get("manifest"))
        extension_id = str(manifest.get("id") or record.get("id") or record.get("extension_id") or "").strip()
        if extension_id:
            output[extension_id] = record
    return output


def _extension_enabled(record: Dict[str, Any]) -> bool:
    if not record:
        return False
    if record.get("registry_enabled") is not None:
        return bool(record.get("registry_enabled"))
    return bool(record.get("enabled")) and str(record.get("status") or "enabled") not in {"disabled", "removed", "parent_missing", "missing_requirements"}


def _extension_route_state(
    record: Dict[str, Any],
    *,
    provider_id: str,
    family: str,
    loader: str,
    workflow_mode: str,
    workspace_app: str,
) -> str:
    manifest = _as_dict(record.get("manifest"))
    supported_backends = {str(item).casefold() for item in _as_list(manifest.get("supported_backends"))}
    provider = str(provider_id or "").casefold()
    if supported_backends and provider not in supported_backends:
        return "provider_unsupported"
    resolved = resolve_extension_manifest_route_state(
        _as_dict(manifest.get("route_states")),
        backend=provider,
        family=family or None,
        loader=loader or None,
        workflow_mode=workflow_mode or None,
        workspace_app=workspace_app or None,
    )
    return str(resolved or "available")


def _overlay_extension_policy(overlay: Dict[str, Any], extension_id: str) -> Dict[str, Any]:
    return _as_dict(_as_dict(overlay.get("extension_policy")).get(extension_id))


def _bridge_capability(profile: Dict[str, Any], capability: str) -> bool:
    runtime = _as_dict(profile.get("runtime"))
    snapshot = _as_dict(runtime.get("forge_admin"))
    bridge = _as_dict(snapshot.get("bridge"))
    if not bool(bridge.get("selected")):
        return False
    caps = _as_dict(bridge.get("capabilities"))
    native_operations = {str(item) for item in _as_list(caps.get("native_operations"))}
    aliases = {"native_post_hires": "native_txt2img_upscale"}
    required_operation = aliases.get(capability)
    if required_operation:
        # Native selected-output Hires is executable only when the Bridge
        # advertises the high-level flag, exact operation, and the size-enforced
        # contract added in Bridge 1.2.1. Older bridges fail closed instead of
        # silently returning a same-resolution refinement.
        return (
            bool(caps.get(capability))
            and required_operation in native_operations
            and bool(caps.get("native_post_hires_size_contract"))
        )
    return bool(caps.get(capability) or capability in native_operations)


def _capability_available(
    capability: str,
    *,
    profile_caps: Dict[str, Any],
    extension_policy: Dict[str, Any],
    extension_record: Dict[str, Any],
) -> bool:
    if not capability:
        return True
    if capability == "face_id":
        policy_text = str(extension_policy).casefold()
        return bool(
            profile_caps.get("face_id")
            or profile_caps.get("faceid")
            or extension_policy.get("face_id_available")
            or extension_policy.get("faceid_available")
            or (extension_policy.get("allowed") and bool(extension_policy.get("faceid_available")) and ("faceid" in policy_text or "face_id" in policy_text))
        )
    if extension_policy:
        required = str(extension_policy.get("required_capability") or "")
        if required == capability and extension_policy.get("allowed") is not None:
            return bool(extension_policy.get("allowed"))
    return bool(profile_caps.get(capability))


def _provider_dispatch_for_action(action: Dict[str, Any], provider_id: str) -> str:
    dispatch_map = _as_dict(action.get("provider_dispatch"))
    provider = str(provider_id or "").strip().casefold()
    return str(dispatch_map.get(provider) or dispatch_map.get("*") or "unavailable")


def _route_for_action(provider_class: str, action_id: str) -> Dict[str, Any]:
    route = deepcopy(_PROVIDER_ROUTES.get(provider_class, {}).get(action_id) or {})
    if route:
        route.setdefault("provider_supported", True)
        route.setdefault("requires_bridge", False)
        route.setdefault("runtime_required", False)
        route.setdefault("implementation_phase", None)
        return route
    if provider_class == "cloud" and action_id.startswith("extension."):
        return {
            "execution_mode": "local_finish_profile_required",
            "required_capability": "",
            "provider_supported": False,
            "dispatch_ready": False,
            "requires_bridge": False,
            "runtime_required": False,
            "implementation_phase": 5,
        }
    return {
        "execution_mode": "none",
        "required_capability": "",
        "provider_supported": False,
        "dispatch_ready": False,
        "requires_bridge": False,
        "runtime_required": False,
        "implementation_phase": None,
    }


_GUIDED_ROUTE_LABELS = {
    "core.img2img": "Stage as Img2Img source",
    "core.inpaint": "Open Inpaint with this image",
    "core.outpaint": "Open Outpaint with this image",
    "extension.controlnet": "Use as ControlNet reference",
    "extension.ip_adapter": "Use as IP Adapter reference",
    "extension.layerdiffuse.source": "Use as LayerDiffuse source",
    "extension.layerdiffuse.background": "Use as LayerDiffuse background",
    "extension.layerdiffuse.foreground": "Use as LayerDiffuse foreground",
    "extension.layerdiffuse.replace_target": "Use as LayerDiffuse target",
    "extension.high_res_lab": "Diffusion High-Res Fix",
    "extension.adetailer": "Automatic face/detail repair",
    "extension.identity_rescue": "Identity-guided repair",
    "extension.image_upscale": "Pixel upscale",
}

_GUIDED_ROUTE_BADGES = {
    "core.img2img": "Source",
    "core.inpaint": "Source",
    "core.outpaint": "Source",
    "extension.controlnet": "Reference",
    "extension.ip_adapter": "Reference",
    "extension.layerdiffuse.source": "Layer slot",
    "extension.layerdiffuse.background": "Layer slot",
    "extension.layerdiffuse.foreground": "Layer slot",
    "extension.layerdiffuse.replace_target": "Layer slot",
    "extension.high_res_lab": "Diffusion",
    "extension.adetailer": "Repair",
    "extension.identity_rescue": "Identity",
    "extension.image_upscale": "Pixel",
}

_EXPERT_EXECUTION_LABELS = {
    "comfy_img2img": "ComfyUI Img2Img source staging",
    "comfy_inpaint": "ComfyUI Inpaint source staging",
    "comfy_outpaint": "ComfyUI Outpaint source staging",
    "forge_img2img": "Forge Img2Img source staging",
    "forge_inpaint": "Forge Inpaint source staging",
    "forge_outpaint": "Forge Outpaint source staging",
    "provider_img2img": "Selected-provider Img2Img source staging",
    "provider_inpaint": "Selected-provider Inpaint source staging",
    "provider_outpaint": "Selected-provider Outpaint source staging",
    "comfy_controlnet": "ComfyUI ControlNet workflow staging",
    "forge_integrated_controlnet": "Forge Integrated ControlNet staging",
    "comfy_ip_adapter": "ComfyUI IP Adapter workflow staging",
    "forge_integrated_ip_adapter": "Forge Integrated ControlNet IP Adapter staging",
    "comfy_layerdiffuse": "ComfyUI LayerDiffuse workflow slot staging",
    "comfy_high_res_finish": "ComfyUI High-Res Lab diffusion workflow",
    "forge_native_txt2img_upscale": "Forge native txt2img_upscale via Neo Forge Bridge",
    "comfy_adetailer_finish": "ComfyUI ADetailer workflow",
    "forge_adetailer_finish": "Forge Img2Img plus ADetailer always-on script",
    "comfy_faceid_finish": "ComfyUI FaceID workflow",
    "forge_faceid_finish": "Forge Img2Img plus Integrated ControlNet FaceID",
    "comfy_image_upscale": "ComfyUI model-upscale workflow",
    "forge_extra_single_image": "Forge Extras /sdapi/v1/extra-single-image",
    "local_finish_profile_required": "Explicit local finishing profile selection",
    "none": "No provider route",
}


def _profile_display_name(profile: Dict[str, Any]) -> str:
    return str(profile.get("display_name") or profile.get("profile_id") or "Selected Image profile")


def _provider_display_name(profile: Dict[str, Any], provider_id: str) -> str:
    return str(profile.get("provider_label") or profile.get("provider_display_name") or provider_id or "Image provider")


def _guided_disabled_reason(
    *,
    action_id: str,
    action_label: str,
    profile_label: str,
    provider_class: str,
    disabled_reason_code: str,
    extension_id: str,
) -> str:
    if disabled_reason_code == "profile_missing":
        return "Select an Image backend profile first."
    if disabled_reason_code == "profile_disabled":
        return f"Enable {profile_label} in Admin before using this action."
    if disabled_reason_code == "provider_unsupported":
        if provider_class == "cloud":
            return "Choose a local finishing profile explicitly; Neo will not switch providers automatically."
        return f"{action_label} is not available on {profile_label}."
    if disabled_reason_code == "extension_missing":
        return f"Install or restore {extension_id or action_label} before using this action."
    if disabled_reason_code == "extension_disabled":
        return f"Enable {extension_id or action_label} in Admin."
    if disabled_reason_code == "route_unavailable":
        return f"{action_label} is not mapped for the current model family, loader, or workflow mode."
    if disabled_reason_code == "bridge_missing":
        return "Update, enable, and restart Neo Forge Bridge to use Forge native High-Res Fix."
    if disabled_reason_code == "runtime_offline":
        return f"Connect or test {profile_label} before running this action."
    if disabled_reason_code == "dispatch_unavailable":
        return f"The provider-owned {action_label} executor is not available."
    if disabled_reason_code == "capability_missing":
        specific = {
            "extension.high_res_lab": "The selected profile did not report a usable High-Res Fix route.",
            "extension.adetailer": "The selected profile did not report a usable ADetailer script and detector.",
            "extension.identity_rescue": "The selected profile did not report a compatible FaceID model and preprocessor.",
            "extension.image_upscale": "The selected profile did not report an available image upscaler.",
            "extension.controlnet": "The selected profile did not report ControlNet support.",
            "extension.ip_adapter": "The selected profile did not report IP Adapter support.",
        }
        return specific.get(action_id, f"{profile_label} is missing a required capability for {action_label}.")
    return f"{action_label} is unavailable for the selected Image profile."


def _requirement_checks(
    *,
    profile_id: str,
    profile_enabled: bool,
    provider_supported: bool,
    extension_id: str,
    extension_record: Dict[str, Any],
    extension_enabled: bool,
    route_available: bool,
    capability: str,
    capability_available: bool,
    bridge_capability: str,
    bridge_available: bool,
    dispatch_ready: bool,
    runtime_required: bool,
    runtime_ready: bool,
) -> List[Dict[str, Any]]:
    def row(check_id: str, label: str, required: bool, ready: bool, detail: str = "") -> Dict[str, Any]:
        state = "not_required" if not required else ("ready" if ready else "blocked")
        return {"check_id": check_id, "label": label, "required": required, "ready": ready if required else True, "state": state, "detail": detail}

    return [
        row("profile_selected", "Image profile selected", True, bool(profile_id)),
        row("profile_enabled", "Profile enabled", True, profile_enabled),
        row("provider_supported", "Provider route supported", True, provider_supported),
        row("extension_registered", "Extension registered", bool(extension_id), bool(extension_record), extension_id),
        row("extension_enabled", "Extension enabled", bool(extension_id), extension_enabled, extension_id),
        row("route_available", "Family/loader/mode route available", bool(extension_id), route_available),
        row("capability_available", "Required capability available", bool(capability), capability_available, capability),
        row("bridge_available", "Neo Forge Bridge capability available", bool(bridge_capability), bridge_available, bridge_capability),
        row("dispatch_ready", "Provider executor ready", True, dispatch_ready),
        row("runtime_ready", "Backend connected", runtime_required, runtime_ready),
    ]


def evaluate_preview_action_for_profile(
    action: Dict[str, Any],
    *,
    profile: Dict[str, Any],
    overlay: Dict[str, Any] | None = None,
    extension_payload: Dict[str, Any] | Iterable[Dict[str, Any]] | None = None,
    family: str = "",
    loader: str = "",
    workflow_mode: str = "generate",
    expert_mode: bool = False,
) -> Dict[str, Any]:
    overlay = _as_dict(overlay)
    provider_id = str(profile.get("provider_id") or "").strip().casefold()
    profile_id = str(profile.get("profile_id") or "").strip()
    provider_class = _provider_class(provider_id)
    route = _route_for_action(provider_class, str(action.get("id") or ""))
    dispatch_type = _provider_dispatch_for_action(action, provider_id)
    extension_id = str(action.get("requires_extension") or "")
    extension_record = _extension_map(extension_payload).get(extension_id, {}) if extension_id else {}
    extension_enabled = True if not extension_id else _extension_enabled(extension_record)
    extension_policy = _overlay_extension_policy(overlay, extension_id) if extension_id else {}
    route_state = "core"
    if extension_id:
        route_state = _extension_route_state(
            extension_record,
            provider_id=provider_id,
            family=family,
            loader=loader,
            workflow_mode=workflow_mode,
            workspace_app=str(action.get("target_workspace") or ""),
        ) if extension_record else "extension_missing"
        if extension_policy.get("allowed") is False:
            route_state = "provider_gated"
    profile_caps = _capabilities(profile, overlay)
    capability = str(route.get("required_capability") or action.get("requires_capability") or "")
    capability_available = _capability_available(
        capability,
        profile_caps=profile_caps,
        extension_policy=extension_policy,
        extension_record=extension_record,
    )
    if provider_class == "comfy" and capability in {"image_upscale", "layerdiffuse_inline"} and extension_record:
        capability_available = True
    profile_enabled = profile.get("enabled") is not False
    connected = _profile_connected(profile, overlay)
    bridge_capability = str(route.get("required_bridge_capability") or "")
    bridge_available = True if not bridge_capability else _bridge_capability(profile, bridge_capability)
    route_available = route_state in ALLOWED_ROUTE_STATES or route_state == "core"
    provider_supported = bool(route.get("provider_supported", True))
    dispatch_ready = bool(route.get("dispatch_ready"))
    runtime_ready = connected or not bool(route.get("runtime_required"))

    action_id = str(action.get("id") or "")
    action_label = str(action.get("label") or action_id or "Action")
    profile_label = _profile_display_name(profile)
    provider_label = _provider_display_name(profile, provider_id)
    execution_mode = str(route.get("execution_mode") or "none")
    disabled_reason = ""
    disabled_reason_code = ""
    if not profile_id:
        disabled_reason_code = "profile_missing"
        disabled_reason = "No Image backend profile is selected."
    elif not profile_enabled:
        disabled_reason_code = "profile_disabled"
        disabled_reason = f"Backend profile {profile_id} is disabled."
    elif not provider_supported:
        disabled_reason_code = "provider_unsupported"
        if provider_class == "cloud" and action_id.startswith("extension."):
            disabled_reason = "This action requires an explicitly selected local finishing profile; automatic cloud-to-Comfy bridging is prohibited."
        else:
            disabled_reason = f"{action_label} is not supported by provider {provider_id or 'unknown'}."
    elif extension_id and not extension_record:
        disabled_reason_code = "extension_missing"
        disabled_reason = f"{extension_id} is not registered."
    elif extension_id and not extension_enabled:
        disabled_reason_code = "extension_disabled"
        disabled_reason = f"{extension_id} is disabled in Admin."
    elif extension_id and not route_available:
        disabled_reason_code = "route_unavailable"
        disabled_reason = str(extension_policy.get("reason") or f"{extension_id} route is {route_state} for the selected {provider_id} profile.")
    elif not capability_available:
        disabled_reason_code = "capability_missing"
        disabled_reason = str(extension_policy.get("reason") or f"The selected {provider_id} profile does not expose required capability: {capability}.")
    elif not dispatch_ready:
        disabled_reason_code = "dispatch_unavailable"
        phase = route.get("implementation_phase")
        disabled_reason = f"Provider-owned {execution_mode or 'action'} dispatch is reserved for Phase {phase}." if phase else "Provider-owned dispatch is not implemented yet."
    elif bridge_capability and not bridge_available:
        disabled_reason_code = "bridge_missing"
        if bridge_capability == "native_post_hires":
            disabled_reason = (
                "The selected Forge profile requires Neo Forge Bridge 1.2.1+ with "
                "native_post_hires, native_txt2img_upscale, and native_post_hires_size_contract."
            )
        else:
            disabled_reason = f"The selected Forge profile does not expose Neo Forge Bridge capability: {bridge_capability}."
    elif not runtime_ready:
        disabled_reason_code = "runtime_offline"
        disabled_reason = f"The selected {provider_id} profile must be connected before this finish action can run."

    guided_disabled_reason = _guided_disabled_reason(
        action_id=action_id,
        action_label=action_label,
        profile_label=profile_label,
        provider_class=provider_class,
        disabled_reason_code=disabled_reason_code,
        extension_id=extension_id,
    ) if disabled_reason_code else ""
    requirement_checks = _requirement_checks(
        profile_id=profile_id,
        profile_enabled=profile_enabled,
        provider_supported=provider_supported,
        extension_id=extension_id,
        extension_record=extension_record,
        extension_enabled=extension_enabled,
        route_available=route_available,
        capability=capability,
        capability_available=capability_available,
        bridge_capability=bridge_capability,
        bridge_available=bridge_available,
        dispatch_ready=dispatch_ready,
        runtime_required=bool(route.get("runtime_required")),
        runtime_ready=runtime_ready,
    )

    provider_enabled = bool(
        profile_id
        and profile_enabled
        and provider_supported
        and extension_enabled
        and route_available
        and capability_available
        and bridge_available
        and dispatch_ready
        and runtime_ready
    )
    visible = provider_enabled or bool(expert_mode)
    return {
        **deepcopy(action),
        "provider_id": provider_id,
        "profile_id": profile_id,
        "provider_class": provider_class,
        "dispatch_type": dispatch_type,
        "dispatch_owner": provider_id or provider_class,
        "derived_action_schema": str(action.get("derived_contract_schema") or ""),
        "selected_profile_only": True,
        "automatic_provider_fallback": False,
        "execution_mode": execution_mode,
        "profile_label": profile_label,
        "provider_label": provider_label,
        "guided_route_label": _GUIDED_ROUTE_LABELS.get(action_id, action_label),
        "guided_route_badge": _GUIDED_ROUTE_BADGES.get(action_id, "Action"),
        "expert_route_label": _EXPERT_EXECUTION_LABELS.get(execution_mode, execution_mode.replace("_", " ") or "No provider route"),
        "required_capability": capability,
        "required_bridge_capability": bridge_capability,
        "requires_bridge": bool(route.get("requires_bridge")),
        "cross_provider_policy": "explicit_only" if dispatch_type == "explicit_cross_provider_bridge" else "selected_provider_only",
        "provider_supported": provider_supported,
        "profile_enabled": profile_enabled,
        "connected": connected,
        "runtime_required": bool(route.get("runtime_required")),
        "runtime_ready": runtime_ready,
        "extension_enabled": extension_enabled,
        "route_state": route_state,
        "route_available": route_available,
        "capability_available": capability_available,
        "bridge_capability_available": bridge_available,
        "dispatch_ready": dispatch_ready,
        "implementation_phase": route.get("implementation_phase"),
        "provider_enabled": provider_enabled,
        "enabled": provider_enabled,
        "visible": visible,
        "diagnostic_visible": bool(expert_mode and not provider_enabled),
        "availability_state": "ready" if provider_enabled else "blocked",
        "disabled_reason_code": "" if provider_enabled else disabled_reason_code,
        "disabled_reason": "" if provider_enabled else disabled_reason,
        "disabled_reason_guided": "" if provider_enabled else guided_disabled_reason,
        "disabled_reason_expert": "" if provider_enabled else disabled_reason,
        "requirement_checks": requirement_checks,
        "extension_policy": deepcopy(extension_policy),
    }


def build_preview_action_provider_evaluation(
    *,
    profile: Dict[str, Any],
    overlay: Dict[str, Any] | None = None,
    extension_payload: Dict[str, Any] | Iterable[Dict[str, Any]] | None = None,
    family: str = "",
    loader: str = "",
    workflow_mode: str = "generate",
    expert_mode: bool = False,
) -> Dict[str, Any]:
    actions = [
        evaluate_preview_action_for_profile(
            action,
            profile=profile,
            overlay=overlay,
            extension_payload=extension_payload,
            family=family,
            loader=loader,
            workflow_mode=workflow_mode,
            expert_mode=expert_mode,
        )
        for action in get_preview_action_registry()
    ]
    groups = []
    for group in ACTION_GROUPS:
        groups.append({**deepcopy(group), "actions": [action for action in actions if action.get("group") == group["id"]]})
    return {
        "schema_id": PREVIEW_ACTION_EVALUATION_SCHEMA_ID,
        "schema_version": PREVIEW_ACTION_EVALUATION_SCHEMA_VERSION,
        "authority": "neo_app.image.preview_action_routing",
        "profile_id": str(profile.get("profile_id") or ""),
        "provider_id": str(profile.get("provider_id") or "").casefold(),
        "profile_label": _profile_display_name(profile),
        "provider_label": _provider_display_name(profile, str(profile.get("provider_id") or "").casefold()),
        "diagnostics_schema_id": "neo.image.preview_action_diagnostics.v1",
        "selected_profile_only": True,
        "automatic_provider_fallback": False,
        "family": family,
        "loader": loader,
        "workflow_mode": workflow_mode,
        "expert_mode": bool(expert_mode),
        "action_count": len(actions),
        "groups": groups,
        "actions": actions,
    }
