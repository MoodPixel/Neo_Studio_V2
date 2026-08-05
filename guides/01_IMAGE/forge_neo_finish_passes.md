---
guide_id: image.forge_neo_finish_passes
title: Forge Neo Provider-Owned Finish Passes
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - forge
  - finish
tags:
  - forge
  - adetailer
  - faceid
  - finish
updated: 2026-08-02
---

# Forge Neo provider-owned Finish passes

Neo Studio keeps output finishing on the selected local provider. Forge actions never become Comfy actions merely because a Comfy profile is available.

## Native Forge finish routes

| Finish action | Forge execution | Capability authority |
|---|---|---|
| High-Res Fix | Native selected-image Hires operation | Neo Forge Bridge native operation |
| ADetailer | `/sdapi/v1/img2img` + ADetailer always-on script | Live Forge `/script-info` contract and detector coverage |
| Identity Rescue | `/sdapi/v1/img2img` + Integrated ControlNet FaceID unit | Live FaceID/InstantID model + InsightFace preprocessor pair |
| Image Upscale | Forge Extras | Phase 10 |

## Derived-action safety

ADetailer and Identity Rescue use `neo.image.derived_action.v2` with `run_provider_img2img_derived`.

Neo validates:

- selected Forge profile/provider ownership;
- materialized source output;
- Img2Img runtime boundary;
- action-specific extension enabled state;
- live detector or FaceID capability;
- single-result batch policy;
- source/output lineage;
- no automatic provider fallback.

Source dimensions are restored when present in the selected output record. The outer Img2Img denoise is clamped to a conservative range; the extension's internal repair strength remains separately configurable.

## ADetailer

The selected source is processed with the normal Forge Img2Img API and the verified `ADetailer` always-on script. At least one auto-detect pass and a Forge-readable `.pt` detector are required. Manual boxes and unsupported detector contracts remain blocked.

## Identity Rescue

Identity Rescue uses one FaceID unit in Forge Integrated ControlNet. The active checkpoint family and selected FaceID model must be SD 1.5 or SDXL compatible. The live Forge catalog must expose a matching FaceID/InstantID preprocessor, such as an InsightFace/IPAdapter module. One reference image is accepted per unit.

Comfy-only FaceID loader/preset/LoRA controls are not fabricated in Forge payloads. They may remain in canonical UI/replay state, while the Forge compiler emits only the verified ControlNet model, module, weight, reference image, and guidance interval.

## Failure behavior

Any provider/profile/source/model/preprocessor/detector mismatch fails before submission. Neo does not select another profile, downgrade FaceID to Standard IP Adapter, or reroute the action through ComfyUI.
