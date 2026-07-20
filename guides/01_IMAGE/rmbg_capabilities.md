---
guide_id: image.rmbg_capabilities
title: RMBG Capability Inventory
surface: image
scope: built_in
applies_to:
  - image_workspace
  - background_removal
  - rmbg
  - birefnet
  - sam
  - segmentation
  - matting
  - mask_utilities
  - image_stitch
tags:
  - image
  - rmbg
  - capability discovery
  - safety gate
  - comfyui
  - portable paths
priority: 111
version: 1
updated: 2026-07-18
---

# RMBG Capability Inventory

Phase RMBG-0 establishes a safe discovery layer for the installed [ComfyUI-RMBG](https://github.com/1038lab/ComfyUI-RMBG) ecosystem. It is an inventory and safety-lock phase only: Neo does not expose new RMBG controls, compile new RMBG graphs, or replace the existing Background Removal execution contracts.

## What Neo already uses

The current Image → Finish → Remove Background utility already supports:

- Comfy BiRefNet with strict live-node and model readiness checks.
- Neo-native rembg/ONNX fallback routing.
- Native Interactive SAM and Comfy Impact Pack SAM discovery.
- Detector discovery for multi-subject selection.
- Non-destructive mask review, edge refinement, transparent PNG output, and optional alpha-mask output.

The new inventory makes the surrounding installed capability surface visible for later, separately verified phases. It does not imply that every discovered node can be combined with every model family or workflow.

## Inventory categories

The capability schema groups exact live node matches into:

- background removal
- semantic segmentation
- semantic regions such as face, clothes, and fashion segmentation
- matting
- mask utilities
- compositing, including image stitch candidates
- batch image/mask list tools
- context conditioning and reference-mask candidates

The upstream RMBG project currently documents background-removal models, segmentation and matting tools, mask/compositing utilities, SAM-family tooling, Florence2/Yolo helpers, and image-list helpers. Those are adoption candidates for later phases, not an execution promise in this phase. Refer to the [RMBG README](https://github.com/1038lab/ComfyUI-RMBG) and [upstream update log](https://github.com/1038lab/ComfyUI-RMBG/blob/main/update.md) for the installed node project's current feature inventory.

## Safety contract

The existing `/api/extensions/background-removal/models` response includes `rmbg_inventory`:

- `/object_info` from the selected live ComfyUI provider is authoritative.
- A capability is available only when one of its exact candidate class names matches live metadata.
- Live input names are recorded for inspection; input compatibility is not guessed.
- Unknown RMBG-like node names are surfaced separately and never auto-enabled.
- Model catalogs contain portable identifiers only; absolute paths, URI roots, and server-only roots are removed from the public response.
- The inventory declares `graph_mutation: false` and `execution_allowed: false` for Phase RMBG-0.

An unavailable `/object_info` catalog is a blocker for future Comfy-backed RMBG routes. Existing Background Removal readiness and native fallback behavior remain unchanged.

## RMBG-2 adoption: Segmentation Lab

RMBG-2 consumes the semantic-segmentation inventory through a separate live adapter contract. It currently supports exact `Segment`, `SegmentV2`, `SAM2Segment`, and `SAM3Segment` interfaces when their required inputs and live model choices are present. The route accepts up to eight natural-language prompts and combines their masks with union, intersection, or subtract. The inventory remains descriptive; the segmentation-lab resolver is the execution gate.

## Adoption boundary

Later phases may introduce one capability at a time with a dedicated workflow contract, model-family matrix, UI control, and fixture-backed tests. Candidates include object/region segmentation, matting, mask cleanup, batch processing, image stitching helpers, and reference-mask conditioning. Each addition must preserve portable paths, exact live-node checks, and explicit route ownership.

### RMBG-6 context-latent capability

The installed RMBG node exposes `AILab_ReferenceLatentMask` for Flux Kontext-style inpaint conditioning. Neo discovers that exact node and its `conditioning`, `latent`, `mask`, `expand`, `blur`, and `mask_only` inputs at runtime. The implemented route is limited to Flux + Safetensors/Components + Kontext + Inpaint and reuses the existing Image 1 latent and inpaint mask. It is not a generic Qwen, SD, GGUF, or standalone background-removal path.
