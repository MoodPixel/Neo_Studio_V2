---
guide_id: image.results
title: Image Results Workspace
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_results
  - results
  - saved_outputs
  - output_inspector
  - metadata_sidecars
  - replay
  - reuse
  - delete_output
  - cascade_delete
tags:
  - image
  - results
  - saved outputs
  - output inspector
  - metadata
  - replay
  - reuse
  - delete
priority: 112
version: 1
updated: 2026-07-09
---

# Image Results Workspace

The **Image → Results** workspace is the saved-output manager. It reads Neo-owned files from `neo_data`, shows saved output cards, loads metadata sidecars, exposes replay/reuse actions, and handles safe deletion.

Results is not the live Preview panel. Preview is for live/final viewing while generating. Results is for saved output inspection and management.

## Results & Save Details

| Field / control | Meaning |
|---|---|
| **Category** | Current Neo output category. Saved outputs are organized under category folders. |
| **New Category** | Adds a new output category. Use client/project-friendly names. |
| **Filename Prefix** | Prefix used when Neo names saved output files. |
| **Padding** | Numeric padding for output filenames. |
| **After saving to Neo_Data, remove backend duplicate output files** | When enabled, Neo keeps the canonical saved output in `neo_data` and deletes safe backend duplicate outputs after persistence. |
| **Output path preview** | Shows where Neo saves generated images. |
| **Metadata path preview** | Shows where Neo saves sidecar metadata. |
| **Save Details** | Saves category/prefix/padding/cleanup settings. |
| **Add Category** | Creates the typed category. |

## Replay Storage Manager

The Replay Storage Manager tracks saved outputs, metadata sidecars, latent restore states, and safe cleanup candidates.

| Control | Meaning |
|---|---|
| **Refresh Storage** | Rescans output, metadata, and latent restore storage. |
| **Delete Orphan Latents** | Deletes latent restore files not referenced by saved metadata. Referenced restore points are preserved. |

Do not confuse orphan latent cleanup with deleting a saved output. Saved output deletion uses the Output Inspector delete preview.

## Saved Outputs list

| Control | Meaning |
|---|---|
| **Category filter** | Shows all categories or one category. |
| **Date sort** | New-to-old or old-to-new. |
| **Saved output cards** | Click a card to load its metadata into Output Inspector. |
| **Refresh Results** | Reloads the list and active metadata from `/api/image/results`. |

If a saved file is missing, Neo hides/removes the broken entry from the Results view instead of crashing the UI.

## Output Inspector

Output Inspector shows the selected result recipe and saved media.

It can display:

- selected image / output file preview;
- Result ID and Created date;
- Provider, backend profile, job ID, job status;
- Model Family, loader, model, VAE;
- Width/height, steps, CFG, seed, clip skip, sampler, scheduler;
- generation time;
- backend cleanup report;
- prompt conditioning mode;
- positive/negative prompt;
- effective positive/effective negative prompt after extensions;
- input/source/control/reference/mask assets;
- extension payload summaries;
- raw metadata JSON when explicitly opened.

Raw metadata is for debugging. Assistant answers should summarize it, not dump it, unless the user asks for raw JSON.

## Output reuse actions

The selected result can be reused as:

| Action | Meaning |
|---|---|
| **Img2Img** | Sends the selected output into Image source for img2img. |
| **Inpaint** | Sends the selected output into inpaint source. A mask is still needed. |
| **Outpaint** | Sends the selected output into outpaint/canvas workflow. |
| **ImgUpscale** | Sends the selected output to Image Upscale. |

The Post-Fix Selected Output panel can stage a result for:

- High-Res Lab;
- ADetailer;
- Identity Rescue / FaceID;
- Image Upscale.

Cloud/API outputs can be staged as source images, but local finish tools still need a compatible local Comfy Image backend.

## Replay / regenerate behavior

Results can restore prompt/parameter/extension information from metadata sidecars. Some replay branches are intentionally guarded:

| Replay source | Meaning |
|---|---|
| **Full recipe** | Restore prompt, params, and supported extension settings. |
| **Base generation only** | Strip finish/enhancement passes for a clean base branch. |
| **Before/after High-Res Fix** | Requires saved latent restore points. Locked until present. |
| **Before ADetailer** | Requires saved pre-ADetailer restore point. Locked until present. |

Restored extension settings can require revalidation. Neo should not blindly re-enable finish/reference/asset extensions when nodes, models, source files, or route state are missing.

## Delete Saved Output

The **Delete Saved Output** button opens a preview modal before deleting anything.

The preview can include:

- generated image output files;
- metadata sidecars;
- latent checkpoint / restore files;
- image job context files;
- unique linked input/control/reference/mask assets;
- shared assets skipped because other results reference them;
- unsafe/skipped paths.

Delete modes:

| Mode | Meaning |
|---|---|
| **Delete output only** | Deletes the saved output and sidecar metadata for this result. |
| **Delete full linked assets** | Deletes output plus unique linked assets, latent restore files, and job context files after Neo's reference/path guard approves them. |

Shared assets are skipped. Unsafe paths outside allowed `neo_data` roots are skipped.

## Assistant rules

When the user asks about Results:

- answer from this guide and `guides/01_IMAGE/output_inspector.md`;
- use metadata summaries, not raw JSON, unless raw trace is requested;
- distinguish Preview vs Results clearly;
- explain that Results works from saved `neo_data` outputs;
- for delete questions, explain preview/cascade safety and shared-asset skipping.
