---
guide_id: image.rmbg_engine_unification
title: Background Removal Engine Unification
surface: image
scope: built_in
applies_to:
  - background_removal
  - birefnet
  - rmbg_node
  - rembg
  - sam
  - interactive_sam
  - mask_review
  - commercial_providers
tags:
  - image
  - background removal
  - engine routing
  - smart routing
  - fallback
  - comfyui
priority: 110
version: 1
updated: 2026-07-19
---

# Background Removal Engine Unification

RMBG-1 gives Image → Finish → Remove Background one engine-resolution contract for the routes that already exist. It does not merge their model runtimes or silently change their execution semantics. Each route keeps its own strict adapter, output persistence, and readiness checks while exposing one common catalog and one explicit fallback vocabulary.

## Unified engine catalog

The existing `/api/extensions/background-removal/models` response now includes `engine_catalog` with portable readiness rows for:

- Smart Routing for standard segmentation.
- Comfy BiRefNet for standard segmentation and reviewed-mask refinement.
- ComfyUI-RMBG generic RMBG Node for standard segmentation with its own live model choices.
- Neo Native rembg/ONNX for standard segmentation.
- Comfy Impact Pack SAM for box-addressed Interactive SAM.
- Neo Native ONNX SAM for box and Keep/Remove point selection.
- Commercial Background Removal API profiles, always external-upload and never fallback-enabled.

The catalog is a readiness description, not an execution grant. The queue route revalidates the selected profile, live Comfy metadata, model choice, workflow mode, subject selection, provider consent, and route-specific node contract immediately before execution.

## Workflow ownership

| Workflow | Unified route choices | Fallback boundary |
|---|---|---|
| Standard segment | Smart, Comfy BiRefNet, ComfyUI-RMBG generic RMBG, Native rembg, commercial profile | Smart may move between ready local Comfy/native routes according to the selected policy; commercial never participates in fallback |
| Reviewed-mask refinement | Comfy mask refinement | No segmentation rerun and no native/commercial fallback |
| Interactive SAM | Comfy Impact SAM or Native ONNX SAM | Auto prefers compatible Comfy box routing, then Native ONNX; correction points require Native ONNX |

## Fallback policy

`never`, `on_unavailable`, and `on_unavailable_or_queue_failure` are represented in one catalog contract. A fallback is recorded with the requested engine, resolved engine, resolved model, policy, and reason. Queue-failure fallback remains limited to Smart standard segmentation and only after a Comfy queue attempt; it does not apply to reviewed-mask refinement, Interactive SAM correction-point constraints, or commercial providers.

## Safety and privacy

- Exact live `/object_info` and the active provider profile remain authoritative.
- Model identifiers in the public catalog are portable names only.
- No personal filesystem path is required or exposed.
- Commercial API credentials remain server-side and per-run upload consent is required.
- Existing output metadata, replay payload, and Results persistence continue to record the resolved route.

The generic ComfyUI-RMBG node route is now activated as a separate adapter with its own live node/model contract. Other ComfyUI-RMBG capabilities from the Phase 0 inventory still require their own workflow adapter, UI contract, and route-specific tests before activation.
