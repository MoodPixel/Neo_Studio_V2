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

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.audio_video.compiler.v21"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.audio_video"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.audio_video"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)


@dataclass(frozen=True)
class LtxAudioVideoCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "audio_video"
    prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    audio_prompt: str = ""
    dialogue_prompt: str = ""
    soundscape_prompt: str = ""
    audio_mode: str = "prompted"
    audio_strength: float | None = None
    sync_strength: float | None = None
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
    filename_prefix: str = "Neo_Video_LTX23_AudioVideo"
    model_name: str | None = None
    unet_name: str | None = None
    gguf_name: str | None = None
    clip_name1: str | None = None
    clip_name2: str | None = None
    vae_name: str | None = None
    audio_vae_name: str | None = None
    chunk_feed_forward: int | None = None
    tile_size: int | None = None
    temporal_tile_size: int | None = None
    tiled_vae_decode: bool = True
    profile_id: str | None = None
    dry_run: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxAudioVideoCompileRequest":
        data = payload or {}
        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "audio_video")) or "audio_video"),
            prompt=str(data.get("prompt", data.get("positive_prompt", "")) or ""),
            negative_prompt=str(data.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT),
            audio_prompt=str(data.get("audio_prompt", data.get("audio_description", "")) or ""),
            dialogue_prompt=str(data.get("dialogue_prompt", data.get("dialogue", "")) or ""),
            soundscape_prompt=str(data.get("soundscape_prompt", data.get("soundscape", "")) or ""),
            audio_mode=str(data.get("audio_mode", "prompted") or "prompted"),
            audio_strength=_float_or_none(data.get("audio_strength", 0.75)),
            sync_strength=_float_or_none(data.get("sync_strength", 0.6)),
            vram_profile=str(data.get("vram_profile", "balanced") or "balanced"),
            width=_int_or_none(data.get("width")), height=_int_or_none(data.get("height")), frames=_int_or_none(data.get("frames")), fps=_float_or_none(data.get("fps")),
            steps=_int_or_none(data.get("steps")), guidance=_float_or_none(data.get("guidance", data.get("cfg"))), seed=_int_or_none(data.get("seed")),
            sampler=str(data.get("sampler") or "") or None, scheduler=str(data.get("scheduler") or "") or None,
            output_format=str(data.get("output_format", "webm") or "webm"), filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_AudioVideo") or "Neo_Video_LTX23_AudioVideo"),
            model_name=str(data.get("model_name", "") or "") or None, unet_name=str(data.get("unet_name", "") or "") or None, gguf_name=str(data.get("gguf_name", "") or "") or None,
            clip_name1=str(data.get("clip_name1", data.get("clip_name", "")) or "") or None, clip_name2=str(data.get("clip_name2", data.get("text_projection", "")) or "") or None,
            vae_name=str(data.get("vae_name", "") or "") or None, audio_vae_name=str(data.get("audio_vae_name", "") or "") or None,
            chunk_feed_forward=_int_or_none(data.get("chunk_feed_forward")), tile_size=_int_or_none(data.get("tile_size")), temporal_tile_size=_int_or_none(data.get("temporal_tile_size")),
            tiled_vae_decode=bool(data.get("tiled_vae_decode", True)), profile_id=str(data.get("profile_id", "") or "") or None, dry_run=bool(data.get("dry_run", True)),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


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


def _audio_prompt(req: LtxAudioVideoCompileRequest) -> str:
    chunks = [req.prompt.strip() or "A short cinematic audio-video scene with synchronized natural motion."]
    audio_chunks = []
    if req.audio_prompt.strip():
        audio_chunks.append(f"Audio: {req.audio_prompt.strip()}")
    if req.dialogue_prompt.strip():
        audio_chunks.append(f"Dialogue/voice: {req.dialogue_prompt.strip()}")
    if req.soundscape_prompt.strip():
        audio_chunks.append(f"Soundscape: {req.soundscape_prompt.strip()}")
    if audio_chunks:
        chunks.append("Audio-video synchronization requirements:")
        chunks.extend(audio_chunks)
        chunks.append(f"Keep audio and visible motion synchronized. Audio strength {req.audio_strength if req.audio_strength is not None else 0.75:g}; sync strength {req.sync_strength if req.sync_strength is not None else 0.6:g}.")
    return "\n".join(chunks).strip()


def build_ltx23_audio_video_workflow(req: LtxAudioVideoCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    loader = normalize_video_loader(req.loader)
    base_req = LtxVideoCompileRequest(
        family="ltx23", loader=loader, generation_type="txt2vid", prompt=_audio_prompt(req), negative_prompt=req.negative_prompt,
        vram_profile=req.vram_profile, width=req.width, height=req.height, frames=req.frames, fps=req.fps, steps=req.steps, guidance=req.guidance, seed=req.seed,
        sampler=req.sampler, scheduler=req.scheduler, output_format=req.output_format, filename_prefix=req.filename_prefix, model_name=req.model_name,
        unet_name=req.unet_name, gguf_name=req.gguf_name, clip_name1=req.clip_name1, clip_name2=req.clip_name2, vae_name=req.vae_name,
        chunk_feed_forward=req.chunk_feed_forward, tile_size=req.tile_size, temporal_tile_size=req.temporal_tile_size, tiled_vae_decode=req.tiled_vae_decode,
        profile_id=req.profile_id, dry_run=req.dry_run,
    )
    compiled = build_ltx23_txt2vid_workflow(base_req, object_info=object_info)
    classes = compiled.get("bindings", {}).get("classes", {}) if isinstance(compiled.get("bindings"), dict) else {}
    info = object_info or {}
    has_audio_vae = any(str(key).casefold() in {"ltxvaudiovaeloader", "audio vae loader"} for key in info.keys())
    compiled.update({
        "schema_version": SCHEMA_VERSION,
        "phase": "V21",
        "route_id": f"ltx23.{loader}.audio_video",
        "audio": {
            "mode": req.audio_mode,
            "audio_prompt": req.audio_prompt,
            "dialogue_prompt": req.dialogue_prompt,
            "soundscape_prompt": req.soundscape_prompt,
            "audio_strength": req.audio_strength if req.audio_strength is not None else 0.75,
            "sync_strength": req.sync_strength if req.sync_strength is not None else 0.6,
            "audio_vae_name": req.audio_vae_name or "",
            "audio_vae_node_detected": has_audio_vae,
            "embedding_strategy": "prompt_conditioned_audio_intent",
        },
        "parameters": {
            **(compiled.get("parameters") if isinstance(compiled.get("parameters"), dict) else {}),
            "audio_mode": req.audio_mode,
            "audio_strength": req.audio_strength if req.audio_strength is not None else 0.75,
            "sync_strength": req.sync_strength if req.sync_strength is not None else 0.6,
            "audio_latent": True,
        },
        "rules": [
            "V21 Audio-Video is active for LTX 2.3 GGUF and UNET/Diffusion routes only.",
            "The first pass is prompt-conditioned audio-video intent with replay metadata, avoiding fragile hard-coded audio-node signatures.",
            "Audio prompts, dialogue, soundscape, and sync strength are persisted in the ledger for exact replay.",
            "Generated outputs go under neo_data/outputs/video/audio_video and parent video lanes remain untouched.",
        ],
    })
    compiled.setdefault("bindings", {}).setdefault("classes", classes)
    return compiled


def video_ltx23_audio_video_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxAudioVideoCompileRequest.from_payload(payload)
    nf, nl, nt = normalize_video_family(req.family), normalize_video_loader(req.loader), normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V21", "ok": False, "queued": False, "error": f"V21 Audio-Video compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.", "request": {"family": nf, "loader": nl, "generation_type": nt}, "route": route.payload() if route else None}
    if not (req.audio_prompt.strip() or req.dialogue_prompt.strip() or req.soundscape_prompt.strip()):
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V21", "ok": False, "queued": False, "error": "V21 Audio-Video requires at least one audio prompt, dialogue prompt, or soundscape prompt.", "request": req.payload(), "route": route.payload() if route else None}

    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX audio-video bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}

    compiled = build_ltx23_audio_video_workflow(req, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("audio_video", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_audio_video')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    response_payload = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_audio_video_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxAudioVideoCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_audio_video_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    if object_info_override is not None and not compile_payload.get("route_readiness", {}).get("ready"):
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 Audio-Video route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {**compile_payload, "ok": True, "queued": True, "dry_run": False, "queue_response": queue_response, "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or ""}
    return _attach_video_output_record(response_payload, req.payload())
