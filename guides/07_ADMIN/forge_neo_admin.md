---
guide_id: admin.forge_neo_backend
title: Forge Neo Admin Integration
surface: admin
scope: built_in
applies_to:
  - admin
  - backends
  - image
tags:
  - forge
  - forge-neo
  - backend
  - admin
  - settings
  - diagnostics
priority: 92
version: 7
updated: 2026-08-03
---

# Forge Neo Admin Integration

Neo Studio inspects a separately installed Forge Neo process from **Admin → Backends → Image**. Admin owns connection, sanitized inventory, settings/script discovery, and selected-profile capability snapshots. The Image surface still applies its own route, model, module, compiler, workflow, extension, and UX gates.

For the complete current support matrix, see:

```text
guides/01_IMAGE/forge_neo_complete_support.md
```

## Setup

1. Install Forge Neo separately.
2. Launch Forge with API access enabled:

```text
--api
```

3. Open **Admin → Backends → Image**.
4. Select the seeded **Forge / Forge Neo** profile.
5. Enable it and confirm the base URL, commonly:

```text
http://127.0.0.1:7860
```

6. Keep Forge and Neo Studio on different listening ports.
7. Save and run **Test Connection** or **Refresh Forge Admin**.

Neo does not install Forge, download models, move modules, or change Forge launch arguments.

## Shared Comfy-style model paths

A centralized Comfy-friendly model library can remain the single path authority for both ComfyUI and Forge Neo. In **Admin → Models → Paths** configure:

- **ComfyUI models root** for the centralized `models` directory;
- **Shared extra_model_paths.yaml** for the Comfy YAML used by that library.

Then launch Forge Neo with either matching native reference:

```text
--forge-ref-comfy-yaml <same YAML path>
--model-ref <same shared models root>
```

Only one matching reference is required. Neo intentionally does **not** save a second Forge copy of the path and does not edit Forge launch files. Forge Admin compares sanitized command-flag state against the server-side shared authority and reports only `configured / active / matched`, reference mode, counts, and blocker status. Absolute paths are never copied into the browser capability snapshot.

IP-Adapter needs no separate Neo extension or duplicated path mapping. Neo may supplement an incomplete live ControlNet model list with verified shared `ipadapter` filenames, but Forge Integrated ControlNet and a compatible live preprocessor remain mandatory. ADetailer's native `<model-ref>/adetailer` folder counts automatically when the model root matches; any additional shared detector folders must be covered by `ad_extra_models_dir` (a parent may cover nested folders recursively). Changing model references or ADetailer settings requires a Forge restart.

## Connection states

| State | Meaning | Corrective action |
|---|---|---|
| Disabled | Profile is intentionally off. | Enable and save it. |
| Missing configuration | Base URL is absent or invalid. | Enter a valid HTTP/HTTPS Forge URL. |
| Disconnected | Host cannot be reached. | Start Forge and verify host, port, and firewall. |
| API disabled | Forge responds but the standard API is unavailable. | Relaunch with `--api`. |
| Authentication required | Forge returned 401/403. | Configure environment-backed Basic auth or remove auth on a trusted local installation. |
| Version incompatible | Required generation/progress/interrupt endpoints are missing. | Update Forge Neo or use its native UI. |
| Connected with warnings | Core execution works; optional discovery failed. | Review diagnostics. Supported routes remain generation-eligible. |
| Connected | Core and expected discovery contracts succeeded. | Use the live Image route selectors. |

A successful connection does not activate every route. It supplies the selected profile's live facts to the route intersection.

## Discovery endpoints

Neo probes the standard Forge/A1111-compatible surface, including:

```text
/sdapi/v1/options
/sdapi/v1/sd-models
/sdapi/v1/sd-modules
/sdapi/v1/samplers
/sdapi/v1/schedulers
/sdapi/v1/upscalers
/sdapi/v1/scripts
/sdapi/v1/script-info
/sdapi/v1/extensions
/sdapi/v1/embeddings
/sdapi/v1/memory
/sdapi/v1/cmd-flags
/openapi.json
```

`cmd_flags` and embeddings discovery are soft optional. Their failure can gate a diagnostic or extension without turning a healthy core Forge API into a disconnected backend.

## Live classification and route intersection

A successful refresh publishes:

```text
neo.provider.forge_live_model_classification.v1
neo.provider.forge_live_route_intersection.v1
neo.provider.forge_ux_gating.v1
```

Admin reports:

- classified, ambiguous, and unclassified primary models;
- classified module roles;
- executable routes;
- compiler-gated assets that are installed but not executable;
- missing module/setting/script blockers;
- sanitized sampler, scheduler, upscaler, extension, and memory data.

Generic names such as `model.safetensors` can remain ambiguous between SD 1.5 and SDXL. Neo does not inspect private model weights to force a guess.

## Refresh Models

**Refresh Models** calls Forge's checkpoint/module refresh endpoints and rebuilds the selected profile snapshot. It does not download, rename, move, or delete files.

Refresh after:

- adding/removing models or encoders;
- renaming a model to improve family classification;
- changing a route-critical Forge setting;
- installing/updating a supported extension;
- switching between Forge installations.

## Settings policy

Neo reads Forge's live options catalog instead of hardcoding one fixed settings form.

### Guided settings

Common safe runtime settings can be changed directly when Forge exposes them, including selected model/module values, preview behaviour, CLIP skip, and compatible image-edit defaults.

### Expert settings

Advanced editable settings appear only in Expert mode and require explicit confirmation.

### Read-only settings

Neo blocks writes for filesystem paths, credentials, server host/port/TLS/CORS, startup-only options, and unsupported object-shaped values. Change them in Forge's own configuration or launch files.

Some settings require a Forge restart. Neo can warn but does not restart Forge automatically.

## Strict Image handoff

The Image surface consumes only the selected profile's executable UX policy. It does not borrow:

- another backend's model catalog;
- the static family manifest as permission;
- Comfy loader/node availability;
- stale family/loader/mode/model selections.

If the live intersection is empty, Image hides the Forge route controls and blocks generation.

## Extension and built-in feature discovery

Script discovery alone is not execution proof. LoRA, Embeddings/TI, High-Res Lab, ControlNet, ADetailer, Image Upscale, Stitch Images, and other provider-mapped surfaces appear only when their live API shape, selected workflow, and provider-owned mapping agree.

E1 keeps the existing Neo cards instead of adding Forge-only duplicates:

- Image Upscale becomes available when `/sdapi/v1/extra-single-image` and a live upscaler catalog are present; Forge native CodeFormer is supported and SeedVR2 remains Comfy-only.
- Stitch Images becomes a Forge reference-input control only when the exact current `ImageStitch Integrated` three-argument script schema is verified on a supported Qwen Image Edit / Flux.2 Klein route.
- A missing or drifted script/API contract hides only the affected Forge feature; its static manifest never grants permission.

Refresh Forge Admin after adding/updating Forge scripts or upscalers so these capability fingerprints are rebuilt.

### E3 generic Forge extension bridge

Forge Admin also publishes a provider-owned compatibility catalog for scripts discovered from enabled external Forge extensions. This catalog is intentionally separate from Neo Studio's own extension registry.

Admin classifies discovered scripts as:

- `neo_mapped` — already owned by a dedicated Neo feature/adapter such as ControlNet, ADetailer, ImageStitch, PiD, Spectrum, or MultiDiffusion;
- `generic_bridge_ready` — attributable to an enabled external Forge extension and described by a complete primitive `/sdapi/v1/script-info` schema;
- `adapter_required` — built-in/unattributed, complex, image/file/object based, incomplete, or otherwise unsafe to infer generically.

Only `generic_bridge_ready` scripts can execute through the E3 bridge, and only on the conservative SD 1.5/SDXL route set. Built-in Forge scripts never gain execution permission merely because their arguments look primitive.

The Forge Extensions section shows the sanitized installed-extension inventory, compatibility reason, invocation type, mode, and schema fingerprint. Installation, update, disable/enable, and removal remain owned by Forge's Extension Manager.

If an extension update changes its argument schema, its fingerprint changes and Neo fails the saved bridge configuration closed until the user refreshes/reconfigures it.

See:

```text
guides/01_IMAGE/forge_neo_extension_compatibility.md
```

## Optional Bridge

Admin probes `/neo-api/v1/handshake` after the standard Forge API succeeds.

- `auto`: prefer a compatible Bridge, otherwise use standard SDAPI for compatible standard routes.
- `standard`: disable Bridge use.
- `required`: block Bridge-owned execution when the Bridge is absent or incompatible.

Bridge history and settings are always scoped to the selected Forge profile. Native selected-output High-Res Fix requires Bridge 1.2.1 and the selected capability snapshot must contain both `native_post_hires: true` and `native_txt2img_upscale` in `native_operations`. Replacing extension files without restarting Forge leaves the old in-memory Bridge active.

Use `python neo_integrations/forge_neo_bridge/install_bridge.py --forge-root <FORGE_ROOT> --replace`, restart Forge, then refresh the exact selected profile. The full release and rollback procedure is in `guides/01_IMAGE/provider_action_release_integration.md`.

## Live preview diagnostics

Neo polls its provider-neutral preview route for Forge jobs. Enable Forge previews and set:

```text
show_progress_every_n_steps > 0
```

No preview during model loading is normal and does not block progress or final output import.

## Runtime cache and privacy

Successful discovery is cached under:

```text
neo_data/provider_cache/<profile_id>/forge_capabilities.json
```

Neo strips credentials, absolute model/module/upscaler paths, and path-bearing command-line values before caching or returning data to the browser. Runtime cache files are excluded from public source releases.

## Current limitations

- Admin connection does not guarantee a specific route; the live intersection remains authoritative.
- The standard Forge API may not expose every control found in the native Gradio UI.
- Qwen Image Edit keeps one main source; optional Stitch reference inputs appear only when the selected profile verifies the current `ImageStitch Integrated` three-argument contract.
- Modern-family inpaint/outpaint remains gated except SD 1.5/SDXL Neo-owned outpaint.
- Neo does not install, launch, or update Forge Neo.
- Offline matrix success is not physical GPU/model validation.


## IP-Adapter and ADetailer shared-catalog diagnostics — 2026-08-01 hotfix

Forge Admin now separates path discovery from execution proof. IP-Adapter diagnostics expose only path-free counts and blocker codes: Integrated ControlNet contract, route slots, live model count, verified shared model count, shared CLIP-Vision encoder count, compatible model count, and compatible preprocessor count. A verified shared IP-Adapter model may supplement an incomplete `/controlnet/model_list`, but it cannot replace the live Integrated ControlNet script or preprocessor contract.

ADetailer diagnostics verify both explicit `ad_extra_models_dir` coverage and Forge ADetailer's native `<model-ref>/adetailer` directory when `--model-ref` is active. The Image panel uses a real Forge detector selector with target-aware suggestions; **Type exact Forge-local name…** preserves the manual fallback. Refresh Forge Admin after restarting Forge or changing model paths.
## Forge `cmd-flags` Path serialization error

Some Forge Neo builds parse `--forge-ref-comfy-yaml` as a Python `Path` but expose `/sdapi/v1/cmd-flags` through a response schema that requires strings. This can produce a provider-side `ResponseValidationError` for `forge_ref_comfy_yaml`. Neo Studio guards that endpoint when the shared YAML is already configured in Admin Models, uses the local shared-model authority, and keeps the endpoint diagnostic-only.

The guard does not bypass execution requirements: IP-Adapter still needs Forge Integrated ControlNet plus a compatible live preprocessor, and ADetailer still needs its recognized always-on script. The Forge log error stops because Neo no longer calls the broken endpoint.

## ForgeCouple capability diagnostics

ForgeCouple is installed and updated in Forge, not in Neo Studio. After changing the Forge extension, restart Forge and refresh Forge Admin.

Neo unlocks `Image · Forge Couple` only when `/sdapi/v1/script-info` exposes an always-on `Forge Couple` script whose seventeen API arguments match the FC3 contract. Admin reports whether the script is missing, detected but schema-drifted, or ready for Txt2Img/Img2Img. Static installation state never grants execution permission.

FC3 exposes Basic, Advanced, and Mask region modes. Mask images remain browser/session assets and are not part of Admin capability data. Tile Mode additionally requires an Img2Img route and Forge's built-in selectable `SD Upscale` script. Admin verifies that script independently through `/sdapi/v1/script-info`, including its exact four-argument schema, live upscaler choices, and the FC3 Basic/Advanced Tile region-mode boundary. Neo compiles SD Upscale directly for Tile Mode; no generic Script Bridge configuration is required.

Refresh Forge Admin after installing/updating ForgeCouple, changing Forge built-in scripts, or changing the available SD Upscale upscalers. No local extension paths, mask bytes, or reference-image data are returned to the browser capability snapshot.


### Native Hires Hotfix 07 verification

After installing Neo Forge Bridge 1.2.1 and restarting Forge, refresh the selected Forge Admin profile and confirm:

```text
native_post_hires = true
native_txt2img_upscale is listed in native_operations
native_post_hires_size_contract = true
```

If the third value is missing, Neo disables selected-output High-Res Fix. This prevents older Bridge builds from returning a same-resolution refinement while appearing to have completed an upscale.
