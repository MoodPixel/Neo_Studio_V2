---
guide_id: image.forge_neo_builtin_feature_remap
title: Forge Neo Built-in Feature Remap
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - extensions
  - finish
  - admin
tags:
  - forge
  - forge-neo
  - high-res
  - controlnet
  - adetailer
  - image-upscale
  - image-stitch
priority: 99
version: 3
updated: 2026-08-02
---

# Forge Neo Built-in Feature Remap

Phase E1 keeps Neo Studio's existing Image feature surfaces and changes only their provider translation when the selected backend is Forge / Forge Neo. Neo does not duplicate these tools as a second set of Forge-specific extension panels.

## Ownership rule

```text
Neo UI / saved state / validation
        ↓
selected backend capability snapshot
        ↓
Comfy graph adapter OR Forge-native adapter
```

The same Neo feature card is therefore reused across providers, but each provider owns its own execution contract. Static manifests describe product support; live Forge discovery is still required before Forge execution is exposed.

## E1 feature map

| Neo feature | Forge execution contract | Current Forge boundary |
|---|---|---|
| High-Res Lab | `forge.txt2img.hires.v1` + `neo.forge_bridge.native_txt2img_upscale.v1` | Native generation-time Hires fields plus Forge Neo's selected-image Hires operation. The post-output operation is not ordinary img2img and requires Neo Forge Bridge 1.2.1+ with `native_post_hires`. |
| ControlNet | `forge.controlnet.unit.v1` | Verified ControlNet always-on script plus live `/controlnet/model_list` and `/controlnet/module_list`; current mapped task is `map_control`. |
| ADetailer | `bing-su.adetailer.api.v1` | Verified official always-on argument schema; mode/pass slots come from live script metadata. |
| Image Upscale | `forge.extras.single_image.v2` | Standalone `/sdapi/v1/extra-single-image` route with selected-profile upscalers, scale/exact sizing, secondary blending, and reported CodeFormer/GFPGAN. SeedVR2 remains Comfy-only. |
| Stitch Images | `forge.image_stitch.integrated.v1` | Existing Neo Stitch pair UI becomes Forge reference inputs for supported Qwen Image Edit and Flux.2 Klein img2img/edit routes when the exact three-argument script contract is detected. |

## Image Upscale

When Forge is selected, Neo keeps the existing **Image → Finish → Image Upscale** panel but swaps the backend path from a Comfy utility graph to Forge Extras.

The Forge compiler sends:

```text
POST /sdapi/v1/extra-single-image
```

and maps the Neo controls to Forge's native upscaler, scale and CodeFormer fields. The selected upscaler must exist in the active Forge profile's live `/sdapi/v1/upscalers` catalog. No backend filesystem paths are copied into portable Neo metadata.

SeedVR2 remains unavailable on the Forge path because it is a Comfy custom-node workflow in Neo Studio, not a Forge Extras contract.

The normal Forge job manager and the optional Neo Forge Bridge both accept the Extras endpoint. Extras responses use the same Neo output spool/import boundary as generation responses.

## Image Stitch

Forge Neo's `ImageStitch Integrated` script is not a bitmap-compositor. For Qwen Image Edit / Flux.2 Klein it supplies extra reference images to the model while Image 1 remains the main img2img source.

Neo verifies the live script shape before enabling it:

```text
ImageStitch Integrated
args[0] = enable boolean
args[1] = Reference Image(s) gallery
args[2] = Maximum Side Length
```

A valid request compiles to:

```text
alwayson_scripts["ImageStitch Integrated"].args = [true, reference_images, max_side]
```

If the script is absent, disabled, renamed beyond recognition, or its argument structure changes, Stitch Images stays hidden for Forge and extra references fail before submission. Single-source Qwen Edit remains available.

Current E1 Forge Stitch routes are deliberately narrow:

- Qwen Image Edit 2509 `img2img` / `edit`;
- Flux.2 Klein `img2img` when its regular-img2img setting is enabled.

Neo does not infer Flux.1 Kontext, Wan, or other multi-image support from the presence of the script alone.

## Preview Source action remap

Img2Img, Inpaint, and Outpaint buttons in Preview/Output Inspector use `stage_source_mode` under the selected Forge profile. They stage `neo.image.preview_source_handoff.v1`, clear old provider upload and mask/outpaint ownership, switch the Neo workflow, and do not submit a job. URL-only outputs are copied into Neo-owned source storage before staging. The request is rejected if the contract provider/profile does not match the selected Forge request; there is no automatic Comfy fallback.

## Provider-owned Finish boundary

Phase 5 removes the generic Comfy-oriented Finish runner. Preview and Output Inspector now create `neo.image.derived_action.v2` and dispatch only through the currently selected Image profile. Forge actions therefore stay bound to Forge even when a connected Comfy profile also exists.

The dispatcher declares native Forge routes without enabling unfinished engines early:

```text
High-Res Lab     -> run_forge_native_hires       (implemented)
ADetailer        -> run_provider_img2img_derived (Phase 7)
Identity Rescue  -> run_provider_img2img_derived (Phase 7)
Image Upscale    -> run_provider_extras          (Phase 10 complete)
```

Each route remains disabled until its executor and live capability contract are ready. There is no automatic Forge-to-Comfy fallback. Cross-provider finishing requires an explicit user-selected finishing profile.

## High-Res Lab post-output boundary

Forge Neo supports two related but distinct Hires paths:

1. generation-time Hires fields on a normal Forge txt2img request;
2. a native selected-output Hires operation used by Forge's txt2img gallery ✨ button.

The selected-output operation calls Forge's internal `txt2img_upscale` path, forces `enable_hr`, marks the processing object as `txt2img_upscale`, assigns the chosen image to `firstpass_image`, updates width/height from that image, and performs the Hires diffusion pass without generating a new first pass. It must not be emulated as ordinary `/sdapi/v1/img2img`.

Neo Phase 2 publishes this intended route as:

```text
dispatch_type: run_forge_native_hires
execution_mode: forge_native_txt2img_upscale
required_bridge_capability: native_post_hires
```

It is enabled only when the selected Forge profile has a selected Bridge advertising `native_post_hires` or `native_txt2img_upscale`. Neo must not call Gradio function indexes, silently select Comfy, or substitute Forge Extras. **Image Upscale** remains the separate pixel-upscale path.

## Capability refresh

After installing/updating Forge scripts or changing upscalers:

1. Open **Admin → Backends → Image → Forge / Forge Neo**.
2. Run **Refresh Forge Admin**.
3. Neo re-reads script metadata, ControlNet catalogs and upscalers.
4. The Image workspace rebuilds the selected-profile feature policy.

No feature is enabled merely because a matching extension folder exists.

## Still outside E1

Phase E1 does not add new Neo extension surfaces for:

- PiD Integrated;
- Spectrum;
- MultiDiffusion;
- IP-Adapter;
- arbitrary third-party Forge extensions.

Those require separate provider mappings or later extension-bridge work.

## E1.1 follow-on — IP-Adapter

The E1 ownership model now also applies to `image.ip_adapter`. Forge's built-in IP-Adapter implementation registers inside Integrated ControlNet, so Neo preserves its existing IP-Adapter card and compiles standard SD 1.5/SDXL units into the same Forge ControlNet argument array as ordinary ControlNet units. See `forge_neo_ip_adapter.md` for the narrower E1.1 support and validation boundary.

## Provider-aware output reference actions — Phase 4

- Preview and Output Inspector **ControlNet** and **IP Adapter** actions stay on the selected Forge profile.
- Forge catalogs come from the selected profile's Admin capability snapshot, not Comfy object-info or local browser paths.
- Standard IP Adapter and ordinary ControlNet share the verified Forge Integrated ControlNet unit pool.
- Neo stages into the first empty unit and never overwrites an occupied unit silently.
- URL-only outputs are materialized into Neo-owned source storage before staging.
- `neo.image.preview_reference_handoff.v1` is validated before provider compilation; provider/profile/source mismatches return HTTP 409.
- Staging opens Image → Reference and never queues generation.

## Provider-owned Forge Finish actions — Phase 7

- ADetailer output repair compiles through Forge Img2Img and the selected profile's verified ADetailer script schema.
- Identity Rescue compiles through Forge Img2Img and a live-verified FaceID/InstantID model/preprocessor pair inside Integrated ControlNet.
- Both actions keep the selected Forge profile, use one derived output, preserve source dimensions when recorded, and write output lineage.
- Standard IP Adapter, FaceID, and ordinary ControlNet share the Forge ControlNet unit pool.
- Capability names or model filenames alone are insufficient: live script/catalog verification is required before the actions enable.



## Phase 10 — Image Upscale Extras v2

The Forge Image Upscale remap is active through `forge.extras.single_image.v2`.

Neo reads `/sdapi/v1/upscalers` and `/sdapi/v1/face-restorers` from the selected Forge profile, exposes only reported options, and compiles scale or exact-dimension Extras requests. Secondary upscaler blending, crop-to-fit, CodeFormer/GFPGAN visibility, CodeFormer weight, and upscale-first are passed only through the native Extras schema.

The panel hides SeedVR2 and Comfy graph controls under Forge. Missing optional face-restorer discovery disables those controls without blocking plain upscale. Forge Extras never substitutes for native selected-image Hires.
