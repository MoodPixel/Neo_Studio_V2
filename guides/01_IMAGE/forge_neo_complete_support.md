---
guide_id: image.forge_neo_complete_support
title: Forge Neo Complete Support Guide
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - admin
  - models
tags:
  - forge
  - forge-neo
  - setup
  - model-families
  - gguf
  - workflows
  - troubleshooting
priority: 99
version: 7
updated: 2026-08-03
---

# Forge Neo Complete Support Guide

This is the canonical user-facing entry point for Neo Studio's Forge / Forge Neo Image backend.

Neo Studio does not replace Forge and does not include Forge, models, or modules. It connects to a separately installed Forge Neo process through its A1111-compatible REST API, classifies the selected profile's live model inventory, translates Neo loader choices into Forge model bundles, compiles only verified workflows, and hides every route that the active profile cannot execute.

## Setup

1. Install Forge Neo separately.
2. Launch Forge with API access enabled:

```text
--api
```

3. Keep Forge and Neo Studio on different local ports. A common arrangement is:

```text
Forge Neo:  http://127.0.0.1:7860
Neo Studio: http://127.0.0.1:7870
```

4. Open `Admin → Backends → Image`.
5. Select the seeded `Forge / Forge Neo` profile.
6. Set the Forge base URL, enable the profile, save, and run **Test Connection**.
7. Use **Refresh Forge Admin** after adding, removing, or renaming Forge models/modules.
8. Return to Image and select only the family, loader, workflow, model, and modules exposed by the live Forge route policy.

A `Connected with warnings` state remains usable when Forge is reachable and its core generation/progress/interrupt endpoints work. Optional discovery failures gate only the affected feature.

## How route readiness works

A Forge route is executable only when every layer agrees:

```text
route authority
∩ live model/module classification
∩ selected-profile settings and scripts
∩ loader translation
∩ registered workflow compiler
∩ strict UX policy
```

The UI does not guess support from filenames alone and does not borrow ComfyUI loaders, nodes, models, or route claims.

## Preview and Finish provider ownership

Preview/Output Inspector actions are evaluated against the selected Image profile only. Source and Reference actions stage provider-bound contracts without auto-running. Finish actions use `neo.image.derived_action.v2`; a Forge Finish action cannot switch to Comfy automatically. Native Forge post-Hires, Forge ADetailer/FaceID, and Forge Extras are enabled only when their dedicated Neo executor phase and live capability checks are complete.

For upgrade order, Bridge refresh, rollback, and provider-action recovery, see `provider_action_release_integration.md`.

## Current workflow support

Legend:

- ✅ available
- 🧪 experimental available
- 🔒 intentionally gated
- ⛔ provider-gated / no verified Neo Forge route

| Family | Loader | Txt2Img | Img2Img | Inpaint | Outpaint | Edit |
|---|---|---:|---:|---:|---:|---:|
| SD 1.5 | Checkpoint | ✅ | ✅ | ✅ | ✅ | — |
| SDXL | Checkpoint | ✅ | ✅ | ✅ | ✅ | — |
| Flux 1 | Safetensors / components | ✅ | 🧪 | 🔒 | 🔒 | — |
| Flux 1 | GGUF | ✅ | 🧪 | 🔒 | 🔒 | — |
| Flux.2 Klein | Safetensors / components | ✅ | 🧪* | 🔒 | 🔒 | — |
| Flux.2 Klein | GGUF | ✅ | 🧪* | 🔒 | 🔒 | — |
| Krea 2 RAW | Safetensors / components or GGUF | ✅ | 🔒 | 🔒 | 🔒 | — |
| Krea 2 Turbo | Safetensors / components or GGUF | ✅ | 🔒 | 🔒 | 🔒 | — |
| Qwen Image | Safetensors / components or GGUF | ✅ | 🔒 | 🔒 | 🔒 | — |
| Qwen Image Edit 2509 | Safetensors / components or GGUF | 🔒 | 🧪 | 🔒 | 🔒 | 🧪 |
| Z-Image | Safetensors / components or GGUF | ✅ | 🔒 | 🔒 | 🔒 | — |
| Z-Image Turbo | Safetensors / components or GGUF | ✅ | 🔒 | 🔒 | 🔒 | — |
| Qwen Rapid AIO | Checkpoint AIO / GGUF | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| Wan Image surface | API/component/GGUF | ⛔ | ⛔ | ⛔ | ⛔ | — |
| Hunyuan Image | API/component/GGUF | ⛔ | ⛔ | ⛔ | ⛔ | — |
| HiDream | API/component/GGUF | ⛔ | ⛔ | ⛔ | ⛔ | — |

`*` Flux.2 Klein regular img2img is shown only when Forge exposes and enables the required `flux2_klein_regular_img2img` setting.

The table is the product contract, but the selected profile may expose fewer rows when models, modules, endpoints, scripts, or settings are missing. Modern-family Inpaint/Outpaint remains gated except for SD 1.5/SDXL Neo-owned outpaint.

Preview and Output Inspector Source actions now preserve the selected Forge profile through Img2Img, Inpaint, and Outpaint staging. Neo materializes URL-only outputs into its own validated source storage, clears stale Comfy/Forge upload aliases and old mask/canvas state, switches the workflow mode, and waits for an explicit Generate action. The API revalidates the `neo.image.preview_source_handoff.v1` provider/profile binding and rejects mismatches; Forge source staging never silently routes through Comfy.

## Model-bundle rules

Forge uses one primary model plus optional or required additional modules.

| Neo selection | Forge meaning |
|---|---|
| Checkpoint | Primary Forge model |
| Diffusion model | Primary Forge model |
| GGUF model | Primary Forge model in GGUF format |
| VAE / AE | Additional Forge module |
| CLIP / T5 / Qwen encoder | Additional Forge module |
| Guidance values | Generation parameters |
| Comfy loader-node requirement | Not a Forge capability |

GGUF is treated as a Forge primary-model format. It does not use Comfy's `UnetLoaderGGUF`, `DualCLIPLoaderGGUF`, encoder-layout selector, or node diagnostics. Nunchaku/SVDQ is a separate packaging type and is never treated as GGUF.

### Required modern-family modules

| Family | Required module roles |
|---|---|
| Flux 1 | Primary text encoder, secondary text encoder, AE/VAE |
| Flux.2 Klein | Qwen3 text encoder, AE/VAE |
| Krea 2 RAW/Turbo | Qwen3-VL-4B text encoder, Qwen Image VAE |
| Qwen Image/Edit | Qwen text encoder, VAE; MMProj is optional where declared |
| Z-Image/Turbo | Qwen3 text encoder, AE/VAE |

Neo requires explicit module selection. It does not silently choose an arbitrary installed encoder or VAE.

## Workflow behaviour

### SD outpaint

SD 1.5 and SDXL outpaint are Neo-owned preprocessing workflows. Neo expands the source canvas, creates a protected-source mask, aligns the output dimensions, and submits the resulting image/mask through Forge img2img.

### Qwen Image Edit and ImageStitch

Qwen Image Edit remains usable with one main source image. When the selected Forge profile exposes the verified three-argument `ImageStitch Integrated` script contract, Neo's existing Stitch Images panel can additionally supply one or more reference images through Forge `alwayson_scripts`; Image 1 remains the main img2img source. If the script is absent or its schema drifts, only the extra-reference path is gated and single-source edit remains available.

E1 enables this reference path for Qwen Image Edit 2509 img2img/edit and Flux.2 Klein img2img only. Forge's ImageStitch script does not physically composite the references.
This is **optional verified Forge ImageStitch references**, not a generic multi-source compositor.

### Experimental routes

Experimental routes are executable but require extra caution. They remain subject to model-specific Forge behavior and the capabilities of the user's installed Forge build. Neo still validates the exact family, loader, model, modules, setting prerequisites, and workflow before submission.

## Strict UX behaviour

When Forge is selected, Neo displays only the active profile's executable:

- model families;
- loader types;
- workflow modes;
- primary models;
- required/optional module controls;
- route-owned generation controls.

Switching profiles or refreshing inventory coerces stale selections to the nearest valid route. If no route is executable, the selectors and generation controls fail closed instead of falling back to ComfyUI or a static family list.

## Built-in Neo feature remaps and extension boundary

Neo keeps one user-facing surface for High-Res Lab, ControlNet, ADetailer, Image Upscale and Stitch Images. When Forge is selected, those existing surfaces switch to verified Forge-native provider contracts rather than creating duplicate Forge-only cards.

Current mappings include native Forge hires fields, ControlNet/ADetailer always-on scripts, standalone Forge Extras upscale with scale/exact sizing, secondary blending and reported CodeFormer/GFPGAN, and ImageStitch references on supported Qwen Edit / Flux.2 Klein routes. SeedVR2 remains Comfy-only.

Forge Neo also has a native selected-output Hires operation: its txt2img gallery button calls the internal `txt2img_upscale` path, forces Hires on, assigns the selected image to `firstpass_image`, and runs the Hires diffusion pass without regenerating the first pass. This is not ordinary img2img and it is separate from Forge Extras. Neo now executes this through the capability-detected Forge Bridge native operation `native_txt2img_upscale`. It does not call fragile Gradio function indexes, ordinary img2img, Forge Extras, or Comfy.

See:

```text
guides/01_IMAGE/forge_neo_builtin_feature_remap.md
guides/01_IMAGE/forge_neo_extension_compatibility.md
```

Installing a Forge extension does not automatically make it executable through Neo. Its live script/API shape and selected route must both match Neo's provider-owned extension contract. E1 covers existing Neo surfaces, E2 adds dedicated PiD/Spectrum/MultiDiffusion adapters, and E3 adds a conservative generic bridge for primitive scripts from enabled external Forge extensions.

## Forge Couple regional prompting

`Image · Forge Couple` is a dedicated Forge-only Neo frontend for the installed Haoming02 ForgeCouple runtime. FC3 supports Basic, Advanced, and Mask regional prompting on SD 1.5/SDXL checkpoint routes. The main Neo Positive Prompt remains the sole prompt authority.

Mask layers are binary session assets submitted only to the native ForgeCouple request. Neo validates prompt-to-mask count and full-canvas mask union when Global Effect is absent, then strips mask bytes from output metadata and replay.

Experimental Tile Mode is Img2Img-only and FC3 exposes it for Basic/Advanced regions. Mask + Tile stays gated pending a verified upstream API mapping path. ForgeCouple assigns prompt regions to tiles but does not create the tile loop, so Neo directly detects Forge's built-in selectable `SD Upscale` script and verifies its exact four-argument Img2Img contract. Tile arguments occupy ForgeCouple slots 11–16, while Neo owns the one selectable-script payload with SD Upscale arguments `[overlap, upscaler, scale factor, save to Extras]`. No generic Script Bridge setup is required, and arbitrary selectable scripts do not satisfy this dependency.

Forge Couple is not part of Scene Director and is not auto-routed through the generic Script Bridge. It conflicts with Scene Director and MultiDiffusion, but may coexist with independent always-on scripts such as ADetailer, ControlNet, and standard IP-Adapter. Outpaint and unsupported modern families remain gated.

See:

```text
guides/01_IMAGE/forge_couple.md
```

## Live preview, jobs, and recovery

- Enable Forge live preview and set `show_progress_every_n_steps` above `0` for preview polling.
- Preview can remain blank while a model is loading without blocking generation.
- Standard SDAPI jobs use Neo's durable local job mirror.
- A compatible optional Bridge can own durable backend jobs and allow reattachment.
- Standard synchronous Forge requests cannot reconstruct a lost HTTP response after Neo restarts; Neo marks the job for explicit requeue.

See:

```text
guides/01_IMAGE/forge_neo_image_job_lifecycle.md
guides/01_IMAGE/forge_neo_optional_bridge.md
```

## Troubleshooting

### No families or models appear

- Confirm the Forge profile is enabled and selected.
- Confirm Forge was launched with `--api`.
- Run **Refresh Models** in Forge, then **Refresh Forge Admin** in Neo.
- Check that the model filename carries a reliable family signal.
- Select every required encoder/VAE module for modern families.
- Review the live-route blockers in Admin diagnostics.

### Generic checkpoint stays ambiguous

A filename such as `model.safetensors` may be valid but does not reliably distinguish SD 1.5 from SDXL. Rename it with a clear portable family signal or select a model with identifiable metadata. Neo intentionally does not inspect private model weights to guess.

### Flux.2 img2img is hidden

Enable the corresponding regular-img2img option in Forge Settings, refresh Forge Admin, and verify the source image and required modules are selected.

### Stitch Images is hidden on this route

Neo shows Forge Stitch Images only when the active family/compiler owns the reference-image path **and** the selected Forge profile exposes the verified `ImageStitch Integrated` three-argument contract. Refresh Forge Admin after changing Forge scripts. Unsupported families and schema-drifted scripts remain hidden rather than borrowing the Qwen/Flux.2 contract.

### Connected with warnings

Open Admin diagnostics. Failures in optional endpoints such as command flags or embeddings discovery do not block core generation, but the affected extension or diagnostic feature remains unavailable.

### No live preview

Enable previews in Forge and set `show_progress_every_n_steps > 0`. Final generation may still complete correctly when preview frames are absent.

## If a route is missing or becomes unavailable

Refresh the selected Forge profile after changing models, modules, Forge settings, extensions, or the Forge Bridge. Neo intentionally hides routes that the running profile can no longer prove ready.

For a step-by-step readiness checklist, see `forge_neo_validation_and_regression.md` (Forge Neo Readiness and Troubleshooting).

## Detailed guides

| Topic | Guide |
|---|---|
| Provider and REST boundary | `forge_neo_provider_foundation.md` |
| Family and loader authority | `forge_neo_family_loader_routing.md` |
| Live Image capability overlay | `forge_neo_capability_overlays.md` |
| Loader translation | `forge_neo_loader_translation.md` |
| Workflow compilers | `forge_neo_workflow_compilers.md` |
| Strict UI gating | `forge_neo_strict_ux_gating.md` |
| Built-in feature remap | `forge_neo_builtin_feature_remap.md` |
| Validation/regression | `forge_neo_validation_and_regression.md` |
| Admin setup and diagnostics | `../07_ADMIN/forge_neo_admin.md` |
| Lifecycle/recovery | `forge_neo_image_job_lifecycle.md` |
| Extension compatibility | `forge_neo_extension_compatibility.md` |
| Generic external script bridge | `forge_neo_generic_extension_bridge_e3.md` |
| Optional Bridge | `forge_neo_optional_bridge.md` |
| Provider-action release/upgrade/rollback | `provider_action_release_integration.md` |
| End-to-end action matrix | `provider_action_regression_matrix.md` |

## Privacy and repository policy

Neo stores runtime discovery under `neo_data/`, which is excluded from public source packages. Public records and diagnostics must not include credentials, absolute model paths, model files, generated outputs, caches, logs, or runtime databases.

## Post-closeout E2 — Forge extra features

After the Phase 1–7 closeout and E1 feature remap, Neo adds three Forge-only built-in Image extensions: PiD Integrated, Spectrum, and MultiDiffusion. They are not mirrored Forge UI panels; Neo renders its own controls and emits the verified Forge always-on script contracts. PiD is incompatible with High-Res/Hires Fix, Spectrum is gated by Forge negative-prompt skip optimizations, and MultiDiffusion is conservatively limited to SD 1.5/SDXL img2img-family routes until Neo has a dedicated compatible route for additional families. See `forge_neo_extra_features_e2.md`.

## Post-closeout E3 — generic Forge extension discovery/bridge

E3 keeps Forge as the extension installer/manager and adds a provider-owned compatibility catalog plus `image.forge_script_bridge`. External extensions appear in Forge Admin after refresh. Only scripts attributable to enabled external extensions and composed entirely of primitive `/script-info` arguments can be bridged automatically. Dedicated E1/E2 mappings always win, complex/unattributed scripts remain adapter-required, schema fingerprints protect positional arguments from extension updates, and generic execution is restricted to SD1.5/SDXL in E3. See `forge_neo_generic_extension_bridge_e3.md`.

## Post-closeout E1.1 — Forge IP-Adapter remap

`image.ip_adapter` now reuses the same Neo Studio surface across ComfyUI and Forge. Forge discovers standard SD 1.5/SDXL IP-Adapter models through Integrated ControlNet, derives the matching `CLIP-ViT-H (IPAdapter)` or `CLIP-ViT-bigG (IPAdapter)` preprocessor, and aggregates IP-Adapter units with normal Neo ControlNet units into one Forge `ControlNet` always-on script. Hires Fix can coexist on supported txt2img routes. FaceID/InstantID is profile-gated by a live model/preprocessor pair; modern families, outpaint, and multiple references inside one unit remain fail-closed. See `forge_neo_ip_adapter.md`.


## 2026-08-01 — Shared model-path routing for IP-Adapter and ADetailer

Neo keeps the existing `image.ip_adapter` and `image.adetailer` surfaces and switches only their provider execution. A centralized Comfy-style model library is configured once through **Admin → Models → Paths**. Forge Neo may reference that authority through `--forge-ref-comfy-yaml <same YAML path>` or through `--model-ref <same shared models root>`; Neo does not create a duplicated Forge model tree or a second YAML setting.

For IP-Adapter, Neo supplements Forge's live ControlNet model list with path-free model names from the verified shared `ipadapter` catalog and uses shared `clip_vision` names for encoder diagnostics. The running Forge profile must still expose Integrated ControlNet, route slots, and a compatible IP-Adapter preprocessor before the extension unlocks.

For ADetailer, Neo suggests shared `.pt` detector basenames discovered from the central `models/adetailer`, `models/ultralytics`, and supported detector entries in the YAML. Forge's native `<model-ref>/adetailer` folder counts as coverage when `--model-ref` matches the shared root; other detector folders must be covered through `ad_extra_models_dir` (a configured parent may cover nested folders recursively). Restart Forge after path changes. The Forge model control is target-aware: Face, Hands, and Person show matching suggestions; Custom shows the full typed pool. Exact Forge-local detector names remain available through the manual-name action because the standard Forge API does not expose the complete ADetailer model catalog.

All absolute path comparison remains server-side. Admin/browser diagnostics expose only readiness, counts, model basenames, and corrective-action text.

## Provider-aware output reference actions — Phase 4

- Preview and Output Inspector **ControlNet** and **IP Adapter** actions stay on the selected Forge profile.
- Forge catalogs come from the selected profile's Admin capability snapshot, not Comfy object-info or local browser paths.
- Standard IP Adapter and ordinary ControlNet share the verified Forge Integrated ControlNet unit pool.
- Neo stages into the first empty unit and never overwrites an occupied unit silently.
- URL-only outputs are materialized into Neo-owned source storage before staging.
- `neo.image.preview_reference_handoff.v1` is validated before provider compilation; provider/profile/source mismatches return HTTP 409.
- Staging opens Image → Reference and never queues generation.

## Phase 7 — Forge ADetailer and Identity Rescue Finish passes

`image.adetailer` and `image.ip_adapter` now provide provider-owned derived Img2Img Finish execution for Forge Neo.

ADetailer requires the selected Forge profile's live ADetailer always-on script contract and detector coverage. Identity Rescue requires an SD 1.5/SDXL FaceID or InstantID model paired with a compatible live InsightFace preprocessor in Integrated ControlNet. The FaceID contract is `forge.controlnet.ip_adapter.v2`; model and preprocessor discovery is profile-specific and fail-closed.

Both routes preserve the selected provider, clamp outer denoise, force a single output, and append `neo.image.derived_action.v2` lineage. Forge Image Upscale is completed separately in Phase 10 through Extras v2.

## Phase 8 — Provider-aware LoRA picker and Forge serialization

The shared **Image → Assets → LoRA Stack** surface now reads the LoRA catalog from the currently selected Image profile.

- Forge uses the selected profile's Extra Networks catalog, supplemented only by verified shared model paths referenced by that Forge process.
- Comfy uses the selected profile's `LoraLoader.lora_name` catalog.
- Neo never enables or populates a selected Forge profile from a different Comfy/default profile.
- Stack rows remain provider-neutral and portable.
- Forge renders `<lora:name:strength>` only in the submitted positive prompt.
- Comfy keeps compiler-owned workflow loader nodes.
- Existing prompt tags are deduplicated against stack rows.
- Absolute model paths are retained server-side and are not returned to the browser or public records.

The Forge LoRA capability contract is `forge.extra_network.lora.v2`.


## Phase 9 — Provider-aware Embeddings / Textual Inversion

The shared **Image → Assets → Embeddings / Textual Inversion** surface now binds its library and serialization to the currently selected Image profile.

```text
Canonical chip: EasyNegative
Forge compile:  EasyNegative / (EasyNegative:1.2)
Comfy compile:  embedding:EasyNegative / (embedding:EasyNegative:1.2)
```

- Forge uses the selected profile's `/sdapi/v1/embeddings` capability and plain textual-inversion triggers.
- Comfy uses the selected profile's embeddings catalog and adds `embedding:` only while compiling prompt text nodes.
- Positive, negative, and both targets are supported.
- Legacy prefixes, weighted wrappers, file suffixes, and local-path variants resolve to one canonical identity.
- The visible prompt is not permanently modified.
- Absolute model paths remain server-side.
- Forge finish-positive and finish-negative targets remain fail-closed.
- Automatic provider fallback is forbidden.

The Forge Embeddings/TI capability contract is `forge.embedding.token.v2`.


## Phase 10 — Selected-profile Forge Extras Image Upscale

Forge Image Upscale is now operational through `forge.extras.single_image.v2`.

Availability requires the selected profile to report `/sdapi/v1/extra-single-image` and at least one `/sdapi/v1/upscalers` entry. `/sdapi/v1/face-restorers` is soft-optional and only controls whether CodeFormer/GFPGAN options appear.

The UI and queue are selected-profile-only. Exact dimensions, crop-to-fit, primary/secondary upscalers, blend visibility, reported face restoration, and upscale-first compile directly to Forge Extras. SeedVR2 and Comfy node controls remain hidden. Output lineage uses the same derived-action contract as the other Finish actions.
