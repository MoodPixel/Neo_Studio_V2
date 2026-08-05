---
guide_id: image.reference_actions
title: Provider-Aware Reference Actions
surface: image
scope: built_in
applies_to:
  - preview actions
  - output inspector
  - controlnet
  - ip adapter
  - forge
  - comfyui
tags:
  - image
  - reference
  - provider aware
  - controlnet
  - ip adapter
priority: 120
version: 1
updated: 2026-08-02
---

# Provider-Aware Reference Actions

The Preview and Output Inspector **ControlNet** and **IP Adapter** buttons stage an existing output as a reference for the currently selected Image provider. They do not run generation.

## Locked workflow

1. Resolve the selected output.
2. Verify the provider evaluation belongs to the currently selected Image profile.
3. Materialize a URL-only preview into Neo-owned validated source storage.
4. Refresh ControlNet and IP Adapter catalogs for that same profile when stale.
5. Select the first empty target unit.
6. Enforce provider capacity; Forge counts ControlNet and Standard IP Adapter in one shared slot pool.
7. Store `neo.image.preview_reference_handoff.v1` in extension metadata.
8. Open Image → Reference for review.
9. Wait for an explicit Generate action.

## Safety rules

- No automatic provider fallback.
- No Forge-to-Comfy profile switch.
- No silent overwrite of an occupied unit.
- No automatic map build or generation.
- Provider/profile/source/asset mismatches fail closed before provider compilation.
- Disabling the extension preserves the staged draft but makes its handoff non-executing.

## Provider behavior

| Provider | ControlNet catalog | IP Adapter catalog | Capacity |
|---|---|---|---|
| ComfyUI | Live Object Info plus registered model folders | Live nodes plus configured model paths | Provider/workflow graph rules |
| Forge Neo | Admin-discovered Integrated ControlNet models/modules | Admin-discovered compatible IP-Adapter models/preprocessors | Shared Integrated ControlNet unit slots |

## Phase 11 temporary Reference handoffs

Preview Reference handoffs are single-run execution state. After success, failure, cancellation, or provider-profile change, Neo removes the handoff and any temporary Forge/Comfy upload alias. Canonical ControlNet/IP Adapter settings may remain, but replay and provider changes restore affected blocks disabled until the selected provider revalidates their catalog and slot mapping.
## Comfy IP Adapter Hotfix 03/04 — 2026-08-03

The shared IP Adapter panel must resolve a selected-profile provider context before rendering any provider badge. A missing local variable must never isolate the extension card. Both Comfy provider IDs (`comfyui` and `comfyui_portable`) remain enabled for validated SDXL checkpoint Generate and Img2Img routes, stage through `comfy_ip_adapter`, and retain the existing first-empty-unit/no-overwrite/no-auto-run rules.

