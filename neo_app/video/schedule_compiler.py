from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any, Final
from urllib.error import HTTPError, URLError

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.first_last_frame_compiler import _attach_video_output_record
from neo_app.video.ltx_txt2vid_compiler import (
    DEFAULT_NEGATIVE_PROMPT,
    LtxVideoCompileRequest,
    _now,
    _post_json,
    build_ltx23_txt2vid_workflow,
)
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.schedule.compiler.v20"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.prompt_schedule"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.prompt_schedule"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)


@dataclass(frozen=True)
class ScheduleEvent:
    at: float = 0.0
    unit: str = "seconds"
    prompt: str = ""
    motion: str = ""
    strength: float = 1.0

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MotionScheduleEvent:
    at: float = 0.0
    unit: str = "seconds"
    camera: str = ""
    motion: str = ""
    strength: float = 0.5

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LtxScheduleCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "prompt_schedule"
    prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    vram_profile: str = "balanced"
    width: int | None = None
    height: int | None = None
    frames: int | None = None
    fps: float | int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    sampler: str | None = None
    scheduler: str | None = None
    output_format: str = "webm"
    filename_prefix: str = "Neo_Video_LTX23_Scheduled"
    prompt_events: tuple[ScheduleEvent, ...] = ()
    motion_events: tuple[MotionScheduleEvent, ...] = ()
    schedule_mode: str = "metadata_guided"
    model_name: str | None = None
    unet_name: str | None = None
    gguf_name: str | None = None
    clip_name1: str | None = None
    clip_name2: str | None = None
    vae_name: str | None = None
    chunk_feed_forward: int | None = None
    tile_size: int | None = None
    temporal_tile_size: int | None = None
    tiled_vae_decode: bool = True
    profile_id: str | None = None
    dry_run: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxScheduleCompileRequest":
        data = payload or {}

        def _int(value: Any) -> int | None:
            try:
                if value is None or value == "":
                    return None
                return int(float(value))
            except (TypeError, ValueError):
                return None

        def _float(value: Any) -> float | None:
            try:
                if value is None or value == "":
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "prompt_schedule")) or "prompt_schedule"),
            prompt=str(data.get("prompt", data.get("positive_prompt", "")) or ""),
            negative_prompt=str(data.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT),
            vram_profile=str(data.get("vram_profile", "balanced") or "balanced"),
            width=_int(data.get("width")), height=_int(data.get("height")), frames=_int(data.get("frames")), fps=_float(data.get("fps")),
            steps=_int(data.get("steps")), guidance=_float(data.get("guidance", data.get("cfg"))), seed=_int(data.get("seed")),
            sampler=str(data.get("sampler") or "") or None, scheduler=str(data.get("scheduler") or "") or None,
            output_format=str(data.get("output_format", "webm") or "webm"), filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_Scheduled") or "Neo_Video_LTX23_Scheduled"),
            prompt_events=_normalize_prompt_events(data.get("prompt_events", data.get("schedule_events", []))),
            motion_events=_normalize_motion_events(data.get("motion_events", data.get("camera_events", []))),
            schedule_mode=str(data.get("schedule_mode", "metadata_guided") or "metadata_guided"),
            model_name=str(data.get("model_name", "") or "") or None, unet_name=str(data.get("unet_name", "") or "") or None, gguf_name=str(data.get("gguf_name", "") or "") or None,
            clip_name1=str(data.get("clip_name1", data.get("clip_name", "")) or "") or None, clip_name2=str(data.get("clip_name2", data.get("text_projection", "")) or "") or None, vae_name=str(data.get("vae_name", "") or "") or None,
            chunk_feed_forward=_int(data.get("chunk_feed_forward")), tile_size=_int(data.get("tile_size")), temporal_tile_size=_int(data.get("temporal_tile_size")),
            tiled_vae_decode=bool(data.get("tiled_vae_decode", True)), profile_id=str(data.get("profile_id", "") or "") or None, dry_run=bool(data.get("dry_run", True)),
        )

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["prompt_events"] = [event.payload() for event in self.prompt_events]
        data["motion_events"] = [event.payload() for event in self.motion_events]
        return data


def _normalize_prompt_events(raw: Any) -> tuple[ScheduleEvent, ...]:
    events: list[ScheduleEvent] = []
    if not isinstance(raw, list):
        return ()
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt", item.get("text", "")) or "").strip()
        motion = str(item.get("motion", "") or "").strip()
        if not prompt and not motion:
            continue
        try:
            at = float(item.get("at", item.get("time", item.get("frame", 0))) or 0)
        except (TypeError, ValueError):
            at = 0.0
        try:
            strength = max(0.0, min(1.0, float(item.get("strength", 1.0) or 1.0)))
        except (TypeError, ValueError):
            strength = 1.0
        unit = str(item.get("unit", "seconds") or "seconds").lower()
        if unit not in {"seconds", "frames"}:
            unit = "seconds"
        events.append(ScheduleEvent(at=at, unit=unit, prompt=prompt, motion=motion, strength=strength))
    return tuple(sorted(events, key=lambda event: event.at))


def _normalize_motion_events(raw: Any) -> tuple[MotionScheduleEvent, ...]:
    events: list[MotionScheduleEvent] = []
    if not isinstance(raw, list):
        return ()
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        camera = str(item.get("camera", item.get("camera_motion", "")) or "").strip()
        motion = str(item.get("motion", item.get("subject_motion", "")) or "").strip()
        if not camera and not motion:
            continue
        try:
            at = float(item.get("at", item.get("time", item.get("frame", 0))) or 0)
        except (TypeError, ValueError):
            at = 0.0
        try:
            strength = max(0.0, min(1.0, float(item.get("strength", 0.5) or 0.5)))
        except (TypeError, ValueError):
            strength = 0.5
        unit = str(item.get("unit", "seconds") or "seconds").lower()
        if unit not in {"seconds", "frames"}:
            unit = "seconds"
        events.append(MotionScheduleEvent(at=at, unit=unit, camera=camera, motion=motion, strength=strength))
    return tuple(sorted(events, key=lambda event: event.at))


def _schedule_prompt(req: LtxScheduleCompileRequest) -> str:
    parts = [req.prompt.strip() or "A short cinematic scheduled video with smooth natural motion."]
    if req.prompt_events:
        parts.append("Timed prompt beats:")
        for event in req.prompt_events:
            stamp = f"{event.at:g} {event.unit}"
            detail = event.prompt or event.motion
            parts.append(f"[{stamp}] {detail} (strength {event.strength:g}).")
    if req.motion_events:
        parts.append("Timed motion beats:")
        for event in req.motion_events:
            stamp = f"{event.at:g} {event.unit}"
            detail = "; ".join(chunk for chunk in (event.camera, event.motion) if chunk)
            parts.append(f"[{stamp}] {detail} (motion strength {event.strength:g}).")
    return "\n".join(parts).strip()


def build_ltx23_schedule_workflow(req: LtxScheduleCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    loader = normalize_video_loader(req.loader)
    base_req = LtxVideoCompileRequest(
        family="ltx23", loader=loader, generation_type="txt2vid", prompt=_schedule_prompt(req), negative_prompt=req.negative_prompt,
        vram_profile=req.vram_profile, width=req.width, height=req.height, frames=req.frames, fps=req.fps, steps=req.steps, guidance=req.guidance, seed=req.seed,
        sampler=req.sampler, scheduler=req.scheduler, output_format=req.output_format, filename_prefix=req.filename_prefix, model_name=req.model_name,
        unet_name=req.unet_name, gguf_name=req.gguf_name, clip_name1=req.clip_name1, clip_name2=req.clip_name2, vae_name=req.vae_name,
        chunk_feed_forward=req.chunk_feed_forward, tile_size=req.tile_size, temporal_tile_size=req.temporal_tile_size, tiled_vae_decode=req.tiled_vae_decode,
        profile_id=req.profile_id, dry_run=req.dry_run,
    )
    compiled = build_ltx23_txt2vid_workflow(base_req, object_info=object_info)
    compiled.update({
        "schema_version": SCHEMA_VERSION,
        "phase": "V20",
        "route_id": f"ltx23.{loader}.prompt_schedule",
        "compiled_at": _now(),
    })
    compiled["parameters"] = {**compiled.get("parameters", {}), "schedule_mode": req.schedule_mode, "prompt_event_count": len(req.prompt_events), "motion_event_count": len(req.motion_events)}
    compiled["schedule"] = {
        "mode": req.schedule_mode,
        "prompt_events": [event.payload() for event in req.prompt_events],
        "motion_events": [event.payload() for event in req.motion_events],
        "implementation": "metadata_guided_prompt_beats",
    }
    compiled["rules"] = [
        "V20 stores prompt and motion schedules as explicit replay metadata and folds them into the LTX prompt beats for this first scheduling compiler.",
        "The schedule lane is non-destructive and writes child outputs under neo_data/outputs/video/schedule.",
        "FizzNodes/keyframe-node wiring stays optional and detection-only until node signatures are stable on the local ComfyUI install.",
        "WAN scheduling remains guarded; V20 targets LTX 2.3 GGUF and UNET/Diffusion routes only.",
    ]
    return compiled


def video_ltx23_schedule_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxScheduleCompileRequest.from_payload(payload)
    nf, nl, nt = normalize_video_family(req.family), normalize_video_loader(req.loader), normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V20", "ok": False, "queued": False, "error": f"V20 Prompt/Motion Schedule compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.", "request": {"family": nf, "loader": nl, "generation_type": nt}, "route": route.payload() if route else None}
    if not req.prompt_events and not req.motion_events:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V20", "ok": False, "queued": False, "error": "Prompt Scheduling requires at least one prompt event or motion event.", "request": req.payload(), "route": route.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX schedule bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_ltx23_schedule_workflow(req, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("schedule", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_schedule')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_schedule_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxScheduleCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_schedule_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok") or req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    if object_info_override is not None and not compile_payload.get("route_readiness", {}).get("ready"):
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 schedule route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {**compile_payload, "ok": True, "queued": True, "dry_run": False, "queue_response": queue_response, "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or ""}
    return _attach_video_output_record(response_payload, req.payload())
