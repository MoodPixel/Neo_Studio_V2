---
guide_id: image.controlnet
title: ControlNet & Pose Reference Guide
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
  - krea2_control
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
  - krea 2
  - control lora
  - loader aware
priority: 118
version: 13
updated: 2026-08-20
---

# ControlNet & Pose

**ControlNet & Pose** is the Image → Reference extension for structural guidance and Qwen pose transfer. It helps Neo guide a generation using a control image or generated map: edges, depth, pose, lineart, softedge, scribble, normal maps, tile/detail, or route-specific inpaint/outpaint control.

Use this guide when the user asks about the ControlNet card, control images, generated maps, map building, canny/depth/pose settings, ControlNet model dropdowns, or why ControlNet is disabled/gated.

## What ControlNet does

ControlNet tells the image model to follow a structure. The prompt still decides what the image should be; ControlNet decides what structure the image should respect.

## Family-aware control support

Control maps and ControlNet execution are separate things in Neo. A map preprocessor can be installed even when the selected model family does not yet have a compatible execution route. For that reason, treat the active model family and route badge as the support authority rather than assuming every map type works everywhere.

The **Type** selector is family-aware. Neo shows only control intents that are implemented for the active model family, loader, workflow mode, and ControlNet task. Installed preprocessors or custom nodes do not add extra Type choices by themselves. If the active route has no implemented ControlNet intent, Neo shows the route as unavailable instead of falling back to a generic list.

Current Krea 2 guidance is now intent- and family-specific. **Krea 2 Raw** exposes **Depth** and **Composition / Silhouette**, and can also expose **Canny / edges** through the NK2E in-context adapter when its required nodes and a compatible NK2E Canny LoRA are installed. **Krea 2 Turbo** exposes Depth + Composition / Silhouette and can expose **OpenPose / DWPose** through the Ostris reference-image adapter. Canny stays hidden on Turbo because the verified public NK2E Canny checkpoint targets Krea 2 Raw. SDXL, Flux/Flux Klein, and Qwen expose only their own currently implemented route-specific types.


Good uses:

- preserve a pose or character silhouette;
- follow edges from a source image;
- keep room/layout perspective;
- guide depth and foreground/background separation;
- preserve lineart or sketches;
- use tile/detail guidance during refinement;
- help inpaint/outpaint follow an existing mask/canvas route where the selected family supports it.

## Pose Control vs Pose Transfer

The **Pose** control type has two different methods. They share Neo's existing Image 1 / Image 2 / Image 3 source-lane system, but they do not use the same backend mechanism.

| Method | What drives the pose | Source-lane use | When to choose it |
|---|---|---|---|
| **Pose Control · ControlNet** | DWPose/OpenPose map + a compatible ControlNet model | Normal ControlNet control image/map flow | Choose this for stronger structural locking and conventional ControlNet behavior. |
| **Pose Transfer · Qwen 2511 + LoRAs** | Qwen Image Edit 2511 + runtime DWPose + two model-only pose LoRAs | Image 1 = subject, Image 2 = pose reference, Image 3 = generated DWPose map | Choose this when the Qwen 2511 edit workflow should copy pose semantically without a ControlNet model. |

For **Pose Transfer**, Neo does not ask the user to upload a pose map as a normal source. At queue time it reads **Image 2**, runs DWPose inside the Comfy graph, feeds the generated pose image into Qwen's **Image 3** input, and applies the selected base/helper LoRAs through `LoraLoaderModelOnly`. The prompt instruction is appended only to positive Qwen conditioning.

Pose Transfer is currently experimental and fail-closed for local **ComfyUI / ComfyUI Portable**, **Qwen Image Edit 2511**, **Safetensors / Components or GGUF**, in **Img2Img / Edit**. It requires a live DWPose preprocessor, `LoraLoaderModelOnly`, and exact LoRA catalog matches. Image 3 must be empty because Neo owns that lane for the generated pose map while the method is active.

The first rollout deliberately does not stack Pose Transfer with normal ControlNet units in the same generation. Use one system or the other so the pose driver remains predictable.

## Preview / Output Inspector reference handoff

Selecting **ControlNet** on an output does not run generation. Neo:

1. keeps the currently selected Image profile;
2. materializes URL-only output previews into Neo-owned source storage;
3. refreshes the selected profile's live ControlNet/IP Adapter catalogs when stale;
4. stages the image into the first empty ControlNet unit;
5. creates a new unit only when the provider has capacity;
6. opens Image → Reference for model, preprocessor, strength, and timing review.

Neo never overwrites an occupied unit silently. Forge ControlNet and IP Adapter share the same discovered Integrated ControlNet slot pool, so capacity is counted across both extensions. The staged payload carries `neo.image.preview_reference_handoff.v1`; the API rejects a provider/profile/source mismatch rather than rerouting through Comfy.

## Main fields

| Field | What it does | Practical note |
|---|---|---|
| **Apply Control / Pose** | Enables the selected ControlNet or Pose Transfer unit for the current generation. | If unchecked, Neo stores the draft but does not apply either system. |
| **+ Add Unit** | Adds another ControlNet unit. | Multiple units can combine pose + depth + edges on supported standard routes. Krea 2 currently supports one active unit across Raw Depth/Composition/Canny and Turbo Depth/Composition/OpenPose, so Add Unit is disabled there. |
| **Clean Disabled** | Removes inactive/disabled units. | Use before saving presets or debugging. |
| **Refresh Nodes** | Refreshes the selected provider's ControlNet catalog. | Comfy reads live node/model sources; Forge reads the selected profile's verified Integrated ControlNet models, modules, and slot limits. |
| **Batch Build Maps** | Builds generated maps for multiple units when possible. | Useful after setting source images and preprocessors. |
| **Use unit** | Enables an individual ControlNet unit. | Disabled units remain in the draft but do not apply. |
| **Type** | Semantic structural-control intent. | The choices are filtered for the active family/loader/mode/task. A type is not shown merely because its preprocessor is installed. |
| **Preprocessor** | Chooses how to build a map from the control image. | `None / use image directly` means the attached image is already the control map. |
| **Model / Control LoRA** | Selects the route-specific control file used by this unit. | Standard routes use a ControlNet/model-patch file. Krea 2 Generate labels this field **Control LoRA** and reads it from `ComfyUI/models/loras`. |
| **Control image** | Source image used directly or used to build a generated map. | Drag/drop, browse, or send an output from Preview/Results. |
| **Generated map** | Preprocessed map produced by Neo/Comfy/local fallback. | Example: a canny edge map built from a normal photo. |
| **Build Map** | Builds a generated map for the selected unit. | Requires a control image and either a preprocessor node or local fallback support. |
| **Strength** | How strongly the model follows the control. | Lower values allow more freedom; higher values force structure more strongly. |
| **Start % / End %** | When supported standard ControlNet routes affect the diffusion process. | `0 → 1` applies through the whole run. Krea 2 **Depth** uses the native adapter and remains full-run only; Krea 2 **Composition / Silhouette** uses Control Plus and exposes Start % / End % for control-latent projection scheduling. Krea 2 Turbo **OpenPose** and Krea 2 Raw **Canny** use reference-conditioning adapters and therefore do not use this Start/End control. |
| **Fit mode** | How the control image/map fits the generation size. | `Contain` preserves the reference shape; `cover/stretch/native` can change layout behavior. |
| **Detect res** | Resolution used by preprocessors such as depth/pose/lineart. | Higher can capture more detail but costs more time. On Krea 2 Generate, Neo keeps Detect res beside Strength so the lower unit controls use the card width cleanly. |
| **Canny low / high** | Edge thresholds for Canny maps. | Low values produce more edges; high values produce cleaner/sparser edges. |

## Control types and preprocessors

The table below describes the control concepts Neo can use across supported families. **It is not a promise that every row appears for every model family.** The live Type selector is the authority for the active route.

| Type / preprocessor | Use for | Notes |
|---|---|---|
| **Canny / edges** | strong silhouette and edge composition | Good for pose/object boundaries; too strong can make outputs rigid. On Krea 2 Raw, Neo can route the generated Canny map through NK2E in-context reference conditioning. |
| **Depth** | perspective and scene depth | Good for rooms, full-body placement, foreground/background separation. |
| **Composition / Silhouette** | broad subject silhouette, framing, layout and pose tendency | Krea 2 uses a direct RGB reference with the Control Plus adapter; it is not a generic preprocessor available to every family. |
| **OpenPose / DWPose** | human body pose | Standard families use their own Pose Control adapters; Qwen 2511 can use Pose Transfer. Krea 2 Turbo uses the Ostris reference-image adapter when installed. Enable hands/face only when needed. |
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
| **Krea 2 Raw + Safetensors / Components** | **Generate structural control available** for Depth through the native Krea2 Control LoRA adapter, Composition / Silhouette through Krea2 Control Plus, and runtime-gated **Canny / NK2E** when its model-wrapper/reference nodes are installed. OpenPose remains hidden on Raw. One active unit; Control LoRAs come from `models/loras`. |
| **Krea 2 Turbo + Safetensors / Components** | Depth + Composition / Silhouette, plus **OpenPose / DWPose** through `ComfyUI-Krea2-Ostris-Edit` when `TextEncodeKrea2OstrisEdit`, `Krea2OstrisEditModelPatch`, and `LoraLoaderModelOnly` are present. One active Krea unit. |
| **Krea 2 Raw + GGUF** | Same Raw Generate capability as Components: Depth native control, Composition / Silhouette through Control Plus, and runtime-gated Canny through NK2E. OpenPose remains hidden. |
| **Krea 2 Turbo + GGUF** | Same Turbo Generate capability as Components, including runtime-gated Ostris OpenPose. GGUF changes the base model loader only; the control adapter remains intent-specific. |
| **Qwen Image Edit + Components or GGUF** | Available through Qwen-safe InstantX/standard map control and DiffSynth/InstantX inpaint/outpaint adapters. |
| **Qwen Rapid AIO + Safetensors / Bundled or GGUF** | Available through Qwen Rapid AIO-specific map and DiffSynth/InstantX adapter policy. |
| **Qwen Image Edit 2509 + Components or GGUF** | Available through Qwen 2509-specific map and DiffSynth/InstantX adapter policy. |
| **Qwen Image Edit 2511 + Components or GGUF** | Standard ControlNet remains route-dependent. **Pose Transfer** is experimental on local Comfy Img2Img/Edit and uses DWPose + two model-only pose LoRAs instead of a ControlNet model. |
| **ZImage / ZImage Turbo** | Implementation target. Neo may preserve settings, but active graph patching should not be promised unless the live route says Ready/Experimental. |
| **HiDream** | Implementation target/provider gated unless the live route matrix promotes the exact route. |
| **xAI Grok Imagine / API profiles** | Not a local Comfy ControlNet graph patch. Do not promise ControlNet execution unless a future API/backend exposes it. |

## Krea 2 Control LoRA

Krea 2 uses intent-specific backend contracts rather than one generic ControlNet graph. On supported **Krea 2 Raw / Turbo → Generate** routes, Neo keeps one ControlNet unit card but chooses the execution adapter from the selected **Type**.

Current Krea 2 intents:

| Type | Adapter | Reference preparation | Extra settings | Runtime requirement |
|---|---|---|---|---|
| **Depth** | Native Krea2 Control | Depth map, then grayscale + per-image min/max normalization before VAE encode | Strength, Detect res | `Krea2ControlLoRALoader` + `Krea2ControlImageEncode` + `Krea2ControlApply` |
| **Composition / Silhouette** | Krea2 Control Plus | Direct RGB control/reference image, no normalization, matched to target latent size | Strength, Start %, End % | `Krea2ControlPlusLoRALoader` + `Krea2ControlPlusImageEncode` + `Krea2ControlPlusApply` |
| **OpenPose / DWPose** *(Turbo only)* | Krea2 Ostris Edit | DWPose/OpenPose map passed as `image1` reference to Ostris text conditioning and VAE reference latents | Strength, Detect res, KV cache | `TextEncodeKrea2OstrisEdit` + `Krea2OstrisEditModelPatch` + `LoraLoaderModelOnly` |
| **Canny / edges** *(Raw only)* | NK2E in-context | Canny edge map -> `VAEEncode` -> `NK2ESetReferenceNode` on positive conditioning; Canny LoRA is applied before `NK2EInContextModelNode` | Strength, Detect res, Canny low, Canny high | `NK2EInContextModelNode` + `NK2ESetReferenceNode` + `LoraLoaderModelOnly` + `VAEEncode` |

Both **Safetensors / Components** and **GGUF** Krea 2 base-model loaders are supported for these Generate routes on local ComfyUI / ComfyUI Portable. Krea remains **single-unit**: do not stack Depth, Composition, OpenPose, or Canny adapters in the same generation yet. OpenPose is available only on **Krea 2 Turbo**; Canny/NK2E is available only on **Krea 2 Raw** in this phase because the verified public Canny LoRA declares Krea-2-Raw as its base model.

Put compatible Krea 2 Control LoRAs under:

```text
<ComfyUI>/models/loras/
```

Then use **Refresh Nodes**. Neo reads the exact live `lora_name` enum from the loader belonging to the selected intent, filters that shared LoRA catalog, and later rebinds the portable `/` path to the exact provider spelling/path separator used by the active Comfy node.

### Depth

Depth remains the physically validated native Krea route. The Control LoRA selector shows only names that clearly identify themselves as a depth control/controlnet LoRA. A recognizable filename such as `depth-control-lora.safetensors` is accepted; a generic `depth.safetensors` is intentionally not offered because Neo cannot safely distinguish it from unrelated LoRAs by filename alone.

Neo prepares the Depth control image using grayscale plus per-image min/max normalization. Map inversion remains owned by Neo's map-building stage rather than being applied again in the Krea encoder. Depth does not expose Start % / End % because the native `Krea2Control*` loader does not provide that schedule contract.

### Composition / Silhouette

Composition / Silhouette is a separate **Krea2 Control Plus** route. It is runtime-gated: if the three Control Plus nodes are not present in Comfy `/object_info`, the Type stays hidden even though Neo knows how to run it.

This intent uses **None / use image directly** as its default preprocessor. Attach a composition/silhouette reference image and Neo sends that RGB reference into `Krea2ControlPlusImageEncode` with `match_latent_size`, `channel_mode=rgb`, `normalize=none`, and `invert=false`. The route then loads the compatible Control Plus LoRA, applies its control latent, and rewires only the sampler MODEL path.

The intended public checkpoint for this first Composition route is `krea2-anythng_step_007000.safetensors`. Neo's strict selector therefore looks for Krea 2 composition/silhouette-style names such as **anythng**, **silhouette**, or **composition**, while excluding depth, pose, canny, lineart, normal, and NK2E checkpoints. Normal character/style LoRAs remain in the regular LoRA Stack and do not appear here.

### OpenPose / DWPose — Krea 2 Turbo only

Krea 2 Turbo OpenPose uses the **Ostris Edit** reference-conditioning path rather than the native `Krea2Control*` or Control Plus graph. Neo keeps the normal ControlNet user flow: attach a source image, choose OpenPose/DWPose, build the pose map, and then Generate. The durable generated pose map becomes the reference image consumed by the Ostris adapter. A prebuilt pose map can also be used directly by choosing the `none` preprocessor.

Runtime requirements are `TextEncodeKrea2OstrisEdit`, `Krea2OstrisEditModelPatch`, and the core `LoraLoaderModelOnly`. Neo reuses the active Krea Qwen3-VL CLIP and Krea/Qwen VAE, replaces both positive and negative conditioning with `TextEncodeKrea2OstrisEdit` using the pose map as `image1`, patches the Krea model for reference latents, then applies the selected OpenPose LoRA through `LoraLoaderModelOnly`.

The Control LoRA selector is strict and reads the live `LoraLoaderModelOnly.lora_name` catalog. For this first route Neo accepts names that identify themselves as Krea 2 + OpenPose + control/controlnet and excludes Depth, Canny, Lineart, Normal, Composition/Silhouette, and NK2E files. The verified route is **Turbo-only**; Raw OpenPose remains hidden rather than guessing compatibility.

**KV cache** is an Ostris-specific setting. Leave it enabled for a pose LoRA trained for cached reference attention. Disable it for a normal Ostris edit LoRA that was not trained with the matching `kv_cache` behavior. For the first physical validation, start around **0.85 strength** and use an obvious full-body pose so the structural effect is easy to compare.

### Canny / NK2E — Krea 2 Raw only

Krea 2 Raw Canny uses **ComfyUI-NK2E** rather than the native Krea Control or Control Plus graph. Neo keeps the normal ControlNet UX: attach a source image, choose Canny, set low/high thresholds, build the edge map, and Generate. The durable generated map is loaded in Comfy, encoded through the active Krea/Qwen VAE with `VAEEncode`, and passed to `NK2ESetReferenceNode` as the in-context reference on positive conditioning.

The model path is `base Krea MODEL -> LoraLoaderModelOnly -> NK2EInContextModelNode -> KSampler`. Neo preserves the base negative conditioning. It deliberately does **not** compile the deprecated `NK2EInContextEditNode` shortcut. The current preferred NK2E model-wrapper + set-reference pair also avoids baking the reference directly into a legacy single-node model clone.

The Control LoRA selector reads the live `LoraLoaderModelOnly.lora_name` catalog and strictly filters it to names containing both **NK2E** and **Canny**, while excluding Depth, Pose/OpenPose, Lineart, Normal, Composition/Silhouette, and generic NK2E edit checkpoints. The first verified model is `NK2E-canny-v0.1.safetensors`. Start around **0.70 strength**, **512 detect resolution**, and **Canny low/high 100/200**, then tune the thresholds based on how dense the edge map is.

Canny is intentionally **Raw-only** in this phase. Krea 2 Turbo does not expose Canny even if NK2E is installed, because Neo does not infer Turbo compatibility from the node pack alone.

Start % / End % are visible only for the Control Plus intent. A good baseline is `Start 0.0 / End 1.0`; narrowing the range limits when the control-latent projection is active during denoising. Strength, Start %, and End % are serialized and validated as route-specific settings.

When **Build Map** succeeds on a local Comfy profile, Neo keeps a durable copy of the generated map and handles the Comfy `LoadImage` handoff automatically, including maps stored in Comfy input subfolders such as `neo_studio_controlnet`. Composition normally does not need a generated map because the reference is already used directly.

**Identity Edit + Krea 2 Control is still not enabled.** Use Krea 2 Control from Generate; Neo fails closed instead of silently stacking Krea edit/model wrappers with these control adapters.

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

Generated maps are tracked as Neo-owned/reference assets and can be recorded in output metadata. Neo also keeps the generated map available for Comfy handoff, so a map that previews correctly should not require manual file placement. If a map builds but the workflow does not use ControlNet, check the extension apply toggle and route state. If generation reports that a generated map is missing, rebuild that map once after confirming the selected Comfy profile is connected; do not move or rename files inside `neo_data/controlnet_maps` manually.

## ControlNet model discovery and placement

Neo does not maintain a separate saved ControlNet catalog. The model dropdown is
built at runtime from the selected Comfy profile and these additive sources:

1. live `ControlNetLoader` choices from Comfy `/object_info`;
2. the registered Comfy `/models/controlnet` folder when available;
3. files under the configured `<Comfy models root>/controlnet` directory;
4. ControlNet folders declared by `extra_model_paths.yaml`.

Nested directories are preserved in the dropdown because Comfy loaders use the
relative filename. Put normal ControlNet loader files in:

```text
<ComfyUI>/models/controlnet/
```

Krea 2 is the exception: its Generate adapters discover **Control LoRAs** from the live native, Control Plus, or Ostris `LoraLoaderModelOnly` `lora_name` choices, so those files belong under:

```text
<ComfyUI>/models/loras/
```

Neo may display nested LoRA paths with `/` separators so saved selections remain portable between systems. For Krea 2, Neo first filters the live shared LoRA catalog to the selected control intent and adapter and then, at generation time, matches that portable selection back to the exact filename published by the active Comfy loader (including Windows `\` separators). You should select the LoRA from Neo's refreshed dropdown rather than manually typing or rewriting the path. If the file was moved or renamed, use **Refresh Nodes** and select it again.

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
request that completes after a newer profile was selected. Standard dedicated
ControlNet catalogs preserve an unresolved saved model while catalog verification
is pending. **Strict shared-LoRA routes such as Krea 2 are different:** once the
live catalog has been checked, a saved Control LoRA that is missing or incompatible
with the selected control intent is cleared and must be chosen again from the
filtered list. A same-profile refresh failure keeps the last successful verified
list instead of borrowing another profile's models.

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
- Building a map but forgetting to check **Apply Control / Pose** or **Use unit**.
- Using too much strength and flattening creativity.
- Using Canny for subtle pose when OpenPose/Depth would be better.
- Expecting ControlNet to preserve identity. Use IP Adapter/FaceID for identity and ControlNet for structure.
- Treating DWPose as if it were itself a ControlNet model. DWPose is a pose extractor/preprocessor; Pose Control and Pose Transfer decide how that pose information is used.
- Uploading a user image into Image 3 while Pose Transfer is active. Image 3 is reserved for Neo's generated DWPose map in that mode.
- Selecting normal LoRA loading for the Qwen 2511 pose pair. Pose Transfer requires model-only loading for both pose LoRAs.
- Assuming visible fields mean execution. The status badge/route state decides execution.
- Pointing Admin Models at the `controlnet` child instead of the parent Comfy `models` directory.
- Placing standard ControlNet files under checkpoints or another unrelated model folder.
- Putting a Krea 2 Control LoRA under `models/controlnet` instead of `models/loras`.
- Expecting Krea Composition / Silhouette to appear before the `Krea2ControlPlus*` node trio is installed and refreshed.
- Expecting Krea Turbo OpenPose to appear before `TextEncodeKrea2OstrisEdit`, `Krea2OstrisEditModelPatch`, and `LoraLoaderModelOnly` are visible in the active Comfy `/object_info`, or expecting the Turbo pose route to appear on Krea 2 Raw.
- Expecting Krea Raw Canny to appear before `NK2EInContextModelNode`, `NK2ESetReferenceNode`, `LoraLoaderModelOnly`, and `VAEEncode` are visible, or expecting the verified Raw NK2E Canny route to appear on Krea 2 Turbo.
- Trying to stack more than one Krea 2 Control unit or combine Krea 2 Identity Edit + Control before that compatibility route is enabled.

## Assistant behavior

When answering ControlNet questions:

1. Check the live Image route: backend, family, loader, workflow mode.
2. Check whether **Apply ControlNet** and at least one unit are enabled.
3. Check whether a control image or generated map is attached.
4. Explain route status before giving settings.
5. For Qwen/Flux routes, mention the adapter policy only when relevant.
6. Do not dump extension payloads unless the user asks for debug JSON.
