from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Callable

from neo_app.video.lora_patch_profiles import build_single_model_lora_patch_profile
from neo_app.video.ltx_lora_integration import (
    LTX_MODEL_ONLY_LOADER,
    _find_chunk_anchor,
    _lora_catalog,
    _validate_live_catalog,
    apply_ltx_model_only_lora_stack,
)
from neo_app.video.ltx_route_lora_capability import validate_ltx_route_lora_topology
from neo_app.video.video_lora_runtime import extract_video_lora_rows

_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("neo_video_ltx_phase14_payload", default=None)
_INSTALLED = False

ROUTES: tuple[tuple[str, str, str, str], ...] = (
    ("first_last_frame_compiler", "build_ltx23_first_last_frame_workflow", "video_ltx23_first_last_frame_compile_payload", "first_last_frame"),
    ("multiscene_compiler", "build_ltx23_multiscene_workflow", "video_ltx23_multiscene_compile_payload", "multiscene"),
    ("extend_compiler", "build_ltx23_extend_workflow", "video_ltx23_extend_compile_payload", "extend"),
    ("vid2vid_compiler", "build_ltx23_vid2vid_workflow", "video_ltx23_vid2vid_compile_payload", "vid2vid"),
    ("depth_motion_compiler", "build_ltx23_depth_motion_workflow", "video_ltx23_depth_motion_compile_payload", "depth_motion"),
    ("schedule_compiler", "build_ltx23_schedule_workflow", "video_ltx23_schedule_compile_payload", "prompt_schedule"),
    ("audio_video_compiler", "build_ltx23_audio_video_workflow", "video_ltx23_audio_video_compile_payload", "audio_video"),
)


def _actual_model_only(info: dict[str, Any]) -> str:
    folded = {str(key).casefold(): str(key) for key in info}
    return folded.get(LTX_MODEL_ONLY_LOADER.casefold(), "")


def _wrap(module: Any, build_name: str, compile_name: str, mode: str) -> None:
    original_build: Callable[..., dict[str, Any]] = getattr(module, build_name)
    original_compile: Callable[..., dict[str, Any]] = getattr(module, compile_name)

    def build(req: Any, *args: Any, object_info: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        info = object_info or {}
        payload = _PAYLOAD.get() or req.payload()
        rows = extract_video_lora_rows(payload)
        compiled = original_build(req, *args, object_info=info, **kwargs)
        route_id = str(compiled.get("route_id") or "")
        if not route_id.endswith(f".{mode}"):
            raise ValueError(f"LTX Phase 14 wrapper expected {mode!r}, got {route_id!r}.")
        workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
        model_ref, chunk_id = _find_chunk_anchor(compiled)
        profile = build_single_model_lora_patch_profile(
            route_id=route_id,
            compiler=f"neo_app.video.{module.__name__.rsplit('.', 1)[-1]}",
            model_ref=model_ref,
            model_consumers=[{"node_id": chunk_id, "input": "model"}],
            loader_type="model_only",
            loader_node_class=LTX_MODEL_ONLY_LOADER,
            validated=any(f".{loader}." in route_id for loader in ("unet", "gguf")),
            notes=[f"Phase 14 owns the LTX {mode} model anchor immediately upstream of LTXVChunkFeedForward."],
        )
        compiled["lora_patch_profile"] = profile
        topology: dict[str, Any] = {}
        if rows:
            topology = validate_ltx_route_lora_topology(route_id, compiled.get("bindings"), info)
            loader_class = _actual_model_only(info)
            _validate_live_catalog(rows, _lora_catalog(info, loader_class), bool(loader_class))
            patched, runtime = apply_ltx_model_only_lora_stack(
                workflow, profile, rows, route_id=route_id, loader_available=bool(loader_class)
            )
            compiled["workflow"] = patched
            prompt = compiled.get("prompt_api_payload") if isinstance(compiled.get("prompt_api_payload"), dict) else {}
            prompt["prompt"] = patched
            compiled["prompt_api_payload"] = prompt
        else:
            runtime = {
                "schema_version": "neo.video.lora_stack.ltx23.runtime.v1",
                "active": False,
                "route_id": route_id,
                "requested_count": 0,
                "applied_count": 0,
                "standard_count": 0,
                "speed_count": 0,
                "loader_node_class": LTX_MODEL_ONLY_LOADER,
                "applied": [],
                "warnings": [],
            }
        runtime.update({
            "phase": "phase_14",
            "route_topology": topology,
            "live_catalog_validated": bool(rows),
            "generic_lora_loader_fallback": False,
        })
        compiled["video_lora_stack"] = runtime
        rules = compiled.get("rules") if isinstance(compiled.get("rules"), list) else []
        rules.extend([
            f"LTX {mode} standard LoRAs use the universal compiler-owned model-only stack.",
            "LTX speed roles and high/low targets remain fail closed.",
            "GGUF LoRA requires live MODEL socket compatibility; native-workflow routes remain blocked without a compiler-owned anchor.",
        ])
        compiled["rules"] = list(dict.fromkeys(str(rule) for rule in rules if str(rule)))
        return compiled

    def compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
        token = _PAYLOAD.set(deepcopy(payload or {}))
        try:
            return original_compile(payload, object_info_override=object_info_override)
        finally:
            _PAYLOAD.reset(token)

    setattr(module, build_name, build)
    setattr(module, compile_name, compile_payload)
    setattr(module, "_neo_phase14_ltx_lora_installed", True)


def install_ltx_complete_route_lora_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    import importlib
    for module_name, build_name, compile_name, mode in ROUTES:
        module = importlib.import_module(f"neo_app.video.{module_name}")
        if not getattr(module, "_neo_phase14_ltx_lora_installed", False):
            _wrap(module, build_name, compile_name, mode)
    _INSTALLED = True
