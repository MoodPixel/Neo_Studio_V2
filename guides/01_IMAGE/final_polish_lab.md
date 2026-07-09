---
guide_id: image.final_polish_lab
title: Image Final Polish Lab
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - final_polish_lab
  - relight
  - layer_polish
  - camera_finish
  - batch_polish
  - look_library
tags:
  - image
  - finish
  - final polish
  - relight
  - ic-light
  - layerstyle
  - propost
  - batch polish
priority: 88
version: 1
updated: 2026-07-09
---

# Image Final Polish Lab

**Final Polish Lab** is an external installed Image → Finish extension. It is not a repo-shipped built-in tool like High-Res Lab, ADetailer, or Image Upscale.

It renders under:

```text
Image → Finish → External Extensions → Image · Final Polish Lab
```

Its mount slot is:

```text
image.finish.external.final_polish_lab
```

## Purpose

Final Polish Lab combines three finishing lanes:

| Lane | Purpose |
|---|---|
| **Relight** | IC-Light-style relighting plan. |
| **Layer Polish** | LayerStyle-style compositing / layer polish plan. |
| **Camera Finish** | ProPost-style camera/color finish plan. |

It also includes Look Library presets, batch polish planning, dependency diagnostics, metadata, replay bundles, and Output Inspector summaries.

## Current runtime boundary

In the current closeout state, Final Polish Lab compiles provider-neutral workflow plans. It does not automatically submit a ComfyUI `/prompt` render.

Current boundary:

```text
No ComfyUI /prompt submission
No actual rendered output generation
No provider graph mutation
No mixed-lane graph merging
No node/package/model installation
No raw image byte storage
```

If the user asks why it does not produce an image, explain that the cockpit can prepare validated finish plans and metadata, but render submission remains gated.

## Built-in looks

Common looks include:

```text
clean_commercial
soft_cinematic
product_hero
moody_poster
natural_photo_cleanup
anime_card_polish
dark_fantasy_cover
luxury_ad_finish
```

These presets map to safe Neo payload fields only. They do not install nodes, inject raw Comfy graphs, or store image bytes.

## Assistant rules

When the user asks about Final Polish Lab:

- call it an **external Image Finish extension**;
- do not describe it as a normal built-in direct-render tool;
- explain Relight / Layer Polish / Camera Finish as finish-plan lanes;
- check the live extension status before saying it is installed or active;
- if the user wants actual upscaling/repair today, suggest High-Res Lab, ADetailer, or Image Upscale depending on the task.
