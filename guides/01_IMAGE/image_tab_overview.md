---
guide_id: image.overview
title: Image Tab Overview
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generate
  - img2img
  - inpaint
  - outpaint
  - edit
  - qwen_rapid_aio
  - comfy
  - assistant
tags:
  - image
  - generation
  - parameters
  - prompt
  - preview
  - workflow
  - output inspector
priority: 100
version: 2
updated: 2026-07-09
---

# Image Tab Overview

The Image tab is Neo's workspace for image generation, image editing, inpainting, outpainting, reference/source-image workflows, preview, metadata capture, replay, and output inspection.

When the selected Assistant scope is **Image Workspace**, questions about Image models, prompts, parameters, extensions, seeds, latent capture, previews, saved metadata, and output cleanup are inside scope. The Assistant should not say the Image tab is outside its capabilities. It should use this guide, the Image parameter guide, the model-family guide, and the live Image snapshot.

## Core areas

| Area | Purpose |
|---|---|
| **Workspace command strip** | Selects Model Family, Main Model Type, Workflow Mode, backend readiness, and run controls. |
| **Prompt panel** | Holds Prompt Library, Positive Prompt, Negative Prompt, Prompt Assist, style chips, and saved prompt pairs. |
| **Parameters panel** | Holds model/component selectors, VAE, sampler/scheduler, width/height, size presets, steps, CFG/guidance, seed controls, batch count, denoise, prompt conditioning, latent capture, and route-specific inpaint/outpaint controls. |
| **Extensions panel** | Mounts route-compatible extension cards such as ControlNet, IP Adapter, LoRA Stack, Style Stack, Wildcards, LayerDiffuse, CFG Fix, High-Res Lab, Image Upscale, and Scene Director where available. |
| **Preview panel** | Shows live preview, selected final preview, and batch thumbnails. Output management belongs in Results/Output Inspector, not the preview panel. |
| **Results / Output Inspector** | Reads Neo-owned metadata sidecars, source asset records, cleanup reports, run timing, replay data, and safe cascade delete manifests. |

## Basic workflow

1. Pick the **Image backend profile** in Admin/Backend or the Image backend selector.
2. Choose **Model Family**.
3. Choose **Main Model Type**.
4. Choose **Workflow Mode**.
5. Fill prompt/source/mask/canvas inputs required by that mode.
6. Check the **Parameters** panel.
7. Use **Validate** if the route may be missing a model, source image, mask, custom node, or backend connection.
8. Click **Generate**.
9. Use Preview for live/final viewing and Results/Output Inspector for saved outputs, metadata, replay, and deletion.

## Workflow modes

| Mode | Use it for | Required input |
|---|---|---|
| **Generate** | Creating a new image from text. | Prompt + valid model route. |
| **Img2Img** | Reworking a source image while preserving layout/composition. | Source image + prompt + denoise. |
| **Inpaint** | Editing or repairing a masked area. | Source image + mask + prompt. |
| **Outpaint** | Extending the canvas beyond the source image. | Source image/canvas + outpaint padding/canvas settings. |
| **Edit** | Instruction-style image editing when the selected route/backend exposes it. | Source image + edit instruction/prompt. |

## Important route behavior

- **Model Family** is not the same as **Main Model Type**. Family chooses the model/workflow family; Main Model Type chooses the loader/file contract.
- **Workflow Mode** changes which route Neo validates and which fields are required.
- The Parameters panel is route-aware. Fields can hide or change when switching family, loader, backend, or workflow mode.
- Cloud image profiles such as **xAI Grok Imagine** hide SD/Comfy-specific fields and expose API-specific defaults instead.
- Preview is for viewing. Output Inspector is for metadata, result management, replay, and deletion.

## Assistant behavior

When answering Image questions:

- Use the live Image snapshot for current values.
- Use `guides/01_IMAGE/image_parameters.md` for field explanations.
- Use `guides/01_IMAGE/image_model_families.md` for supported families/loaders/modes.
- Use metadata sidecars only when the user asks about previous generations, saved outputs, prompts, seeds, or history.
- Do not dump raw JSON/metadata unless the user asks for raw trace/debug data.

## Built-in Generation extensions

The Generation workspace can mount built-in Image extensions. These are route-aware and should be interpreted together with the current Model Family, Main Model Type, Workflow Mode, backend profile, and installed custom nodes.

| Extension | Main purpose | Guide |
|---|---|---|
| **CFG Fix / Dynamic Thresholding** | High-CFG sampler/model patch for overbake control. | `guides/01_IMAGE/cfg_fix_dynamic_thresholding.md` |
| **ComfyUI LayerDiffuse** | Transparent RGBA assets, alpha masks, foreground/background compositing. | `guides/01_IMAGE/layerdiffuse.md` |
| **LoRA Stack / LoRA Library** | LoRA rows, pass targeting, metadata, triggers, CivitAI enrichment. | `guides/01_IMAGE/lora_stack.md` |
| **Style Stack** | Prompt-only saved/manual style text merging. | `guides/01_IMAGE/style_stack.md` |
| **Wildcards** | Prompt-only seeded wildcard token expansion and queue variants. | `guides/01_IMAGE/wildcards.md` |
| **Scene Director** | Regional scene planning, region masks, Character Lock, Pair Pose, background lanes, and extension routing. | `guides/01_IMAGE/scene_director.md` |

Scene Director is larger than the normal Generation extension cards because it owns regional subjects, masks, Character Lock, identity/reference routing, Pair Pose, background lanes, and cross-extension assignments. Treat it as a separate guide area: `guides/01_IMAGE/scene_director.md`.

When answering user questions about these cards, the Assistant should prefer the extension-specific guide plus the live Image snapshot. It should not assume a visible extension can execute; check whether the route state is Ready, Experimental, Route gated, Provider gated, or Unsupported.
