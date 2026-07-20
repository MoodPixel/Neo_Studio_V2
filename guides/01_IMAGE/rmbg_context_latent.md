---
guide_id: image.rmbg_context_latent
title: RMBG Context and Latent-Assisted Routes
surface: image
scope: built_in
applies_to:
  - image_workspace
  - flux
  - flux_kontext
  - inpaint
tags:
  - image
  - rmbg
  - context
  - latent
  - flux kontext
priority: 100
version: 1
updated: 2026-07-19
---

# RMBG Context and Latent-Assisted Routes — Phase RMBG-6

RMBG-6 adds a narrow, experimental **Reference Latent Mask** route inside the existing Image inpaint workflow. It uses the installed ComfyUI-RMBG `AILab_ReferenceLatentMask` node to attach the current Image 1 latent and inpaint mask to Flux Kontext conditioning.

## Supported route

The control is visible only for:

- Model Family: **Flux**;
- Main Model Type: **Safetensors / Components** (`diffusion_model`);
- Flux Variant: **Kontext**;
- Workflow Mode: **Inpaint**.

Qwen, SD, Flux GGUF, Flux 2 Klein, text-to-image, img2img, and outpaint routes do not claim this adapter. They remain unchanged until their own context node and latent contract is validated.

## Data flow

```text
Image 1 → existing VAE/inpaint latent ─┐
                                       ├→ AILab_ReferenceLatentMask → conditioning + latent → KSampler
existing inpaint mask ────────────────┘
```

The node requires the exact live inputs `conditioning`, `latent`, `mask`, `expand`, `blur`, and `mask_only`. Neo reads `/object_info` immediately before compilation. If the node, input contract, KSampler, or mask reference is unavailable, Neo blocks before `/prompt` and does not downgrade to ordinary inpaint.

## Controls

- **Enable Reference Latent Mask** turns the route on for the current run.
- **Mask Expand** grows or shrinks the existing mask from `-64` to `64` pixels.
- **Mask Blur** softens the mask from `0` to `64`.
- **Mask-only latent** preserves the node’s noise-mask behavior for the masked region.

The route reuses the existing Source Image and Inpaint Mask panels. It does not add a duplicate upload section or expose a server-local filename.

## Safety boundary

This is a conditioning adapter, not a background-removal graph. It does not run BiRefNet again, replace the selected model family, or use a Qwen VAE/ControlNet contract. The selected live node and route are recorded in runtime metadata for replay and debugging.
