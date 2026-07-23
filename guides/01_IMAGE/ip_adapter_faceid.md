---
guide_id: image.ip_adapter_faceid
title: IP Adapter and FaceID Reference Guide
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - reference
  - ip_adapter
  - faceid
  - face id
  - identity
  - reference images
  - clip vision
  - insightface
  - identity profiles
  - scene director routing
tags:
  - image
  - reference
  - ip adapter
  - faceid
  - face identity
  - identity reference
  - clip vision
  - insightface
  - reference image
  - route aware
  - loader aware
priority: 118
version: 7
updated: 2026-07-18
---

# IP Adapter / FaceID

**IP Adapter / FaceID** is the Image → Reference extension for image-reference guidance. It can use one or more reference images to influence identity, face likeness, character consistency, style, and composition.

Use this guide when the user asks about the IP Adapter / FaceID card, same-face reference, identity preservation, reference images, CLIP Vision, InsightFace, FaceID models, identity profiles, or why the IP Adapter card is disabled.

## What IP Adapter / FaceID does

| Mode | Use it for | Notes |
|---|---|---|
| **Standard IP Adapter** | style reference, character reference, object/product reference, composition guidance | Uses CLIP Vision + IP Adapter model. Good for visual influence, not strict face identity. |
| **FaceID / FaceID Plus** | face likeness and identity preservation | Uses InsightFace/FaceID-compatible nodes and FaceID model files. Best when the reference image has a clear face. |

IP Adapter does not replace prompting. The prompt still describes the desired image. IP Adapter supplies visual reference pressure.

## Node readiness

The card can show a node-readiness panel and a **Check nodes / refresh dropdowns** button. This checks Comfy object info and model dropdowns.

| Readiness area | Required nodes |
|---|---|
| **Standard IP Adapter** | `CLIPVisionLoader`, `IPAdapterModelLoader`, `IPAdapterAdvanced` |
| **FaceID** | `CLIPVisionLoader`, `IPAdapterUnifiedLoaderFaceID`, `IPAdapterFaceID` |
| **Optional helpers** | `ImageBatch`, `IPAdapterInsightFaceLoader` |

If Standard nodes are ready but FaceID nodes are missing, standard reference can work while FaceID is unavailable. If FaceID nodes are ready but Standard nodes are missing, FaceID can be usable while Standard is unavailable. The UI may show **partial** readiness in those cases.

Model dropdowns are populated from the selected Comfy profile's live loader choices, registered model folders, configured primary models root, and supported `extra_model_paths.yaml` entries. Neo does not require a hardcoded user path or a separately maintained catalog. Register shared folders with `ipadapter` and `clip_vision`; see [Comfy extra model paths](../07_ADMIN/comfy_extra_model_paths.md) for the complete template.

## Model discovery and placement

| Dropdown | Standard Comfy folder | Notes |
|---|---|---|
| **IP Adapter model** | `<ComfyUI>/models/ipadapter` | Standard adapter files are listed here. Nested relative paths are supported. |
| **FaceID model** | `<ComfyUI>/models/ipadapter` | FaceID adapter files stay in the IP Adapter folder and should contain `faceid`, `face_id`, or `face-id` in the filename/path so Neo can separate them from standard adapters. |
| **CLIP Vision** | `<ComfyUI>/models/clip_vision` | Contains the CLIP Vision encoder files used by the installed IP Adapter nodes. |

Do not confuse `ip-adapter-plus-face_*` with `ip-adapter-faceid-*`:

- `ip-adapter-plus-face_sdxl_vit-h.safetensors` is a **Standard IP Adapter**
  portrait/face model;
- `ip-adapter-faceid-plusv2_sdxl.bin` is a **FaceID** model used with
  InsightFace and the matching FaceID preset/LoRA contract.

The runtime list merges:

1. model filenames exposed by Comfy `/object_info`;
2. registered Comfy `ipadapter` and `clip_vision` folder endpoints;
3. direct scans beneath **Admin → Models → ComfyUI models root**;
4. matching `extra_model_paths.yaml` categories.

Neo expects the Admin Models value to point at the parent Comfy `models`
directory, not directly at `ipadapter` or `clip_vision`. Absolute paths remain
server-side and are not stored in the public extension package.

Some Comfy versions expose every adapter file through the generic
`IPAdapterModelLoader` choice list while the Unified FaceID node exposes only
preset labels. Neo therefore splits both the live node response and the
selected backend profile's generic `ip_adapter_models` bucket by real filename.
Names containing `faceid`, `face_id`, `face-id`, or `insightface` go to the
FaceID dropdown; `plus-face` remains Standard.

After copying models, restart or refresh ComfyUI if needed, then use **Check
nodes / refresh dropdowns**. FaceID preset labels exposed by unified loader nodes
are not treated as model files.

## FaceID execution contract

The installed `IPAdapterUnifiedLoaderFaceID` node owns model resolution by
**FaceID preset + active checkpoint family**. It does not expose the selected
FaceID filename as a direct graph input. Neo therefore treats the **FaceID
model** dropdown as an explicit resolution assertion:

1. the selected filename must identify a FaceID variant and `sd15` or `sdxl`;
2. its family must match the active checkpoint route;
3. its variant must match the selected FaceID preset;
4. only then may Neo compile the unified loader node.

For example, `ip-adapter-faceid-plusv2_sdxl.bin` matches an SDXL checkpoint
with `FACEID PLUS V2`. Selecting an SD1.5 file on an SDXL route, selecting
`FACEID PORTRAIT` for a Plus V2 file, or using an ambiguous renamed file blocks
the unit before graph mutation. This prevents the UI from claiming that one
file is active while Comfy's unified loader resolves another.

This is filename-contract validation, not a hardcoded model catalog or personal
path rule. Models are still discovered from the effective Comfy model folders.
Unified-loader installations should retain the canonical IPAdapter Plus FaceID
filenames and matching LoRAs. The workflow patch records only the selected
basename, family, preset, loader strategy, and validation result; absolute
filesystem paths remain server-side.

## Backend profile refresh behavior

Standard IP Adapter, FaceID, and CLIP Vision lists are bound to the backend
profile currently selected in the Image header. When the profile changes, Neo
invalidates the previous profile's transient node/catalog response and refreshes
all three lists for the newly selected profile.

Each request captures the selected profile and uses an epoch guard. A response
from an older profile cannot overwrite a newer selection, even if the older
Comfy request finishes last. Saved unit model values are preserved; a value not
found in the current profile remains visible as **selected · not in current
profile catalog**. If a same-profile refresh fails, Neo keeps the last
successful model inputs and displays the refresh error for retry.

## Frontend state reliability

The unit draft and the executable payload have separate responsibilities:

- switching between **Standard** and **FaceID** preserves each mode's model and
  FaceID settings in the local draft, so switching back restores the previous
  choices;
- payload construction still emits only the active mode's fields, preventing a
  hidden Standard model or FaceID setting from affecting execution;
- unit control events resolve the owning unit by stable UID, not only its visual
  list position, so a removed or reordered row cannot receive a stale update;
- changing the FaceID model, preset, or provider immediately rerenders the
  execution-contract state and restores focus to the changed control;
- after the active backend profile has returned a checked catalog, a saved
  FaceID selection missing from that profile is retained for review but blocked
  from execution instead of being cleared or silently replaced;
- incompatible enabled FaceID units show a shared-loader blocker when their
  model, preset, family, provider, or LoRA strength differs.

Profile refresh responses remain protected by the selected-profile request
epoch introduced in the earlier catalog-reliability phase. A late response from
an old profile cannot change the current profile's dropdown state.

## Main toolbar fields

| Field | What it does |
|---|---|
| **Apply IP Adapter** | Enables IP Adapter / FaceID for this generation. |
| **+ Add Unit** | Adds another reference unit. Sequential units can combine multiple references. |
| **Prep Identity** | Quickly prepares an identity-oriented FaceID-style unit from the Identity Presets area. |
| **Active count** | Shows how many enabled units have usable settings/reference images. |

## Identity presets

Identity presets sit above the unit list and help prepare the route for common identity goals.

| Field | Meaning |
|---|---|
| **Identity goal** | What the user wants: Off, Same face, Same character, or Style reference. |
| **Route** | Auto, Standard IP Adapter, or FaceID. Auto lets Neo choose based on goal/readiness. |
| **Reference strength** | Global identity/reference pressure. Higher means stronger likeness/style pressure. |
| **FaceID LoRA strength** | Strength for FaceID LoRA-assisted identity paths. |
| **Start at / End at** | Diffusion timing window for identity/reference influence. |
| **Identity notes** | Human-readable notes for the intended identity/character/style goal. |

## Identity Profile Library

The **Identity Profile Library** stores reusable identity/reference profiles. It is owned by IP Adapter but can be assigned from Scene Director regions.

A profile can contain:

- profile name/id;
- mode: FaceID, IPAdapter, or trigger-only;
- reference image list;
- trigger words;
- CLIP Vision selection;
- weight and timing;
- FaceID LoRA strength;
- optional LoRA link/name;
- notes.

Use it when the user wants to reuse the same character/person/product across multiple generations.

## Unit fields

Each IP Adapter unit can be Standard or FaceID.

| Field | Meaning | Practical note |
|---|---|---|
| **Use unit** | Enables that individual unit. | Disabled units stay in the draft but do not run. |
| **Mode** | Standard IP Adapter or FaceID / FaceID Plus. | Use FaceID for faces; Standard for style/composition/general references. |
| **IP Adapter model** | Model file for Standard IP Adapter. | Must match the selected family/base model type. |
| **FaceID model** | FaceID model file asserted for FaceID mode. | With the unified loader, Neo verifies this filename against the preset and checkpoint family; it is not passed as a direct node socket. |
| **CLIP Vision** | CLIP Vision model used to encode reference images. | Required for standard/reference image conditioning. |
| **Reference image(s)** | Images used for identity/style/composition guidance. | Use clear, relevant images. FaceID needs a clear visible face. |
| **Add Image** | Uploads/adds a reference image. | Multiple images can be used in a unit. |
| **Paste ref** | Adds a reference path/URL/asset reference. | Useful for existing Neo/Comfy assets. |
| **Clear** | Clears the unit images. | Does not delete original source files. |
| **Weight** | Main reference influence strength. | Too high can override prompt/style; too low may do nothing. |
| **Start at / End at** | Diffusion timing window. | Earlier/longer influence usually gives stronger identity/composition lock. |
| **Weight type** | Weight curve. | Linear is the safest default. Style/composition curves can change influence behavior. |
| **Combine embeds** | How multiple image embeddings combine. | `Add`/`average` can blend references; `concat` is a common default. |
| **Embeds scaling** | How K/V embeddings are applied. | `V only` is a safer default; K+V can be stronger and more invasive. |
| **FaceID preset** | FaceID variant/preset. | `FACEID PLUS V2` is the default; some presets are SD1.5-only or SDXL-only. |
| **InsightFace provider** | Runtime provider for face embedding. | CUDA is fastest when available; CPU is slower but useful for troubleshooting. |
| **FaceID v2 weight** | FaceID v2-specific strength. | Usually keep near the main weight unless troubleshooting likeness. |
| **FaceID LoRA strength** | FaceID LoRA helper strength. | Higher can improve likeness but can also over-constrain face/style. |

## Reference identity and deduplication

Neo separates durable reference identity from temporary Comfy input names:

- uploaded/pasted references are stored as durable asset records in the unit;
- immediately before compile, the provider uploads each unique durable asset to the active Comfy input folder;
- the returned Comfy names exist only in a runtime lane and are not added back as new saved references;
- replay restores the durable assets, not old run-specific Comfy handoff aliases.

This prevents one reference from becoming two `LoadImage` nodes when a previous Comfy handoff name and its original Neo asset both appear in restored state. Deduplication uses asset IDs or stored asset identity when available, with normalized exact references as the compatibility fallback. It does not compare image basenames or use personal path rules.

Intentional multi-reference batches are preserved. Two different durable assets remain two references and are combined through `ImageBatch`; only repeated representations of the same reference are collapsed.

Workflow metadata records path-free per-unit counts under `reference_deduplication`: submitted references, durable assets, runtime references, removed duplicates, and the identity source policy.

## Family / loader support summary

Always check the live route badge. Guide-level support:

| Family / loader | IP Adapter / FaceID status |
|---|---|
| **SDXL + Safetensors / Checkpoint** | Available for Generate, Img2Img, and Inpaint when required nodes/models are installed. Outpaint is planned/gated. |
| **SD 1.5 + Safetensors / Checkpoint** | Experimental for Generate, Img2Img, and Inpaint. Outpaint is planned/gated. |
| **Flux / Flux2 Klein / Qwen / ZImage / HiDream component/GGUF/bundled routes** | Not active in the current IP Adapter graph contract unless the live route promotes support. Treat as unsupported/planned/provider-gated. |
| **xAI Grok Imagine / API profiles** | Not a local Comfy IP Adapter graph patch. Do not promise IP Adapter or FaceID execution. |

## Recommended starting settings

| Goal | Suggested start |
|---|---|
| Same face | FaceID mode, weight `0.7–0.9`, FaceID LoRA strength `0.65–0.85`, start `0`, end `1` |
| Same character but not exact face | Standard or FaceID depending on route, weight `0.45–0.75` |
| Style reference only | Standard IP Adapter, weight `0.35–0.6`, use a style-focused reference image |
| Composition reference | Standard IP Adapter, composition/linear weight type, pair with ControlNet for stronger layout |
| Weak influence | lower weight to `0.25–0.45` or shorten End at |

## Common mistakes

- Expecting FaceID to fix pose/body layout. Use ControlNet or Scene Director for pose/layout.
- Using blurry or tiny face references for FaceID.
- Forgetting to run **Check nodes / refresh dropdowns** after installing IP Adapter nodes/models.
- Using a model file that does not match SDXL/SD1.5 route expectations.
- Renaming a unified-loader FaceID file so its family or variant can no longer be verified.
- Selecting a FaceID preset that does not match the selected FaceID model variant.
- Setting weight too high and overpowering the prompt.
- Restoring an old result that contains a temporary Comfy input name as though it were a second reference. Current builds automatically collapse that alias back to its durable asset.
- Assuming a saved model marked **selected · not in current profile catalog** will execute; refresh the active profile or select one of its available models.
- Expecting IP Adapter to run on Qwen/Flux/API routes when the live route is unsupported.
- Putting FaceID adapter files in a separate manual folder that Comfy has not registered.
- Pointing Admin Models directly at `ipadapter` instead of the parent Comfy `models` directory.

## Scene Director relationship

Scene Director can assign regional identity/reference intent, but IP Adapter owns the actual IP Adapter / FaceID units and identity profiles. When Scene Director routes a character region to identity guidance, the Assistant should explain:

- Scene Director owns region/subject placement and regional intent;
- IP Adapter owns reference images, identity profiles, CLIP Vision, FaceID model settings, and unit execution;
- ControlNet/OpenPose may still be needed for exact limb placement or contact pose.

## Assistant behavior

When answering IP Adapter / FaceID questions:

1. Check active route: backend, family, loader, workflow mode.
2. Check node readiness: Standard, FaceID, partial, provider gated, or missing nodes.
3. Check whether **Apply IP Adapter** and at least one unit are enabled.
4. Check whether reference images exist.
5. Give direct settings recommendations for the user's goal.
6. Do not promise identity execution on unsupported routes.
7. Do not dump payload JSON unless the user asks for debugging.
