---
guide_id: image.output_inspector
title: Image Output Inspector and Metadata
surface: image
scope: built_in
applies_to:
  - image_workspace
  - output_inspector
  - metadata_sidecars
  - image_results
  - delete_output
  - cascade_delete
  - latent_capture
  - replay
  - reuse
tags:
  - image
  - output inspector
  - metadata
  - sidecar
  - cleanup
  - results
  - replay
priority: 105
version: 4
updated: 2026-07-23
---

# Image Output Inspector and Metadata

The **Output Inspector** is the recipe/detail card inside **Image → Results**. It reads Neo-owned output files and metadata sidecars from `neo_data`, then shows the selected output, prompt recipe, model settings, source assets, extension summaries, replay data, cleanup reports, and safe delete options.

Use `guides/01_IMAGE/image_results.md` for the full Results workspace. Use this guide when the user asks what a saved output used, how to reuse it, how metadata works, or what delete/replay actions do.

## What metadata can include

Metadata sidecars can include:

- result ID and created date;
- provider/backend profile and job ID;
- Image route: mode, subtab, family, loader, workflow mode;
- main model, VAE/AE, sampler, scheduler;
- prompt and negative prompt;
- effective positive/effective negative prompt after prompt-only or extension merges;
- width, height, steps, CFG/guidance, seed, clip skip;
- prompt conditioning mode and changed/clamped weighted tags;
- source images, masks, outpaint/canvas assets, reference assets, ControlNet maps, IP Adapter references;
- extension payloads and workflow patches;
- run timing / elapsed generation time;
- cleanup report for backend duplicate outputs and handoff files;
- latent restore states when latent capture is enabled;
- replay/reuse bundle.

## Inspector sections

| Section | Meaning |
|---|---|
| **Media preview** | Shows the selected output or selected inspector asset. |
| **Preview action toolbar** | Sends the selected output/asset to source, reference, LayerDiffuse, or finish tools. |
| **Chips** | Compact summary of mode, size, seed, steps, CFG, model, runtime, cleanup, and prompt conditioning. |
| **Reuse selected output as source** | Sends output to Img2Img, Inpaint, Outpaint, or Image Upscale. |
| **Replay/regenerate panel** | Restores saved prompt/params/extensions or guarded latent branches when available. |
| **Provider replay validation** | Shows why replay is ready, blocked, or requires revalidation. |
| **Output file strip** | Shows files saved for the selected result. |
| **Input asset strip** | Shows source/control/mask/reference assets recorded with the result. |
| **Extension asset strip** | Shows extension-owned preview/source assets where available. |
| **Prompt blocks** | Positive, effective positive, negative, and effective negative prompt text. |
| **Meta grid** | Result ID, Created, Provider, Backend Profile, Job ID, Status, Generation Time, Cleanup, Family, Loader, VAE, Clip Skip, Conditioning, etc. |
| **Extension Inspector** | Human-readable summaries for Style Stack, CFG Fix, LoRA, Embeddings/TI, ControlNet, IP Adapter, Scene Director, ADetailer, High-Res Lab, Image Upscale, LayerDiffuse, and other extension payloads. |
| **Raw metadata JSON** | Developer/debug view. Do not dump this in normal Assistant replies unless requested. |

## Safe delete behavior

Delete actions should use a safe preview/cascade contract.

| Delete mode | Meaning |
|---|---|
| **Delete output only** | Removes the saved output file(s) and metadata sidecar(s) for this result. |
| **Delete full linked assets** | Removes output plus unique linked source/control/reference/mask assets, latent restore files, and job context files only after Neo confirms they are safe and not shared. |

Shared source/control/reference assets should be skipped. Unsafe paths outside allowed `neo_data` roots should be skipped.

## Replay safety

Replay does not mean “blindly restore everything and run.” Neo should revalidate:

- backend profile;
- model file availability;
- source/mask/reference asset availability;
- extension route state;
- custom node/model readiness;
- latent restore availability.

Some branches such as **Before High-Res Fix**, **After High-Res Fix**, and **Before ADetailer** stay locked until the metadata includes the corresponding restore point.

### Latent persistence and replay availability

Neo stores latent information in two separate forms:

- **Comfy provider reference:** the provider-relative filename, subfolder, type, and `LoadLatent` name used for execution.
- **Neo copy:** a retained file under `neo_data/outputs/image_latents/` used for storage accounting, inspection, and safe deletion.

The Neo copy is not an executable Comfy reference. When a saved latent has bytes but no valid provider coordinates, Output Inspector records it as `neo_copy_only`. That state does not unlock **Final latent** or phase-checkpoint replay.

Before a latent branch is queued, Neo verifies the saved provider reference against Comfy. When the original Comfy output has been removed, replay stops before the workflow is submitted and reports:

```txt
Latent checkpoint is no longer available in ComfyUI. Start a clean generation or select another checkpoint.
```

Neo does not silently substitute or upload the retained copy. This avoids false replay claims and prevents host filesystem paths from leaking into the provider workflow.

### Comfy latent path portability

Saved Comfy latent metadata is provider-relative. Neo normalizes Windows and POSIX separators into one `/`-based Comfy reference before persistence or replay, for example:

```txt
NeoStudio_latent/neo-run-123/final.latent [output]
```

Absolute machine paths, drive letters, UNC paths, home paths, URLs, and traversal components are not valid replay references and must be blocked before queueing. The Neo-owned copy under `neo_data/outputs/image_latents/` is retained for Neo storage and cleanup; it is not a substitute for the original Comfy provider-relative `LoadLatent` name.

## Assistant rules

When answering Output Inspector questions:

- summarize metadata in plain language;
- do not dump raw JSON unless the user asks for raw metadata/debug trace;
- explain saved-output actions as Results actions, not live Preview actions;
- explain delete as preview-first and Neo_Data path-guarded;
- when asked about prompts/settings used, answer from prompt/model/params/extension summaries first.
