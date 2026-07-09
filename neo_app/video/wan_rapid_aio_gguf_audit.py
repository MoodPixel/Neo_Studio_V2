from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Final

from neo_app.video.model_discovery import RAPID_AIO_NONE_TEST_ID, video_model_discovery_from_object_info
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.wan22_rapid_aio_gguf.production_discovery.v25_9_19_phase10y"
PHASE: Final[str] = "V25.9.19-10y"
RAPID_ROUTE_IDS: Final[set[str]] = {"wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"}


@dataclass(frozen=True)
class WanRapidAioGgufAuditRequest:
    family: str = "wan22"
    loader: str = "rapid_aio_gguf"
    generation_type: str = "img2vid"
    rapid_aio_model: str | None = None
    rapid_aio_text_encoder: str | None = None
    rapid_aio_vae: str | None = None
    clip_name: str | None = None
    vae_name: str | None = None
    source_image: str | None = None
    dry_run: bool = True
    filename_prefix: str = "Neo_Video_WAN22_Rapid_AIO_GGUF_Audit"

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "WanRapidAioGgufAuditRequest":
        data = dict(payload or {})
        text_encoder = _clean(data.get("rapid_aio_text_encoder", data.get("text_encoder", data.get("clip_name"))))
        vae = _clean(data.get("rapid_aio_vae", data.get("vae_name")))
        return cls(
            family=normalize_video_family(data.get("family", "wan22")),
            loader=normalize_video_loader(data.get("loader", "rapid_aio_gguf")),
            generation_type=normalize_video_generation_type(data.get("generation_type", data.get("mode", "img2vid"))),
            rapid_aio_model=_clean(data.get("rapid_aio_model", data.get("model_name", data.get("gguf_model")))),
            rapid_aio_text_encoder=text_encoder,
            rapid_aio_vae=vae,
            clip_name=None if text_encoder == RAPID_AIO_NONE_TEST_ID else text_encoder,
            vae_name=None if vae == RAPID_AIO_NONE_TEST_ID else vae,
            source_image=_clean(data.get("source_image")),
            dry_run=bool(data.get("dry_run", True)),
            filename_prefix=str(data.get("filename_prefix") or "Neo_Video_WAN22_Rapid_AIO_GGUF_Audit"),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"provider_default", "automatic", "auto"} or text.startswith("select_"):
        return None
    return text


def _production_route_status(req: WanRapidAioGgufAuditRequest) -> dict[str, Any]:
    return {
        "id": "auto_encoder_auto_vae",
        "text_encoder": req.rapid_aio_text_encoder or "provider_default",
        "vae": req.rapid_aio_vae or "provider_default",
        "queue_allowed": req.generation_type in {"img2vid", "image_to_video", "i2v"},
        "intent": "Production Rapid AIO Img2Vid route using live model, encoder, VAE, and the Rapid AIO single-model output topology.",
    }


def video_wan22_rapid_aio_gguf_audit_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = WanRapidAioGgufAuditRequest.from_payload(payload)
    route = find_video_route(req.family, req.loader, req.generation_type, include_planned=True)
    route_id = route.route_id if route else ""
    discovery = video_model_discovery_from_object_info(
        object_info_override or {},
        family=req.family,
        loader=req.loader,
        generation_type=req.generation_type,
        fallback_models={},
        rapid_aio_model=req.rapid_aio_model,
        clip_name=req.clip_name,
        vae_name=req.vae_name,
    )
    output_paths = get_video_output_paths("metadata", create=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sidecar = output_paths.output_dir / f"{sanitize_path_part(req.filename_prefix, 'wan22_rapid_aio_audit')}_{stamp}_audit.json"
    payload_out = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "surface": "video",
        "ok": True,
        "queued": False,
        "dry_run": True,
        "audit_only": False,
        "queue_allowed": req.generation_type in {"img2vid", "image_to_video", "i2v"},
        "route_id": route_id,
        "route": route.payload() if route else None,
        "request": req.payload(),
        "model_discovery": discovery,
        "hardcoded_model_names": False,
        "production_route": _production_route_status(req),
        "warnings": [
            "WAN 2.2 Rapid AIO GGUF is a Phase 10y production discovery route, not a visible queue-test lane.",
            "All model dropdowns come from live ComfyUI object_info or Admin catalogs; no hardcoded filenames are used.",
            "Rapid AIO uses a true single-model CreateVideo/SaveVideo output topology.",
        ],
        "neo_output": {"category": "metadata", "metadata_sidecar": str(sidecar)},
    }
    sidecar.write_text(__import__("json").dumps(payload_out, indent=2), encoding="utf-8")
    return payload_out


def video_wan22_rapid_aio_gguf_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    audit = video_wan22_rapid_aio_gguf_audit_payload(payload, object_info_override=object_info_override)
    return {
        **audit,
        "ok": False,
        "queued": False,
        "error": "WAN 2.2 Rapid AIO GGUF discovery is available; use the production compile/generate endpoint for real Img2Vid queueing.",
    }
