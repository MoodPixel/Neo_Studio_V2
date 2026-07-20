---
guide_id: image.rmbg_dual_engine_routes
title: ComfyUI-RMBG and BiRefNet Route Boundary
surface: image
scope: built_in
applies_to:
  - background_removal
  - comfyui_rmbg
  - rmbg_node
  - birefnet
tags:
  - image
  - background removal
  - ComfyUI-RMBG
  - BiRefNet
  - live object info
  - portable paths
priority: 113
version: 1
updated: 2026-07-19
---

# ComfyUI-RMBG and BiRefNet Route Boundary

The Remove Background panel supports two independent ComfyUI node families. The shared UI is an orchestration surface; the selected route owns its own live contract, model list, graph, and metadata label.

## Route matrix

| Route | Live class contract | Model source | Output path |
|---|---|---|---|
| Comfy BiRefNet | `LoadRembgByBiRefNetModel`, `RembgByBiRefNetAdvanced` | BiRefNet model catalog | BiRefNet graph, optional mask review/refinement |
| ComfyUI-RMBG · RMBG Node | `RMBG` with `image` and `model` inputs | `RMBG` node choices from live `/object_info` | Generic `RMBG` graph, optional shared edge post-processing |

The generic node returns foreground image and mask outputs. Neo saves the foreground PNG and, when enabled, converts the mask output to the shared grayscale alpha-mask PNG. The generic route does not load or invoke the BiRefNet-specific nodes.

## Readiness rules

- The selected Comfy profile is authoritative.
- The generic route is available only when `RMBG` appears in live `/object_info`, required inputs are exact, and at least one model choice is exposed.
- The browser receives portable model identifiers and input names only. Server roots and personal absolute paths are never returned or stored in the public payload.
- A stale saved model remains visible as unavailable; it is not silently replaced by a BiRefNet model.
- A missing generic contract blocks the explicit generic route. Smart routing may choose another ready local route according to its fallback policy and records that decision.

## Installation expectation

Install and restart the upstream [ComfyUI-RMBG node](https://github.com/1038lab/ComfyUI-RMBG), then use **Refresh engines** in Remove Background. The generic node's own model setup and model cache remain owned by ComfyUI-RMBG. Neo does not download third-party node packages or model weights.

## Verification

The route is covered by focused contract tests for live catalog separation, exact generic graph compilation, resolver selection, and incomplete-contract blocking. GitHub is not modified by this local implementation package.
