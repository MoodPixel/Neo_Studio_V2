---
guide_id: image.provider_action_regression_matrix
title: Provider Action Regression Matrix
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
  - regression
  - forge
  - comfyui
  - replay
  - lineage
priority: 119
version: 2
updated: 2026-08-03
---

# Provider Action Regression Matrix

Neo includes a deterministic regression runner for the complete Preview and Output Inspector action system:

```text
python scripts/validate_provider_actions_phase13.py
```

To save a machine-readable report:

```text
python scripts/validate_provider_actions_phase13.py --json-out <OUTPUT_PATH>/provider_action_matrix.json
```

The runner does not require a live GPU backend. It validates the contracts and route decisions that must be true before a request reaches Forge Neo or ComfyUI.

## Coverage

The matrix covers:

- the 13-action canonical registry;
- Forge and Comfy selected-profile action evaluation;
- unsupported cloud/local-finish behavior;
- Forge Bridge native post-Hires capability pairing;
- Source handoffs for Img2Img, Inpaint, and Outpaint;
- Reference handoffs for ControlNet and IP Adapter;
- Finish dispatch for High-Res Fix, ADetailer, Identity Rescue, and Image Upscale;
- replay sanitization and provider-cache cleanup;
- repeated Finish lineage parent/root/depth behavior;
- Preview and Output Inspector renderer/dispatcher parity;
- success, cancellation, failure, and recovery cleanup locks;
- strict prohibition of automatic provider fallback.

## Result contract

The report schema is:

```text
neo.image.provider_action_regression_matrix.v1
```

Each case uses:

```text
neo.image.provider_action_regression_case.v1
```

A release-ready report must contain:

```text
status: passed
failed: 0
selected_profile_only: true
automatic_provider_fallback: false
```

## Physical runtime limitation

This runner validates deterministic routing, payload boundaries, cleanup, and lineage. It does not replace a physical Forge/Comfy GPU smoke test for image quality, model compatibility, VRAM behavior, or third-party extension runtime behavior.


## Release integration audit

Phase 13 proves routing contracts. Phase 14 adds the release-facing integration gate:

```text
python scripts/audit_provider_action_release_phase14.py
```

That audit reruns this matrix, checks Bridge 1.2.1 capability pairing, verifies the release guide/action inventory, builds and audits a temporary public runtime archive, scans release-facing text for non-portable paths and obvious live credential formats, and confirms release-lock records. See `provider_action_release_integration.md`.
