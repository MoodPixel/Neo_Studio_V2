---
guide_id: image.lanpaint_route_family
title: LanPaint Inpaint and Outpaint
surface: image
scope: built_in
applies_to:
  - image
  - inpaint
  - outpaint
  - comfyui
  - comfyui_portable
tags:
  - lanpaint
  - inpaint
  - outpaint
  - crop-stitch
  - masks
  - comfyui
priority: 104
version: 2
updated: 2026-08-16
---

# LanPaint Inpaint and Outpaint

LanPaint is an optional masked-edit engine for supported local ComfyUI / ComfyUI Portable image routes. It can be selected for **Inpaint** and **Outpaint** when the current model family, loader, and live ComfyUI node set are compatible.

For the full Native-vs-LanPaint comparison, see `inpaint_outpaint_engines.md`.

## When to use LanPaint

Choose LanPaint when you want a supported model family to process a masked edit through the LanPaint sampler path rather than Neo's Native masked-edit path.

Good uses include:

- replacing or repairing part of an image;
- editing a subject while protecting the rest of the frame;
- extending an image with Outpaint;
- working with routes where LanPaint is the supported masked-edit engine.

Neo keeps your selected family, loader, source image, mask, outpaint padding, and normal sampling controls. Choosing LanPaint changes the masked-edit engine, not the whole model route.

## Basic Inpaint workflow

1. Open **Image → Generation**.
2. Choose a family/loader that exposes **Inpaint**.
3. Add the source image.
4. Create or load the inpaint mask.
5. Set **Engine → LanPaint** when it is available.
6. Choose whether **Crop & Stitch** should be enabled.
7. Set prompt and normal generation parameters.
8. Generate and inspect the result.

If LanPaint is disabled for the selected family, use the available engine or refresh the ComfyUI profile after installing the required nodes.

## Basic Outpaint workflow

1. Select **Outpaint**.
2. Add the source image.
3. Set the amount to extend on the left, top, right, and/or bottom.
4. Choose **LanPaint** when the active route supports it.
5. Set feather/mask and generation parameters.
6. Generate.

Neo creates the padded canvas and outpaint mask, then passes that masked canvas into the selected engine. Outpaint is not treated as ordinary Img2Img.

## Crop & Stitch

**Crop & Stitch** is independent from the engine selection.

### LanPaint + Crop & Stitch ON

Neo focuses processing around the masked area, runs the LanPaint family path, restores the processed crop to its original size, and composites it back into the untouched image.

This is useful when the masked area is small relative to the full image and you want more effective detail at the working resolution.

### LanPaint + Crop & Stitch OFF

The full source or padded outpaint canvas is processed with the mask. This can be preferable when the edit depends on broad scene context.

## Common required custom nodes

The exact requirements depend on the active model-family adapter. The common LanPaint crop/stitch path uses:

```text
LanPaint
ComfyUI-InpaintEasy
ComfyUI-KJNodes
ComfyUI-GGUF        (GGUF routes only)
```

Install missing packs through ComfyUI Manager when available, then fully restart ComfyUI and refresh/Test the selected ComfyUI profile in Neo.

Some families require additional loader or conditioning nodes. Neo's live route state is authoritative; installing LanPaint alone does not guarantee that every family becomes available.

## Sampling controls

Steps, CFG/guidance, sampler, scheduler, denoise, seed, batch count, and family-specific guidance remain user-owned where the selected route exposes them. LanPaint should not silently replace explicit values just because a masked engine was selected.

## If LanPaint is unavailable

Check:

- the workflow is Inpaint or Outpaint;
- the selected family/loader has a LanPaint adapter;
- ComfyUI is running and the intended profile is selected;
- required custom nodes are installed;
- ComfyUI was restarted after installing nodes;
- Neo's ComfyUI profile was refreshed/Tested after the restart.

If the route is still disabled, read the visible disabled reason instead of forcing the workflow. Neo intentionally fails closed when a required family-specific node or model component is missing.

## Krea 2 Turbo custom-model readiness fallback (2026-08-28)

LanPaint readiness for `krea2_turbo + diffusion_model` now trusts Neo's explicit Krea route contract when a selected custom model filename is **opaque** rather than self-identifying.

- If the selected model exists in the connected Comfy catalog and does **not** positively contradict the chosen Krea 2 Turbo route, LanPaint no longer rejects it just because the filename lacks `krea`/`turbo`.
- The same fallback now applies to the selected VAE slot for custom/experimental Krea-compatible VAEs already allowed by the shared Krea 2 compatibility contract.
- Exact contradictions still fail closed. Example: a selected filename that clearly resolves to `RAW` on a Turbo route is still blocked.
- The text encoder remains architecture-strict and must still satisfy the Qwen3-VL-4B Krea contract.

Neo records these accepted custom-model/VAE cases as runtime-override warnings so the UI stays honest: the route is allowed, but Neo is trusting the explicit route contract and letting Comfy validate the custom file at runtime.

## Krea 2 selected-asset UI reconciliation (2026-08-28)

The LanPaint readiness card is rendered from a cached backend capability snapshot created during `/object_info` discovery. That snapshot is intentionally built without the user's current model selection, so a custom Krea 2 Turbo filename can still appear as `missing_compatible_model` even though the later runtime evaluator accepts the selected asset.

Neo now reconciles the live selected Krea 2 assets against the snapshot before rendering readiness:

- selected custom model must be a concrete selection and exist in the exact connected Comfy catalog;
- selected custom VAE must likewise exist in the exact connected catalog;
- false `missing_compatible_model` / `missing_compatible_vae` snapshot blockers are removed only for those exact selected assets;
- explicit RAW/Base model evidence on a Turbo route is still not overridden;
- node/signature/text-encoder/catalog/stale-snapshot blockers remain authoritative;
- accepted selections are surfaced as runtime-override warnings and stored in the client capability result for diagnostics.

This UI reconciliation mirrors the backend selected-asset fallback added to `lanpaint_capabilities.py`; both halves are required for custom model names to become selectable in the actual Neo interface.
