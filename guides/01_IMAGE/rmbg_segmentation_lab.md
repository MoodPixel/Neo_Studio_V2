---
guide_id: image.rmbg_segmentation_lab
title: RMBG Segmentation Lab
surface: image
scope: built_in
applies_to:
  - image_workspace
  - background_removal
  - segmentation_lab
  - prompt_segmentation
  - mask_algebra
tags:
  - image
  - rmbg
  - sam
  - groundingdino
  - sam2
  - sam3
  - segmentation
  - masks
priority: 113
version: 1
updated: 2026-07-19
---

# RMBG Segmentation Lab

Segmentation Lab is a mode inside **Image → Finish → Remove Background**. It keeps the current source upload, selected-result handoff, mask review, output ledger, and non-destructive child-output behavior. It is not a new tab and it does not modify a generation workflow.

## Prompt model

Enter one natural-language object description per line. Neo stores bounded prompt rows under `neo.image.background_removal.segmentation_lab.v1`; empty rows are discarded and the current limit is eight prompt rows. Multiple rows are independent mask-producing requests, so prompts can represent several object classes or regions.

The active Comfy profile chooses one verified adapter from live `/object_info` metadata:

- RMBG Segmentation V1 or V2: SAM + GroundingDINO text-to-box segmentation.
- SAM2 Segmentation: SAM2 plus GroundingDINO text-prompted segmentation.
- SAM3 Segmentation: SAM3 text-prompted segmentation with confidence and instance controls.

Neo only exposes an adapter when its exact node class, required input names, and live model choices are present. It never silently switches to another adapter, downloads a missing dependency, or falls back to Native rembg for a prompt request.

## Mask operations

Prompt masks are combined in the order entered:

| Operation | Result |
|---|---|
| Union | Keep pixels from any prompt mask. |
| Intersection | Keep only overlapping pixels. |
| Subtract | Keep the first mask and remove later prompt masks. |

The same explicit operation contract is available to multi-subject Interactive SAM selection through `sam_mask_operation`; the default remains union for backward compatibility.

## Readiness and troubleshooting

Refresh engines after installing or updating ComfyUI-RMBG. GroundingDINO-backed adapters need their local GroundingDINO/SAM model choices and compatible Python dependencies. SAM2/SAM3 may also require their own runtime packages and model assets. If `/object_info` does not expose the exact adapter contract, the UI blocks the run and shows the reason.

The public catalog returns portable model identifiers only. Comfy roots, user home paths, download URLs, and server-side model paths do not enter the browser payload.
