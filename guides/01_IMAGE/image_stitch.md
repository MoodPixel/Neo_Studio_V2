---
guide_id: image.stitch
title: Image Stitching
surface: image
status: implemented
---

# Image Stitching

Stitch Images is a shared source-conditioning capability in the Image workspace. It combines two uploaded images with the installed ComfyUI stitch node and routes the result into the selected model family without hardcoding a machine path.

## Where it appears

Open the collapsible **Stitch Images** section under **Source Image**. The section reuses the existing source-image upload and Neo_Data ownership boundary, while keeping stitch pairs separate from direct Image 1/Image 2/Image 3 controls.

The UI is visible only for source-conditioned routes that support Stitch Images:

| Family | Loader(s) | Supported workflows | Stitch behavior |
|---|---|---|---|
| SDXL, SD15 | `checkpoint` | Img2Img, Inpaint, Outpaint | One composite Image 1/source |
| Flux | `diffusion_model`, `gguf` | Img2Img, Inpaint, Outpaint | One composite Image 1/source |
| Flux 1 Fill | `diffusion_model` | Inpaint, Outpaint | One composite Image 1/source |
| Flux 2 Klein | `diffusion_model`, `gguf` | Img2Img, Edit, Inpaint, Outpaint | One composite Image 1/source |
| Qwen Image, Qwen Image Edit 2509 | `diffusion_model`, `gguf` | Img2Img, Edit, Inpaint, Outpaint | One composite Image 1/source |
| Qwen Rapid AIO | `checkpoint_aio`, `gguf` | Img2Img, Edit | Qwen image lanes; multiple groups |
| Qwen Rapid AIO | `checkpoint_aio`, `gguf` | Inpaint, Outpaint | One composite Image 1/source |
| Z-Image, Z-Image Turbo | `diffusion_model`, `gguf` | Img2Img, Inpaint, Outpaint | One composite Image 1/source |

Text-only routes, including HiDream’s current txt2img route, do not show the section.

## Capacity

Every Stitch Group consumes exactly two raw images. Non-Qwen-lane routes accept one enabled group for the first rollout, producing one composite source. Qwen Rapid AIO Img2Img/Edit uses the live `TextEncodeQwenImageEditPlus` image lanes:

| Live Qwen image lanes | Maximum enabled Stitch Groups | Raw inputs |
|---:|---:|---:|
| 3 (`image1`–`image3`) | 2 | 4 |
| 4 (`image1`–`image4`) | 3 | 6 |

Image 1 remains the direct source when one is supplied on Qwen Rapid AIO. If no direct source is selected, the first complete Stitch Group can become Image 1, which enables Stitch-only Img2Img. On non-Qwen routes, the stitched composite is always the single source anchor.

## ComfyUI contract

Neo selects the installed `ImageStitch` or `AILab_ImageStitch` class from the live object-info catalog. The compiler adapts both common node schemas:

- `image1` + `image2` pair inputs;
- list-valued `images` input used by ComfyUI-RMBG’s `AILab_ImageStitch`.

For the shared-source routes, Neo first lets the family compiler construct its normal VAE, latent, mask, and canvas branch. It then replaces only the source `LoadImage` node with the stitch node. This keeps each family’s latent and mask contract intact. Inpaint still requires a compatible mask; Outpaint still requires padding.

For Qwen Rapid AIO Img2Img/Edit, the route instead connects each stitch result to a free Qwen image-conditioning lane. Direct source lanes and Stitch output lanes cannot silently overwrite each other.

## Upload, provider handoff, and replay boundary

Stitch inputs use the existing `/api/image/source-image` validator. Neo retains the returned `source_id` and stored filename as portable references, resolves Neo-owned inputs into the backend input folder before queueing, and records the resolved handoff for replay. Browser preview URLs are not used as ComfyUI filenames.

The payload remains compatible with the existing `neo.image.qwen_stitch.v1` envelope while the provider exposes the backend-neutral `neo.image.stitch.v2` route metadata. The compatibility name is retained so existing saved UI state and Qwen payloads continue to load.

## Current boundary

The first shared rollout supports one composite group on non-Qwen-lane routes. Future phases may add multiple composites for families that expose multiple conditioning inputs, but they should be introduced only with a verified family-specific graph contract.
