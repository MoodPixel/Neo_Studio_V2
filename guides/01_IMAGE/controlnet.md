---
guide_id: image.controlnet
title: ControlNet Reference Guide
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - reference
  - controlnet
  - canny
  - depth
  - openpose
  - dwpose
  - lineart
  - softedge
  - scribble
  - normalbae
  - tile
  - inpaint_control
  - outpaint_control
  - qwen_controlnet
  - flux_controlnet
tags:
  - image
  - reference
  - controlnet
  - control image
  - generated map
  - canny
  - depth
  - pose
  - openpose
  - dwpose
  - lineart
  - softedge
  - tile
  - route aware
  - loader aware
priority: 118
version: 5
updated: 2026-07-23
---

# ControlNet

**ControlNet** is the Image → Reference extension for structural guidance. It helps Neo guide a generation using a control image or generated map: edges, depth, pose, lineart, softedge, scribble, normal maps, tile/detail, or route-specific inpaint/outpaint control.

Use this guide when the user asks about the ControlNet card, control images, generated maps, map building, canny/depth/pose settings, ControlNet model dropdowns, or why ControlNet is disabled/gated.

## What ControlNet does

ControlNet tells the image model to follow a structure. The prompt still decides what the image should be; ControlNet decides what structure the image should respect.

Good uses:

- preserve a pose or character silhouette;
- follow edges from a source image;
- keep room/layout perspective;
- guide depth and foreground/background separation;
- preserve lineart or sketches;
- use tile/detail guidance during refinement;
- help inpaint/outpaint follow an existing mask/canvas route where the selected family supports it.

## Main fields

| Field | What it does | Practical note |
|---|---|---|
| **Apply ControlNet** | Enables ControlNet for the current generation. | If unchecked, Neo stores the draft but does not patch ControlNet into the workflow. |
| **+ Add Unit** | Adds another ControlNet unit. | Multiple units can combine pose + depth + edges, but too many can over-constrain output. |
| **Clean Disabled** | Removes inactive/disabled units. | Use before saving presets or debugging. |
| **Refresh Nodes** | Refreshes Comfy node discovery and the ControlNet model list. | Neo checks the selected Comfy profile's live loader choices and configured model folders. Use it after installing models or restarting ComfyUI. |
| **Batch Build Maps** | Builds generated maps for multiple units when possible. | Useful after setting source images and preprocessors. |
| **Use unit** | Enables an individual ControlNet unit. | Disabled units remain in the draft but do not apply. |
| **Type** | Semantic control type, such as Canny, Depth, OpenPose, Lineart, SoftEdge, Scribble, NormalBae, or Tile. | This tells Neo what kind of structural control the unit represents. |
| **Preprocessor** | Chooses how to build a map from the control image. | `None / use image directly` means the attached image is already the control map. |
| **Model** | Selects the ControlNet/model-patch file used by this unit. | Must match the selected family/route. SDXL ControlNet models are not automatically valid for Flux/Qwen routes. |
| **Control image** | Source image used directly or used to build a generated map. | Drag/drop, browse, or send an output from Preview/Results. |
| **Generated map** | Preprocessed map produced by Neo/Comfy/local fallback. | Example: a canny edge map built from a normal photo. |
| **Build Map** | Builds a generated map for the selected unit. | Requires a control image and either a preprocessor node or local fallback support. |
| **Strength** | How strongly the model follows the control. | Lower values allow more freedom; higher values force structure more strongly. |
| **Start % / End %** | When ControlNet affects the diffusion process. | `0 → 1` applies through the whole run. Shorter windows allow looser influence. |
| **Fit mode** | How the control image/map fits the generation size. | `Contain` preserves the reference shape; `cover/stretch/native` can change layout behavior. |
| **Detect res** | Resolution used by preprocessors such as depth/pose/lineart. | Higher can capture more detail but costs more time. |
| **Canny low / high** | Edge thresholds for Canny maps. | Low values produce more edges; high values produce cleaner/sparser edges. |

## Control types and preprocessors

| Type / preprocessor | Use for | Notes |
|---|---|---|
| **Canny / edges** | strong silhouette and edge composition | Good for pose/object boundaries; too strong can make outputs rigid. |
| **Depth** | perspective and scene depth | Good for rooms, full-body placement, foreground/background separation. |
| **OpenPose / DWPose** | human body pose | Enable hands/face only when needed; more pose detail can also introduce constraints. |
| **Lineart** | drawings, clean contours, anime/comic-style line guidance | Works best with clean source images. |
| **Anime Lineart** | anime/manga-style line maps | Useful for stylized character workflows. |
| **SoftEdge / HED** | softer structure than Canny | Good when Canny is too harsh. |
| **Scribble / XDoG** | loose sketch guidance | Useful for rough blocking and concept layouts. |
| **NormalBae** | surface normals / 3D-like shape cues | More specialized; route/model support matters. |
| **Tile / detail** | detail preservation/refinement | Often used to preserve texture/detail rather than full pose. |
| **None / use image directly** | use an already-created map | Best when the user supplies a map or a previous generated map. |

## Control tasks

Neo has a task selector internally. The UI exposes task behavior depending on workflow mode and route support.

| Task | Available when | Meaning |
|---|---|---|
| **Standard map control** | Generate, Img2Img, Edit, Inpaint, Outpaint where route supports map control | Uses a normal control image or generated map. |
| **Inpaint control** | Inpaint mode only | Uses the Image tab source image + painted mask/source mask with a family-specific adapter. |
| **Outpaint control** | Outpaint mode only | Uses the padded outpaint canvas/mask with a family-specific adapter. |

Do not explain inpaint/outpaint ControlNet as a generic SDXL ControlNet fallback. Neo uses route-specific adapters for SD, Flux, Flux2 Klein, and Qwen when the route is active.

## Family / loader support summary

Always check the live route badge first. The current guide-level summary is:

| Family / loader | ControlNet status |
|---|---|
| **SDXL + Safetensors / Checkpoint** | Available for map control on Generate, Img2Img, and Inpaint. SD checkpoint mask/canvas adapters are available for Inpaint/Outpaint control where the base route is active. |
| **SD 1.5 + Safetensors / Checkpoint** | Experimental parity support for Generate, Img2Img, Inpaint, and SD mask/canvas adapters. Validate before batch work. |
| **Flux 1 + Safetensors / Components** | Available on routed Comfy paths. Uses Flux-compatible ControlNet paths and Flux Alimama-style inpaint/canvas adapters where active. |
| **Flux 1 + GGUF** | Available on routed Comfy paths. Uses Flux GGUF-specific ControlNet policy; do not treat it as SD checkpoint ControlNet. |
| **Flux 2 Klein + Safetensors / Components** | Available through the Flux2/Klein Fun Union adapter policy. |
| **Flux 2 Klein + GGUF** | Available through Flux2/Klein GGUF adapter policy. |
| **Qwen Image Edit + Components or GGUF** | Available through Qwen-safe InstantX/standard map control and DiffSynth/InstantX inpaint/outpaint adapters. |
| **Qwen Rapid AIO + Safetensors / Bundled or GGUF** | Available through Qwen Rapid AIO-specific map and DiffSynth/InstantX adapter policy. |
| **Qwen Image Edit 2509 + Components or GGUF** | Available through Qwen 2509-specific map and DiffSynth/InstantX adapter policy. |
| **ZImage / ZImage Turbo** | Implementation target. Neo may preserve settings, but active graph patching should not be promised unless the live route says Ready/Experimental. |
| **HiDream** | Implementation target/provider gated unless the live route matrix promotes the exact route. |
| **xAI Grok Imagine / API profiles** | Not a local Comfy ControlNet graph patch. Do not promise ControlNet execution unless a future API/backend exposes it. |

## Route-specific adapter choices

### Qwen ControlNet adapter

When the selected Qwen route exposes inpaint/outpaint ControlNet adapter controls:

| Option | Meaning |
|---|---|
| **Auto · prefer DiffSynth** | Neo chooses DiffSynth when Qwen model-patch nodes are available, otherwise InstantX/native ControlNet if available. |
| **DiffSynth model patch** | Uses Qwen model-patch nodes. Model patches usually live in `ComfyUI/models/model_patches`. |
| **InstantX Qwen ControlNet** | Uses native/standard Qwen ControlNet loader/apply paths where installed. |

### Qwen ControlNet VAE contract

Qwen’s two adapter lanes do **not** share one generic VAE rule. Neo reads the actual apply-node schema discovered from the selected Comfy profile and applies an adapter-specific contract.

| Adapter | VAE behavior |
|---|---|
| **DiffSynth model patch** | Uses the `QwenImageDiffsynthControlnet` schema only. It does not inherit the generic `ControlNetApplyAdvanced` VAE rule. An optional `vae` input is filled when the active Qwen VAE is available, but does not block the route. It blocks only when the DiffSynth apply node itself declares `vae` as required. |
| **InstantX / native Qwen ControlNet** | When the selected InstantX apply node explicitly exposes a `vae` input—required or optional—Neo treats it as a VAE-aware Qwen node, resolves the matching active route-owned Qwen VAE, injects the graph reference, and blocks before queueing if no matching VAE can be found. |
| **Schema has no `vae` input** | Neo does not add one and does not create a synthetic VAE requirement. |
| **Schema unavailable** | Neo does not guess. Refresh the selected Comfy profile’s nodes so Neo can inspect the real node contract. |

A VAE-aware Qwen ControlNet node without the active Qwen VAE can produce errors such as:

```text
This Controlnet needs a VAE but none was provided
```

Use the VAE already owned by the active Qwen workflow. Do not substitute an unrelated SD or Flux VAE, and do not configure a filesystem path manually—the workflow passes a portable Comfy graph reference.

### Flux ControlNet adapter

| Option | Meaning |
|---|---|
| **Auto · match Flux route** | Neo chooses the safest adapter for the selected Flux route. |
| **Alimama Flux.1 Inpaint ControlNet** | Flux 1 inpaint/canvas path. |
| **FLUX.2 Klein Fun Union ControlNet** | Flux2/Klein Fun Union path for Klein inpaint/outpaint testing. |

## Map building

The **Build Map** button turns a normal image into a control map. It can use:

- detected Comfy preprocessor nodes from `comfyui_controlnet_aux` or compatible packs;
- local fallback support for some map types;
- direct input when preprocessor is `None / use image directly`.

Generated maps are tracked as Neo-owned/reference assets and can be recorded in output metadata. If a map builds but the workflow does not use ControlNet, check the extension apply toggle and route state.

## ControlNet model discovery and placement

Neo does not maintain a separate saved ControlNet catalog. The model dropdown is
built at runtime from the selected Comfy profile and these additive sources:

1. live `ControlNetLoader` choices from Comfy `/object_info`;
2. the registered Comfy `/models/controlnet` folder when available;
3. files under the configured `<Comfy models root>/controlnet` directory;
4. ControlNet folders declared by `extra_model_paths.yaml` under the canonical `controlnet` key or a supported alias. See [Comfy extra model paths](../07_ADMIN/comfy_extra_model_paths.md) for the complete shared-model template.

Nested directories are preserved in the dropdown because Comfy loaders use the
relative filename. Put normal ControlNet loader files in:

```text
<ComfyUI>/models/controlnet/
```

If Neo Studio and ComfyUI are separate folders, set **Admin → Models → ComfyUI
models root** to Comfy's `models` directory. Neo derives `controlnet` beneath
that root; no extension-specific manual path is required.

After adding a model, restart or refresh ComfyUI if its loader list is stale,
then use **Refresh Nodes** in Neo. A model appearing in the dropdown confirms
discovery, but it must still match the active family and loader route.

## Backend profile refresh behavior

The ControlNet dropdown is bound to the backend profile currently selected in
the Image header. Changing that profile immediately removes the previous
profile's transient catalog from the dropdown and starts a new **Refresh
Nodes** request for the newly selected profile.

Neo tags the response with the resolved profile id and ignores any older
request that completes after a newer profile was selected. A saved model value
is not deleted during refresh. If it is absent from the new profile, it remains
visible as **selected · not in current profile catalog** so the user can switch
back or choose a valid replacement. A same-profile refresh failure keeps the
last successful list and shows a retry error instead of silently replacing it
with another profile's models.

## Good starting values

| Goal | Suggested start |
|---|---|
| Gentle composition help | Strength `0.35–0.55`, start `0`, end `0.7–1` |
| Strong pose/layout lock | Strength `0.65–0.9`, start `0`, end `1` |
| Canny edge guidance | Canny low `100`, high `200`, strength around `0.45–0.7` |
| Depth layout | Detect res `512–1024`, strength around `0.45–0.75` |
| Pose guidance | OpenPose/DWPose, body enabled, hands/face only if needed |
| Tile/detail | lower strength first; raise only if detail is too loose |

## Common mistakes

- Using an SDXL ControlNet model on a Flux/Qwen route.
- Building a map but forgetting to check **Apply ControlNet** or **Use unit**.
- Using too much strength and flattening creativity.
- Using Canny for subtle pose when OpenPose/Depth would be better.
- Expecting ControlNet to preserve identity. Use IP Adapter/FaceID for identity and ControlNet for structure.
- Assuming visible fields mean execution. The status badge/route state decides execution.
- Pointing Admin Models at the `controlnet` child instead of the parent Comfy `models` directory.
- Placing ControlNet files under checkpoints or another unrelated model folder.

## Assistant behavior

When answering ControlNet questions:

1. Check the live Image route: backend, family, loader, workflow mode.
2. Check whether **Apply ControlNet** and at least one unit are enabled.
3. Check whether a control image or generated map is attached.
4. Explain route status before giving settings.
5. For Qwen/Flux routes, mention the adapter policy only when relevant.
6. Do not dump extension payloads unless the user asks for debug JSON.
