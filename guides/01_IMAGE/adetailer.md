---
guide_id: image.adetailer
title: Image ADetailer
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - adetailer
  - selective_repair
  - face_repair
  - hand_repair
  - manual_boxes
  - detailer_passes
tags:
  - image
  - finish
  - adetailer
  - repair
  - faces
  - hands
  - detailer
  - impact pack
priority: 111
version: 1
updated: 2026-07-09
---

# Image ADetailer

**ADetailer** is the built-in Image → Finish selective repair tool. Use it when the generated image is mostly good, but a local region needs repair: face, eyes, hands, person, clothing, object, product detail, or a manually drawn target.

ADetailer is a finish-stage extension. It should run after the base generation and after other structure/style/reference tools. If High-Res Lab is active, ADetailer should repair the high-res output, not the earlier base decode.

## Supported route shape

| Route | State |
|---|---|
| ComfyUI / ComfyUI Portable + SDXL checkpoint + Generate/Img2Img/Inpaint | Available |
| ComfyUI / ComfyUI Portable + SDXL checkpoint + Outpaint | Planned/gated |
| ComfyUI / ComfyUI Portable + SD 1.5 checkpoint + Generate/Img2Img/Inpaint | Experimental |
| ComfyUI / ComfyUI Portable + SD 1.5 checkpoint + Outpaint | Planned/gated |
| Flux/Qwen/ZImage/HiDream component or GGUF routes | Unsupported/gated in the current ADetailer graph contract |
| xAI Grok / cloud API routes | Not a local Comfy ADetailer graph |

ADetailer depends on local detection/detailer nodes and detector assets. Impact Pack/SEGS-style paths and detector models must be installed and discoverable.

## Header and state chips

| Chip | Meaning |
|---|---|
| **Enabled / Disabled** | Whether ADetailer is applied to this generation/finish run. |
| **Available / Experimental / Route gated / Unsupported** | Whether the current route can support the tool. |

Disabled does not mean broken. It means the extension exists but is not currently applied.

## Shared defaults

Shared defaults affect the whole detailer stack and are inherited by pass cards unless a pass overrides them.

| Field | Meaning |
|---|---|
| **Custom detector path** | Optional folder for detector models. |
| **Custom SAM path** | Optional SAM model folder, usually `ComfyUI/models/sams`. |
| **SAM preset / SAM model** | Segmentation model selection for mask refinement. |
| **Detector provider** | Usually Ultralytics or ONNX. |
| **Custom classes / notes** | Optional detector class hints such as person, face, hand, clothing. |
| **Confidence** | Detection confidence threshold. Higher is stricter. |
| **Top-K** | Limits how many detected targets are kept. 0 means no explicit cap. |
| **BBox grow** | Expands or shrinks the detected box before detail pass. |
| **Mask blur** | Softens mask edge to avoid hard seams. |
| **Denoise** | Strength of local repair. Low preserves; high changes more. |
| **Steps** | Detail pass sampler steps. |
| **CFG cap** | Caps prompt strength for repair. Lower values reduce overcorrection. |
| **Use main prompts** | Reuses the main positive/negative prompt as context. |
| **Force inpaint pass** | Routes repair through an inpaint-style pass even if the base mode was not inpaint. |

## Detailer pass cards

ADetailer supports one primary pass plus optional additional passes.

| Pass field | Meaning |
|---|---|
| **Mode** | Face, hands, person, or custom repair target. |
| **Detector type/model** | Detection path/model for this pass. |
| **Target order** | Which targets are repaired first: auto, left-to-right, largest-first, etc. |
| **Start index / Count** | Which detected targets to repair. Useful for multi-face images. |
| **Min / Max area** | Filters detections by size. |
| **Target mode** | Auto detect or manual boxes. |
| **Reference lock** | Optional identity/style/control reference policy. |
| **Positive / Negative prompt** | Pass-specific repair prompts. |

## Manual boxes and visual target picker

Use manual boxes when detection fails or the target is not a normal face/hand/person.

Manual boxes can be written as:

```text
xywh:120,80,300,300,#1
xyxy:120,80,420,380,#2
12%,10%,28%,28%,#3
```

The visual target picker can use the current output/source, detect targets, add canvas boxes, remove targets, sync canvas/text, and export/import snapshots. Per-target prompts are compiled with `[SEP]` chunks.

## Recommended starter settings

For face repair:

```text
Mode: face
Confidence: 0.30–0.45
BBox grow: 8–16
Mask blur: 4–12
Denoise: 0.10–0.25
Steps: 8–16
CFG cap: 5–8
Use main prompts: On
Force inpaint: On
```

For hands or difficult local repairs, denoise may need to be higher, but warn the user that identity/detail drift can increase.

## Assistant rules

When the user asks about ADetailer:

- explain it as Image → Finish selective repair;
- check route support and detector/SAM readiness before promising execution;
- recommend lower denoise for identity/face preservation;
- use manual boxes when auto detection misses the region;
- do not suggest it for cloud/API-only execution unless a local Comfy finish backend is connected.
