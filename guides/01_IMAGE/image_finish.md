---
guide_id: image.finish
title: Image Finish Workspace
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - finish
  - high_res_lab
  - adetailer
  - image_upscale
  - final_polish_lab
  - output_reuse
  - post_fix
tags:
  - image
  - finish
  - high res
  - upscale
  - adetailer
  - post output
  - reuse
  - repair
priority: 112
version: 5
updated: 2026-07-15
---

# Image Finish Workspace

The **Image → Finish** workspace owns finishing, repair, upscale, and post-output reuse tools. It is separate from base generation:

- **Generation** builds the main image graph.
- **Assets** owns LoRA and embedding assets.
- **Reference** owns ControlNet/IP Adapter guidance.
- **Finish** refines, repairs, upscales, or stages an existing output/source for another pass.
- **Results** reviews saved outputs, metadata, replay, cleanup, and deletion.

Finish tools are route-aware. A card can be visible but disabled when the selected backend, model family, loader, workflow mode, custom node set, or source image does not support the tool.

## Shared image and mask preparation

**Remove Background → Mask & Object Utilities** also exposes the installed ComfyUI-RMBG pixel tools as independent operations:

- **Mask Overlay**: inspect a mask over the source image without changing the source.
- **Object Remover · Lama**: remove a masked object and save a derived image.
- **Image + Mask Resize**: resize, pad, or crop an image and its mask under one aligned contract.
- **Image Crop**: prepare a source/Stitch image; a supplied mask receives the same crop.

These are preparation/finish operations, not generation engines. Inpaint and Outpaint consume the resulting prepared image/mask; Scene Director consumes a region mask; Stitch consumes a prepared source image. Neo validates each RMBG node and its inputs against the active ComfyUI `/object_info` response and blocks unavailable operations without silent fallback.

## Finish tools

| Tool | Main purpose | Execution type | Guide |
|---|---|---|---|
| **High-Res Lab** | High-resolution diffusion refine / highres-style finish pass. | Normal Image workflow patch / finish pass. | `guides/01_IMAGE/high_res_lab.md` |
| **ADetailer** | Selective local repair for faces, hands, people, clothing, products, or manual regions. | Normal Image workflow patch / final repair pass. | `guides/01_IMAGE/adetailer.md` |
| **Image Upscale** | Standalone upscale utility for selected outputs or uploaded images. | Standalone queue route, not normal generation compiler. | `guides/01_IMAGE/image_upscale.md` |
| **Final Polish Lab** | External finish cockpit for relight, layer polish, camera/color looks, fixed-order chaining, bounded batch polish, and source-explicit replay. | Reliable external standalone/chained ComfyUI prompts with recoverable monitoring and completion-aware saved-result metadata. | `guides/01_IMAGE/final_polish_lab.md` |

## Finish vs Results

Use **Finish** when the user wants to change or improve an image.

Use **Results** when the user wants to inspect, reuse, replay, organize, delete, or understand saved outputs.

Examples:

| User asks | Best place |
|---|---|
| “Make this image larger but keep the same look.” | Finish → Image Upscale or High-Res Lab |
| “Repair the face/hands after generation.” | Finish → ADetailer |
| “Run a high-res pass after base generation.” | Finish → High-Res Lab |
| “Use this saved output as img2img source.” | Results → Output Inspector reuse actions |
| “Delete this saved output and all linked unique assets.” | Results → Output Inspector delete preview |
| “Show the seed/model/prompt used for this image.” | Results → Output Inspector |

## Route and family behavior

| Family / backend route | High-Res Lab | ADetailer | Image Upscale | Final Polish Lab |
|---|---|---|---|---|
| **ComfyUI SDXL checkpoint** | Available for Generate/Img2Img/Inpaint/Outpaint. | Available for Generate/Img2Img/Inpaint; Outpaint is gated/planned. | Available as standalone utility. | Camera Finish/Layer Polish can run image-only; IC-Light Relight stays unavailable because it requires SD 1.5. |
| **ComfyUI SD 1.5 checkpoint** | Available for Generate/Img2Img/Inpaint/Outpaint. | Experimental for Generate/Img2Img/Inpaint; Outpaint is gated/planned. | Available as standalone utility. | Standalone lanes, fixed-order chains, and up-to-20-source batches are available when every enabled lane dependency is ready. |
| **Flux / Flux 2 / Qwen / ZImage / HiDream local routes** | Gated or hidden unless the route matrix says otherwise. | Unsupported/gated for the current checkpoint-style detailer graph. | Available if a connected Comfy backend can accept uploaded/selected image sources. | Usually provider-gated/planned unless the external extension says available. |
| **xAI Grok / cloud API** | Not a local Comfy finish graph. Use API result as a source and stage it into a compatible local Comfy finish route if needed. | Not a local Comfy detailer graph. | Can use a Grok output as a source only if a compatible local Comfy image backend is connected. | Not a direct API render path in the current extension contract. |

## Output source behavior

Finish tools can get a source from:

- the current Preview/final output;
- a selected saved Result;
- an uploaded image file;
- a staged output sent from Results/Post-Fix actions.

When a saved output is staged from Results, Neo should preserve the selected image as the source. It should not silently re-run the original base generation unless the user chooses replay/regenerate.

## Assistant rules

When answering Finish questions, use this guide plus the tool-specific guide. Check the live Image snapshot for:

- active backend profile;
- Model Family;
- Main Model Type / loader;
- Workflow Mode;
- selected/staged source image;
- extension enabled/disabled state;
- route state: Available, Experimental, Provider gated, Planned, or Unsupported.

Do not promise a finish pass can execute just because the panel is visible. Visible can mean “installed but gated.”

For Final Polish Lab, a temporary browser status error does not mean the
ComfyUI job failed. Use its Resume monitoring action when available. Stop
monitoring stops only the browser poll; it does not cancel the provider job or
authorize submitting a replacement job.

Final Polish Lab is distributed separately from Neo Base. Install or update the
complete standalone ZIP/repository through Admin, approve its version-bound
permissions, and restart Neo. Do not copy it into `neo_app`. Custom nodes and
extra models remain ComfyUI-owned; follow each node project's model page and
place files where that selected ComfyUI installation exposes them.

Final Polish replay restoration also does not submit automatically. **Reuse same
polish** waits for a new source; **Polish this output again** binds to the owning
saved Neo result. Original-source and batch restore must revalidate recorded
assets, and batch restore requires confirmation.
