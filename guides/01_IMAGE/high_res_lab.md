---
guide_id: image.high_res_lab
title: Image High-Res Lab
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - high_res_lab
  - highres
  - high_res_fix
  - upscale_refine
  - diffusion_refine
tags:
  - image
  - finish
  - high res
  - upscale
  - diffusion refine
  - selected output
priority: 111
version: 1
updated: 2026-07-09
---

# Image High-Res Lab

**High-Res Lab** is the built-in Image → Finish tool for high-resolution finishing. It is used when the user wants a larger, cleaner, more detailed output while still using the current Image generation route/prompt/model context.

It is different from **Image Upscale**:

- **High-Res Lab** can do a highres-style diffusion refine pass.
- **Image Upscale** is a standalone utility for resizing/upscaling selected or uploaded images without normal prompt context.

## Supported route shape

High-Res Lab is primarily for local Comfy checkpoint routes.

| Route | State |
|---|---|
| ComfyUI / ComfyUI Portable + SDXL checkpoint + Generate/Img2Img/Inpaint/Outpaint | Available |
| ComfyUI / ComfyUI Portable + SD 1.5 checkpoint + Generate/Img2Img/Inpaint/Outpaint | Available |
| Forge / A1111 style routes | Planned/gated in this V2 UI contract |
| Flux, Qwen, ZImage, HiDream component/GGUF/API routes | Do not promise unless the live route snapshot says available |
| xAI Grok / cloud API route | Not a local Comfy high-res graph |

## Main controls

| Control | Meaning |
|---|---|
| **Enable High-Res Lab payload** | Adds High-Res Lab settings to the next generation or staged finish pass. |
| **Profile** | Applies a preset group such as Custom, Gentle polish, Balanced finish, Detail push, Bigger finish, Latent rebuild, or Upscale only. |
| **High-res mode** | Chooses the finish strategy. `Latent upscale + rebuild` works like a highres/refine pass; `Pixel upscale + diffusion refine` upscales the image then refines it. |
| **Resize method** | Pixel resize method before/around refinement. Common values include Lanczos, Bicubic, Bilinear, Area, and Nearest-exact. |
| **Scale** | Multiplier for output size. Higher values cost more VRAM/time and can cause artifacts. |
| **Steps** | Number of refinement sampler steps. More steps can increase detail but can overwork the image. |
| **Denoise** | How much the finish pass can change the image. Low values preserve, high values rebuild. |
| **CFG** | Prompt strength for the finish pass on SD-style routes. |
| **Sampler / Scheduler** | Can reuse the main sampler/scheduler or override them for the finish pass. |
| **Upscaler model** | Optional upscale model loaded from Comfy upscale model catalog. |
| **Tiled VAE safety** | Helps reduce memory pressure during high-resolution encode/decode. |
| **Tile size / Tile overlap** | Controls tiled processing. Smaller tiles reduce VRAM use; overlap helps avoid seams. |

## Profiles

| Profile | Use it for |
|---|---|
| **Gentle polish** | Small cleanup while preserving the original output. |
| **Balanced finish** | General high-res/detail pass. |
| **Detail push** | More visible detail; watch for over-sharpening or face drift. |
| **Bigger finish** | Larger size delivery. Needs more VRAM. |
| **Latent rebuild** | Stronger highres rebuild behavior. |
| **Upscale only** | Resize/upscale without diffusion refine; better for clean delivery resizing. |

## Source behavior

High-Res Lab can run from a normal generation draft or from a staged selected output. If the user sends a saved output to High-Res Lab from Results, Neo should use that selected image as the source, not silently restart from the base prompt.

## Recommended starter settings

For SDXL checkpoint:

```text
Profile: Balanced finish
Scale: 1.5–2.0
Steps: 10–20
Denoise: 0.20–0.40
CFG: reuse main CFG or slightly lower
Tiled VAE: On for large images
```

Use lower denoise when the user wants the exact image preserved. Use higher denoise only when they want the result rebuilt or more stylized.

## Assistant rules

When the user asks about High-Res Lab:

- check whether the route is ComfyUI + SDXL/SD1.5 checkpoint;
- check whether High-Res Lab is enabled or only available;
- distinguish High-Res Lab from Image Upscale;
- recommend conservative denoise before high denoise;
- warn that cloud/API outputs need to be staged into a compatible local Comfy finish route.
