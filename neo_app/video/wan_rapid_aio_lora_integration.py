from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Callable

from neo_app.video.video_lora_runtime import extract_video_lora_rows
from neo_app.video.wan_rapid_aio_lora import apply_rapid_aio_lora_stack

_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("neo_video_wan_rapid_aio_phase16_payload", default=None)
_INSTALLED = False


def install_wan_rapid_aio_lora_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from neo_app.video import wan_rapid_aio_gguf_compiler as compiler
    if getattr(compiler, "_neo_phase16_rapid_aio_lora_installed", False):
        _INSTALLED = True
        return

    original_graph: Callable[..., dict[str, Any]] = compiler._build_mega_generation_prompt
    original_compile: Callable[..., dict[str, Any]] = compiler.video_wan22_rapid_aio_gguf_compile_payload

    def graph(req: Any, object_info: dict[str, Any], nodes: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
        result = original_graph(req, object_info, nodes, variant)
        rows = extract_video_lora_rows(_PAYLOAD.get() or req.payload())
        if not rows:
            result["video_lora_stack"] = {
                "schema_version": "neo.video.wan22.rapid_aio_lora.v1", "phase": "phase_16", "active": False,
                "route_id": f"wan22.rapid_aio_gguf.{req.generation_type}", "requested_count": 0,
                "applied_count": 0, "standard_count": 0, "speed_count": 0, "applied": [], "warnings": [],
                "gpu_inference_proven": False,
            }
            return result
        if not result.get("ok") or not isinstance(result.get("prompt"), dict):
            return result
        model_loader = nodes.get("model_loader", {}) if isinstance(nodes.get("model_loader"), dict) else {}
        route_id = f"wan22.rapid_aio_gguf.{req.generation_type}"
        patched, runtime = apply_rapid_aio_lora_stack(
            result["prompt"], list(result.get("model_link") or []), rows,
            route_id=route_id, object_info=object_info, model_loader_class=str(model_loader.get("class_type") or ""),
        )
        result["prompt"] = patched
        result["queue_payload"] = {**(result.get("queue_payload") or {}), "prompt": patched}
        result["model_link"] = runtime["final_model_ref"]
        result["video_lora_stack"] = runtime
        result["lora_patch_profile"] = {
            "schema_version": "neo.video.lora_patch_profile.v1",
            "route_id": route_id,
            "owner": "compiler",
            "compiler": "neo_app.video.wan_rapid_aio_gguf_compiler",
            "loader_type": "model_only",
            "loader_node_class": runtime["topology"]["lora_loader_class"],
            "targets": ["all"],
            "validated": True,
            "branches": [{"target": "all", "model_ref": runtime["initial_model_ref"], "model_consumers": runtime["model_consumers"]}],
            "notes": ["Phase 16 owns the final Rapid AIO single-MODEL link after performance adapters and before ModelSamplingSD3."],
        }
        return result

    def compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
        token = _PAYLOAD.set(deepcopy(payload or {}))
        try:
            result = original_compile(payload, object_info_override=object_info_override)
            for variant in result.get("variants", []) if isinstance(result.get("variants"), list) else []:
                graph_meta = variant.get("generation_graph", {}) if isinstance(variant, dict) else {}
                runtime = graph_meta.get("video_lora_stack") if isinstance(graph_meta, dict) else None
                if runtime:
                    result["video_lora_stack"] = runtime
                    result["lora_patch_profile"] = graph_meta.get("lora_patch_profile", {})
                    break
            return result
        finally:
            _PAYLOAD.reset(token)

    compiler._build_mega_generation_prompt = graph
    compiler.video_wan22_rapid_aio_gguf_compile_payload = compile_payload
    compiler._neo_phase16_rapid_aio_lora_installed = True
    _INSTALLED = True
