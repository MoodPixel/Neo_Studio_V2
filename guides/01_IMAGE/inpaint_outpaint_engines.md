# Inpaint and Outpaint Engines

Status: current masked-edit authority
Date: 2026-08-07

## What the user chooses

For local ComfyUI and ComfyUI Portable masked workflows, **Inpaint** and **Outpaint** share one engine selector:

- **Native** — Neo uses the selected model-family graph with ComfyUI's base masked-conditioning path. Where the compiler exposes a normal sampler anchor, Neo rebases it through `InpaintModelConditioning` and `DifferentialDiffusion`.
- **LanPaint** — Neo keeps the selected family/model conditioning but replaces the normal masked sampler path with the LanPaint family adapter.

The selected engine does not change the model family, loader, user sampling parameters, source image, mask ownership, or outpaint dimensions.

## Crop & Stitch

**Crop & Stitch** is an independent checkbox. It is not an inpainting engine.

### Native + Crop & Stitch OFF

Neo processes the full masked canvas through the Native masked-edit graph.

### Native + Crop & Stitch ON

Neo wraps the Native graph with the external **ComfyUI-Inpaint-CropAndStitch** nodes:

- `InpaintCropImproved` — shown in ComfyUI as **✂️ Inpaint Crop**
- `InpaintStitchImproved` — shown in ComfyUI as **✂️ Inpaint Stitch**

The cropped image and mask are passed to `InpaintModelConditioning`; the decoded result is then stitched back into the original/padded canvas. Neo does not use the plugin's separate outpaint-extension behavior. Outpaint padding remains owned by Neo/Comfy core through `ImagePadForOutpaint`, so padding cannot be applied twice.

When a family graph already ends in its own composite or post-decode image node, Neo reassigns final output ownership to the stitched result instead of leaving Preview/Save connected to the pre-stitch family node. That keeps the live/final output consistent with the actual stitched image.

If the checkbox is enabled and either custom node is missing, Neo blocks compilation instead of silently running a full-frame workflow. If the stitched result cannot become the final output owner, Neo also fails closed instead of saving the wrong image.

### LanPaint + Crop & Stitch ON

Neo uses the existing LanPaint crop/context/restore graph. This path already uses:

- `CropByMask` — **ComfyUI-InpaintEasy**
- `ImageResizeKJv2` — **ComfyUI-KJNodes**
- `GrowMaskWithBlur` — **ComfyUI-KJNodes**
- `ImageCompositeMasked` — ComfyUI core
- `LanPaint_KSampler` or the family-specific LanPaint sampler — **LanPaint**

GGUF LanPaint families additionally require the relevant **ComfyUI-GGUF** loader.

### LanPaint + Crop & Stitch OFF

Neo bypasses the crop, processing resize, restore resize, stitch-mask, and composite stages. LanPaint receives the full source/padded canvas plus the full mask, and the decoded full-frame result becomes the output.

## Outpaint

Outpaint is a real masked-edit route, not an Img2Img alias.

For both Native and LanPaint, Neo owns the requested left/top/right/bottom padding and feathering through the core `ImagePadForOutpaint` node. The resulting padded IMAGE and MASK become the engine inputs.

For LanPaint, the same family-aware LanPaint graph used for inpainting receives this generated outpaint mask. This is intentional: upstream LanPaint supports arbitrary masks and publishes image outpainting workflows.

## Scene Director parity on modern Native inpaint (IMG-SD1D)

When modern `lightweight_regional` Scene Director is enabled on a supported Native inpaint route, regional conditioning is combined **before** `InpaintModelConditioning`. The provider KSampler stays connected to `InpaintModelConditioning` outputs 0/1/2 so source-image/mask conditioning metadata and the masked latent remain authoritative.

The compact Scene Director subject-count bridge is merged into the provider global text upstream of that wrapper. It is not encoded as a separate full-canvas conditioning entry. Krea 2 Turbo zero-negative validation traces through `InpaintModelConditioning` and confirms the upstream negative source is still `ConditioningZeroOut`.

This rule applies only when Scene Director is active. Normal Native and LanPaint masked workflows retain their existing compiler topology.

## User parameter authority

The Parameter Truth contract remains active for both engines. Explicit user values such as Steps, CFG, Sampler, Scheduler, Denoise, Batch Count, Seed, and family guidance controls must not be silently replaced by engine defaults.

Crop & Stitch changes the spatial region being sampled. It does not grant permission to change sampling parameters.

## Required custom nodes

### Native Crop & Stitch only

Install:

```text
ComfyUI-Inpaint-CropAndStitch
https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch.git
```

Use ComfyUI Manager and search for **ComfyUI-Inpaint-CropAndStitch**, or install the Git URL into `ComfyUI/custom_nodes` and restart ComfyUI.

Neo detects these exact node classes:

```text
InpaintCropImproved
InpaintStitchImproved
```

Native masked generation with Crop & Stitch OFF requires no extra crop/stitch pack; `InpaintModelConditioning`, `DifferentialDiffusion`, and `ImagePadForOutpaint` are ComfyUI core nodes.

### LanPaint engine

Install the packs required by the active family adapter. The common LanPaint crop/stitch path expects:

```text
LanPaint                         → LanPaint_KSampler
ComfyUI-InpaintEasy             → CropByMask
ComfyUI-KJNodes                 → ImageResizeKJv2, GrowMaskWithBlur
ComfyUI-GGUF (GGUF routes only) → GGUF model loader
```

LanPaint itself can be installed from ComfyUI Manager by searching for **LanPaint**, or from:

```text
https://github.com/scraed/LanPaint.git
```

Neo's live backend capability report remains authoritative: a family may require additional loader/conditioning nodes beyond this common list.

## Runtime order

Native, no crop:

```text
source/mask or padded outpaint canvas
→ family conditioning anchors
→ InpaintModelConditioning
→ DifferentialDiffusion
→ selected sampler
→ VAE decode
→ output
```

Native, Crop & Stitch:

```text
source/mask or padded outpaint canvas
→ InpaintCropImproved
→ InpaintModelConditioning
→ DifferentialDiffusion
→ selected sampler
→ VAE decode
→ InpaintStitchImproved
→ output
```

LanPaint, Crop & Stitch:

```text
source/mask or padded outpaint canvas
→ CropByMask
→ processing resize + mask refinement
→ family latent/conditioning graph
→ LanPaint sampler
→ VAE decode
→ restore size + stitch mask
→ ImageCompositeMasked
→ output
```

LanPaint, no crop:

```text
source/mask or padded outpaint canvas
→ family latent/conditioning graph
→ LanPaint sampler
→ VAE decode
→ output
```

## Fail-closed rules

Neo blocks or preserves the family-native graph rather than inventing graph anchors when:

- Native Crop & Stitch is enabled but the two plugin nodes are absent;
- Outpaint has no positive padding on any side;
- the selected LanPaint family adapter is not live-ready;
- a compiler uses a non-standard sampler/guider graph that does not expose the safe Native masked-edit anchors.

A non-standard sampler graph being preserved is not equivalent to claiming the Native base engine was injected. Runtime metadata records the actual masked-edit state.

## Phase 7 — family-specific masked-engine gating

The Native/LanPaint selector now follows the exact family compatibility matrix. Neo does not assume that every family with an Inpaint/Outpaint label has both engines.

Current important restrictions include SD 3.5, Flux 2 Dev, Anima, Ideogram 4, and HiDream masked routes that are LanPaint-only in the verified local contract. Krea 2 RAW keeps its Native masked path while its unresolved LanPaint adapter stays gated. The UI preserves the user's chosen engine state but disables an engine that is not executable on the active route rather than silently changing it.
