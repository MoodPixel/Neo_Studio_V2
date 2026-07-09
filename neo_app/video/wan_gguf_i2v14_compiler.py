from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import random
from pathlib import Path
from typing import Any, Final
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.gguf_loader_adapter import build_wan22_gguf_loader_plan
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import register_video_generation_result
from neo_app.video.performance_adapter import build_video_performance_adapter_payload
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader
from neo_app.video.sage_attention_adapter import build_sage_attention_node_inputs, build_wan22_sage_attention_plan
from neo_app.video.teacache_adapter import build_teacache_node_inputs, build_wan22_teacache_plan
from neo_app.video.low_vram_adapter import build_vae_decode_inputs, build_wan_block_swap_node_inputs, build_wan22_low_vram_plan
from neo_app.video.comfy_input_handoff import prepare_video_source_image_handoff, resolve_video_source_image_path
from neo_app.video.runtime_preflight import apply_wan22_gguf_first_test_preset, video_runtime_preflight_payload
from neo_app.video.video_lora_adapter import build_lora_node_inputs, build_wan22_video_lora_plan
from neo_app.video.vram_engine import apply_video_vram_engine

SCHEMA_VERSION: Final[str] = "neo.video.wan22.gguf_i2v14.compiler.vg13"
SUPPORTED_ROUTE_ID: Final[str] = "wan22.gguf.img2vid_14b_dual_noise"
DEFAULT_TEMPLATE_NAME: Final[str] = "wan22_i2v14_dual_noise_native"
TEMPLATE_PATH: Final[Path] = Path(__file__).resolve().parent / "workflows" / f"{DEFAULT_TEMPLATE_NAME}.json"

DEFAULT_NEGATIVE_PROMPT: Final[str] = (
    "flicker, jitter, unstable motion, static frame, warped anatomy, malformed hands, extra limbs, "
    "morphing face, broken eyes, blurry, low resolution, compression artifacts, text, logos, watermark"
)

FALLBACK_MODELS: Final[dict[str, str]] = {
    "high_noise": "wan2.2_i2v_high_noise_14B_Q4_K_M.gguf",
    "low_noise": "wan2.2_i2v_low_noise_14B_Q4_K_M.gguf",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
    "high_noise_lora": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "low_noise_lora": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
}

@dataclass(frozen=True)
class WanGgufI2V14CompileRequest:
    family: str = "wan22"
    loader: str = "gguf"
    generation_type: str = "img2vid"
    prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    vram_profile: str = "low"
    width: int | None = None
    height: int | None = None
    frames: int | None = None
    fps: float | int | None = None
    steps: int | None = None
    guidance: float | None = None
    split_step: int | None = None
    seed: int | None = None
    sampler: str | None = None
    scheduler: str | None = None
    source_image: str | None = None
    source_image_name: str | None = None
    source_image_comfy_name: str | None = None
    comfy_source_image_name: str | None = None
    resize_mode: str = "fit_crop"
    image_strength: float | None = None
    high_noise_model: str | None = None
    low_noise_model: str | None = None
    wan_high_noise_gguf: str | None = None
    wan_low_noise_gguf: str | None = None
    gguf_high_noise_model: str | None = None
    gguf_low_noise_model: str | None = None
    high_noise_gguf_name: str | None = None
    low_noise_gguf_name: str | None = None
    unet_name: str | None = None
    model_name: str | None = None
    clip_name: str | None = None
    vae_name: str | None = None
    enable_lightx2v: bool = False
    enable_video_lora: bool = False
    video_lora_mode: str = "off"
    video_lora_model: str | None = None
    video_lora_strength: float | None = None
    video_lora_target: str = "both"
    high_noise_lora: str | None = None
    low_noise_lora: str | None = None
    high_noise_lora_strength: float | None = None
    low_noise_lora_strength: float | None = None
    output_format: str = "auto"
    output_codec: str = "auto"
    filename_prefix: str = "Neo_Video_WAN22_GGUF_I2V14"
    profile_id: str | None = None
    dry_run: bool = True
    first_test_mode: bool = False
    queue_preflight: bool = True
    allow_manual_danger: bool = False
    performance_profile: str = "safe_12gb"
    enable_sage_attention: bool = False
    sage_attention_mode: str = "auto"
    sage_attention_target: str = "both"
    enable_teacache: bool = False
    teacache_profile: str = "conservative"
    teacache_target: str = "both"
    enable_cpu_offload: bool = False
    enable_vae_offload: bool = False
    enable_block_swap: bool = False
    block_swap_target: str = "both"
    block_swap_blocks: int | None = None
    enable_torch_compile: bool = False
    preserve_user_overrides: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "WanGgufI2V14CompileRequest":
        data = payload or {}
        generation_type = data.get("generation_type", data.get("mode", "img2vid"))
        return cls(
            family=str(data.get("family", "wan22") or "wan22"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(generation_type or "img2vid"),
            prompt=str(data.get("prompt", data.get("positive_prompt", "")) or ""),
            negative_prompt=str(data.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT),
            vram_profile=str(data.get("vram_profile", "low") or "low"),
            width=_int_or_none(data.get("width")),
            height=_int_or_none(data.get("height")),
            frames=_int_or_none(data.get("frames", data.get("length"))),
            fps=_float_or_none(data.get("fps")),
            steps=_int_or_none(data.get("steps")),
            guidance=_float_or_none(data.get("guidance", data.get("cfg"))),
            split_step=_int_or_none(data.get("split_step", data.get("splitStep"))),
            seed=_int_or_none(data.get("seed")),
            sampler=str(data.get("sampler") or "") or None,
            scheduler=str(data.get("scheduler") or "") or None,
            source_image=str(data.get("source_image", data.get("image", "")) or "") or None,
            source_image_name=str(data.get("source_image_comfy_name", data.get("comfy_source_image_name", data.get("source_image_name", data.get("image_name", "")))) or "") or None,
            source_image_comfy_name=_string_or_none(data.get("source_image_comfy_name")),
            comfy_source_image_name=_string_or_none(data.get("comfy_source_image_name")),
            resize_mode=str(data.get("resize_mode", "fit_crop") or "fit_crop"),
            image_strength=_float_or_none(data.get("image_strength", data.get("denoise"))),
            high_noise_model=_string_or_none(data.get("high_noise_model", data.get("wan_high_noise_gguf"))),
            low_noise_model=_string_or_none(data.get("low_noise_model", data.get("wan_low_noise_gguf"))),
            wan_high_noise_gguf=_string_or_none(data.get("wan_high_noise_gguf")),
            wan_low_noise_gguf=_string_or_none(data.get("wan_low_noise_gguf")),
            gguf_high_noise_model=_string_or_none(data.get("gguf_high_noise_model")),
            gguf_low_noise_model=_string_or_none(data.get("gguf_low_noise_model")),
            high_noise_gguf_name=_string_or_none(data.get("high_noise_gguf_name")),
            low_noise_gguf_name=_string_or_none(data.get("low_noise_gguf_name")),
            unet_name=_string_or_none(data.get("unet_name")),
            model_name=_string_or_none(data.get("model_name")),
            clip_name=_string_or_none(data.get("clip_name", data.get("text_encoder"))),
            vae_name=_string_or_none(data.get("vae_name")),
            enable_lightx2v=bool(data.get("enable_lightx2v", data.get("use_lightx2v", data.get("lightx2v", False)))),
            enable_video_lora=bool(data.get("enable_video_lora", data.get("video_lora_enabled", False))),
            video_lora_mode=str(data.get("video_lora_mode", data.get("lora_mode", "off")) or "off"),
            video_lora_model=_string_or_none(data.get("video_lora_model", data.get("lora_model"))),
            video_lora_strength=_float_or_none(data.get("video_lora_strength", data.get("lora_strength"))),
            video_lora_target=str(data.get("video_lora_target", data.get("lora_target", "both")) or "both"),
            high_noise_lora=_string_or_none(data.get("high_noise_lora")),
            low_noise_lora=_string_or_none(data.get("low_noise_lora")),
            high_noise_lora_strength=_float_or_none(data.get("high_noise_lora_strength")),
            low_noise_lora_strength=_float_or_none(data.get("low_noise_lora_strength")),
            output_format=str(data.get("output_format", "auto") or "auto"),
            output_codec=str(data.get("output_codec", data.get("codec", "auto")) or "auto"),
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_WAN22_GGUF_I2V14") or "Neo_Video_WAN22_GGUF_I2V14"),
            profile_id=str(data.get("profile_id", "") or "") or None,
            dry_run=bool(data.get("dry_run", True)),
            first_test_mode=bool(data.get("first_test_mode", data.get("safe_test", data.get("queue_test", False)))),
            queue_preflight=bool(data.get("queue_preflight", True)),
            allow_manual_danger=bool(data.get("allow_manual_danger", False)),
            performance_profile=str(data.get("performance_profile", "safe_12gb") or "safe_12gb"),
            enable_sage_attention=bool(data.get("enable_sage_attention", False)),
            sage_attention_mode=str(data.get("sage_attention_mode", "auto") or "auto"),
            sage_attention_target=str(data.get("sage_attention_target", "both") or "both"),
            enable_teacache=bool(data.get("enable_teacache", False)),
            teacache_profile=str(data.get("teacache_profile", "conservative") or "conservative"),
            teacache_target=str(data.get("teacache_target", "both") or "both"),
            enable_cpu_offload=bool(data.get("enable_cpu_offload", False)),
            enable_vae_offload=bool(data.get("enable_vae_offload", False)),
            enable_block_swap=bool(data.get("enable_block_swap", False)),
            block_swap_target=str(data.get("block_swap_target", data.get("low_vram_target", "both")) or "both"),
            block_swap_blocks=_int_or_none(data.get("block_swap_blocks", data.get("blocks_to_swap"))),
            enable_torch_compile=bool(data.get("enable_torch_compile", False)),
            preserve_user_overrides=bool(data.get("preserve_user_overrides", False)),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _seed(value: int | None) -> int:
    if value is None or value < 0:
        return random.randint(0, 2_147_483_647)
    return max(0, min(int(value), 9_999_999_999_999))


def _class_exists(object_info: dict[str, Any], *candidates: str) -> str | None:
    folded = {str(key).casefold(): str(key) for key in object_info.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return None


def _required_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    return required if isinstance(required, dict) else {}


def _optional_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    optional = inputs.get("optional", {}) if isinstance(inputs, dict) else {}
    return optional if isinstance(optional, dict) else {}


def _combo_values(object_info: dict[str, Any], class_type: str, input_name: str) -> list[str]:
    for inputs in (_required_inputs(object_info, class_type), _optional_inputs(object_info, class_type)):
        spec = inputs.get(input_name)
        if isinstance(spec, list) and spec:
            values = spec[0]
            if isinstance(values, list):
                return [str(item) for item in values]
    return []


def _field_name(inputs: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    folded = {str(key).casefold(): str(key) for key in inputs.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


def _first_matching(values: list[str], needles: tuple[str, ...], fallback: str) -> str:
    if not values:
        return fallback
    lowered = [(value, value.casefold()) for value in values]
    for needle in needles:
        hit = next((value for value, low in lowered if needle.casefold() in low), None)
        if hit:
            return hit
    return values[0]


def _comfy_image_name(source_image: str | None, source_image_name: str | None = None) -> str:
    # Comfy LoadImage requires a filename that exists inside ComfyUI/input.
    # The generate path now uploads Neo-owned sources through /upload/image and
    # passes the returned filename as source_image_name. Keep the original
    # source_image path only for Neo metadata/preflight.
    candidate = Path(str(source_image_name or source_image or "")).name
    return sanitize_path_part(candidate, fallback="neo_video_source.png")


def _load_template(path: Path = TEMPLATE_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Workflow template is not a ComfyUI object graph: {path}")
    return data


def discover_wan22_gguf_i2v14_bindings(
    object_info: dict[str, Any] | None = None,
    *,
    high_noise_model: str | None = None,
    low_noise_model: str | None = None,
    clip_name: str | None = None,
    vae_name: str | None = None,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
) -> dict[str, Any]:
    plan = build_wan22_gguf_loader_plan(
        object_info or {},
        fallback_models=FALLBACK_MODELS,
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        clip_name=clip_name,
        vae_name=vae_name,
        high_noise_lora=high_noise_lora,
        low_noise_lora=low_noise_lora,
    )
    return plan.payload()


def _apply_vram_safety(req: WanGgufI2V14CompileRequest) -> dict[str, Any]:
    engine = apply_video_vram_engine(
        {**req.payload(), "preserve_user_overrides": req.preserve_user_overrides},
        family=req.family,
        loader=req.loader,
        generation_type=req.generation_type,
        vram_profile=req.vram_profile,
    )
    values = dict(engine.get("normalized_parameters") or {})
    fps = float(values.get("fps") or 12)
    frames = int(values.get("frames") or 49)
    steps = max(1, int(values.get("steps") or 12))
    split_step = req.split_step if req.split_step is not None else values.get("split_step")
    try:
        split = int(float(split_step)) if split_step is not None else max(1, steps // 2)
    except (TypeError, ValueError):
        split = max(1, steps // 2)
    split = max(1, min(split, max(1, steps - 1))) if steps > 1 else 1
    return {
        "width": int(values.get("width") or 640),
        "height": int(values.get("height") or 360),
        "frames": frames,
        "fps": fps,
        "duration": max(0.1, (frames - 1) / fps) if fps > 0 else 3.0,
        "steps": steps,
        "split_step": split,
        "guidance": float(values.get("guidance") if values.get("guidance") is not None else 3.5),
        "seed": _seed(req.seed if req.seed is not None else values.get("seed")),
        "sampler": str(values.get("sampler") or "euler"),
        "scheduler": str(values.get("scheduler") or "simple"),
        "batch_count": 1,
        "decode_mode": str(values.get("decode_mode") or "tiled"),
        "tile_size": int(values.get("tile_size") or 384),
        "temporal_tile_size": int(values.get("temporal_tile_size") or 4096),
        "vram_profile": values.get("vram_profile") or req.vram_profile,
        "vram_engine": engine,
    }


def _loader_inputs(object_info: dict[str, Any], class_type: str, model_field: str, model_name: str) -> dict[str, Any]:
    known_inputs = {**_required_inputs(object_info, class_type), **_optional_inputs(object_info, class_type)}
    inputs = {model_field: model_name}
    if "weight_dtype" in known_inputs:
        values = _combo_values(object_info, class_type, "weight_dtype")
        inputs["weight_dtype"] = "default" if "default" in values or not values else values[0]
    return inputs


def _clip_inputs(object_info: dict[str, Any], class_type: str, field_name: str, clip_name: str) -> dict[str, Any]:
    known_inputs = {**_required_inputs(object_info, class_type), **_optional_inputs(object_info, class_type)}
    inputs: dict[str, Any] = {field_name: clip_name}

    # WAN text encoders such as UMT5 must be loaded with CLIPLoader type="wan".
    # If the type field is omitted or replaced with Comfy's first/default CLIP type, Comfy falls
    # back to SD1ClipModel and execution fails with:
    #   clip missing ... / Requested to load SD1ClipModel / Cannot copy out of meta tensor; no data!
    # This WAN GGUF I2V compiler is route-specific, so "wan" is the only safe type. Do not
    # downgrade to type_values[0] even if a stale/custom /object_info dropdown omits "wan".
    inputs["type"] = "wan"

    device_values = _combo_values(object_info, class_type, "device")
    folded_device_values = [value.casefold() for value in device_values]
    if not device_values or "default" in folded_device_values:
        inputs["device"] = "default"
    elif "device" in known_inputs:
        inputs["device"] = device_values[0]
    else:
        inputs["device"] = "default"
    return inputs


def _vae_inputs(object_info: dict[str, Any], class_type: str, field_name: str, vae_name: str) -> dict[str, Any]:
    known_inputs = {**_required_inputs(object_info, class_type), **_optional_inputs(object_info, class_type)}
    inputs: dict[str, Any] = {field_name: vae_name}
    if "device" in known_inputs:
        values = _combo_values(object_info, class_type, "device")
        inputs["device"] = "default" if "default" in values or not values else values[0]
    return inputs


def _lora_inputs(object_info: dict[str, Any], class_type: str, lora_field: str, lora_name: str, model_link: list[Any]) -> dict[str, Any]:
    known_inputs = {**_required_inputs(object_info, class_type), **_optional_inputs(object_info, class_type)}
    inputs: dict[str, Any] = {"model": model_link, lora_field: lora_name}
    if "strength_model" in known_inputs:
        inputs["strength_model"] = 1.0
    return inputs


def _remove_nodes(workflow: dict[str, Any], *node_ids: str) -> None:
    for node_id in node_ids:
        workflow.pop(node_id, None)


def _wan_clip_loader_guard(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return a compact safety report for the WAN text encoder node.

    This catches stale prompt graphs before /prompt queueing. The native Wan template must use
    CLIPLoader with type=wan; any SD/SD1 type will crash in CLIPTextEncode before sampling.
    """
    node = workflow.get("129:84") if isinstance(workflow, dict) else {}
    inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
    class_type = str(node.get("class_type") or "") if isinstance(node, dict) else ""
    clip_type = str(inputs.get("type") or "") if isinstance(inputs, dict) else ""
    clip_name = str(inputs.get("clip_name") or inputs.get("text_encoder_name") or inputs.get("clip_name1") or "") if isinstance(inputs, dict) else ""
    ok = class_type == "CLIPLoader" and clip_type == "wan"
    errors = [] if ok else [
        f"WAN text encoder must use CLIPLoader type='wan'; got class='{class_type or 'missing'}' type='{clip_type or 'missing'}'."
    ]
    return {
        "schema_version": "neo.video.wan22.clip_loader_guard.vg13_7",
        "ok": ok,
        "class_type": class_type,
        "clip_type": clip_type,
        "clip_name": clip_name,
        "errors": errors,
        "action_items": [] if ok else ["Refresh backend probe and recompile; Neo should patch CLIP node 129:84 to CLIPLoader with type='wan'."],
    }


def build_wan22_gguf_i2v14_workflow(req: WanGgufI2V14CompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    # V-G4 keeps the dual-noise pair explicit. Generic model_name/unet_name is intentionally
    # not copied into both slots because that silently breaks the high/low sampler split.
    high_override = req.high_noise_model or req.wan_high_noise_gguf or req.gguf_high_noise_model or req.high_noise_gguf_name
    low_override = req.low_noise_model or req.wan_low_noise_gguf or req.gguf_low_noise_model or req.low_noise_gguf_name
    adapter_plan = build_wan22_gguf_loader_plan(
        info,
        fallback_models=FALLBACK_MODELS,
        high_noise_model=high_override,
        low_noise_model=low_override,
        clip_name=req.clip_name,
        vae_name=req.vae_name,
        high_noise_lora=req.high_noise_lora,
        low_noise_lora=req.low_noise_lora,
    )
    bindings = adapter_plan.payload()
    classes = bindings["classes"]
    fields = bindings["fields"]
    models = dict(bindings["models"])
    dual_noise_mapping = bindings.get("adapter", {}).get("dual_noise_mapping", {})

    lora_plan = build_wan22_video_lora_plan(
        object_info=info,
        adapter_bindings=bindings,
        enable_video_lora=req.enable_video_lora,
        video_lora_mode=req.video_lora_mode,
        video_lora_model=req.video_lora_model,
        video_lora_strength=req.video_lora_strength,
        video_lora_target=req.video_lora_target,
        enable_lightx2v=req.enable_lightx2v,
        high_noise_lora=req.high_noise_lora,
        low_noise_lora=req.low_noise_lora,
        high_noise_lora_strength=req.high_noise_lora_strength,
        low_noise_lora_strength=req.low_noise_lora_strength,
    )
    lora_payload = lora_plan.payload()
    performance_adapter = build_video_performance_adapter_payload(
        req.payload(),
        object_info=info,
        family=req.family,
        loader=req.loader,
        generation_type=req.generation_type,
        route_id=SUPPORTED_ROUTE_ID,
    )

    params = _apply_vram_safety(req)
    sampling_override_report: dict[str, Any] = {"mode": "none", "applied": {}, "preserved": {}, "recommended": {}}
    if lora_plan.sampling_overrides:
        overrides = lora_plan.sampling_overrides
        sampling_override_report["mode"] = "recommendation_only" if req.preserve_user_overrides else "legacy_cap"
        sampling_override_report["recommended"] = {key: value for key, value in overrides.items() if key in {"steps", "guidance", "split_step"}}
        if req.preserve_user_overrides:
            # Phase 10m: LightX2V/Lightning LoRAs may recommend 4/1.0/2, but they must not
            # silently overwrite user-entered WAN dual-noise sampling values from the UI.
            for key in ("steps", "guidance", "split_step"):
                if key in overrides:
                    sampling_override_report["preserved"][key] = params.get(key)
        else:
            if "steps" in overrides:
                before = params["steps"]
                params["steps"] = min(params["steps"], int(overrides["steps"]))
                if params["steps"] != before:
                    sampling_override_report["applied"]["steps"] = {"from": before, "to": params["steps"]}
            if "guidance" in overrides:
                before = params["guidance"]
                params["guidance"] = min(params["guidance"], float(overrides["guidance"]))
                if params["guidance"] != before:
                    sampling_override_report["applied"]["guidance"] = {"from": before, "to": params["guidance"]}
            if "split_step" in overrides:
                before = params["split_step"]
                params["split_step"] = min(max(1, int(params["split_step"])), int(overrides["split_step"]))
                if params["split_step"] != before:
                    sampling_override_report["applied"]["split_step"] = {"from": before, "to": params["split_step"]}

    prompt = req.prompt.strip() or "A short cinematic image-to-video clip with smooth natural motion."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    prefix = sanitize_path_part(req.filename_prefix or "Neo_Video_WAN22_GGUF_I2V14", fallback="Neo_Video_WAN22_GGUF_I2V14")
    workflow = deepcopy(_load_template())

    # V-G4 loader adapter: replace the two safetensor UNET loaders with schema-aware GGUF nodes.
    workflow["129:95"] = adapter_plan.high_node.node("GGUF Loader · WAN 2.2 high-noise model")
    workflow["129:96"] = adapter_plan.low_node.node("GGUF Loader · WAN 2.2 low-noise model")

    workflow["129:84"] = {
        "class_type": classes["clip_loader"],
        "inputs": _clip_inputs(info, classes["clip_loader"], fields["clip_name"], models["clip_name"]),
        "_meta": {"title": "CLIP Loader · WAN text encoder"},
    }
    workflow["129:90"] = {
        "class_type": classes["vae_loader"],
        "inputs": _vae_inputs(info, classes["vae_loader"], fields["vae_name"], models["vae_name"]),
        "_meta": {"title": "VAE Loader · WAN video VAE"},
    }

    workflow["97"]["inputs"]["image"] = _comfy_image_name(req.source_image, req.source_image_name)
    workflow["129:93"]["inputs"]["text"] = prompt
    workflow["129:89"]["inputs"]["text"] = negative
    workflow["129:98"]["inputs"].update({
        "width": params["width"],
        "height": params["height"],
        "length": params["frames"],
        "batch_size": 1,
    })
    workflow["129:94"]["inputs"]["fps"] = params["fps"]
    workflow["108"]["inputs"].update({
        "filename_prefix": f"video/{prefix}",
        "format": req.output_format or "auto",
        "codec": req.output_codec or "auto",
    })

    high_model_link: list[Any] = ["129:95", 0]
    low_model_link: list[Any] = ["129:96", 0]
    _remove_nodes(workflow, "129:101", "129:102", "9001", "9002")
    for branch in lora_plan.branches:
        workflow[branch.node_id] = {
            "class_type": lora_plan.lora_loader,
            "inputs": build_lora_node_inputs(
                info,
                lora_plan.lora_loader,
                lora_plan.lora_field,
                branch.lora_name,
                branch.source_model_link,
                strength=branch.strength_model,
            ),
            "_meta": {"title": f"Video LoRA · {lora_plan.mode} · {branch.role}"},
        }
        if branch.role == "high_noise":
            high_model_link = branch.output_model_link
        if branch.role == "low_noise":
            low_model_link = branch.output_model_link

    sage_plan = build_wan22_sage_attention_plan(
        object_info=info,
        enable_sage_attention=req.enable_sage_attention,
        sage_attention_mode=req.sage_attention_mode,
        sage_attention_target=req.sage_attention_target,
        high_model_link=high_model_link,
        low_model_link=low_model_link,
    )
    sage_payload = sage_plan.payload()
    for branch in sage_plan.branches:
        workflow[branch.node_id] = {
            "class_type": sage_plan.class_type,
            "inputs": build_sage_attention_node_inputs(
                info,
                sage_plan.class_type,
                model_link=branch.source_model_link,
                mode=sage_plan.mode,
                model_field=sage_plan.model_field,
                mode_field=sage_plan.mode_field,
            ),
            "_meta": {"title": f"Sage Attention KJ · {branch.role}"},
        }
        if branch.role == "high_noise":
            high_model_link = branch.output_model_link
        if branch.role == "low_noise":
            low_model_link = branch.output_model_link

    teacache_plan = build_wan22_teacache_plan(
        object_info=info,
        enable_teacache=req.enable_teacache,
        teacache_profile=req.teacache_profile,
        teacache_target=req.teacache_target,
        high_model_link=high_model_link,
        low_model_link=low_model_link,
    )
    teacache_payload = teacache_plan.payload()
    for branch in teacache_plan.branches:
        workflow[branch.node_id] = {
            "class_type": teacache_plan.class_type,
            "inputs": build_teacache_node_inputs(
                info,
                teacache_plan.class_type,
                model_link=branch.source_model_link,
                profile=teacache_plan.profile,
                model_field=teacache_plan.model_field,
            ),
            "_meta": {"title": f"TeaCache · {teacache_plan.profile} · {branch.role}"},
        }
        if branch.role == "high_noise":
            high_model_link = branch.output_model_link
        if branch.role == "low_noise":
            low_model_link = branch.output_model_link

    low_vram_plan = build_wan22_low_vram_plan(
        object_info=info,
        enable_cpu_offload=req.enable_cpu_offload,
        enable_vae_offload=req.enable_vae_offload,
        enable_block_swap=req.enable_block_swap,
        block_swap_target=req.block_swap_target,
        block_swap_blocks=req.block_swap_blocks,
        high_model_link=high_model_link,
        low_model_link=low_model_link,
    )
    low_vram_payload = low_vram_plan.payload()
    for branch in low_vram_plan.branches:
        workflow[branch.node_id] = {
            "class_type": low_vram_plan.class_type,
            "inputs": build_wan_block_swap_node_inputs(
                info,
                low_vram_plan.class_type,
                model_link=branch.source_model_link,
                model_field=low_vram_plan.model_field,
                blocks_to_swap=low_vram_plan.blocks_to_swap,
                cpu_offload=low_vram_plan.cpu_offload_enabled or low_vram_plan.block_swap_enabled,
            ),
            "_meta": {"title": f"Low-VRAM Block Swap · {branch.role}"},
        }
        if branch.role == "high_noise":
            high_model_link = branch.output_model_link
        if branch.role == "low_noise":
            low_model_link = branch.output_model_link

    if low_vram_plan.vae_decode_tiled:
        workflow["129:87"] = {
            "class_type": low_vram_plan.vae_decode_class_type,
            "inputs": build_vae_decode_inputs(
                info,
                low_vram_plan.vae_decode_class_type,
                samples_link=["129:85", 0],
                vae_link=["129:90", 0],
                tile_size=int(params.get("tile_size") or 384),
                temporal_tile_size=int(params.get("temporal_tile_size") or 4096),
            ),
            "_meta": {"title": "VAE Decode · tiled / low-VRAM"},
        }

    # Remove switch/math/primitive helper graph and patch literals. This makes the primary GGUF route
    # independent from optional switch/math node packs while preserving the uploaded workflow topology.
    _remove_nodes(
        workflow,
        "129:116", "129:117", "129:118", "129:119", "129:120", "129:122", "129:124",
        "129:125", "129:126", "129:127", "129:128", "129:131", "129:161", "129:162", "129:163",
    )
    workflow["129:104"]["inputs"].update({"model": high_model_link, "shift": 5.0})
    workflow["129:103"]["inputs"].update({"model": low_model_link, "shift": 5.0})
    workflow["129:86"]["inputs"].update({
        "noise_seed": params["seed"],
        "steps": params["steps"],
        "cfg": params["guidance"],
        "sampler_name": params["sampler"],
        "scheduler": params["scheduler"],
        "start_at_step": 0,
        "end_at_step": params["split_step"],
        "model": ["129:104", 0],
    })
    workflow["129:85"]["inputs"].update({
        "steps": params["steps"],
        "cfg": params["guidance"],
        "sampler_name": params["sampler"],
        "scheduler": params["scheduler"],
        "start_at_step": params["split_step"],
        "end_at_step": params["steps"],
        "model": ["129:103", 0],
    })

    client_id = f"neo-video-vg13-{uuid4().hex[:10]}"

    clip_loader_guard = _wan_clip_loader_guard(workflow)

    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": "V-G13",
        "route_id": SUPPORTED_ROUTE_ID,
        "template": DEFAULT_TEMPLATE_NAME,
        "compiled_at": _now(),
        "parameters": {key: value for key, value in params.items() if key != "profile"},
        "vram_engine": params.get("vram_engine", {}),
        "bindings": bindings,
        "adapter_diagnostics": bindings.get("adapter", {}).get("diagnostics", []),
        "dual_noise_mapping": dual_noise_mapping,
        "video_lora_adapter": lora_payload,
        "sampling_override_report": sampling_override_report,
        "user_override_preservation_schema": "neo.video.wan22.gguf_i2v14.user_override_preservation.v25_9_19_phase10m",
        "sage_attention_adapter": sage_payload,
        "teacache_adapter": teacache_payload,
        "low_vram_adapter": low_vram_payload,
        "performance_adapter": performance_adapter,
        "clip_loader_guard": clip_loader_guard,
        "selected_models": {
            "high_noise_model": models["high_noise_model"],
            "low_noise_model": models["low_noise_model"],
            "dual_noise_ready": bool(dual_noise_mapping.get("ready", True)),
            "dual_noise_swap_applied": bool(dual_noise_mapping.get("swap_applied", False)),
            "clip_name": models["clip_name"],
            "vae_name": models["vae_name"],
            "performance_profile": performance_adapter.get("selected", {}).get("performance_profile", req.performance_profile),
            "enable_sage_attention": sage_plan.enabled,
            "sage_attention_mode": sage_plan.mode,
            "sage_attention_target": sage_plan.target,
            "enable_teacache": teacache_plan.enabled,
            "teacache_profile": teacache_plan.profile,
            "teacache_target": teacache_plan.target,
            "enable_cpu_offload": low_vram_plan.cpu_offload_enabled,
            "enable_vae_offload": low_vram_plan.vae_offload_enabled,
            "enable_block_swap": low_vram_plan.block_swap_enabled,
            "block_swap_target": low_vram_plan.block_swap_target,
            "block_swap_blocks": low_vram_plan.blocks_to_swap,
            "enable_lightx2v": lora_plan.mode == "lightx2v_4step",
            "enable_video_lora": lora_plan.enabled,
            "video_lora_mode": lora_plan.mode,
            "video_lora_model": lora_payload.get("selected", {}).get("video_lora_model", ""),
            "video_lora_target": lora_plan.target,
            "high_noise_lora": lora_payload.get("selected", {}).get("high_noise_lora", "") if lora_plan.enabled else "",
            "low_noise_lora": lora_payload.get("selected", {}).get("low_noise_lora", "") if lora_plan.enabled else "",
        },
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow, "client_id": client_id},
        "client_id": client_id,
        "source": {
            "required": True,
            "source_image": req.source_image or "",
            "source_image_name": req.source_image_name or "",
            "comfy_image_name": _comfy_image_name(req.source_image, req.source_image_name),
            "image_strength": req.image_strength if req.image_strength is not None else 0.7,
            "resize_mode": req.resize_mode,
        },
        "rules": [
            "V-G13 uses the uploaded Wan 2.2 14B I2V graph as a native template with queue-safe preflight, Video LoRA, Sage Attention, TeaCache, and low-VRAM offload/block-swap adapters.",
            "The loader layer is adapted through a schema-aware GGUF adapter that reads ComfyUI /object_info.",
            "High-noise and low-noise GGUF models are mapped as an explicit validated pair; do not collapse them into one model field.",
            "Switch/math helper nodes are removed; Video LoRA nodes are added only when Normal LoRA or LightX2V 4-step mode is explicitly enabled.",
            "TeaCache and low-VRAM block-swap/offload are active and patch the final model branches after LoRA/Sage before ModelSamplingSD3.",
            "LightX2V 4-step mode auto-patches steps=4, CFG=1.0, and split_step=2 before queueing.",
            "Batch count stays locked to 1 and V10/V-G13 VRAM guards run before compile; V-G7 runtime preflight blocks unsafe /prompt queueing.",
        ],
    }


def _post_json(base_url: str, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NeoStudioWanGgufI2V14Compiler/1.0"}, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local user-configured Comfy URL.
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data) if data else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _attach_video_output_record(result: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        ledger = register_video_generation_result(result, request=request_payload or {})
    except Exception as exc:  # noqa: BLE001 - output ledger must not block compile/generate response.
        return {**result, "neo_persisted": {"ok": False, "error": f"Video output ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def _compile_error(message: str, req: WanGgufI2V14CompileRequest, route: Any | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": "V-G13",
        "ok": False,
        "queued": False,
        "error": message,
        "request": req.payload(),
        "route": route.payload() if route else None,
    }


def video_wan22_gguf_i2v14_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    effective_payload = apply_wan22_gguf_first_test_preset(payload if isinstance(payload, dict) else {})
    req = WanGgufI2V14CompileRequest.from_payload(effective_payload)
    nf = normalize_video_family(req.family)
    nl = normalize_video_loader(req.loader)
    nt = normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id != SUPPORTED_ROUTE_ID:
        return _compile_error(f"V-G13 WAN GGUF compiler only supports {SUPPORTED_ROUTE_ID}.", req, route)
    if not req.source_image:
        return _compile_error("WAN 2.2 GGUF Img2Vid 14B requires a source_image from the Video source upload/select panel.", req, route)

    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback GGUF bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}

    compiled = build_wan22_gguf_i2v14_workflow(req, object_info=object_info)
    for diagnostic in compiled.get("adapter_diagnostics", []):
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    lora_probe = compiled.get("video_lora_adapter", {}) if isinstance(compiled.get("video_lora_adapter"), dict) else {}
    for diagnostic in lora_probe.get("warnings", []) if isinstance(lora_probe.get("warnings"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    for diagnostic in lora_probe.get("errors", []) if isinstance(lora_probe.get("errors"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    sage_probe = compiled.get("sage_attention_adapter", {}) if isinstance(compiled.get("sage_attention_adapter"), dict) else {}
    for diagnostic in sage_probe.get("warnings", []) if isinstance(sage_probe.get("warnings"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    for diagnostic in sage_probe.get("errors", []) if isinstance(sage_probe.get("errors"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    teacache_probe = compiled.get("teacache_adapter", {}) if isinstance(compiled.get("teacache_adapter"), dict) else {}
    for diagnostic in teacache_probe.get("warnings", []) if isinstance(teacache_probe.get("warnings"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    for diagnostic in teacache_probe.get("errors", []) if isinstance(teacache_probe.get("errors"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    low_vram_probe = compiled.get("low_vram_adapter", {}) if isinstance(compiled.get("low_vram_adapter"), dict) else {}
    for diagnostic in low_vram_probe.get("warnings", []) if isinstance(low_vram_probe.get("warnings"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    for diagnostic in low_vram_probe.get("errors", []) if isinstance(low_vram_probe.get("errors"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    perf_probe = compiled.get("performance_adapter", {}) if isinstance(compiled.get("performance_adapter"), dict) else {}
    for diagnostic in perf_probe.get("warnings", []) if isinstance(perf_probe.get("warnings"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    for diagnostic in perf_probe.get("errors", []) if isinstance(perf_probe.get("errors"), list) else []:
        if diagnostic and diagnostic not in warnings:
            warnings.append(str(diagnostic))
    clip_guard = compiled.get("clip_loader_guard", {}) if isinstance(compiled.get("clip_loader_guard"), dict) else {}
    if not clip_guard.get("ok", False):
        for diagnostic in clip_guard.get("errors", []) if isinstance(clip_guard.get("errors"), list) else []:
            if diagnostic and diagnostic not in warnings:
                warnings.append(str(diagnostic))
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    if object_info and not readiness.get("ready"):
        warnings.append("Selected GGUF route is missing one or more required ComfyUI node classes; compile sidecar was still written for inspection.")
    dual_noise = compiled.get("dual_noise_mapping", {})
    if dual_noise and not dual_noise.get("ready", True):
        warnings.append("WAN GGUF dual-noise model mapping is not ready; generation will stay blocked until high/low model selections are fixed.")

    output_paths = get_video_output_paths("img2vid", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'wan22_gguf_i2v14')}_{stamp}_compile.json"
    prompt_sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'wan22_gguf_i2v14')}_{stamp}_prompt_api.json"
    sidecar_path = metadata_dir / sidecar_name
    prompt_sidecar_path = metadata_dir / prompt_sidecar_name
    sidecar_payload = {
        **compiled,
        "request": req.payload(),
        "backend_profile": profile,
        "warnings": warnings,
        "route_readiness": readiness,
    }
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    prompt_sidecar_path.write_text(json.dumps(compiled["prompt_api_payload"], indent=2), encoding="utf-8")
    return {
        **sidecar_payload,
        "ok": True,
        "queued": False,
        "dry_run": True,
        "backend": {"profile": profile, "base_url": base_url},
        "neo_output": {
            "category": output_paths.category,
            "output_dir": output_paths.relative_output_dir,
            "metadata_sidecar": sidecar_path.as_posix(),
            "prompt_api_sidecar": prompt_sidecar_path.as_posix(),
            "prompt_export": {"path": prompt_sidecar_path.as_posix(), "format": "comfy_prompt_api_json"},
        },
    }


def _comfy_queue_error(exc: BaseException) -> dict[str, Any]:
    body = ""
    status_code = None
    if isinstance(exc, HTTPError):
        status_code = getattr(exc, "code", None)
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
    text = str(exc)
    preview = body[:4000] if body else ""
    hint = ""
    lowered = f"{text} {preview}".casefold()
    if "invalid image file" in lowered or "loadimage" in lowered and "image" in lowered:
        hint = "The source image was not present in ComfyUI/input. Re-upload the Video source image; Neo should hand it off through Comfy /upload/image before queueing."
    elif "value not in list" in lowered or "not in" in lowered:
        hint = "A selected model is not visible in the ComfyUI loader dropdown; refresh model discovery and reselect the model."
    elif "return type mismatch" in lowered and "cacheargs" in lowered:
        hint = "A TeaCache CACHEARGS node was linked into a MODEL input. Disable TeaCache for this WAN GGUF KSamplerAdvanced route, or use a MODEL-returning TeaCache patch node."
    elif "invalid prompt" in lowered or "prompt_outputs_failed_validation" in lowered:
        hint = "ComfyUI rejected the prompt graph; inspect the prompt API sidecar and node/input names."
    elif "out of memory" in lowered or "cuda" in lowered:
        hint = "The GPU likely ran out of VRAM; apply the WAN GGUF first-test preset or reduce frames/resolution/steps."
    return {
        "type": exc.__class__.__name__,
        "message": text,
        "status_code": status_code,
        "body_preview": preview,
        "hint": hint,
    }


def video_wan22_gguf_i2v14_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    effective_payload = apply_wan22_gguf_first_test_preset(payload if isinstance(payload, dict) else {})
    req = WanGgufI2V14CompileRequest.from_payload(effective_payload)
    if req.dry_run:
        return video_wan22_gguf_i2v14_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)

    # First compile before source upload so route/model/adapter errors still return
    # their real diagnostics instead of being masked by a local test image handoff.
    compile_payload = video_wan22_gguf_i2v14_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if not compile_payload.get("dual_noise_mapping", {}).get("ready", True):
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": "WAN 2.2 GGUF dual-noise mapping is not ready; choose separate high-noise and low-noise GGUF models."}
    if not compile_payload.get("video_lora_adapter", {}).get("queue_ready", True):
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": "Video LoRA adapter is not queue-ready; check selected LoRA model(s)."}
    if not compile_payload.get("sage_attention_adapter", {}).get("queue_ready", True):
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": "Sage Attention adapter is not queue-ready; check KJNodes installation and selected Sage mode."}
    if not compile_payload.get("teacache_adapter", {}).get("queue_ready", True):
        errors = compile_payload.get("teacache_adapter", {}).get("errors", [])
        detail = f" {' '.join(str(item) for item in errors)}" if isinstance(errors, list) and errors else ""
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": f"TeaCache adapter is not queue-ready; check TeaCache node compatibility or disable TeaCache.{detail}"}
    if not compile_payload.get("low_vram_adapter", {}).get("queue_ready", True):
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": "Low-VRAM adapter is not queue-ready; check WAN block-swap/offload nodes or disable CPU Offload / Block Swap."}
    if not compile_payload.get("performance_adapter", {}).get("queue_ready", True):
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": "Video Performance adapter is not queue-ready; check selected optimizer nodes/modes or disable guarded future optimizers."}
    if not compile_payload.get("clip_loader_guard", {}).get("ok", True):
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": "WAN text encoder guard blocked queueing: CLIPLoader must use type='wan'."}

    profile = video_backend_profile_payload(req.profile_id)
    base_url_for_handoff = profile["connection"]["base_url"]
    local_source_exists = resolve_video_source_image_path(effective_payload) is not None
    source_handoff: dict[str, Any]
    # Unit tests pass object_info_override without a real Neo source file. Preserve
    # that no-network test path, while real local runtime always uploads the image
    # into ComfyUI/input before sending /prompt.
    if object_info_override is not None and not local_source_exists and not (req.source_image_comfy_name or req.comfy_source_image_name):
        source_handoff = {
            "ok": True,
            "uploaded": False,
            "verified": False,
            "bypassed_for_object_info_override": True,
            "comfy_image_name": req.source_image_name or Path(str(req.source_image or "neo_video_source.png")).name,
            "payload": effective_payload,
            "source_path": "",
        }
    else:
        source_handoff = prepare_video_source_image_handoff(effective_payload, base_url_for_handoff, timeout=max(timeout, 10.0))
    if not source_handoff.get("ok"):
        return {
            "schema_version": SCHEMA_VERSION,
            "surface": "video",
            "phase": "V-G13",
            "ok": False,
            "queued": False,
            "dry_run": False,
            "error": f"Video source image handoff to Comfy failed: {source_handoff.get('error') or 'unknown error'}",
            "source_handoff": source_handoff,
            "request": req.payload(),
        }

    effective_payload = source_handoff.get("payload") if isinstance(source_handoff.get("payload"), dict) else effective_payload
    req = WanGgufI2V14CompileRequest.from_payload(effective_payload)
    if source_handoff.get("uploaded") or source_handoff.get("verified") or source_handoff.get("comfy_image_name"):
        compile_payload = video_wan22_gguf_i2v14_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    compile_payload["source_handoff"] = source_handoff
    if isinstance(compile_payload.get("source"), dict):
        compile_payload["source"]["source_handoff"] = source_handoff
    if not compile_payload.get("ok"):
        return compile_payload
    if not compile_payload.get("clip_loader_guard", {}).get("ok", True):
        return {**compile_payload, "ok": False, "queued": False, "dry_run": False, "error": "WAN text encoder guard blocked queueing after source handoff: CLIPLoader must use type='wan'."}

    preflight = video_runtime_preflight_payload(
        {**req.payload(), "dry_run": False},
        object_info_override=object_info_override,
        compile_payload=compile_payload,
        timeout=2.5,
    ) if req.queue_preflight else {"queue_allowed": True, "schema_version": "neo.video.runtime_preflight.vg7", "phase": "V-G7", "warnings": ["Queue preflight was disabled by request."]}
    if not preflight.get("queue_allowed", False):
        return {
            **compile_payload,
            "ok": False,
            "queued": False,
            "dry_run": False,
            "runtime_preflight": preflight,
            "error": "WAN 2.2 GGUF runtime preflight blocked queueing before /prompt.",
        }
    base_url = compile_payload.get("backend", {}).get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    route_ready = compile_payload.get("route_readiness", {}).get("ready")
    if object_info_override is not None and not route_ready:
        return {**compile_payload, "ok": False, "queued": False, "runtime_preflight": preflight, "error": "Selected WAN 2.2 GGUF route is missing required ComfyUI nodes."}
    if not compile_payload.get("dual_noise_mapping", {}).get("ready", True):
        return {**compile_payload, "ok": False, "queued": False, "runtime_preflight": preflight, "error": "WAN 2.2 GGUF dual-noise mapping is not ready; choose separate high-noise and low-noise GGUF models."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        queue_error = _comfy_queue_error(exc)
        return {**compile_payload, "ok": False, "queued": False, "runtime_preflight": preflight, "queue_error": queue_error, "error": f"ComfyUI queue failed: {queue_error['message']}"}
    response_payload = {
        **compile_payload,
        "ok": True,
        "queued": True,
        "dry_run": False,
        "runtime_preflight": preflight,
        "queue_response": queue_response,
        "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or "",
        "client_id": compile_payload.get("client_id") or compile_payload.get("prompt_api_payload", {}).get("client_id") or "",
    }
    return _attach_video_output_record(response_payload, req.payload())

