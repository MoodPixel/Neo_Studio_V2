from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Callable

from neo_app.video.lora_patch_profiles import build_single_model_lora_patch_profile
from neo_app.video.video_lora_runtime import extract_video_lora_rows, normalize_video_lora_rows

LTX_MODEL_ONLY_LOADER = "LoraLoaderModelOnly"
_RUNTIME_SCHEMA = "neo.video.lora_stack.ltx23.runtime.v1"
_PHASE7_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("neo_video_ltx_phase7_payload", default=None)
_INSTALLED = False


def _actual_class(object_info: dict[str, Any], class_name: str) -> str:
    folded = {str(key).casefold(): str(key) for key in (object_info or {})}
    return folded.get(class_name.casefold(), "")


def _lora_catalog(object_info: dict[str, Any], loader_class: str) -> list[str]:
    if not loader_class:
        return []
    entry = object_info.get(loader_class, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    spec = required.get("lora_name") if isinstance(required, dict) else None
    if not isinstance(spec, list) or not spec:
        return []
    values = spec[0]
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item)]


def _validate_live_catalog(rows: list[dict[str, Any]], live_values: list[str], loader_available: bool) -> None:
    if not rows:
        return
    if not loader_available:
        raise ValueError("LTX 2.3 LoRA rows were requested but ComfyUI does not expose LoraLoaderModelOnly.")
    if not live_values:
        raise ValueError("LTX 2.3 LoRA rows were requested but LoraLoaderModelOnly exposes no LoRA files in its live catalog.")
    live_folded = {value.casefold() for value in live_values}
    missing = [str(row.get("name") or "") for row in rows if str(row.get("name") or "").casefold() not in live_folded]
    if missing:
        raise ValueError(
            "Selected LTX 2.3 Video LoRA is not visible in the live LoraLoaderModelOnly catalog: "
            + ", ".join(missing)
        )


def _next_numeric_node_id(workflow: dict[str, Any]) -> int:
    numeric = [int(key) for key in workflow if str(key).isdigit()]
    return max(numeric, default=0) + 1


def _find_chunk_anchor(compiled: dict[str, Any]) -> tuple[list[Any], str]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
    classes = bindings.get("classes") if isinstance(bindings.get("classes"), dict) else {}
    chunk_class = str(classes.get("chunk_node") or "")
    matches: list[tuple[str, list[Any]]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or str(node.get("class_type") or "") != chunk_class:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        model_ref = inputs.get("model")
        if isinstance(model_ref, list) and len(model_ref) == 2:
            matches.append((str(node_id), list(model_ref)))
    if len(matches) != 1:
        raise ValueError("LTX compiler did not expose exactly one chunk-feed-forward model consumer for the Video LoRA anchor.")
    node_id, model_ref = matches[0]
    return model_ref, node_id


def _validate_profile(
    workflow: dict[str, Any],
    profile: dict[str, Any],
    route_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(profile, dict) or profile.get("owner") != "compiler":
        raise ValueError("LTX Video LoRA Stack requires a compiler-owned LoRA patch profile.")
    if str(profile.get("route_id") or "") != route_id:
        raise ValueError("LTX Video LoRA patch profile route does not match the compiled route.")
    if str(profile.get("loader_type") or "") != "model_only":
        raise ValueError("LTX Video LoRA integration requires a model_only patch profile.")
    if str(profile.get("loader_node_class") or "") != LTX_MODEL_ONLY_LOADER:
        raise ValueError("LTX Video LoRA integration requires LoraLoaderModelOnly; generic LoraLoader is not accepted.")
    if bool(profile.get("allow_generic_lora_loader_fallback", False)):
        raise ValueError("Generic LoraLoader fallback is forbidden for LTX Video LoRA integration.")
    if profile.get("targets") != ["all"]:
        raise ValueError("LTX patch profile must expose only target='all'.")
    if not bool(profile.get("validated", False)):
        raise ValueError("LTX Video LoRA patch profile is not validated for this route.")
    branches = profile.get("branches") if isinstance(profile.get("branches"), list) else []
    if len(branches) != 1 or not isinstance(branches[0], dict) or branches[0].get("target") != "all":
        raise ValueError("LTX patch profile must contain exactly one all-target model branch.")
    branch = branches[0]
    model_ref = branch.get("model_ref") if isinstance(branch.get("model_ref"), list) else []
    consumers = branch.get("model_consumers") if isinstance(branch.get("model_consumers"), list) else []
    if len(model_ref) != 2 or not consumers:
        raise ValueError("LTX patch profile is missing its model reference or consumer declaration.")
    if str(model_ref[0]) not in workflow:
        raise ValueError("LTX patch profile model reference no longer exists in the compiled workflow.")
    for consumer in consumers:
        node_id = str(consumer.get("node_id") or "")
        input_name = str(consumer.get("input") or "")
        node = workflow.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else {}
        if inputs.get(input_name) != model_ref:
            raise ValueError(f"LTX Video LoRA anchor is stale: {node_id}.{input_name} no longer consumes the declared model ref.")
    return branch, consumers


def apply_ltx_model_only_lora_stack(
    workflow: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None,
    *,
    route_id: str,
    loader_available: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = deepcopy(workflow) if isinstance(workflow, dict) else {}
    normalized = normalize_video_lora_rows(rows)
    if not normalized:
        return graph, {
            "schema_version": _RUNTIME_SCHEMA,
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
    if ".unet." not in route_id:
        raise ValueError("LTX 2.3 GGUF Video LoRA remains fail-closed until GGUF LoRA-loader compatibility is validated.")
    if not loader_available:
        raise ValueError("LTX 2.3 LoRA rows were requested but ComfyUI does not expose LoraLoaderModelOnly.")

    speed_rows = [row for row in normalized if row.get("role") == "speed"]
    if speed_rows:
        raise ValueError("LTX 2.3 Phase 7 supports standard Video LoRAs only; role='speed' is not enabled for LTX.")
    invalid_targets = [row for row in normalized if row.get("target") != "all"]
    if invalid_targets:
        raise ValueError("LTX 2.3 supports only target='all'; high/low branch targeting is WAN-only.")

    branch, consumers = _validate_profile(graph, profile or {}, route_id)
    upstream_ref = list(branch["model_ref"])
    next_id = _next_numeric_node_id(graph)
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []

    for row in normalized:
        node_id = str(next_id)
        next_id += 1
        graph[node_id] = {
            "class_type": LTX_MODEL_ONLY_LOADER,
            "inputs": {
                "model": upstream_ref,
                "lora_name": row["name"],
                "strength_model": float(row["strength_model"]),
            },
            "_meta": {"title": "Video LoRA · LTX 2.3 · standard"},
        }
        upstream_ref = [node_id, 0]
        applied.append(
            {
                "uid": row.get("uid") or "",
                "name": row["name"],
                "strength_model": row["strength_model"],
                "role": "standard",
                "target": "all",
                "node_id": node_id,
            }
        )
        if row.get("strength_clip") is not None:
            warnings.append(f"{row['name']}: strength_clip is ignored because LTX 2.3 Phase 7 uses a model-only LoRA loader.")

    for consumer in consumers:
        node_id = str(consumer.get("node_id") or "")
        input_name = str(consumer.get("input") or "")
        graph[node_id]["inputs"][input_name] = list(upstream_ref)

    return graph, {
        "schema_version": _RUNTIME_SCHEMA,
        "active": True,
        "route_id": route_id,
        "requested_count": len(normalized),
        "applied_count": len(applied),
        "standard_count": len(applied),
        "speed_count": 0,
        "loader_node_class": LTX_MODEL_ONLY_LOADER,
        "final_model_ref": upstream_ref,
        "applied": applied,
        "warnings": warnings,
    }


def _wrap_ltx_compiler(
    module: Any,
    *,
    build_name: str,
    compile_name: str,
    compiler_name: str,
) -> None:
    original_build: Callable[..., dict[str, Any]] = getattr(module, build_name)
    original_compile: Callable[..., dict[str, Any]] = getattr(module, compile_name)

    def phase7_build(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        info = object_info or {}
        payload = _PHASE7_PAYLOAD.get() or req.payload()
        rows = extract_video_lora_rows(payload)

        compiled = original_build(req, object_info=info)
        route_id = str(compiled.get("route_id") or "")
        workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
        model_ref, chunk_node_id = _find_chunk_anchor(compiled)

        profile = build_single_model_lora_patch_profile(
            route_id=route_id,
            compiler=compiler_name,
            model_ref=model_ref,
            model_consumers=[{"node_id": chunk_node_id, "input": "model"}],
            loader_type="model_only",
            loader_node_class=LTX_MODEL_ONLY_LOADER,
            validated=".unet." in route_id,
            notes=[
                "Phase 7 LTX anchor is the model input immediately upstream of LTXVChunkFeedForward.",
                "Phase 7 enables standard model-only LoRAs for LTX 2.3 UNET Txt2Vid and Img2Vid only.",
            ],
        )
        compiled["lora_patch_profile"] = profile

        if rows:
            if ".unet." not in route_id:
                raise ValueError("LTX 2.3 GGUF Video LoRA remains fail-closed until GGUF LoRA-loader compatibility is validated.")
            model_only_class = _actual_class(info, LTX_MODEL_ONLY_LOADER)
            live_loras = _lora_catalog(info, model_only_class)
            _validate_live_catalog(rows, live_loras, bool(model_only_class))
            patched_workflow, runtime = apply_ltx_model_only_lora_stack(
                workflow,
                profile,
                rows,
                route_id=route_id,
                loader_available=bool(model_only_class),
            )
            compiled["workflow"] = patched_workflow
            prompt_payload = compiled.get("prompt_api_payload") if isinstance(compiled.get("prompt_api_payload"), dict) else {}
            prompt_payload["prompt"] = patched_workflow
            compiled["prompt_api_payload"] = prompt_payload
        else:
            runtime = {
                "schema_version": _RUNTIME_SCHEMA,
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

        runtime["manual_selection_classifier_gate"] = False
        runtime["generic_lora_loader_fallback"] = False
        runtime["live_catalog_validated"] = bool(rows)
        runtime["phase"] = "phase_7"
        compiled["video_lora_stack"] = runtime

        rules = compiled.get("rules") if isinstance(compiled.get("rules"), list) else []
        rules.extend(
            [
                "LTX 2.3 UNET Txt2Vid/Img2Vid standard LoRAs use the universal Video LoRA stack and a compiler-owned model-only patch profile.",
                "Phase 7 rejects LTX speed/Turbo roles, WAN-only high/low targets, generic LoraLoader fallback, and LTX GGUF LoRA injection.",
                "Selected LTX LoRA files must exist in the live LoraLoaderModelOnly catalog before the workflow is accepted.",
            ]
        )
        compiled["rules"] = list(dict.fromkeys(str(rule) for rule in rules if str(rule)))
        return compiled

    def phase7_compile(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
        token = _PHASE7_PAYLOAD.set(dict(payload or {}))
        try:
            return original_compile(payload, object_info_override=object_info_override)
        finally:
            _PHASE7_PAYLOAD.reset(token)

    setattr(module, build_name, phase7_build)
    setattr(module, compile_name, phase7_compile)


def install_ltx_lora_integration() -> None:
    """Install Phase-7 LTX UNET standard-LoRA integration once per Python process."""
    global _INSTALLED
    if _INSTALLED:
        return

    from neo_app.video import ltx_img2vid_compiler as img2vid
    from neo_app.video import ltx_txt2vid_compiler as txt2vid

    if getattr(txt2vid, "_neo_phase7_video_lora_installed", False) and getattr(
        img2vid, "_neo_phase7_video_lora_installed", False
    ):
        _INSTALLED = True
        return

    _wrap_ltx_compiler(
        txt2vid,
        build_name="build_ltx23_txt2vid_workflow",
        compile_name="video_ltx23_txt2vid_compile_payload",
        compiler_name="neo_app.video.ltx_txt2vid_compiler",
    )
    _wrap_ltx_compiler(
        img2vid,
        build_name="build_ltx23_img2vid_workflow",
        compile_name="video_ltx23_img2vid_compile_payload",
        compiler_name="neo_app.video.ltx_img2vid_compiler",
    )

    txt2vid._neo_phase7_video_lora_installed = True
    img2vid._neo_phase7_video_lora_installed = True
    _INSTALLED = True
