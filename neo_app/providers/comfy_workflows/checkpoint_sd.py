from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SDCheckpointDefaults:
    """Family-specific defaults for SD checkpoint Comfy workflows.

    These are provider compiler defaults only. The Image surface still owns the
    generic family + loader + mode contract, and callers may override every
    value through params.
    """

    family: str
    width: int
    height: int
    steps: int
    cfg: float
    denoise_txt2img: float
    denoise_img2img: float
    denoise_inpaint: float
    clip_skip: int
    workflow_label: str


SD_CHECKPOINT_DEFAULTS: dict[str, SDCheckpointDefaults] = {
    "sdxl": SDCheckpointDefaults(
        family="sdxl",
        width=1024,
        height=1024,
        steps=28,
        cfg=7.0,
        denoise_txt2img=1.0,
        denoise_img2img=0.65,
        denoise_inpaint=0.72,
        clip_skip=1,
        workflow_label="SDXL checkpoint",
    ),
    "sd15": SDCheckpointDefaults(
        family="sd15",
        width=512,
        height=512,
        steps=25,
        cfg=7.0,
        denoise_txt2img=1.0,
        denoise_img2img=0.65,
        denoise_inpaint=0.72,
        clip_skip=1,
        workflow_label="SD 1.5 checkpoint",
    ),
}


def resolve_sd_checkpoint_defaults(family: str | None) -> SDCheckpointDefaults:
    """Return checkpoint defaults for SD-family Comfy routes.

    Unknown families fall back to SDXL to preserve the legacy Phase 11 behavior
    for jobs that did not yet send a family id.
    """

    normalized = str(family or "sdxl").strip() or "sdxl"
    return SD_CHECKPOINT_DEFAULTS.get(normalized, SD_CHECKPOINT_DEFAULTS["sdxl"])


def sd_checkpoint_workflow_type(family: str | None, mode: str | None) -> str:
    normalized_family = str(family or "sdxl").strip() or "sdxl"
    normalized_mode = str(mode or "txt2img").strip() or "txt2img"
    return f"image.{normalized_mode}.{normalized_family}"
