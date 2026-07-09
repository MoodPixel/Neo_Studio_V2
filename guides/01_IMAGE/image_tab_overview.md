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
| **Workspace apps** | Image is organized into **Generation**, **Assets**, **Reference**, **Finish**, and **Results**. Each app owns a different class of controls. |
| **Generation extension area** | Mounts generation-time extension cards such as CFG Fix, LayerDiffuse, Style Stack, Wildcards, and Scene Director where available. |
| **Assets app** | Owns reusable/source assets such as source images, masks/canvas inputs, LoRA Stack / LoRA Library, and Embeddings / Textual Inversion. |
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
- Use `guides/01_IMAGE/image_reference.md`, `guides/01_IMAGE/controlnet.md`, and `guides/01_IMAGE/ip_adapter_faceid.md` for Reference subtab questions.
- Use metadata sidecars only when the user asks about previous generations, saved outputs, prompts, seeds, or history.
- Do not dump raw JSON/metadata unless the user asks for raw trace/debug data.

## Workspace app guide map

The Image tab has workspace apps. The Assistant should answer from the guide that matches the currently selected app or the user's wording.

| Workspace app | Main purpose | Guides |
|---|---|---|
| **Generation** | Base generation controls and generation-time extension cards. | `guides/01_IMAGE/image_generation_extensions.md`, `guides/01_IMAGE/cfg_fix_dynamic_thresholding.md`, `guides/01_IMAGE/layerdiffuse.md`, `guides/01_IMAGE/style_stack.md`, `guides/01_IMAGE/wildcards.md`, `guides/01_IMAGE/scene_director.md` |
| **Assets** | Source/reusable assets, LoRA assets, and Embeddings/Textual Inversion assets. | `guides/01_IMAGE/image_assets.md`, `guides/01_IMAGE/lora_stack.md`, `guides/01_IMAGE/embeddings_textual_inversion.md` |
| **Reference** | Source/reference controls, structural guidance, identity/reference helpers, ControlNet, and IP Adapter / FaceID. | `guides/01_IMAGE/image_reference.md`, `guides/01_IMAGE/controlnet.md`, `guides/01_IMAGE/ip_adapter_faceid.md` |
| **Finish** | Finish/reuse/upscale/repair preparation and post-output actions. | `guides/01_IMAGE/image_finish.md`, `guides/01_IMAGE/high_res_lab.md`, `guides/01_IMAGE/adetailer.md`, `guides/01_IMAGE/image_upscale.md`, `guides/01_IMAGE/final_polish_lab.md` |
| **Results** | Saved outputs, metadata, replay, cleanup, and deletion. | `guides/01_IMAGE/image_results.md`, `guides/01_IMAGE/output_inspector.md` |


## Built-in Reference extensions

The Reference workspace owns image/map/identity guidance tools. These are route-aware and should be interpreted together with the current backend profile, Model Family, Main Model Type, Workflow Mode, installed Comfy nodes, and attached reference assets.

| Reference tool | Main purpose | Guide |
|---|---|---|
| **ControlNet** | Structural guidance from control images or generated maps: canny, depth, pose, lineart, softedge, scribble, normal maps, tile/detail, and route-specific inpaint/outpaint control. | `guides/01_IMAGE/controlnet.md` |
| **IP Adapter / FaceID** | Reference-image guidance for identity, face, character, style, or composition. | `guides/01_IMAGE/ip_adapter_faceid.md` |
| **Reference workspace overview** | Explains when to use ControlNet vs IP Adapter/FaceID and how Reference differs from Generation/Assets/Finish/Results. | `guides/01_IMAGE/image_reference.md` |

ControlNet is for structure. IP Adapter / FaceID is for visual reference and identity. For strict character/person work, use them together: ControlNet for body/pose/layout and IP Adapter/FaceID for identity/reference likeness.

## Built-in Generation extensions

The Generation workspace can mount generation-time Image extensions. These are route-aware and should be interpreted together with the current Model Family, Main Model Type, Workflow Mode, backend profile, and installed custom nodes.

| Extension | Main purpose | Guide |
|---|---|---|
| **CFG Fix / Dynamic Thresholding** | High-CFG sampler/model patch for overbake control. | `guides/01_IMAGE/cfg_fix_dynamic_thresholding.md` |
| **ComfyUI LayerDiffuse** | Transparent RGBA assets, alpha masks, foreground/background compositing. | `guides/01_IMAGE/layerdiffuse.md` |
| **Style Stack** | Prompt-only saved/manual style text merging. | `guides/01_IMAGE/style_stack.md` |
| **Wildcards** | Prompt-only seeded wildcard token expansion and queue variants. | `guides/01_IMAGE/wildcards.md` |
| **Scene Director** | Regional scene planning, region masks, Character Lock, Pair Pose, background lanes, and extension routing. | `guides/01_IMAGE/scene_director.md` |

## Built-in Assets extensions

The Assets workspace owns reusable/source assets. LoRA Stack, LoRA Library, and Embeddings/Textual Inversion should be explained as **Image → Assets** tools, not as base Generation controls.

| Asset tool | Main purpose | Guide |
|---|---|---|
| **LoRA Stack / LoRA Library** | Active LoRA rows, LoRA metadata, triggers, CivitAI enrichment, and route-aware LoRA payloads. | `guides/01_IMAGE/lora_stack.md` |
| **Embeddings / Textual Inversion** | Prompt-token assets such as `embedding:name`, positive/negative chips, folder scan, and CivitAI metadata. | `guides/01_IMAGE/embeddings_textual_inversion.md` |

Scene Director is larger than the normal Generation extension cards because it owns regional subjects, masks, Character Lock, identity/reference routing, Pair Pose, background lanes, and cross-extension assignments. Treat it as a separate guide area: `guides/01_IMAGE/scene_director.md`.

When answering user questions about these cards, the Assistant should prefer the extension-specific guide plus the live Image snapshot. It should not assume a visible extension can execute; check whether the route state is Ready, Experimental, Route gated, Provider gated, or Unsupported.


## Built-in Finish tools

The Finish workspace owns post-generation/refinement tools. It is route-aware and should be explained separately from Generation, Assets, Reference, and Results.

| Finish tool | Main purpose | Guide |
|---|---|---|
| **High-Res Lab** | Highres-style diffusion refine, target scaling, tiled VAE safety, selected-output finish passes. | `guides/01_IMAGE/high_res_lab.md` |
| **ADetailer** | Selective repair for faces, hands, people, products, or manual boxes. | `guides/01_IMAGE/adetailer.md` |
| **Image Upscale** | Standalone upscale utility for selected results or uploaded image batches, with optional CodeFormer / SeedVR2 paths. | `guides/01_IMAGE/image_upscale.md` |
| **Final Polish Lab** | External installed finish cockpit for Relight, Layer Polish, Camera Finish, Look Library, and batch polish plans. | `guides/01_IMAGE/final_polish_lab.md` |

Use Finish when the user wants to improve or modify an output. Use Results when the user wants to inspect, replay, reuse, organize, or delete saved outputs.

## Results and Output Inspector

The Results workspace owns saved outputs from `neo_data`, category/prefix settings, saved-output cards, metadata inspection, replay storage cleanup, source reuse, post-fix staging, and safe delete preview/cascade behavior.

| Results area | Main purpose | Guide |
|---|---|---|
| **Results workspace** | Saved-output list, categories, result filters, output save details, replay storage manager, and result-level actions. | `guides/01_IMAGE/image_results.md` |
| **Output Inspector** | Recipe card with prompt/model/params/source assets/extension metadata, raw JSON debug view, replay, and delete actions. | `guides/01_IMAGE/output_inspector.md` |
