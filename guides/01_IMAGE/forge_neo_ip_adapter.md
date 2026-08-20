---
guide_id: image.forge_neo_ip_adapter
title: Forge Neo IP-Adapter Remap
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - extensions
  - admin
tags:
  - forge
  - forge-neo
  - ip-adapter
  - controlnet
priority: 99
version: 2
updated: 2026-08-01
---

# Forge Neo IP-Adapter Remap

Phase E1.1 reuses Neo Studio's existing `image.ip_adapter` feature and changes only its provider execution when Forge / Forge Neo is selected. ComfyUI keeps the existing node-based IP-Adapter workflow. Forge uses the built-in `sd_forge_ipadapter` integration through Forge Integrated ControlNet.

## Ownership

```text
image.ip_adapter
      ↓ selected backend
ComfyUI             Forge Neo
current node graph  live ControlNet/IP-Adapter catalog
                         ↓
                   Forge ControlNet units
```

Neo does not install or copy Forge's built-in IP-Adapter extension and does not create a second Forge-only IP-Adapter panel.

## E1.1 Forge boundary

Supported for initial Forge execution:

- SD 1.5 checkpoint: txt2img, img2img, inpaint;
- SDXL checkpoint: txt2img, img2img, inpaint;
- Standard IP-Adapter models only;
- one reference image per Neo IP-Adapter unit;
- multiple references by adding multiple Neo units;
- ControlNet + IP-Adapter in the same generation;
- native Forge Hires Fix together with IP-Adapter on supported txt2img routes.

Still gated:

- FaceID and InstantID;
- Flux, Flux.2 Klein, Krea 2, Qwen, Z-Image and other modern families;
- outpaint;
- multiple reference images inside one IP-Adapter unit;
- any model/preprocessor pair not verified by the selected Forge profile's live catalog.

## Live model and preprocessor discovery

Forge registers IP-Adapter execution through Integrated ControlNet and publishes its preprocessors through the live ControlNet module catalog. Some Forge builds do not publish every model loaded from a referenced Comfy path through `/controlnet/model_list`, so Neo combines two verified sources: the live ControlNet catalog and the shared Comfy `ipadapter` catalog resolved from the active `extra_model_paths.yaml`.

E1.1 classifies recognizable standard model names and pairs them with the Forge preprocessor required by that variant:

| Model family/variant | Required Forge preprocessor |
| --- | --- |
| SDXL standard | `CLIP-ViT-bigG (IPAdapter)` |
| SDXL Plus / ViT-H | `CLIP-ViT-H (IPAdapter)` |
| SD 1.5 standard / Plus | `CLIP-ViT-H (IPAdapter)` |
| SD 1.5 ViT-G / bigG | `CLIP-ViT-bigG (IPAdapter)` |

A model is selectable only when the selected Forge profile exposes the Integrated ControlNet script plus a compatible IP-Adapter preprocessor. The model name may come from Forge's live ControlNet model list or from the verified shared Comfy `ipadapter` catalog. Shared `clip_vision` filenames are used for pairing diagnostics, but absolute paths are never copied into extension state or browser capability payloads.

## Shared Comfy-style model library

Neo can keep one centralized Comfy-friendly model library for both backends. Configure **Admin → Models → Paths** with the central models root and, when used, its **Shared extra_model_paths.yaml**. Forge may prove that it references the same authority in either supported way:

```text
--forge-ref-comfy-yaml <same YAML path>
--model-ref <same shared models root>
```

Only one matching reference is required. Neo does not copy or mirror IP-Adapter files into a Forge-only tree and does not create a second Forge path configuration. A matching YAML or model-root reference verifies the shared `ipadapter` and `clip_vision` catalogs. Forge Integrated ControlNet's live script and `/controlnet/module_list` remain mandatory execution proof, while `/controlnet/model_list` is supplemented when it omits models already present in the verified shared catalog.

The capability card reports a specific blocker: missing Integrated ControlNet contract, missing route slots, no discovered model, unsupported FaceID/InstantID-only catalog, or missing matching preprocessor. Shared filesystem discovery alone never bypasses the live ControlNet script/preprocessor requirement.

Absolute YAML/model paths stay server-side. Forge Admin reports only whether a shared authority is configured, whether the running process matches it through YAML or model-root reference, the reference mode, and readiness.

## ControlNet aggregation

Forge IP-Adapter is not submitted as a second always-on script. Neo compiles both features into one Forge ControlNet array:

```text
Neo ControlNet units
+
Neo IP-Adapter units
        ↓
alwayson_scripts["ControlNet"].args
```

The combined array shares Forge's live ControlNet unit-slot limit. Neo fails before submission when normal ControlNet units plus IP-Adapter units exceed the available slots.

## High-Res Fix

Forge ControlNet explicitly handles the low-resolution and Hires target dimensions. E1.1 therefore permits supported SD 1.5/SDXL txt2img jobs to combine:

```text
IP-Adapter + optional ControlNet + Forge Hires Fix
```

This does not relax the separate PiD/High-Res conflict from E2.

## UI behavior

The existing IP-Adapter card remains the only product surface. On Forge:

- Standard mode remains visible;
- FaceID/InstantID controls appear only when the selected Forge profile verifies a compatible live model/preprocessor pair;
- manual Comfy CLIP Vision selection is hidden because the Forge preprocessor is derived from the selected IP-Adapter model;
- advanced Comfy-only controls are hidden;
- the model list comes from the selected Forge profile's verified IP-Adapter capability;
- refresh uses the Forge Admin capability snapshot.

Switching back to ComfyUI retains the existing Comfy node-based behavior.

## Forge command-flag diagnostic fallback

When Forge cannot serialize `forge_ref_comfy_yaml` through `/sdapi/v1/cmd-flags`, Neo skips that diagnostic endpoint and uses the configured Admin shared-model authority. This only replaces command-line path verification; it does not replace the live Integrated ControlNet script or compatible IP-Adapter preprocessor checks.


## Recovering a saved FaceID unit on Forge

A draft created under ComfyUI may retain a FaceID unit when the backend changes to Forge. FaceID availability is profile-specific: a missing live model/preprocessor pair is a **unit-level capability mismatch**, not proof that Standard IP-Adapter is unavailable.

When Standard Forge IP-Adapter is verified:

- the panel reports partial Forge support instead of locking the whole extension;
- the stale FaceID unit remains visible for draft preservation;
- its **Mode** selector remains enabled so it can be changed to **Standard**;
- the unit may also be disabled;
- FaceID-specific execution remains disabled until the selected profile verifies a compatible pair; on a verified profile the unit can execute through Integrated ControlNet.

New Forge units continue to default to Standard mode. Neo never silently converts or deletes a saved FaceID unit.

## Phase 7 update — live-verified FaceID and Identity Rescue

Forge FaceID/InstantID is no longer globally gated. It is enabled per selected profile only when Forge Admin verifies a compatible FaceID/InstantID model and InsightFace-style Integrated ControlNet preprocessor for the active SD 1.5/SDXL family.

On a verified profile:

- the shared IP Adapter card exposes FaceID mode and live FaceID model choices;
- Standard and FaceID units use the same Forge ControlNet unit pool;
- each unit accepts exactly one reference image;
- Identity Rescue runs as a low-denoise Forge Img2Img derived pass;
- Comfy-only FaceID loader controls are normalized away before Forge submission;
- provider/profile/model/preprocessor/source mismatches fail before queueing.

A saved FaceID draft remains recoverable on profiles without the required pair: the unit stays visible, but execution is disabled until the user changes mode, disables it, or refreshes a compatible Forge profile.
