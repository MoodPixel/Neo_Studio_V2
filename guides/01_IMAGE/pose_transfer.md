---
guide_id: image.pose_transfer
title: Qwen 2511 Pose Transfer
surface: image
scope: built_in
applies_to:
  - image
  - reference
  - qwen_image_edit_2511
  - img2img
  - edit
  - dwpose
  - pose
  - lora
  - controlnet
tags:
  - pose transfer
  - qwen 2511
  - dwpose
  - anypose
  - image 1
  - image 2
  - image 3
  - model only lora
priority: 119
version: 1
updated: 2026-08-07
---

# Qwen 2511 Pose Transfer

Pose Transfer is an experimental method inside **Image → Reference → ControlNet & Pose**. It is separate from conventional Pose Control even though both can use DWPose.

## Source lane contract

When Pose Transfer is selected, Neo reuses the normal Qwen multi-reference lanes:

| Lane | Role |
|---|---|
| **Image 1** | Subject / person whose appearance should be preserved. |
| **Image 2** | Pose reference. DWPose reads this image at runtime. |
| **Image 3** | Reserved runtime lane. Neo injects the generated DWPose pose map here. |

Do not upload a third reference while Pose Transfer is active. The UI reserves Image 3 and generation blocks if a stale Image 3 source is still present.

## Runtime graph

The intended graph is:

```text
Image 1 ───────────────────────────────┐
Image 2 ──> DWPose ──> Qwen Image 3 ──┼─> TextEncodeQwenImageEditPlus
                                      │
Qwen base MODEL ─> Pose Base LoRA ─> Pose Helper LoRA ─> sampling
```

The selected pose LoRAs use `LoraLoaderModelOnly`, not the normal MODEL+CLIP LoRA loader. If the Qwen graph contains `ModelSamplingAuraFlow`, the pose LoRA chain is inserted before it. Otherwise Neo rewires the active sampler model input directly.

The generated DWPose map is supplied to both positive and negative Qwen edit conditioning so the reference lanes stay aligned. The pose instruction is appended only to the positive prompt.

## Current support

Pose Transfer is enabled only when all of these are true:

- backend is local ComfyUI or ComfyUI Portable;
- family is Qwen Image Edit 2511;
- loader is Safetensors / Components (`diffusion_model`) or GGUF;
- workflow is Img2Img or Edit;
- Image 1 and Image 2 are present;
- Image 3 is free;
- DWPose is detected in the live Comfy node catalog;
- `LoraLoaderModelOnly` is detected and publishes the LoRA catalog;
- both selected pose LoRA names resolve exactly in that catalog.

The method fails closed if any requirement is missing. It does not fall back to a generic ControlNet model, SDXL graph, or normal LoRA loader.

## Pose map preview

**Preview Pose Map** is optional. It runs the existing ControlNet map-preview service against Image 2 so the user can inspect DWPose output before generation. The preview asset is not treated as a normal ControlNet input. Generation builds a fresh DWPose node from Image 2 inside the final graph.

## Coexistence rules

The first rollout permits one Pose Transfer unit per generation and does not combine that unit with normal ControlNet units. Pose Control remains available as the conventional alternative when a compatible pose ControlNet model is preferred.
