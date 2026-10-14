---
guide_id: comfy_spaces.value_only_presets
title: Comfy Spaces Value-Only Presets
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
tags:
  - comfyui
  - spaces
  - presets
priority: 80
version: 1
updated: 2026-10-08
---

# Value-only Presets (NCS-14)

Presets are saved input values tied to an exact published Space version. They are not ComfyUI workflow copies or executable scripts. Use the dedicated **Presets** panel in a published Space to enter a name, save values, select a preset to apply, or delete it.

- Presets store only existing manifest input IDs and non-media JSON-serializable values. Image, mask, video, audio and file inputs are not persisted; select assets for each run.
- A Preset overlays the currently entered values without resetting media inputs, stage selections, Graph Add-ons, or Variant configuration. Generating still performs the existing live queue-time preflight.
- A Preset pins the exact `space_id`, semantic version, and SHA-256 fingerprint of the publication receipt. If the base becomes unavailable or changes, it displays as stale and cannot be applied.
- Presets live under `neo_data/comfy_spaces/presets/`, separately from Variants and published immutable packages. `user` / `space` scope markers describe preset intent in a local-first runtime; they are not a multi-user authorization system.
- No topology, stages, Graph Add-ons, workflow nodes, media attachments or executable JavaScript/Python fields belong in a Preset. Use a **Variant** if you want reusable Graph Add-on/stage configuration.

## API contracts

- `GET /api/comfy-spaces/presets?space_id=...&version=...` lists version-specific presets and readiness.
- `POST /api/comfy-spaces/presets` accepts `name`, `base_space_id`, `base_version`, `saved_values` and optional `description`, `scope`.
- `DELETE /api/comfy-spaces/presets/{preset_id}` deletes only the saved local preset.

The API rejects topology/stage settings at creation; stored preset payloads are strictly validated. Never treat a Preset as authorization to queue a workflow.
