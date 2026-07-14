from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from neo_app.assistant.contracts import compact_json_payload, normalize_surface_id, trim_text
from neo_app.assistant.store import assistant_profile, get_project, list_context_items, list_surface_context_payload
from neo_app.runtime.surface_state import normalize_surface_runtime
from neo_app.services.ui_state import read_ui_state
from neo_app.providers.profiles import get_backend_profile, get_backend_profile_payload

SURFACE_PROJECT_CONTEXT_SCHEMA_ID = "neo.assistant.surface_project_context.v1"

PROJECT_SURFACE_MAP = {
    "image_workspace": "image",
    "prompt_captioning_workspace": "prompt_captioning",
    "roleplay_workspace": "roleplay",
    "voice_workspace": "voice",
    "video_workspace": "video",
    "neo_development_workspace": "admin",
}

SURFACE_CONTEXT_PROJECT_IDS = {
    "image": "image_workspace",
    "prompt_captioning": "prompt_captioning_workspace",
    "roleplay": "roleplay_workspace",
    "voice": "voice_workspace",
    "video": "video_workspace",
    "admin": "neo_development_workspace",
}

IMAGE_PARAMETER_KEYS = (
    "family",
    "loader",
    "model",
    "checkpoint",
    "diffusion_model",
    "gguf_model",
    "gguf_unet",
    "gguf_clip_mode",
    "gguf_clip_type",
    "text_encoder_1",
    "text_encoder_2",
    "qwen_text_encoder",
    "qwen_mmproj",
    "flux_variant",
    "flux_guidance",
    "vae",
    "sampler",
    "scheduler",
    "positive_prompt",
    "negative_prompt",
    "width",
    "height",
    "size_preset",
    "steps",
    "cfg",
    "seed",
    "batch_count",
    "latent_capture_mode",
    "denoise",
    "clip_skip",
    "clamp",
    "qwen_source_slot_count",
    "qwen_composition_source_mode",
    "mask_brush_size",
    "mask_mode",
    "inpaint_selection_target",
    "inpaint_context_mode",
    "mask_grow",
    "mask_blur",
    "outpaint_left",
    "outpaint_top",
    "outpaint_right",
    "outpaint_bottom",
    "outpaint_feather",
    "outpaint_source_resolution_mode",
    "outpaint_source_max_long_edge",
    "outpaint_source_max_megapixels",
)

IMAGE_ASSET_KEYS = (
    "source_image",
    "source_image_name",
    "source_image_url",
    "source_image_1_role",
    "source_image_2",
    "source_image_2_name",
    "source_image_2_url",
    "source_image_2_role",
    "source_image_3",
    "source_image_3_name",
    "source_image_3_url",
    "source_image_3_role",
    "mask_image",
    "mask_image_name",
    "mask_image_url",
    "mask_image_preview_url",
    "source_image_width",
    "source_image_height",
)

VIDEO_PARAMETER_KEYS = (
    "family",
    "loader",
    "mode",
    "vram_profile",
    "performance_profile",
    "enable_sage_attention",
    "sage_attention_mode",
    "enable_teacache",
    "teacache_profile",
    "enable_cpu_offload",
    "enable_vae_offload",
    "enable_block_swap",
    "block_swap_blocks",
    "positive_prompt",
    "negative_prompt",
    "width",
    "height",
    "frames",
    "fps",
    "steps",
    "guidance",
    "seed",
    "sampler",
    "scheduler",
    "model_name",
    "unet_name",
    "gguf_name",
    "clip_name",
    "clip_name1",
    "clip_name2",
    "text_encoder",
    "vae_name",
    "batch_count",
    "output_format",
    "decode_mode",
    "source_image",
    "source_image_name",
    "first_image",
    "first_image_name",
    "last_image",
    "last_image_name",
    "rapid_aio_frame_mode",
    "image_strength",
    "resize_mode",
    "multiscene_segments",
    "extend_continuation_strength",
    "vid2vid_denoise_strength",
    "vid2vid_motion_strength",
    "depth_motion_control_type",
    "depth_motion_control_strength",
    "audio_prompt",
    "dialogue_prompt",
    "soundscape_prompt",
    "audio_mode",
)

PROMPT_CAPTIONING_KEYS = (
    "mode",
    "activeWorkspaceMode",
    "activeChildTabsByMode",
    "promptBuilder",
    "captioning",
)

VOICE_PARAMETER_KEYS = (
    "family",
    "runtime",
    "model_id",
    "job_type",
    "voice_source_type",
    "voice_id",
    "saved_profile_id",
    "language",
    "script_title",
    "script_body",
    "delivery_notes",
    "split_long_text",
    "punctuation_cleanup",
    "speaking_rate",
    "expression_strength",
    "reference_strength",
    "seed",
    "output_format",
    "max_chunk_chars",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in _safe_dict(overlay).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _pick(source: dict[str, Any], keys: tuple[str, ...], *, include_empty: bool = False) -> dict[str, Any]:
    picked: dict[str, Any] = {}
    for key in keys:
        if key not in source:
            continue
        value = source.get(key)
        if not include_empty and value in (None, "", [], {}):
            continue
        picked[key] = deepcopy(value)
    return picked


def _truncate_nested_strings(value: Any, *, limit: int = 1200) -> Any:
    if isinstance(value, str):
        return trim_text(value, limit)
    if isinstance(value, list):
        return [_truncate_nested_strings(item, limit=limit) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _truncate_nested_strings(item, limit=limit) for key, item in list(value.items())[:80]}
    return value


def _client_snapshot(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _safe_dict(payload)
    return _safe_dict(
        payload.get("surface_context_snapshot")
        or payload.get("active_surface_context")
        or payload.get("surface_context")
        or payload.get("client_surface_context")
    )


def _snapshot_state(client_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        persisted = read_ui_state()
    except Exception:
        persisted = {}
    snapshot = _safe_dict(client_snapshot)
    # The frontend sends either a full state-shaped snapshot or a contract payload with
    # an inner `ui_state` block. Merge it over persisted neo_data/ui_state so live UI
    # values win without requiring legacy autosave to be re-enabled.
    overlay = _safe_dict(snapshot.get("ui_state")) or snapshot
    return _deep_merge(_safe_dict(persisted), overlay)


def _project_surface(project_id: str = "", explicit_surface: str = "") -> str:
    explicit = normalize_surface_id(explicit_surface, default="")
    if explicit:
        return explicit
    project_id = str(project_id or "").strip()
    if project_id in PROJECT_SURFACE_MAP:
        return PROJECT_SURFACE_MAP[project_id]
    project = get_project(project_id) if project_id else None
    surface = normalize_surface_id((project or {}).get("surface") or "", default="")
    if surface:
        return surface
    if str((project or {}).get("type") or "") == "surface_workspace":
        for surface_id, mapped_project_id in SURFACE_CONTEXT_PROJECT_IDS.items():
            if project_id == mapped_project_id:
                return surface_id
    return "assistant"


def resolve_surface_project_context_surface(project_id: str = "", active_surface: str = "") -> str:
    return _project_surface(project_id, active_surface)


def _selected_profile_id(state: dict[str, Any], surface: str) -> str:
    selected = _safe_dict(state.get("activeBackendProfileIdsBySurface"))
    profile_id = str(selected.get(surface) or "").strip()
    if profile_id:
        return profile_id
    try:
        payload = get_backend_profile_payload()
        defaults = _safe_dict(payload.get("defaults"))
        return str(defaults.get(surface) or defaults.get("text") or defaults.get("assistant") or "").strip()
    except Exception:
        return ""


def _profile_summary(profile_id: str) -> dict[str, Any]:
    if not profile_id:
        return {}
    try:
        profile = get_backend_profile(profile_id) or {}
    except Exception:
        profile = {}
    if not profile:
        return {"profile_id": profile_id, "status": "missing"}
    runtime = _safe_dict(profile.get("runtime"))
    connection = _safe_dict(profile.get("connection"))
    return {
        "profile_id": str(profile.get("profile_id") or profile_id),
        "display_name": str(profile.get("display_name") or profile_id),
        "provider_id": str(profile.get("provider_id") or ""),
        "surface": str(profile.get("surface") or ""),
        "runtime_status": str(profile.get("runtime_status") or runtime.get("status") or "unknown"),
        "reachable": runtime.get("reachable"),
        "model": str(connection.get("model") or ""),
        "base_url": str(connection.get("base_url") or ""),
        "capability_flags": _safe_dict(profile.get("capability_flags")),
    }


def _image_context(state: dict[str, Any], surface_runtime: dict[str, Any]) -> dict[str, Any]:
    draft = _safe_dict(state.get("imageDraft"))
    family = str(draft.get("family") or "sdxl")
    loader = str(draft.get("loader") or "checkpoint")
    mode = str(surface_runtime.get("workflow_mode") or surface_runtime.get("subtab") or "generate")
    if mode == "edit":
        route_mode = "edit"
    elif mode == "generate":
        route_mode = "txt2img"
    else:
        route_mode = mode
    route = {}
    available_modes: list[str] = []
    try:
        from neo_app.models.route_matrix import available_modes_for_route, resolve_model_backend_route

        route = resolve_model_backend_route(family, loader, route_mode, "comfyui").as_dict()
        available_modes = available_modes_for_route(family, loader, "comfyui")
    except Exception as exc:
        route = {"state": "unknown", "reason": str(exc)[:300]}
    recent = {}
    try:
        from neo_app.image.output_service import list_image_results

        recent = list_image_results(category="all", limit=5, sort="newest")
    except Exception as exc:
        recent = {"ok": False, "error": str(exc)[:300], "results": []}
    return {
        "current_parameters": _truncate_nested_strings(_pick(draft, IMAGE_PARAMETER_KEYS, include_empty=False)),
        "asset_inputs": _truncate_nested_strings(_pick(draft, IMAGE_ASSET_KEYS, include_empty=False), limit=600),
        "extension_settings": _image_extension_settings(draft),
        "route_snapshot": route,
        "available_modes_for_family_loader": available_modes,
        "recent_metadata": _summarize_image_results(recent),
        "recommendation_guidance": [
            "Use current_parameters as the live Image UI source of truth before giving settings advice.",
            "Use route_snapshot.state before suggesting a model family / loader / mode combination.",
            "Use extension_settings to explain Style Stack, LoRA Stack, Wildcards, LayerDiffuse, ControlNet/IP Adapter, or other active add-ons when present.",
        ],
    }


def _image_extension_settings(draft: dict[str, Any]) -> dict[str, Any]:
    extension_ids = (
        "style_stack",
        "wildcards",
        "lora_stack",
        "embeddings_ti",
        "cfg_fix_dynamic_thresholding",
        "image.layerdiffuse",
        "image.controlnet",
        "image.ip_adapter",
        "image.scene_director",
        "image.adetailer",
        "image.high_res_lab",
        "image.image_upscale",
        "image.background_removal",
    )
    result: dict[str, Any] = {}
    for extension_id in extension_ids:
        payload = draft.get(extension_id)
        if isinstance(payload, dict):
            result[extension_id] = _truncate_nested_strings(payload, limit=800)
    # Legacy fields sometimes store extension sub-payloads directly.
    for key, value in draft.items():
        if isinstance(key, str) and key.startswith("image.") and isinstance(value, dict) and key not in result:
            result[key] = _truncate_nested_strings(value, limit=800)
    return result


def _summarize_image_results(recent: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in _safe_list(recent.get("results"))[:5]:
        if not isinstance(item, dict):
            continue
        rows.append({
            "result_id": item.get("result_id"),
            "created_at": item.get("created_at"),
            "category": item.get("category") or item.get("subtab") or item.get("save_category"),
            "filename": item.get("filename") or item.get("primary_file") or item.get("title"),
            "prompt": trim_text(item.get("prompt") or item.get("positive_prompt") or item.get("summary") or "", 500),
            "family": item.get("family"),
            "loader": item.get("loader"),
            "seed": item.get("seed"),
        })
    return {
        "source": recent.get("source") or "neo_data/outputs/image_metadata",
        "total": recent.get("total", len(rows)),
        "results": rows,
    }


def _video_context(state: dict[str, Any], surface_runtime: dict[str, Any]) -> dict[str, Any]:
    draft = _safe_dict(state.get("videoDraft"))
    recent = {}
    try:
        from neo_app.video.output_records import list_video_output_records

        recent = list_video_output_records(limit=5)
    except Exception as exc:
        recent = {"ok": False, "error": str(exc)[:300], "records": []}
    return {
        "current_parameters": _truncate_nested_strings(_pick(draft, VIDEO_PARAMETER_KEYS, include_empty=False)),
        "generation_mode": surface_runtime.get("generation_mode") or draft.get("mode") or "txt2vid",
        "recent_metadata": _summarize_video_results(recent),
        "recommendation_guidance": [
            "Use current_parameters as the live Video UI source of truth before giving settings advice.",
            "Use generation_mode before suggesting source image, first/last-frame, vid2vid, depth, audio, or finish workflows.",
        ],
    }


def _summarize_video_results(recent: dict[str, Any]) -> dict[str, Any]:
    raw = _safe_list(recent.get("records") or recent.get("results"))[:5]
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rows.append({
            "result_id": item.get("result_id"),
            "created_at": item.get("created_at"),
            "category": item.get("category") or item.get("route_category"),
            "status": item.get("status"),
            "prompt": trim_text(item.get("prompt") or item.get("positive_prompt") or "", 500),
            "family": item.get("family") or (_safe_dict(item.get("model")).get("family")),
            "mode": item.get("mode") or item.get("generation_mode"),
        })
    return {"total": recent.get("total", len(rows)), "results": rows}


def _prompt_captioning_context(state: dict[str, Any], surface_runtime: dict[str, Any]) -> dict[str, Any]:
    pc = _safe_dict(state.get("promptCaptioning"))
    picked = _pick(pc, PROMPT_CAPTIONING_KEYS, include_empty=False)
    # Heavy histories/libraries are not useful in the live context pack; summarize instead.
    library = _safe_dict(pc.get("library"))
    picked["library_summary"] = {
        "prompt_records": len(_safe_list(library.get("promptRecords"))),
        "caption_records": len(_safe_list(library.get("captionRecords"))),
        "character_records": len(_safe_list(library.get("characterRecords"))),
        "caption_batch_results": len(_safe_list(library.get("captionBatchResults"))),
    }
    return {
        "current_parameters": _truncate_nested_strings(picked, limit=1000),
        "workspace_mode": surface_runtime.get("workspace_mode") or pc.get("mode") or "prompt_builder",
        "recommendation_guidance": [
            "Use promptBuilder and captioning blocks to answer questions about current Prompt/Captioning settings.",
            "Do not invent generated captions or prompt outputs when fields are blank.",
        ],
    }


def _roleplay_context(state: dict[str, Any], surface_runtime: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "roleplayRuntime",
        "roleplayRuntimePreparedPacket",
        "roleplayStudio",
        "roleplayForge",
        "roleplayStories",
        "roleplayEngineBridge",
        "roleplayScopedCompile",
        "roleplayScopedCompilePlan",
        "roleplayScopedCompileLastRun",
    )
    data = {key: _safe_dict(state.get(key)) for key in keys if isinstance(state.get(key), dict)}
    return {
        "current_parameters": _truncate_nested_strings(data, limit=1000),
        "workspace_app": surface_runtime.get("workspace_app") or surface_runtime.get("subtab") or "workspace",
        "recommendation_guidance": [
            "Use roleplay runtime/state packets to answer roleplay scope, compile, character, scene, and guardrail questions.",
            "Never write user-controlled characters' dialogue/actions when advising on Roleplay runtime outputs.",
        ],
    }


def _voice_context(state: dict[str, Any], surface_runtime: dict[str, Any]) -> dict[str, Any]:
    draft = _safe_dict(state.get("voiceDraft"))
    return {
        "current_parameters": _truncate_nested_strings(_pick(draft, VOICE_PARAMETER_KEYS, include_empty=False), limit=1000),
        "workflow_mode": surface_runtime.get("workflow_mode") or draft.get("job_type") or "quick_preview",
        "recommendation_guidance": ["Use current voice parameters before advising on voice generation, script chunking, profile, or export settings."],
    }


def _generic_context(state: dict[str, Any], surface: str, surface_runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_parameters": {},
        "surface_runtime": surface_runtime,
        "recommendation_guidance": [f"No specialized live context provider exists for {surface}; answer from scope knowledge and memory only."],
    }


def _surface_specific_context(surface: str, state: dict[str, Any], surface_runtime: dict[str, Any]) -> dict[str, Any]:
    if surface == "image":
        return _image_context(state, surface_runtime)
    if surface == "video":
        return _video_context(state, surface_runtime)
    if surface == "prompt_captioning":
        return _prompt_captioning_context(state, surface_runtime)
    if surface == "roleplay":
        return _roleplay_context(state, surface_runtime)
    if surface == "voice":
        return _voice_context(state, surface_runtime)
    return _generic_context(state, surface, surface_runtime)


def _extension_summary(surface: str, surface_runtime: dict[str, Any]) -> dict[str, Any]:
    if surface not in {"image", "video", "voice"}:
        return {"available": False, "reason": "surface_has_no_extension_registry_payload"}
    try:
        from neo_app.extensions.registry import get_surface_extension_payload

        payload = get_surface_extension_payload(
            surface,
            subtab_id=str(surface_runtime.get("subtab") or ""),
            workspace_app=str(surface_runtime.get("workspace_app") or ""),
            workflow_mode=str(surface_runtime.get("workflow_mode") or surface_runtime.get("generation_mode") or ""),
        )
        records = []
        for record in _safe_list(payload.get("extensions"))[:40]:
            manifest = _safe_dict(record.get("manifest"))
            extension_id = str(record.get("id") or manifest.get("id") or record.get("extension_id") or "")
            records.append({
                "extension_id": extension_id,
                "name": manifest.get("name") or record.get("name") or extension_id,
                "enabled": bool(record.get("registry_enabled", record.get("enabled", False))),
                "workflow_enabled": record.get("workflow_enabled"),
                "origin": record.get("origin") or manifest.get("extension_origin"),
                "workspace_apps": manifest.get("workspace_apps") or [],
                "workflow_modes": manifest.get("workflow_modes") or manifest.get("subtabs") or [],
                "mount_slots": manifest.get("mount_slots") or [],
            })
        return {
            "available": True,
            "surface": surface,
            "enabled_extensions": payload.get("enabled_extensions") or [],
            "disabled_extensions": payload.get("disabled_extensions") or [],
            "extensions": records,
            "ui_hidden_policy": payload.get("ui_hidden_policy") or "",
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)[:300]}


def _saved_handoffs(project_id: str, surface: str) -> dict[str, Any]:
    try:
        handoffs = list_surface_context_payload(project_id=project_id, surface=surface, limit=8).get("surface_context", [])
    except Exception:
        handoffs = []
    try:
        context_items = list_context_items(project_id=project_id, surface=surface, limit=8)
    except Exception:
        context_items = []
    return {
        "surface_handoffs": [
            {
                "handoff_id": item.get("handoff_id"),
                "title": item.get("title"),
                "summary": trim_text(item.get("summary") or "", 500),
                "subtab": item.get("subtab"),
                "suggested_action": item.get("suggested_action"),
                "created_at": item.get("created_at"),
            }
            for item in _safe_list(handoffs)[:8]
            if isinstance(item, dict)
        ],
        "scope_context_items": [
            {
                "context_id": item.get("context_id"),
                "title": item.get("title"),
                "kind": item.get("kind"),
                "summary": trim_text(item.get("text") or "", 500),
                "created_at": item.get("created_at"),
            }
            for item in _safe_list(context_items)[:8]
            if isinstance(item, dict)
        ],
    }


def build_surface_project_context(
    *,
    surface: str = "",
    project_id: str = "",
    session_id: str = "",
    payload: dict[str, Any] | None = None,
    client_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _safe_dict(payload)
    explicit_surface = surface or str(payload.get("active_surface") or payload.get("surface") or "")
    resolved_surface = _project_surface(project_id or str(payload.get("project_id") or ""), explicit_surface)
    snapshot = _client_snapshot(payload) or _safe_dict(client_snapshot)
    state = _snapshot_state(snapshot)
    runtime_map = _safe_dict(state.get("surfaceRuntime"))
    surface_runtime = normalize_surface_runtime(resolved_surface, _safe_dict(runtime_map.get(resolved_surface)))
    profile_id = _selected_profile_id(state, resolved_surface)
    target_project_id = str(project_id or payload.get("project_id") or SURFACE_CONTEXT_PROJECT_IDS.get(resolved_surface) or assistant_profile().get("default_project_id") or "general").strip() or "general"
    specialized = _surface_specific_context(resolved_surface, state, surface_runtime)
    context = {
        "ok": True,
        "schema_id": SURFACE_PROJECT_CONTEXT_SCHEMA_ID,
        "built_at": _now_iso(),
        "surface": resolved_surface,
        "project_id": target_project_id,
        "session_id": str(session_id or payload.get("session_id") or ""),
        "source_priority": ["client_snapshot", "neo_data/ui_state", "surface_runtime", "extension_registry", "output_metadata", "saved_surface_handoffs"],
        "live_snapshot_included": bool(snapshot),
        "surface_runtime": surface_runtime,
        "backend_profile": _profile_summary(profile_id),
        "extensions": _extension_summary(resolved_surface, surface_runtime),
        "saved_context": _saved_handoffs(target_project_id, resolved_surface),
        **specialized,
        "policy": {
            "read_only": True,
            "assistant_may_answer_settings_questions": True,
            "assistant_should_not_claim_live_code_inspection": True,
            "client_snapshot_overrides_persisted_ui_state": True,
            "only_neo_data_metadata_is_used_for_generation_history": True,
        },
    }
    return compact_json_payload(context, limit=32000)


def surface_project_context_payload(surface: str = "", *, project_id: str = "", session_id: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_surface_project_context(surface=surface, project_id=project_id, session_id=session_id, payload=payload or {})


def surface_project_context_text(context: dict[str, Any], *, limit: int = 12000) -> str:
    if not isinstance(context, dict) or not context.get("ok"):
        return "No live surface project context available."
    lines = [
        f"Surface project context schema: {context.get('schema_id')}",
        f"Surface: {context.get('surface')}",
        f"Project ID: {context.get('project_id')}",
        f"Live client snapshot included: {bool(context.get('live_snapshot_included'))}",
        f"Surface runtime: {json.dumps(context.get('surface_runtime') or {}, ensure_ascii=False)}",
    ]
    backend = _safe_dict(context.get("backend_profile"))
    if backend:
        lines.append(f"Backend profile: {backend.get('display_name') or backend.get('profile_id')} · provider={backend.get('provider_id') or 'unknown'} · status={backend.get('runtime_status') or 'unknown'} · model={backend.get('model') or 'unknown'}")
    current_parameters = _safe_dict(context.get("current_parameters"))
    if current_parameters:
        lines.append("Current live parameters:")
        lines.append(json.dumps(current_parameters, ensure_ascii=False, indent=2, default=str)[:5000])
    asset_inputs = _safe_dict(context.get("asset_inputs"))
    if asset_inputs:
        lines.append("Current linked input/source assets:")
        lines.append(json.dumps(asset_inputs, ensure_ascii=False, indent=2, default=str)[:2500])
    ext_settings = _safe_dict(context.get("extension_settings"))
    if ext_settings:
        lines.append("Current extension settings payloads from the UI draft:")
        lines.append(json.dumps(ext_settings, ensure_ascii=False, indent=2, default=str)[:3500])
    extensions = _safe_dict(context.get("extensions"))
    if extensions:
        lines.append("Available/enabled surface extensions:")
        lines.append(json.dumps(extensions, ensure_ascii=False, indent=2, default=str)[:3500])
    route_snapshot = _safe_dict(context.get("route_snapshot"))
    if route_snapshot:
        lines.append("Route snapshot:")
        lines.append(json.dumps(route_snapshot, ensure_ascii=False, indent=2, default=str)[:2500])
    recent_metadata = _safe_dict(context.get("recent_metadata"))
    if recent_metadata:
        lines.append("Recent Neo-owned generation metadata:")
        lines.append(json.dumps(recent_metadata, ensure_ascii=False, indent=2, default=str)[:3500])
    saved = _safe_dict(context.get("saved_context"))
    if saved and (_safe_list(saved.get("surface_handoffs")) or _safe_list(saved.get("scope_context_items"))):
        lines.append("Saved surface handoffs / scope context:")
        lines.append(json.dumps(saved, ensure_ascii=False, indent=2, default=str)[:3000])
    guidance = _safe_list(context.get("recommendation_guidance"))
    if guidance:
        lines.append("Assistant usage guidance:")
        lines.extend(f"- {trim_text(item, 500)}" for item in guidance)
    return trim_text("\n".join(lines), limit)
