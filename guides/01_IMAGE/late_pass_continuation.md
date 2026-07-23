---
guide_id: image.late_pass_continuation
title: Late-Pass Continuation from Saved Latents
surface: image
scope: built_in
applies_to:
  - image_results
  - output_inspector
  - latent_capture
  - high_res_fix
  - adetailer
  - replay
  - comfyui
tags:
  - image
  - latent
  - continuation
  - high-res fix
  - adetailer
  - results
priority: 106
version: 2
updated: 2026-07-23
---

# Late-Pass Continuation from Saved Latents

Use **Late-Pass Continuation** when the initial image is already generated and you later want to add or rerun a finishing pass without repeating the initial sampler.

Current supported passes:

- **High-Res Fix**
- **ADetailer**

Future finishing extensions can use this system after Neo defines a stable latent consumer edge and restore-point compatibility for them.

## This is separate from Replay

| Action | What it restores | Best use |
|---|---|---|
| **Replay / Regenerate Recipe** | Prompt, model, parameters, and saved extension recipe | Rebuild or modify a normal generation draft |
| **Use output as source** | Rendered image pixels | Img2Img, Inpaint, Outpaint, Upscale, or other image-based work |
| **Late-Pass Continuation** | Provider-owned latent tensor | Add or rerun supported finishing passes without rerunning initial generation |

Do not use Late-Pass Continuation as a general source-image replacement.

## Before the initial generation

Enable an appropriate latent-save mode before generating. Neo can only offer restore points that were actually captured and still exist in ComfyUI.

The saved metadata may contain:

- `before_high_res_fix`
- `after_high_res_fix`
- `before_adetailer`
- `final_latent`

## Load a continuation

1. Open **Image → Results**.
2. Select the saved result.
3. Find **Late-Pass Continuation** in Output Inspector.
4. Select an available restore point.
5. Click **Load Latent for Late Passes**.
6. Neo opens the Finish workspace and displays **Late-Pass Continuation Active**.
7. Enable High-Res Fix or ADetailer as allowed by that restore point.
8. Review settings and generate.

Neo blocks generation until a supported late pass is enabled.

## Restore-point compatibility

| Restore point | High-Res Fix | ADetailer |
|---|---:|---:|
| **Before High-Res Fix** | Yes | Yes, after the High-Res path |
| **Final Latent** | Yes | Yes |
| **After High-Res Fix** | No | Yes |
| **Before ADetailer** | No | Yes |

A disallowed pass is blocked rather than silently routed through an incorrect graph.

## What Neo does internally

Neo restores the saved recipe for model and prompt compatibility, but it starts with late passes disabled. You explicitly choose what to run.

At compile time Neo:

- verifies the provider-owned latent still exists in ComfyUI;
- inserts `LoadLatent` using the provider-relative reference;
- connects that latent directly to the selected late-pass input;
- leaves the original generation sampler orphaned;
- avoids PNG decoding and Img2Img fallback.

If Neo cannot identify the correct late-pass input, it blocks the run instead of rerunning initial generation.

## UI presets

UI presets may remember the selected latent checkpoint, but only as an **inactive reference**. Loading a UI preset never activates `LoadLatent` and never forces new generations to reuse that checkpoint.

When a preset contains a saved checkpoint, Neo displays **Preset Latent Checkpoint Available**. You can then:

- generate new images normally from a fresh latent;
- click **Load for Late Passes** to explicitly activate the saved checkpoint;
- click **Remove Reference** and update the UI preset if you no longer want it stored.

Older presets that contain an active latent replay context are migrated to this inactive-reference behavior when they are loaded. Prompts, model choices, dimensions, extension settings, and other preset fields remain unchanged.

## Exit continuation

Use **Exit Continuation** in the active banner when you are finished or want to return to normal generation.

Neo also clears continuation state when you send an output to:

- Img2Img;
- Inpaint;
- Outpaint;
- Image Upscale;
- another pixel-space Finish action.

This prevents a latent continuation and rendered-image source from being active at the same time.

## Missing latent files

The latent remains executable only while its original Comfy provider artifact exists. Neo's retained copy under `neo_data` is for storage and cleanup; it is not automatically uploaded back into Comfy.

When the provider artifact is gone, Neo stops before queueing and asks you to start a clean generation or select another checkpoint.

## Portability

Latent references are stored as provider-relative names such as:

```text
NeoStudio_latent/job-123/final_latent.latent [output]
```

Neo does not store or require a personal Comfy installation path.
