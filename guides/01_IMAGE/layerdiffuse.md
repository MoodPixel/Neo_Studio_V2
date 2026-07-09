---
guide_id: image.layerdiffuse
title: ComfyUI LayerDiffuse
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generations
  - reference
  - layerdiffuse
  - transparent asset
  - rgba
  - alpha mask
  - background aware blend
  - sdxl
  - sd15
  - checkpoint
  - generate
  - img2img
tags:
  - image
  - layerdiffuse
  - rgba
  - transparent png
  - alpha
  - foreground
  - background
  - compositing
  - route aware
priority: 111
version: 1
updated: 2026-07-09
---

# ComfyUI LayerDiffuse

**LayerDiffuse** is a built-in Image extension for transparent RGBA generation and foreground/background compositing through ComfyUI LayerDiffuse workflows.

It is different from prompt-only extensions. LayerDiffuse can replace the base workflow with a LayerDiffuse-specific graph, so it is heavily route-gated.

## Best use cases

- Transparent PNG character/object assets.
- RGB + alpha split outputs.
- Overlay FX such as glow, smoke, rain, HUD, energy, glass, or particles.
- Foreground/background blending where the route supports the required images.
- Extracting a foreground from a composite when a matching background image is provided.

## Required custom nodes

LayerDiffuse requires ComfyUI LayerDiffuse nodes such as:

```text
LayeredDiffusionApply
LayeredDiffusionJointApply
LayeredDiffusionCondApply
LayeredDiffusionCondJointApply
LayeredDiffusionDiffApply
LayeredDiffusionDecode
LayeredDiffusionDecodeRGBA
LayeredDiffusionDecodeSplit
```

If those nodes are missing, the card can be visible but disabled or blocked.

## Fields

| Field | What it does | Advice |
|---|---|---|
| **Enable LayerDiffuse for this generation** | Enables LayerDiffuse for the current route. | Only enable when you actually need transparency/compositing. It is not a general quality booster. |
| **Mode** | Chooses the LayerDiffuse task, such as Transparent Asset or Background-Aware Blend. | Pick the mode that matches the desired output. Different modes require different image slots. |
| **Decode** | Chooses output decode type: RGBA PNG, RGB + Alpha Split, or Preview Only. | Use **RGBA PNG** for transparent assets. Use **RGB + Alpha Split** when you need separate mask/sidecars. |
| **Output** | Chooses where the result goes: Preview only, New run, Append, or Replace target. | Use **New run** for normal generations. Use Replace only when a target is selected and confirmed. |
| **SD** | Compatibility / SD version behavior. | Auto is usually best. LayerDiffuse mainly supports SDXL/SD-style checkpoint workflows. |
| **Weight** | LayerDiffuse influence strength. | Keep near default first. Too high can create weird transparency or compositing artifacts. |
| **Sub-batch** | Internal sub-batch size. | Higher can be faster but heavier. Reduce if VRAM errors happen. |
| **Blend strength** | Background-aware blend preservation strength. | Lower tends to preserve foreground more; higher blends more strongly. |
| **Foreground image** | Required for blend/generate-background modes. | Upload or send from preview/asset controls. |
| **Background image** | Required for foreground-on-background/blend/extract modes. | Upload or send from preview/asset controls. |
| **Output bundle / advanced IDs** | Shows advanced output/source IDs. | Use only for debugging or exact replay handoff. |

## Ready modes

| Mode | What it does | Requires | Output |
|---|---|---|---|
| **Transparent Asset** | Prompt → transparent PNG asset. | Prompt | RGBA image, alpha mask, preview. |
| **RGB + Alpha Split** | Prompt → RGB image plus alpha sidecar. | Prompt | RGBA/RGB/alpha bundle. |
| **Foreground on Background** | Prompt + background → foreground designed for the scene. | Prompt + background image | RGBA foreground/alpha/preview. |
| **Background-Aware Blend** | Foreground + background → coherent blended composite. | Prompt + foreground + background | Blended image/preview/alpha. |
| **Extract Foreground** | Composite/source + known background → extracted foreground. | Source + background | RGBA/RGB/alpha/preview. |
| **Transparent Overlay FX** | Prompt → transparent overlay effect asset. | Prompt | RGBA/RGB/alpha/preview. |

Some extra modes are visible as blocked/experimental until verified workflow exports exist. Do not describe blocked modes as ready.

## Route support

| Family | Loader | Workflow | State |
|---|---|---|---|
| **SDXL** | Safetensors / Checkpoint | Generate | Available |
| **SDXL** | Safetensors / Checkpoint | Img2Img | Experimental / image-conditioned modes |
| **SD 1.5** | Safetensors / Checkpoint | Generate | Experimental |
| Flux / Qwen / ZImage / HiDream / GGUF / API | Component/GGUF/API | Any | Not supported unless route matrix explicitly promotes it |
| Inpaint / Outpaint | Any | Inpaint/Outpaint | Unsupported / gated |

## How to explain it to users

Good answer pattern:

```text
LayerDiffuse is for transparent PNG/alpha/compositing workflows, not normal image quality improvement. On your current route it is [ready/gated]. For a transparent asset, use Transparent Asset + RGBA PNG + New run. For background-aware blend, upload both foreground and background images first.
```
