---
guide_id: image.finish
title: Image Finish Workspace
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - finish
  - high_res_lab
  - adetailer
  - image_upscale
  - final_polish_lab
  - output_reuse
  - post_fix
tags:
  - image
  - finish
  - high res
  - upscale
  - adetailer
  - post output
  - reuse
  - repair
priority: 112
version: 7
updated: 2026-08-02
---

# Image Finish Workspace

The **Image → Finish** workspace owns finishing, repair, upscale, and post-output reuse tools. It is separate from base generation:

- **Generation** builds the main image graph.
- **Assets** owns LoRA and embedding assets.
- **Reference** owns ControlNet/IP Adapter guidance.
- **Finish** refines, repairs, upscales, or stages an existing output/source for another pass.
- **Results** reviews saved outputs, metadata, replay, cleanup, and deletion.

Finish tools are route-aware. A card can be visible but disabled when the selected backend, model family, loader, workflow mode, custom node set, or source image does not support the tool.

## Shared Finish action registry

Preview and Output Inspector obtain the Finish action inventory from the same backend registry. The locked order is:

1. High-Res Lab
2. ADetailer
3. Identity Rescue / FaceID
4. Image Upscale

The browser does not define these actions independently. The registry owns labels, icons, required extensions/capabilities, destination panels, handler identities, and metadata-preservation policy. `GET /api/image/preview-actions/evaluate` then evaluates those definitions against exactly the selected Image backend profile. It publishes capability truth separately from `dispatch_ready`, so a physically available Forge capability cannot activate a Finish button before Neo's matching provider-owned dispatcher is implemented.

## Provider-owned Finish dispatcher

Preview and Output Inspector now call the same provider-owned Finish dispatcher. Executable actions stage `neo.image.derived_action.v2`, lock the selected finishing provider/profile, preserve source and parent lineage, and save through the `append_derived` lane. Backend validation rejects provider, profile, dispatch, source, runtime-mode, and unconfirmed cross-provider mismatches.

Neo does not temporarily switch Forge to Comfy. A cloud or other-provider output may cross into a local finishing backend only after the user explicitly chooses that finishing profile. The compatibility profile-list route cannot automatically select one. See `guides/01_IMAGE/image_finish_dispatch.md`.

## Shared image and mask preparation

**Remove Background → Mask & Object Utilities** also exposes the installed ComfyUI-RMBG pixel tools as independent operations:

- **Mask Overlay**: inspect a mask over the source image without changing the source.
- **Object Remover · Lama**: remove a masked object and save a derived image.
- **Image + Mask Resize**: resize, pad, or crop an image and its mask under one aligned contract.
- **Image Crop**: prepare a source/Stitch image; a supplied mask receives the same crop.

These are preparation/finish operations, not generation engines. Inpaint and Outpaint consume the resulting prepared image/mask; Scene Director consumes a region mask; Stitch consumes a prepared source image. Neo validates each RMBG node and its inputs against the active ComfyUI `/object_info` response and blocks unavailable operations without silent fallback.

## Finish tools

| Tool | Main purpose | Execution type | Guide |
|---|---|---|---|
| **High-Res Lab** | High-resolution diffusion refine / highres-style finish pass. | Normal Image workflow patch / finish pass. | `guides/01_IMAGE/high_res_lab.md` |
| **ADetailer** | Selective local repair for faces, hands, people, clothing, products, or manual regions. | Normal Image workflow patch / final repair pass. | `guides/01_IMAGE/adetailer.md` |
| **Image Upscale** | Standalone upscale utility for selected outputs or uploaded images. | Standalone queue route, not normal generation compiler. | `guides/01_IMAGE/image_upscale.md` |
| **Final Polish Lab** | External finish cockpit for relight, layer polish, camera/color looks, fixed-order chaining, bounded batch polish, and source-explicit replay. | Reliable external standalone/chained ComfyUI prompts with recoverable monitoring and completion-aware saved-result metadata. | `guides/01_IMAGE/final_polish_lab.md` |

## Finish vs Results

Use **Finish** when the user wants to change or improve an image.

Use **Results** when the user wants to inspect, reuse, replay, organize, delete, or understand saved outputs.

Examples:

| User asks | Best place |
|---|---|
| “Make this image larger but keep the same look.” | Finish → Image Upscale or High-Res Lab |
| “Repair the face/hands after generation.” | Finish → ADetailer |
| “Run a high-res pass after base generation.” | Finish → High-Res Lab |
| “Use this saved output as img2img source.” | Results → Output Inspector reuse actions |
| “Delete this saved output and all linked unique assets.” | Results → Output Inspector delete preview |
| “Show the seed/model/prompt used for this image.” | Results → Output Inspector |

## Route and family behavior

| Selected backend route | High-Res Lab | ADetailer | Image Upscale | Final Polish Lab |
|---|---|---|---|---|
| **ComfyUI SDXL checkpoint** | Existing Comfy derived finish route when its extension/capability route is ready. | Existing Comfy detailer finish route where supported. | Existing Comfy standalone utility route. | Camera Finish/Layer Polish can run image-only; IC-Light Relight stays unavailable because it requires SD 1.5. |
| **ComfyUI SD 1.5 checkpoint** | Existing Comfy derived finish route when ready. | Experimental where the current detailer route permits it. | Existing Comfy standalone utility route. | Standalone lanes, fixed-order chains, and bounded batches are available when every enabled lane dependency is ready. |
| **Forge / Forge Neo** | Native selected-image Hires is available through `run_forge_native_hires` when the selected Forge profile exposes Bridge capability `native_post_hires`. | Forge-owned derived Img2Img with the selected profile's verified ADetailer always-on script. | Provider-owned Forge Extras `/sdapi/v1/extra-single-image` is available when the selected Forge profile reports Extras plus at least one upscaler. Exact sizing, secondary blending, and reported face restoration are supported. | Provider-gated unless that external extension publishes a validated Forge contract. |
| **xAI Grok / cloud API** | Cloud outputs may be sources, but local finishing requires an explicitly selected local finishing profile. | Same explicit local-profile rule. | Same explicit local-profile rule. | Not a direct API render path in the current extension contract. |

## Output source behavior

Finish tools can get a source from:

- the current Preview/final output;
- a selected saved Result;
- an uploaded image file;
- a staged output sent from Results/Post-Fix actions.

When a saved output is staged from Results, Neo should preserve the selected image as the source. It should not silently re-run the original base generation unless the user chooses replay/regenerate. It must also preserve the selected backend profile; cross-provider finishing is an explicit user choice, never an availability fallback.

## Assistant rules

When answering Finish questions, use this guide plus the tool-specific guide. Check the live Image snapshot for:

- active backend profile;
- Model Family;
- Main Model Type / loader;
- Workflow Mode;
- selected/staged source image;
- extension enabled/disabled state;
- route state: Available, Experimental, Provider gated, Planned, or Unsupported.

Do not promise a finish pass can execute just because the panel is visible. Visible can mean “installed but gated.”

For Final Polish Lab, a temporary browser status error does not mean the
ComfyUI job failed. Use its Resume monitoring action when available. Stop
monitoring stops only the browser poll; it does not cancel the provider job or
authorize submitting a replacement job.

Final Polish Lab is distributed separately from Neo Base. Install or update the
complete standalone ZIP/repository through Admin, approve its version-bound
permissions, and restart Neo. Do not copy it into `neo_app`. Custom nodes and
extra models remain ComfyUI-owned; follow each node project's model page and
place files where that selected ComfyUI installation exposes them.

Final Polish replay restoration also does not submit automatically. **Reuse same
polish** waits for a new source; **Polish this output again** binds to the owning
saved Neo result. Original-source and batch restore must revalidate recorded
assets, and batch restore requires confirmation.

## Forge derived Finish passes — Phase 7

Forge now owns two additional output-derived Finish routes:

- **ADetailer** — selected output → Forge Img2Img → verified ADetailer always-on script.
- **Identity Rescue** — selected output → Forge Img2Img → verified FaceID/InstantID Integrated ControlNet unit.

Both use `neo.image.derived_action.v2`, preserve the selected provider/profile, force a single derived result, clamp outer denoise, and append lineage metadata. Missing detector, FaceID model, preprocessor, reference image, or route capacity blocks execution. Neither route may fall back to ComfyUI.



## Forge Image Upscale — Phase 10

Image Upscale now completes the Forge Finish set through `run_provider_extras`.

- The selected Forge profile remains selected from click through queue and result polling.
- The panel renders Forge Extras controls only; SeedVR2 and Comfy workflow/node controls are hidden.
- Primary/secondary upscalers come from the selected profile.
- Scale and exact-dimension modes are supported.
- CodeFormer and GFPGAN appear only when reported by that Forge instance.
- The operation uses `/sdapi/v1/extra-single-image`, not Img2Img, native Hires, or Comfy.
- Derived outputs keep `neo.image.derived_action.v2` source and parent lineage.

## Provider-aware Finish diagnostics — Phase 12

The Preview and Output Inspector Finish toolbars now show the selected provider/profile and the real class of each operation:

- **High-Res Fix** — diffusion second pass;
- **ADetailer** — automatic repair;
- **Identity Rescue** — identity-guided repair;
- **Image Upscale** — pixel/post-processing upscale.

Guided mode shows concise missing-requirement guidance. Expert mode shows the exact provider execution route and failed checks. Replay profile binding, restored-extension revalidation, and output parent/root/depth lineage are visible on both surfaces. These diagnostics do not authorize provider fallback; the selected Image profile remains the sole execution owner. See `provider_aware_preview_diagnostics.md`.
