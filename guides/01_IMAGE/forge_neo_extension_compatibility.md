---
guide_id: image.forge_neo_extension_compatibility
title: Forge Neo Extension Compatibility
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
  - extensions
  - controlnet
  - adetailer
  - high-res
priority: 95
version: 5
updated: 2026-08-02
---

# Forge Neo Extension Compatibility

For the canonical current Forge setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`.

**Status:** Phase 5 mappings plus E1/E2/E3 extension integration  
**Scope:** Dedicated Neo mappings plus conservative generic external-script discovery/bridge

## Principle

Forge extension compatibility is never inferred from an extension name alone. Neo requires both:

1. an extension manifest that declares Forge support; and
2. a selected-profile capability snapshot that proves the required Forge API or script contract.

When either side is missing, the extension stays visibly gated and the provider refuses the job.

## Available mappings

| Neo extension | Forge contract | Supported scope |
|---|---|---|
| Wildcards | Provider-neutral prompt transform | Existing prompt-resolution behavior |
| Style Stack | Provider-neutral prompt transform | Existing prompt-resolution behavior |
| LoRA Stack | `forge.extra_network.lora.v2` | Global base/both rows compiled to positive-prompt `<lora:name:strength>` tags |
| Embeddings / TI | `forge.embedding.token.v2` | Selected-profile catalog; plain Forge triggers for positive, negative, or both targets; weighted syntax compiled at submission time |
| High-Res Lab | `forge.txt2img.hires.v1` | SD 1.5/SDXL checkpoint `txt2img` through native Forge hires fields |
| ControlNet | `forge.controlnet.unit.v1` | `map_control` through a verified always-on script and live model/module catalogs |
| ADetailer | `bing-su.adetailer.api.v1` | Official auto-detect passes through a verified always-on argument schema |
| Image Upscale | `forge.extras.single_image.v1` | Standalone Forge Extras upscale using the live upscaler catalog; built-in CodeFormer supported, SeedVR2 Comfy-only |
| Stitch Images | `forge.image_stitch.integrated.v1` | Neo Stitch pairs become Forge reference images only on verified Qwen Image Edit / Flux.2 Klein routes |

## Dynamic script discovery

Neo reads `/sdapi/v1/script-info` and sanitizes each script's mode, argument count, labels, defaults, limits, and choices. Script-backed mappings are activated only when their expected shape is recognized.

### ControlNet

ControlNet additionally requires:

```text
/controlnet/model_list
/controlnet/module_list
```

The capability snapshot records separate `txt2img` and `img2img` unit-slot counts. Inpaint uses Forge's `img2img` script contract. Neo refuses jobs that request more units than the discovered script exposes.

### ADetailer

Neo recognizes the official always-on API shape: two leading boolean controls followed by one or more ADetailer argument dictionaries. The discovered slot count is enforced separately for `txt2img` and `img2img`/inpaint.

Forge's standard API does not expose an ADetailer detector-model catalog. Neo therefore allows the exact installed detector filename to be entered manually while keeping the script-schema gate active.

### Image Upscale

E1 maps the existing Image → Finish panel to `/sdapi/v1/extra-single-image` when Forge Extras and `/sdapi/v1/upscalers` are available. The selected upscaler is validated against the live profile catalog. Forge's built-in CodeFormer fields are used directly; the Comfy SeedVR2 graph path remains hidden on Forge.

### ImageStitch Integrated

E1 recognizes the exact current built-in script shape (`enable`, `Reference Image(s)`, `Maximum Side Length`). Neo compiles extra references through `alwayson_scripts` only when this schema is verified. The existing Neo Stitch panel remains the UI owner; Forge's script supplies model references and does not physically composite them.

## Fail-closed limits

The following remain gated rather than silently downgraded:

- regional or finish-only LoRA rows;
- finish-prompt Embeddings/TI targets;
- High-Res Lab on img2img or inpaint;
- ControlNet `inpaint_control` and `outpaint_control` task contracts;
- ControlNet units beyond the live script slot count;
- ControlNet advanced schedules, non-auto batching, sliding context, map inversion, unsupported soft/strict weighting, OpenPose hand/face sub-toggles, and requested masks without supplied mask assets;
- ADetailer manual boxes, reference lock, custom target ordering, start-index offsets, and area filters;
- IP-Adapter FaceID/InstantID, modern-family routes, outpaint, and multiple references inside one unit; standard SD1.5/SDXL IP-Adapter is mapped by E1.1 through Integrated ControlNet;
- SeedVR2 on the Forge Image Upscale path;
- Scene Director, LayerDiffuse, GGUF Loader, and Comfy graph-dependent finishing chains;

## Prompt-native mappings

LoRA and embedding names are portable identifiers, not filesystem paths. The selected Forge profile supplies its LoRA catalog through the live LoRA endpoint when available and/or verified shared model paths, and supplies its Embeddings/TI catalog through the selected-profile embeddings capability. Neo strips common file suffixes, normalizes legacy `embedding:` forms, and deduplicates path, weighted, and provider-syntax variants. Forge Embeddings/TI compiles as `EasyNegative` or `(EasyNegative:1.2)`; Comfy compiles the same canonical chip as `embedding:EasyNegative` or `(embedding:EasyNegative:1.2)`. Neither path rewrites the visible prompt.

## Refresh workflow

After installing, removing, or updating Forge scripts or models:

1. Open **Admin → Backends → Image → Forge / Forge Neo**.
2. Run **Test Connection** or **Refresh Forge Admin**.
3. Return to Image and use **Refresh Forge** if the overlay is already open.

The refreshed selected-profile snapshot is authoritative for catalogs, script modes, and slot counts.

## Bridge interaction

The Bridge transports already compiled Forge payloads and does not expand compatibility. E1 additionally permits `/sdapi/v1/extra-single-image` so the existing Image Upscale surface can use durable Bridge execution. LoRA, Embeddings/TI, High-Res Lab, ControlNet, ADetailer, Image Upscale and ImageStitch still remain subject to selected-profile contract gates before submission.

## Preview independence

Phase 6.2 live-preview polling does not change extension compatibility. Preview frames are read from the active Forge lifecycle after the compiled payload is accepted; absent previews never cause LoRA, Embeddings/TI, High-Res Lab, ControlNet, or ADetailer to be silently enabled or disabled.

## E2 — Neo-managed Forge extras

`image.pid_integrated`, `image.spectrum`, and `image.multidiffusion` are first-class Neo built-ins whose execution is supplied by exact Forge built-in script adapters. Discovery is profile-local and schema-verified. A changed/missing script becomes provider-gated; Neo never guesses positional arguments. Generic third-party extension discovery remains outside E2.

## E3 — generic external Forge script bridge

E3 adds `image.forge_script_bridge` as a single Neo-owned dynamic surface. Forge still owns extension installation and lifecycle. Neo reads `/sdapi/v1/extensions`, `/sdapi/v1/scripts`, and `/sdapi/v1/script-info`, classifies each script, and only exposes `generic_bridge_ready` scripts whose complete API shape is primitive and attributable to an enabled external Forge extension.

Built-in/unattributed scripts, image/file/mask/object contracts, scripts without API metadata, and any script already owned by a dedicated Neo adapter remain `adapter_required` or `neo_mapped`. Schema fingerprints are mandatory and generic execution is limited to SD 1.5/SDXL until explicit adapters or physical evidence widen support. See `forge_neo_generic_extension_bridge_e3.md`.

## E1.1 — existing Neo IP-Adapter surface on Forge

IP-Adapter is no longer an unmapped Forge capability. The existing `image.ip_adapter` feature owns the UX while Forge execution is provided by `neo.provider.forge_ip_adapter_remap.v1` through Integrated ControlNet. E1.1 supports standard SD 1.5/SDXL checkpoint txt2img/img2img/inpaint only, derives the required live Forge IP-Adapter preprocessor from the selected model, and shares the same ControlNet unit pool as Neo ControlNet. FaceID/InstantID and modern-family execution remain gated. The generic E3 bridge must never create a second IP-Adapter path. See `forge_neo_ip_adapter.md`.
