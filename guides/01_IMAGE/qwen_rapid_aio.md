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
version: 6
updated: 2026-07-18
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

## P2.3 — CFG and generation error behavior

The **Safetensors / Bundled** route now exposes **CFG Scale** because its graph uses `KSampler.cfg` directly.

- Start at **1.0** for Rapid/distilled AIO checkpoints unless the model documentation recommends another value.
- Raise CFG carefully; Rapid models are usually designed around low guidance.
- Neo preserves the exact selected CFG in the Comfy graph.

This is the normal sampler CFG field. It is **not** the separate **CFG Fix / Dynamic Thresholding** extension. CFG Fix remains route-gated for Qwen Rapid AIO until that model-patch path is physically validated.

When Comfy fails during execution, Neo now reports the backend node and exception reason. A successful completion with no output files remains recoverable, but a real Comfy execution error is shown as failed instead of being masked by a no-output recovery state.

## Phase 3 — Stitch Images route

The **Stitch Images** route is available for **Qwen Rapid AIO** `img2img` / `edit` workflows on the `checkpoint_aio` and `gguf` loaders. The shared capability also supports the source-conditioned routes listed in [Image Stitching](image_stitch.md). Qwen Rapid AIO keeps its multi-lane behavior; other families use one stitched composite as their Image 1/source anchor. Neo supports both the canonical `ImageStitch` class and the `AILab_ImageStitch` class exposed by ComfyUI-RMBG.

When a direct source image is present, it remains **Image 1** and Stitch outputs use the remaining live Qwen image lanes. If no direct source is selected, a complete enabled Stitch Group may be promoted to the base **Image 1** lane for Stitch-only Img2Img:

- the first complete Stitch Group becomes Image 1;
- later Stitch Groups keep their selected optional lanes;
- an incomplete or disabled group cannot satisfy the Img2Img source requirement.

Stitch outputs normally use the remaining live Qwen image lanes:

| Live Qwen encoder lanes | Maximum stitched outputs | Raw source images consumed |
|---:|---:|---:|
| 3 total (`image1`–`image3`) | 2 | 4 |
| 4 total (`image1`–`image4`) | 3 | 6 |

Neo reads the installed `TextEncodeQwenImageEditPlus` and stitch-node inputs at runtime. A stitch output lane must be free; direct source lanes and stitch outputs cannot silently overwrite each other. The route selects `ImageStitch` or `AILab_ImageStitch` from the live Comfy node catalog and adapts the direction field (`direction` or `concat_direction`) before queueing. If neither class is present, the route reports a validation error before queueing.

Use the existing source-image state as the canonical upload/asset ownership layer. The implemented UI exposes Stitch Images as a separate collapsible subsection under Source Image, so each stitch pair is visually distinct while still sharing the same asset picker, upload validation, and replay metadata. Qwen Inpaint/Outpaint and other supported families use the stitched result as a single-source mask/canvas anchor.

## Phase 4 — Stitch Images UI

For a supported source-conditioned route, open the **Stitch Images** subsection inside **Source Image**. It reuses the existing image upload endpoint and asset ownership rules, but keeps each stitch pair separate from direct Image 1/Image 2/Image 3 source lanes.

- **Image 1** remains the base source image.
- Each **Stitch Group** accepts **Image A** and **Image B**.
- Each group selects a free optional Qwen output lane and exposes direction, spacing, spacing color, and match-size controls.
- The UI reads the live Qwen encoder lane count when available: two groups on a three-lane encoder, or three groups on a four-lane encoder.
- Direct source lanes and stitch output lanes are not silently overwritten; a collision is shown before queueing.

Stitch Images is hidden for text-only routes. On Qwen Rapid AIO it is submitted as the `neo.image.qwen_stitch.v1` envelope for backward compatibility; the provider applies the shared source-anchor contract on other supported routes.

## Phase 5 — Upload and provider handoff

Stitch uploads use the existing `/api/image/source-image` validator and Neo_Data ownership boundary. The browser keeps the returned `source_id`/stored filename as the portable canonical reference; the API URL is used for preview, not as a ComfyUI filename.

Before queueing, the Comfy provider resolves every Stitch `LoadImage` node from the Neo-owned source image into ComfyUI's input folder. The compiled runtime metadata records the resolved Comfy filenames and handoff status. Missing or unreadable Stitch inputs remain a blocking preflight error and are never silently dropped.

## Phase 6 — Documentation and support boundary

Public documentation now describes Stitch Images as an implemented Rapid AIO feature, documents the shared Neo_Data-to-ComfyUI upload boundary, and records the supported capacity rules. The ControlNet guide separately documents the Qwen VAE requirement: a Qwen ControlNet apply path must receive the active Qwen VAE, not only the ControlNet model.
