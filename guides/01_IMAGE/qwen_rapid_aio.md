---
guide_id: image.qwen_rapid_aio
title: Qwen Rapid AIO Image Workflow
surface: image
scope: built_in
applies_to:
  - image_workspace
  - qwen_rapid_aio
  - checkpoint_aio
  - gguf
  - qwen_image_edit
  - qwen_image_edit_2509
tags:
  - image
  - qwen
  - rapid aio
  - image edit
  - parameters
  - model
priority: 100
version: 2
updated: 2026-07-09
---

# Qwen Rapid AIO Image Workflow

**Qwen Rapid AIO** is a compact Image workflow route intended to make Qwen-style generation/editing easier to run. In the Image dropdown it appears as a **Model Family**. Its normal Main Model Types are:

- **Safetensors / Bundled** (`checkpoint_aio`)
- **GGUF** (`gguf`)

Use this family when the user wants a simpler Qwen route with fewer exposed component fields than full split-component model routes.

## Main controls

| Control | Qwen Rapid AIO behavior |
|---|---|
| **Model Family** | Select **Qwen Rapid AIO**. |
| **Main Model Type** | Use **Safetensors / Bundled** for the AIO checkpoint route or **GGUF** for the quantized route. |
| **Workflow Mode** | Generate, Img2Img, Inpaint, and Outpaint are available through route readiness. Edit-style behavior can be available through Qwen edit routes/profiles. |
| **Main Model** | Select the bundled Qwen AIO model or matching GGUF file. |
| **VAE / encoders** | Usually hidden for the bundled route. GGUF/component readiness may expose extra required fields. |
| **Prompt** | Describe the target image or edit clearly. For source-image edits, include preservation rules. |
| **Negative Prompt** | Use only when the selected route supports it. Some Qwen/edit/API-style routes may not behave like SD negative prompting. |
| **Denoise** | Relevant for source-image modes. Lower preserves more of the source; higher changes more. |
| **Latent Capture** | Use Off for normal runs. Use Final/Milestone/Full only when testing resume/debug/branch behavior. |

## Suggested starter settings

Use the live Image snapshot first when available. If the user asks for starter settings without a snapshot:

- Start with a preset resolution like **1024×1024**, **832×1216**, or the target platform size.
- Use a moderate step count first.
- Keep guidance/CFG moderate when the selected route exposes it.
- Use random seed for exploration.
- Lock/reuse seed when improving a good output.
- Use Img2Img when source composition should be preserved.
- Use Inpaint only when a mask is provided.
- Use Outpaint only when canvas/padding controls are set.

## Qwen edit prompting style

For Qwen-style image edit requests, the prompt should say:

1. what to change;
2. what must stay unchanged;
3. how the new element should match lighting, angle, perspective, and image quality.

Example pattern:

```text
Edit the provided image by adding [new subject/detail]. Keep the original person, pose, clothing, background, camera angle, lighting, lens perspective, and overall image quality unchanged. The added element should look naturally present in the same scene, with matching shadows, scale, depth, and color temperature.
```

For identity/source preservation, avoid vague prompts like “make it better.” Use direct constraints such as “preserve the original face and background.”

## Qwen Image Edit vs Qwen Rapid AIO

- **Qwen Rapid AIO** is the compact bundled/quantized Qwen route.
- **Qwen Image Edit** and **Qwen Image Edit 2509** are Qwen-native edit families. Use them when the task is specifically source-image editing or multi-source editing and the route/profile supports it.
- **Qwen Image Edit 2509** can expose newer multi-source behavior where live route readiness confirms it.

When answering a Qwen question, mention the selected Model Family, Main Model Type, Workflow Mode, source image/mask status, and whether the route is bundled, GGUF, or edit-focused.

## V25.9.20 P2.1 — Selecting the bundled Safetensors checkpoint

Qwen Rapid AIO bundled models are discovered by ComfyUI through its normal checkpoint loader. In Neo Studio, choose **Qwen Rapid AIO** and **Safetensors / Bundled**; the **Main Model** dropdown reads the same installed checkpoint catalog used by standard checkpoint families.

The bundled route does not need a separate VAE, text encoder, diffusion model, or MMProj file. Neo passes the selected filename exactly as ComfyUI reports it.

When a newly downloaded model is missing from the dropdown:

1. Confirm the file is inside a ComfyUI checkpoint model path.
2. Refresh or reconnect the ComfyUI backend so `/object_info` is rebuilt.
3. Select the checkpoint explicitly before generating.

Neo no longer guesses `Qwen-Image-Edit-Rapid-AIO.safetensors`. A missing selection is reported as a readiness error instead of submitting a likely invalid filename.
