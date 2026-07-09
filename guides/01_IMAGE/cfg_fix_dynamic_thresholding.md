---
guide_id: image.cfg_fix_dynamic_thresholding
title: CFG Fix / Dynamic Thresholding
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generations
  - cfg_fix_dynamic_thresholding
  - cfg fix
  - dynamic thresholding
  - sdxl
  - sd15
  - checkpoint
  - generate
tags:
  - image
  - cfg fix
  - dynamic thresholding
  - sampler
  - cfg
  - high cfg
  - route aware
priority: 112
version: 1
updated: 2026-07-09
---

# CFG Fix / Dynamic Thresholding

**CFG Fix / Dynamic Thresholding** is a built-in Image Generation extension that reduces high-CFG overbaking by patching the Comfy sampler model path through Dynamic Thresholding nodes.

Use it when high CFG values cause harsh contrast, blown highlights, crunchy detail, oversaturation, or overbaked anatomy/detail.

## What it patches

When active and route-ready, Neo inserts a Dynamic Thresholding node before the sampler consumes the model.

| Mode | Node | Use |
|---|---|---|
| **Simple** | `DynamicThresholdingSimple` | Safer default. Requires the base dynamic-thresholding node. |
| **Full** | `DynamicThresholdingFull` | Advanced control. Only use when the optional Full node exists. |

Required custom node package:

```text
sd-dynamic-thresholding
```

## Fields

| Field | What it does | Advice |
|---|---|---|
| **Apply CFG Fix** | Enables the extension for the current generation route. | Leave off for normal/low CFG generations. Enable when CFG is high and output looks overcooked. |
| **Preset** | Chooses a tuning preset. | Start with **Safe detail** or **Smart auto**. Use Advanced/custom only when you know the mimic/percentile values you want. |
| **Mode** | Selects Simple or Full node behavior. | Use **Simple** first. Use **Full** only if the Full node is installed and the route validates. |
| **Mimic CFG** | The CFG level the dynamic-thresholding patch tries to mimic. | Lower than the real CFG can reduce harshness while keeping prompt strength. |
| **Threshold percentile** | Controls percentile clipping/threshold behavior. | Higher values are lighter; lower values can clamp harder. Keep changes small. |
| **Resolved values** | Shows the actual mimic/percentile values after preset resolution. | Use this to confirm what Neo will send to the extension payload. |
| **Low-CFG auto-skip** | Neo can skip the patch when CFG is already low. | If CFG is low, the extension may correctly show skipped/disabled even when checked. |

## Route support

Current normal support is intentionally conservative.

| Family | Loader | Workflow | State |
|---|---|---|---|
| **SDXL** | Safetensors / Checkpoint | Generate | Available |
| **SD 1.5** | Safetensors / Checkpoint | Generate | Experimental |
| Flux / Qwen / ZImage / HiDream / GGUF / component routes | Component or GGUF | Generate | Planned/gated unless promoted by route matrix |
| Img2Img / Inpaint / Outpaint | Any | Source-image modes | Planned/gated unless route matrix says otherwise |
| xAI Grok Imagine / API routes | API model | API image | Unsupported / hidden |

## How to explain it to users

Good answer pattern:

```text
CFG Fix helps when high CFG makes the image look too harsh or overbaked. On your current route it is [ready/gated]. If ready, start with Safe detail or Smart auto, keep Simple mode first, and only use Advanced mimic/percentile values if you need tighter control.
```

Do not promise CFG Fix on routes that do not expose a safe sampler MODEL patch point.
