---
guide_id: image.scene_director_runtime_readiness
title: Scene Director Runtime Readiness and Troubleshooting
surface: image
scope: built_in
applies_to:
  - image
  - scene_director
  - comfyui
  - regional_prompting
  - regional_lora
tags:
  - scene-director
  - troubleshooting
  - regional-lora
  - krea2
  - flux2-klein
  - z-image
priority: 108
version: 2
updated: 2026-08-16
---

# Scene Director Runtime Readiness and Troubleshooting

Use this guide when Scene Director is visible but a route, regional prompt, or regional LoRA is unavailable. For the current supported-family matrix and feature behavior, see `scene_director_current.md`.

## First checks

1. Use a **ComfyUI / ComfyUI Portable** Image profile.
2. Select a family, loader, and workflow that Scene Director currently supports.
3. Make sure ComfyUI is running.
4. Restart ComfyUI after installing or updating required custom nodes.
5. Refresh/Test the selected ComfyUI profile in Neo Admin.
6. Return to Image and check Scene Director readiness again.

Scene Director keeps the editor available where possible, but individual execution capabilities remain gated when their required nodes are missing.

## Current runtime dependencies

### Krea 2 RAW / Turbo

Modern Krea Scene Director uses the external **ComfyUI-Krea2-Regional** runtime. ComfyUI must expose:

```text
Krea2RegionalBuilder
Krea2ApplyRegional
```

If they are missing, install/update `januspluto/ComfyUI-Krea2-Regional`, restart ComfyUI, and refresh the Neo ComfyUI profile. Krea does not fall back to a global LoRA when the regional runtime is missing.

### FLUX.2 Klein / Z-Image

These modern routes use Neo's lightweight regional path. Region-targeted LoRA execution can require the bundled `NeoRegionalLoRADelta` custom node.

If Neo reports that node as missing, copy the bundled `neo_scene_director` package from the Neo Studio root into:

```text
<ComfyUI-root>/custom_nodes/neo_scene_director
```

Then restart ComfyUI completely and refresh/Test the selected ComfyUI profile.

### SDXL / SD 1.5 classic Scene Director

Classic checkpoint routes require the bundled `NeoSceneDirectorV054` runtime. The same `neo_scene_director` custom-node package provides the Neo Scene Director nodes used by supported classic routes.

## If a regional LoRA is unavailable

Check that:

- the LoRA exists in Neo's LoRA Stack;
- the LoRA row is assigned to the intended Scene Director region;
- the LoRA matches the selected model family/scale where Neo can determine compatibility;
- the region has a valid box/mask;
- the required regional runtime node is available for that family;
- the route is not Outpaint, which is currently planned-gated for Scene Director.

Neo does not fall back to a global LoRA when a region-targeted LoRA cannot be executed safely.

## Krea 2 recommended starting settings

Current Krea defaults are intentionally conservative:

- **Adaptive Masks:** Refine boxes
- **Exclusive Masks:** On
- **Restrict Image Attention:** Off
- **Layout in Base:** Position hints
- **Region Lock Strength:** 0.4

Start there before changing isolation controls. Restrict Image Attention is optional rather than a default because it can hurt some layouts.

## If subjects leak or duplicate

Try these in order:

1. reduce overlap between region boxes;
2. keep each subject prompt focused on that subject rather than rewriting the whole scene locally;
3. keep the main prompt responsible for global composition, camera, lighting, and relationships;
4. use Exclusive Masks where supported;
5. avoid stacking unrelated global LoRAs with region-targeted LoRAs while troubleshooting;
6. test the same setup without the regional LoRA to determine whether the problem comes from layout or the LoRA itself.

Regional isolation is a controlled generation technique, not a guarantee that every pixel outside a region will be mathematically unchanged.

## If Scene Director disappears after changing route

Scene Director is route-aware. Check the current family, loader, and workflow against `scene_director_current.md`. Changing from a supported checkpoint/components/GGUF route to an unsupported family or planned-gated Outpaint route can intentionally disable execution.

Saved region layouts may remain available for replay/planning even when the current route cannot execute them.

## What to share when asking for help

Include:

- selected Image profile;
- family, loader, workflow, and model filename;
- ComfyUI build and relevant custom-node versions;
- whether the route is classic or modern;
- Scene Director readiness/disabled message;
- LoRA filename and family/scale metadata when regional LoRA is involved;
- GPU/VRAM if the failure occurs only during generation.

Do not share private tokens or unnecessary personal filesystem paths.
