---
guide_id: image.provider_action_regression_matrix
title: Provider-Aware Image Actions
surface: image
scope: built_in
applies_to:
  - image_preview
  - output_inspector
  - provider_routing
  - source_actions
  - reference_actions
  - finish_actions
  - replay
  - output_lineage
tags:
  - image
  - provider
  - preview
  - output-inspector
  - forge
  - comfyui
  - replay
  - lineage
priority: 119
version: 3
updated: 2026-08-16
---

# Provider-Aware Image Actions

Image Preview and Output Inspector expose actions that reuse an existing result. The **currently selected Image profile owns the action**. Neo does not silently send a Forge result to ComfyUI, or a ComfyUI result to Forge, just because another connected backend could perform the operation.

For the detailed Guided/Expert display behavior, see `provider_aware_preview_diagnostics.md`.

## Three action types

### Source actions

Use an output as the source for another generation workflow:

- Img2Img
- Inpaint
- Outpaint

These actions stage the image into the selected provider workflow and wait for you to review settings and press **Generate**.

### Reference actions

Use an output as a reference without replacing the main source:

- ControlNet
- IP Adapter / FaceID where supported

Neo places the result into a compatible reference slot for the selected profile. It does not overwrite an occupied unit silently.

### Finish actions

Run a derived operation on the selected result when the provider supports it, for example:

- High-Res Fix / High-Res Lab;
- ADetailer;
- Identity Rescue / FaceID repair;
- Image Upscale.

Finish actions produce a new result linked to the original output so repeated finishing can preserve parent/root lineage.

## Why an action may be dimmed or hidden

Availability depends on the selected profile and the saved output context. A control can be unavailable because:

- the selected backend does not support that action class;
- a required model, detector, preprocessor, upscaler, custom node, Forge script, or Bridge capability is missing;
- the current result is incompatible with the requested route;
- the output was created with a profile that no longer exists or needs revalidation;
- a backend refresh is still in progress.

Guided mode keeps the message concise. Expert mode exposes more exact requirement and route information.

## Safe workflow when changing providers

If you intentionally want to finish an image with a different backend:

1. select that Image profile yourself;
2. let Neo refresh/revalidate the action state;
3. review any disabled or migration warning;
4. run the action only after the intended profile is shown as the owner.

This prevents accidental cross-provider fallback.

## Replay and derived results

Replay restores recorded generation intent, but Neo revalidates it against the current backend. If models, profiles, or extensions changed since the original image was created, Neo can ask you to review or replace unavailable selections rather than pretending the old route is still executable.

Each successful Finish pass creates a derived result. Output Inspector can use that lineage to show where the image came from and which operation produced the current version.
