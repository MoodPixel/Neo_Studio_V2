---
guide_id: image.ux_transparency
title: Image UX Transparency
surface: image
scope: built_in
applies_to:
  - image_workspace
  - parameters
  - results
  - output_inspector
  - lora_stack
tags:
  - image
  - ux
  - transparency
  - generation setup
  - output inspector
  - lora
priority: 118
version: 1
updated: 2026-08-07
---

# Image UX Transparency

Phase 8 separates **artist-facing execution truth** from **developer diagnostics**.

## Parameters

The **Generation Setup** card summarizes what Neo intends to run:

- family and main model type;
- workflow mode;
- Native or LanPaint for masked workflows;
- Crop & Stitch when enabled;
- Standard KSampler or ClownsharKSampler;
- one, two, or three sampling stages;
- inter-stage latent-upscale count.

This card is read-only. The actual Parameters controls remain the source of truth.

## Output Inspector

The Output Inspector has a **Generation Setup** summary built from recorded runtime/actual parameters. It shows the workflow architecture and the important applied values: size, steps, CFG, sampler, scheduler, denoise, seed, and batch.

When Parameter Integrity has verified the run, the summary shows **Parameters verified**. A concrete mismatch remains prominent and its dedicated mismatch panel remains visible. Detailed parameter traces and Multi-KSampler physical-node diagnostics are Expert-only when there is no error.

Raw metadata JSON and extension payload keys are Expert-only. This keeps normal/guided Results readable without removing diagnostic evidence.

## LoRA Stack

The normal LoRA card contains LoRA-specific controls and concise route gating only. Provider serialization strings, exact graph-loader details, route keys, and matrix diagnostics are Expert-only.

The cleanup does not weaken provider exact-catalog validation, graph proof, Parameter Truth, Parameter Integrity, family compatibility, or replay contracts.
