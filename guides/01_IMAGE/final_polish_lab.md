---
guide_id: image.final_polish_lab
title: Image Final Polish Lab
surface: image
scope: external_extension
applies_to:
  - image_workspace
  - image_finish
  - final_polish_lab
  - relight
  - layer_polish
  - camera_finish
  - batch_polish
  - look_library
tags:
  - image
  - finish
  - final polish
  - relight
  - ic-light
  - layerstyle
  - propost
  - batch polish
  - frontend reliability
  - output metadata
  - safe replay
priority: 88
version: 6
updated: 2026-07-15
---

# Image Final Polish Lab

**Final Polish Lab** is an external installed Image → Finish extension. It is not a repo-shipped built-in tool like High-Res Lab, ADetailer, or Image Upscale.

It renders under:

```text
Image → Finish → External Extensions → Image · Final Polish Lab
```

Its mount slot is:

```text
image.finish.external.final_polish_lab
```

## Purpose

Final Polish Lab combines three finishing lanes:

| Lane | Purpose |
|---|---|
| **Relight** | Standalone IC-Light Native SD 1.5 relighting. |
| **Layer Polish** | Standalone LayerStyle compositing / layer polish. |
| **Camera Finish** | Standalone ProPost camera/color finishing. |

It also includes Look Library presets, live batch execution, dependency diagnostics, metadata, replay bundles, and Output Inspector summaries.

## Current runtime boundary

The standalone release includes live execution, browser recovery,
completion-aware output provenance, and source-explicit replay. Every completed
item is persisted as its own derived Neo result before the panel receives the
saved result binding.

Current boundary:

```text
One lane or fixed Relight -> Layer Polish -> Camera Finish chain
Exact live /object_info schemas required
Relight requires a selected SD 1.5 checkpoint
Checkpoint and IC-Light model lists come from ComfyUI loader choices
Batch limited to 20 unique sources per submission
Continue or stop-on-error per-item recovery
Durable batch parent and child job contexts
One active frontend mutation/run at a time
Stale active-profile catalog responses rejected
Recoverable browser-session job/batch monitoring
Completion metadata finalized before Neo result persistence
Output Inspector and provenance retained in Neo-approved extension slots
Replay actions declare new, current-output, original, or batch source ownership
Replay restore never auto-submits
No provider graph mutation
No node/package/model installation
No raw image byte storage
```

IC-Light Native is a diffusion-model patch, not a simple image filter. Relight
therefore runs an isolated SD 1.5 checkpoint + IC-Light UNet + conditioning +
sampler + decode graph. It does not modify Neo's base Image generation graph.

## Lane chaining

When more than one lane is enabled, the runtime uses this fixed order:

```text
Relight -> Layer Polish -> Camera Finish
```

Disabled lanes are skipped. The extension validates each isolated lane against
the live profile, removes downstream source loaders and intermediate save nodes,
and hands the IMAGE output directly to the next lane. One final `SaveImage`
produces the derived result. The chain stays separate from Neo's generation
graph.

## Batch Polish

- `selected_results` accepts Neo result IDs.
- `uploaded_images` accepts staged upload IDs. The panel can stage multiple
  files directly.
- `mixed_sources` accepts `result:<id>` and `upload:<id>` entries.
- The execution cap is 20 unique sources.
- `continue` keeps submitting after an item fails.
- `stop_on_error` marks later items skipped after the first submission failure.

The parent batch status aggregates child progress. Each successful child still
uses the normal permission-gated Neo result persister and remains independently
recoverable after restart.

## Reliable running and monitoring

- **Run** is locked after the execution snapshot is taken. Re-rendering the
  panel cannot enable a second submission.
- Controls remain frozen while assets are staging, a job is submitting, or the
  current job/batch is actively monitored.
- Changing the active ComfyUI profile during preflight cancels that submission
  before the queue POST. Review the new profile's models and run again.
- Temporary status/network failures show **reconnecting** and retry the same
  status endpoint. They do not submit another ComfyUI job.
- Browser refresh or panel remount reconnects to the same active job/batch when
  its browser-session monitor descriptor is available.
- **Stop monitoring** stops browser polling only. The ComfyUI job continues.
- **Resume monitoring** reconnects to that same job/batch; it does not rerun the
  lane chain.
- If queue submission times out before Neo confirms the job ID, inspect the
  ComfyUI queue before pressing Run again because the first submission may have
  been accepted.

Only safe GET/status calls receive bounded transient retries. Asset upload and
queue POST requests are never automatically repeated.

## Output, inspector, and metadata

The saved result records the actual terminal execution state, fixed lane order,
execution schema, operation list, resolved node roles, selected model/LUT names,
public source and supporting-asset identifiers, public output descriptors,
whole-workflow timing, and batch-child context.

The Output Inspector and provenance are stored under the extension's approved
`extensions.validation[]` record. Final Polish does not add a custom output
record format or a Final-Polish-specific persistence branch to Neo Base.

Metadata must not contain raw image bytes, local/absolute paths, provider base
URLs, authorization values, or the runtime-only Comfy client ID.

## Safe replay

- **Reuse same polish** restores settings but clears old source, supporting
  assets, and batch references. Select a new source before running.
- **Polish this output again** uses the owning saved Neo result as the next
  source.
- **Send to Finish** restores the Finish state against that saved result.
- **Reuse original source** restores recorded public source references only
  after Neo revalidates them.
- Lane-disable actions use the owning saved result and remove one lane.
- Batch replay preserves its recorded list only after source revalidation and
  explicit user confirmation.

Restoring an action never queues ComfyUI. Review the source and let the selected
profile's nodes, models, and assets revalidate before pressing Run.

## Relight setup

1. Install `ComfyUI-IC-Light-Native` in the selected ComfyUI backend.
2. Follow that node repository's model page and place the official LDM IC-Light
   FC/FBC files under `ComfyUI/models/unet`.
3. Make sure at least one compatible SD 1.5 checkpoint is visible to
   `CheckpointLoaderSimple`.
4. Open Final Polish Lab and refresh the active profile models.
5. Select the SD 1.5 checkpoint. The extension auto-selects FC for foreground or
   light-map modes and FBC for foreground/background mode.

Directional foreground modes generate a temporary light map. Custom/light-map
mode needs an uploaded light map. Foreground/background mode needs an uploaded
background. Mask mode needs a grayscale matte with white/light foreground.

The extension does not own a custom model catalog, scan personal paths, or
download models.

## Install and update

- Install the complete release ZIP or the standalone GitHub repository through
  **Neo → Admin → Extensions**. Do not copy extension files into `neo_app`.
- Approve the version-bound `custom_ui`, `backend_routes`, and `result_write`
  permissions, then restart Neo to activate the Python runtime.
- A version change requires a new approval. GitHub installs may use Neo's
  updater; ZIP installs should use a complete new release ZIP.
- Install only the custom nodes/models required by the lanes you use. Check each
  custom node project's model page and place extra models in the role/folder it
  documents for the selected ComfyUI installation.
- Missing optional dependencies block only their lane. Live `/object_info` and
  loader choices—not a package-owned catalog or a personal path—determine
  readiness.

## Built-in looks

Common looks include:

```text
clean_commercial
soft_cinematic
product_hero
moody_poster
natural_photo_cleanup
anime_card_polish
dark_fantasy_cover
luxury_ad_finish
```

These presets map to safe Neo payload fields only. They do not install nodes, inject raw Comfy graphs, or store image bytes.

## Assistant rules

When the user asks about Final Polish Lab:

- call it an **external Image Finish extension**;
- direct install/update questions to the complete standalone ZIP or repository,
  version-bound permission approval, and required Neo restart;
- never tell users to copy it into Neo Base or combine only selected files from
  different versions;
- do not describe it as a normal built-in direct-render tool;
- explain that each lane can run alone and multiple enabled lanes use the fixed safe chain;
- describe Batch Polish as live per-source execution, capped at 20, not as planning-only UI;
- explain that Stop monitoring does not cancel the provider job;
- explain that replay restore never submits a provider job automatically;
- distinguish **Reuse same polish** (new source required) from **Polish this
  output again** (owning saved result becomes the source);
- explain that original-source and batch replay require asset revalidation, and
  batch replay also requires confirmation;
- recommend Resume monitoring for a known accepted job instead of pressing Run again;
- if submission outcome is unknown, tell the user to inspect the ComfyUI queue before retrying;
- check the live extension status before saying it is installed or active;
- remind users that IC-Light Native supports the SD 1.5 checkpoint route;
- do not suggest that SDXL/Flux checkpoints are valid for the IC-Light Native Relight lane;
- direct missing-model users to the custom node's own model instructions and the active ComfyUI loader catalogs.
