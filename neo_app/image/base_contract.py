
from __future__ import annotations

from copy import deepcopy

from neo_app.image.schema import ImageJobDraft, ImageOption, ImageShellSection, ImageSubtabBase, ImageSurfaceBaseContract
from neo_app.models.registry import get_loader_types, get_surface_families


def _image_family_options() -> list[ImageOption]:
    return [
        ImageOption(id=family.family_id, label=family.display_name, description=family.description)
        for family in get_surface_families("image")
    ]


def _loader_type_options() -> list[ImageOption]:
    return [
        ImageOption(id=loader.loader_id, label=loader.display_name, description=loader.description)
        for loader in get_loader_types()
    ]


SIZE_PRESETS = [
    ImageOption(id="custom", label="Custom", description="Manual width and height. Use 💾 to save later."),
    ImageOption(id="square_1024", label="Square 1024 × 1024", description="General square image preset."),
    ImageOption(id="portrait_832_1216", label="Portrait 832 × 1216", description="Portrait image preset."),
    ImageOption(id="landscape_1216_832", label="Landscape 1216 × 832", description="Landscape image preset."),
    ImageOption(id="reel_1080_1920", label="Reel / Shorts 1080 × 1920", description="Vertical social video/image preset."),
    ImageOption(id="feed_4x5_1024_1280", label="Feed 4:5 1024 × 1280", description="Instagram-style 4:5 portrait preset."),
    ImageOption(id="thumbnail_1280_720", label="YouTube Thumbnail 1280 × 720", description="16:9 thumbnail preset."),
]




DEFAULT_PARAMS = {
    "width": 1024,
    "height": 1024,
    "steps": 28,
    "cfg": 7.0,
    "seed": -1,
    "model": "provider_default",
    "vae": "automatic",
    "sampler": "provider_default",
    "scheduler": "provider_default",
    "denoise": 1.0,
    "batch_count": 1,
    "clip_skip": 1,
    "clamp": "raw",
    "prompt_conditioning_mode": "raw",
}


def _sections(subtab: str, include_source: bool = False, include_mask: bool = False, include_instruction: bool = False) -> list[ImageShellSection]:
    base = [
        ImageShellSection(section_id="backend", title="Backend", description="Select the backend provider and connection target.", slot=f"image.{subtab}.backend", fields=["backend", "provider_status"]),
        ImageShellSection(section_id="model", title="Model", description="Select model family, model, and loader type.", slot=f"image.{subtab}.model", fields=["family", "model", "vae", "loader"]),
    ]
    if include_source:
        base.append(ImageShellSection(section_id="source", title="Source", description="Source image, reference, canvas, or previous output input.", slot=f"image.{subtab}.source", fields=["source_image", "reuse_metadata"]))
    if include_mask:
        base.append(ImageShellSection(section_id="mask", title="Mask", description="Mask or repair area for inpaint workflows.", slot=f"image.{subtab}.mask", fields=["mask_image", "mask_mode"]))
    if include_instruction:
        base.append(ImageShellSection(section_id="instruction", title="Instruction", description="Instruction prompt for image editing providers.", slot=f"image.{subtab}.instruction", fields=["edit_instruction"]))
    base.extend([
        ImageShellSection(section_id="prompt", title="Prompt", description="Positive/negative prompt shell shared by base and extensions.", slot=f"image.{subtab}.prompt", fields=["positive_prompt", "negative_prompt", "style_chips"]),
        ImageShellSection(section_id="params", title="Parameters", description="Backend-neutral generation parameters, size presets, and reusable seed controls.", slot=f"image.{subtab}.params", fields=["model", "vae", "size_preset", "width", "height", "steps", "cfg", "seed", "seed_lock", "seed_randomize", "seed_reuse", "seed_copy", "sampler", "scheduler", "batch_count", "clip_skip", "clamp", "denoise"]),
        ImageShellSection(section_id="extensions", title="Extensions", description="Compatible extensions mount here based on surface, subtab, backend, family, and loader.", slot=f"image.{subtab}.extensions", fields=["mounted_extensions", "compatibility_status"]),
        ImageShellSection(section_id="preview", title="Preview", description="Live preview, final preview, and batch thumbnails. Output management belongs in the Results workspace/subtab, not this preview panel.", slot=f"image.{subtab}.preview", fields=["live_preview", "final_preview", "batch_thumbnails"]),
    ])
    return base


def get_image_surface_base_contract() -> ImageSurfaceBaseContract:
    return ImageSurfaceBaseContract(
        model_families=_image_family_options(),
        loader_types=_loader_type_options(),
        default_params=DEFAULT_PARAMS,
        size_presets=SIZE_PRESETS,
        memory_events=[
            "image.surface.opened",
            "image.subtab.opened",
            "image.job.draft_created",
            "image.backend.selected",
            "image.family.selected",
            "image.loader.selected",
            "image.extension.slot_rendered",
        ],
        subtabs=[
            ImageSubtabBase(subtab_id="generate", display_name="Generate", mode="txt2img", description="Base text-to-image workspace.", sections=_sections("generate")),
            ImageSubtabBase(subtab_id="img2img", display_name="Img2Img", mode="img2img", description="Source-image guided generation workspace.", sections=_sections("img2img", include_source=True)),
            ImageSubtabBase(subtab_id="inpaint", display_name="Inpaint", mode="inpaint", description="Masked image repair workspace.", sections=_sections("inpaint", include_source=True)),
            ImageSubtabBase(subtab_id="outpaint", display_name="Outpaint", mode="outpaint", description="Canvas expansion workspace.", sections=_sections("outpaint", include_source=True)),
            ImageSubtabBase(subtab_id="upscale", display_name="Upscale", mode="upscale", description="Resolution enhancement workspace.", sections=_sections("upscale", include_source=True)),
            ImageSubtabBase(subtab_id="edit", display_name="Edit", mode="edit", description="Instruction/image editing workspace.", sections=_sections("edit", include_source=True, include_instruction=True)),
            ImageSubtabBase(subtab_id="batch", display_name="Batch", mode="batch", description="Batch prompt/source processing workspace.", sections=_sections("batch", include_source=True)),
            ImageSubtabBase(subtab_id="history", display_name="History", mode="history", description="History and metadata replay workspace.", sections=[
                ImageShellSection(section_id="filters", title="Filters", description="Filter image history by backend, family, loader, extension, or project.", slot="image.history.filters", fields=["backend", "family", "loader", "extension", "date_range"]),
                ImageShellSection(section_id="results", title="History Results", description="Previously completed image jobs and metadata.", slot="image.history.results", fields=["result_cards", "metadata"]),
                ImageShellSection(section_id="reuse", title="Reuse", description="Send metadata back into an image subtab or Board.", slot="image.history.reuse", fields=["reuse_prompt", "reuse_params", "send_to_board"]),
            ]),
        ],
    )


def create_image_job_draft(payload: dict | None = None) -> ImageJobDraft:
    data = payload or {}
    params = deepcopy(DEFAULT_PARAMS)
    params.update(data.get("params") or {})
    return ImageJobDraft(
        subtab=data.get("subtab", "generate"),
        mode=data.get("mode", "txt2img"),
        backend=data.get("backend", "comfyui_portable"),
        family=data.get("family", "sdxl"),
        loader=data.get("loader", "checkpoint"),
        prompt=data.get("prompt", ""),
        negative_prompt=data.get("negative_prompt", ""),
        params=params,
        extensions=data.get("extensions", []),
    )
