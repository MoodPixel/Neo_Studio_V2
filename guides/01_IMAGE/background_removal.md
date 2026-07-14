---
guide_id: image.background_removal
title: AI Background Removal
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - background_removal
  - birefnet
  - transparent_png
  - alpha_mask
  - mask_review
  - edge_refinement
  - smart_routing
  - rembg
  - fallback_engines
  - commercial_providers
  - remove_bg
  - clipdrop
tags:
  - image
  - finish
  - background removal
  - birefnet
  - transparent png
  - alpha mask
  - mask review
  - edge refinement
  - smart routing
  - rembg
  - fallback engines
  - commercial providers
  - privacy
  - credits
priority: 112
version: 8
updated: 2026-07-14
---

# AI Background Removal

**Remove Background** is a built-in utility inside **Image → Finish**. It uses smart routing between the established Comfy BiRefNet path and an optional Neo-native rembg/ONNX fallback, saves a transparent PNG and optional alpha-mask PNG, and can refine a reviewed mask without rerunning segmentation.

It reuses the current Image workspace, selected result, uploaded-source handling, Comfy backend profile, polling, Neo-owned output storage, Results gallery, replay metadata, and Output Inspector. No separate tab or background-removal workspace is created.

## Optional commercial providers

P6.5 adds two optional paid cloud routes inside the same Finish utility:

| Provider | Admin profile | Credential environment variable | Notes |
|---|---|---|---|
| remove.bg | `image.remove_bg_background_removal` | `REMOVE_BG_API_KEY` | Output size, subject type, and semitransparency controls |
| Clipdrop | `image.clipdrop_background_removal` | `CLIPDROP_API_KEY` | Existing-transparency handling |

Set up the desired profile under **Admin → Backends → Image**, then choose **Commercial API · opt-in paid provider** inside Remove Background. These are utility profiles, not Image generation backends, so they do not appear in the main Image backend selector and cannot become the Image default.

Every run requires a fresh confirmation that the selected source will be uploaded to an external provider and may consume account credits. Consent resets after a successful run, after changing the source/provider, and during replay. Neo never silently falls back from a local engine to a commercial API.

The API key stays on the Neo server. The browser sends the selected utility profile ID and the consent flag, while Neo resolves the manual local secret or provider environment variable. Provider output is normalized into a Neo-owned transparent PNG; an optional alpha-mask PNG is derived locally. Results use the same gallery, Output Inspector, lineage, verification, and replay system as local routes.

### Provider controls and limits

**remove.bg** exposes output size, subject type, and semitransparency. Neo blocks sources above 50 megapixels. For a large source, Neo may request transparent WebP from the provider and normalize it back to PNG because provider PNG limits are lower.

**Clipdrop** exposes its transparency-handling mode. Neo blocks sources above 25 megapixels or 30 MB before upload.

Provider plans, prices, rate limits, retention terms, and image constraints can change. Neo records credit/rate headers when the provider returns them, but does not predict price or claim that a request is free.


## Smart routing and execution engines

P6.3 keeps the same **Image → Finish → Remove Background** workspace and adds three execution choices:

| Engine | Behaviour |
|---|---|
| **Smart — recommended** | Chooses an available engine from the selected preset and records the exact resolved engine/model. |
| **Comfy BiRefNet** | Strictly uses the connected Comfy profile, live BiRefNet node catalog, and installed `ComfyUI/models/BiRefNet/` checkpoint. |
| **Neo native rembg** | Strictly runs the optional local rembg/ONNX runtime without requiring Comfy. |

Smart routing never invents a missing model. General, fine-edge, portrait, product, and low-VRAM presets prefer Comfy BiRefNet when its exact required nodes and a compatible installed model are ready. The Anime preset prefers native `isnet-anime` because it is purpose-built for illustrated characters.

### Fallback policy

| Policy | Behaviour |
|---|---|
| **Never fallback** | The chosen/primary route must be ready. |
| **Fallback when unavailable** | Smart mode may use the other installed engine when the primary route cannot start. |
| **Also fallback after queue failure** | Smart mode may retry with native rembg when the Comfy graph fails before queue completion. |

A fallback records `requested_engine`, `resolved_engine`, `resolved_model`, `fallback_used`, and `fallback_reason` in the output metadata and replay payload. It is never silent.

## Optional native rembg runtime

Native fallback is deliberately excluded from Neo's core requirements. The current rembg release requires Python 3.11–3.13; if Neo runs on an older Python environment, keep using Comfy BiRefNet or create a separate compatible runtime. Install one appropriate option from the repository root:

```bash
# CPU
pip install -r requirements-background-removal-cpu.txt

# NVIDIA / CUDA
pip install -r requirements-background-removal-gpu.txt
```

For AMD/ROCm, install a compatible `onnxruntime-rocm` build and the rembg library according to the machine's ROCm stack. Do not install multiple conflicting ONNX Runtime packages into the same environment.

Supported P6.3 native model IDs:

```text
isnet-general-use
isnet-anime
u2net_human_seg
u2netp
birefnet-general
birefnet-general-lite
birefnet-portrait
```

rembg downloads the selected ONNX model on first use into `U2NET_HOME` or its default `~/.u2net/` cache. Neo reports this before execution. The rembg wrapper and individual model weights can have different licences; review the licence of the selected model before commercial use.

## Requirements

### Initial BiRefNet extraction

- Connected ComfyUI or ComfyUI Portable Image backend.
- `ComfyUI_BiRefNet_ll` installed and loaded.
- `LoadImage`
- `LoadRembgByBiRefNetModel`
- `RembgByBiRefNetAdvanced`
- `MaskToImage`
- `SaveImage`
- At least one BiRefNet model under `ComfyUI/models/BiRefNet/`.

### Mask Review refinement

- A prior Background Removal result with **Save alpha mask** enabled.
- The original source image must remain staged or be recoverable from the saved result.
- `LoadImage`
- `ImageToMask`
- `MaskToImage`
- `SaveImage`
- `GrowMask` only when edge expand/contract is non-zero.
- `FeatherMask` only when feathering is non-zero.
- `BlurFusionForegroundEstimation` when foreground estimation is enabled, otherwise `JoinImageWithAlpha`.

Mask Review has its own readiness gate. Missing review/refinement nodes must not disable the normal BiRefNet extraction route.

## Presets

| Preset | Smart route preference | Best use |
|---|---|---|
| **Smart Auto** | Comfy General-dynamic/General/General-HR → native ISNet General | General subjects, characters, and mixed content. |
| **Fine Edges / Hair** | Comfy Matting-HR/Matting/General-HR → native BiRefNet General | Hair, fur, fabric edges, glow, and soft transparency. |
| **Portrait** | Comfy Portrait → native BiRefNet Portrait/U²-Net Human | People and portrait cutouts. |
| **Product / Object** | Comfy General-dynamic/General-HR → native ISNet General | Products, props, logos, and isolated objects. |
| **Anime / Illustration** | Native ISNet Anime → Comfy General-dynamic | Anime characters and illustrated subjects. |
| **Low VRAM** | Comfy General-Lite → native U²-Net P | Reduced-memory systems and faster previews. |

The engine resolver checks actual runtime readiness and exact catalog entries. Selecting a preset does not claim that its preferred model is installed.

## Main controls

| Control | Meaning |
|---|---|
| **Source image** | Upload/drop an image or use the currently selected Image result. |
| **Execution engine** | Smart, strict Comfy BiRefNet, or strict Neo native rembg. |
| **Fallback policy** | Controls whether Smart may switch engines when unavailable or after a Comfy queue failure. |
| **Native model/provider** | Selects the rembg model and Auto/CPU/CUDA ONNX provider when native execution is used. |
| **Preset / BiRefNet model** | Selects the extraction workload and exact installed checkpoint. |
| **Processing width / height** | BiRefNet preprocessing resolution; output keeps the source dimensions. |
| **Mask threshold** | Keep at `0` for soft hair, fur, glow, fabric, shadows, and partial transparency. |
| **Edge expand / contract** | Positive values expand the foreground mask; negative values contract it. |
| **Edge feather** | Softens the final mask boundary after expansion/contraction. |
| **Foreground colour estimation** | Re-estimates edge colours before producing RGBA. Disable it to join the source RGB with the refined alpha directly. |
| **Foreground blur / secondary blur** | Parameters for foreground colour estimation. |
| **Review background** | Checkerboard, white, or black inspection background. It affects preview only. |
| **Save alpha mask** | Saves the soft grayscale mask required for Mask Review. |

## Mask Review

After a Background Removal job finishes with an alpha mask:

1. Keep the source staged or select the saved Background Removal result.
2. Click **Review mask**.
3. Inspect the foreground over checkerboard, white, and black.
4. Paint with **Keep** to restore foreground pixels.
5. Paint with **Remove** to cut foreground pixels away. Holding **Alt** temporarily removes while Keep is active.
6. Adjust brush size as needed.
7. Click **Stage Mask** to keep the reviewed mask in the Finish draft, or **Apply Refinement** to queue immediately.

The editor preserves grayscale mask values outside painted regions. Keep paints white; Remove paints black. The original AI mask can be restored with **Reset AI Mask**.

## Non-destructive refinement route

Applying a reviewed mask creates a new derived child output:

```text
Saved source image
+ reviewed grayscale mask
→ ImageToMask
→ optional GrowMask
→ optional FeatherMask
→ foreground estimation or JoinImageWithAlpha
→ transparent PNG
→ optional refined mask PNG
```

This route runs **without rerunning BiRefNet**. It does not load a BiRefNet model and must not contain `LoadRembgByBiRefNetModel` or `RembgByBiRefNetAdvanced`.

The parent result/file IDs are retained so Output Inspector and replay can show that the refined image came from a reviewed mask rather than a new segmentation pass.

## Recommended settings

General first pass:

```text
Preset: Smart Auto
Mask threshold: 0
Edge expand / contract: 0
Edge feather: 0
Foreground colour estimation: On
Save alpha mask: On
```

Hair or fur cleanup:

```text
Preset: Fine Edges / Hair
Mask threshold: 0
Review background: Black, then White
Edge feather: 1–3 px only when needed
```

Halo cleanup:

```text
Edge expand / contract: -1 to -3 px
Edge feather: 1–2 px
Foreground colour estimation: On
```

Missing edge cleanup:

```text
Paint Keep over missing areas
Edge expand / contract: 0 to +2 px
Edge feather: 0–2 px
```

Large expand/feather values can remove detail or create artificial outlines. Use the smallest effective correction.

## Output contract

Initial extraction:

```text
LoadImage
→ LoadRembgByBiRefNetModel
→ RembgByBiRefNetAdvanced
├→ SaveImage foreground RGBA PNG
└→ MaskToImage → SaveImage alpha mask PNG
```

Reviewed-mask refinement:

```text
LoadImage source
+ LoadImage reviewed mask
→ ImageToMask
→ optional GrowMask / FeatherMask
→ BlurFusionForegroundEstimation or JoinImageWithAlpha
├→ SaveImage foreground RGBA PNG
└→ MaskToImage → SaveImage refined mask PNG
```

Neo verifies persisted foreground alpha and optional mask output. Suspicious or flattened results remain available for diagnosis but receive warnings instead of being silently reported as valid transparent assets.

## Replay and safety

Replay restores the recorded parameters but must revalidate:

- requested engine and fallback policy;
- Comfy connection and route-specific nodes when Comfy is resolved;
- selected BiRefNet model for Comfy segmentation;
- optional rembg package, ONNX provider, and native model when native execution is resolved;
- source file availability;
- reviewed mask availability for refinement.

The source image and manually reviewed masks remain under `neo_data`. Repository code and manifests must not contain user images or runtime masks.

## Interactive SAM Selection

Use **Interactive Select · SAM points / box** when automatic removal chooses the wrong subject or a scene contains multiple people, products, props, or overlapping objects.

The feature stays inside:

```text
Image → Finish → Remove Background
```

Workflow:

```text
Choose/upload source
→ Preset: Interactive Select
→ Open SAM selector
→ add Keep / Remove points or drag a Box
→ Run Selection
→ optional BiRefNet soft-edge handoff
→ transparent PNG + optional mask PNG
```

### Prompt tools

- **Keep**: place inside the subject to retain it.
- **Remove**: place on an accidentally included nearby object or background region.
- **Box**: drag a rectangle around the target in crowded scenes.
- **Undo / Clear**: edit prompt history before running.

At least one Keep point or Box is required. Start with one Keep point near the centre of the subject. Add only the minimum number of extra prompts needed.

Prompt coordinates are stored normalized from `0` to `1`, not as screen pixels. This allows Neo to reproduce the same selection against the original source dimensions during replay. Replay still revalidates that the source file and native SAM runtime are available.

### SAM model

- **SAM ViT-B** is the recommended default and lower-memory option.
- **SAM ViT-H** is larger and can improve difficult selections, but requires a much larger first-use download and more memory.
- **Use quantized SAM encoder** is enabled by default to reduce memory use where supported.

Interactive SAM uses the optional Neo-native `rembg`/ONNX runtime. It does not require a ComfyUI profile. The first run may download the selected SAM encoder and decoder into `U2NET_HOME` or `~/.u2net/`.

### Edge handoff

**BiRefNet soft-edge refinement** runs a native BiRefNet mask and constrains it with the expanded/feathered SAM region. This is recommended for hair, fur, fabric, glow, and softer contours.

```text
SAM selection gate
+ native BiRefNet soft mask
→ multiply inside selected region
→ final alpha
```

Controls:

- **Selection gate expand** gives BiRefNet room outside the hard SAM boundary.
- **Selection gate feather** softens the gate transition.
- **Fall back to SAM-only** keeps the SAM result when the selected BiRefNet model cannot run.

Choose **SAM mask only** when you need a fast, harder object cutout or do not want a second model pass.

### Limitations

- One source image is accepted per Interactive SAM run.
- Interactive SAM is selection-guided, not a guarantee of perfect hair matting. Use Mask Review afterward for manual Keep/Remove cleanup.
- Source images and normalized prompt metadata are stored through Neo's normal runtime/result records under `neo_data`; they are not written into repository source files.

## Extension not visible after applying P6

Install P6.5.1 or a later cumulative package if the `image.background_removal` folder exists but the tool is absent from both Admin → Extensions and Image → Finish. The original cumulative P6 manifest used invalid scalar native-runtime requirements and was skipped by Neo's manifest registry. Restart Neo and hard-refresh the browser after applying the hotfix.

## Troubleshooting: render warning

If the extension appears under **Admin → Extensions** but Image → Finish shows a render warning instead of fields, restart Neo after applying the latest Background Removal hotfix and hard-refresh the browser. P6.5.2 fixes the specific frontend isolation error `commercialCard is not defined`.

## Multi-subject selection and Comfy SAM

P6.6 extends **Interactive Select** for group photos and crowded scenes without creating a separate workspace.

```text
Image → Finish → Remove Background
→ Preset: Interactive Select
→ Detect people or draw subject boxes
→ select only the subjects to keep
→ Run Selection
```

Each detected or manually boxed subject is stored independently with:

- a normalized source-space bounding box;
- selected/unselected state;
- optional Keep points;
- optional Remove points;
- detector source and confidence.

Neo generates one SAM mask for each selected subject and unions only those masks. Unselected people remain outside the foreground.

### Comfy SAM execution

**Auto** prefers the installed Impact Pack SAM checkpoint from `ComfyUI/models/sams` when all selected subjects are box-addressable:

```text
ComfyUI/models/sams/*.pth
→ SAMLoader
→ one SAMDetectorCombined pass per selected subject
→ union selected masks
```

The Comfy route loads the SAM model once per workflow. It does not copy or redownload the `.pth` checkpoint. Neo discovers models from the standard `ComfyUI/models/sams` folder and requires the exact Impact Pack node contract.

If any selected subject contains Keep/Remove correction points, Auto uses **Neo Native ONNX SAM** because the shared box route cannot represent those point prompts directly. The native route may use its own encoder/decoder cache under `U2NET_HOME` or `~/.u2net/`.

### Person detection

**Detect people** now reads the same active-profile detector snapshot as ADetailer. Neo resolves the selected Comfy profile, prefers the server-side **Admin → Models → ComfyUI models root** when configured, and scans these standard folders beneath it:

```text
ComfyUI/models/adetailer/
ComfyUI/models/ultralytics/bbox/
ComfyUI/models/ultralytics/segm/
```

Neo also merges detector names registered by Comfy model-folder endpoints and live Impact Pack node choices. The dropdown is a current runtime scan, not a separate user-maintained catalog. BBox and Segmentation models stay in separate lists; face-specific, person-specific, and arbitrarily named custom checkpoints remain visible in their detected type. Person-capable preference affects only the automatic default for **Detect people** and never removes other installed choices.

Absolute model roots are used only on the server. The browser receives portable model identifiers, counts, source labels, and safe diagnostics. No personal drive, home-folder, or custom-path value is hardcoded or returned.

P6.6.9 Phase 1 repairs the Background Removal `/models` source used by its detector selector.

P6.6.9 Phase 2 binds **Detect people** to that same selected Comfy profile. The endpoint resolves the chosen relative model identifier through the configured Admin Models root, including native Ultralytics folders, flat `models/adetailer`, and supported `extra_model_paths.yaml` folders. The selected YOLO model runs directly in Neo's local detector runtime.

If an explicit arbitrary one-class custom model is selected, Neo preserves it. Missing or face/hand/head-only selections repair to the current person-preferred model. A selected detector that cannot be read or executed now returns an actionable error and suggests a manual subject box; Neo does not silently substitute OpenCV HOG boxes. A valid selected-detector run that finds zero people remains a real zero-result run.

Registered models exposed by a remote Comfy server must also be readable from Neo's local/shared filesystem for this preview endpoint. Absolute resolved paths never enter the response.

P6.6.9 Phase 3 makes the browser scan state reliable without introducing a separate catalog. Each effective Comfy profile has its own runtime-only last-successful scan, and duplicate refreshes for the same profile share one request. Switching the Image backend automatically activates or scans that profile; a late response from the previous profile cannot replace the visible choices. A failed refresh keeps the last successful model lists available while showing the error and a retry action.

Saved BiRefNet, SAM, BBox, and Segmentation selections are not silently overwritten when a current scan cannot find them. The dropdown labels the saved value as missing from the current type-specific scan. Intentionally changing **Person detector type** is different: it clears the other type's value and chooses the discovered default for the newly selected BBox or Segmentation pool. Loading, error, empty, and ready states are announced in the Engine readiness card.

#### P6.6.9 detector pipeline completion

The detector repair is complete across all three ownership layers:

| Layer | Final behavior | Status |
|---|---|---|
| Backend model scan | Reads the effective profile's Admin Models root, standard detector folders, flat `models/adetailer`, registered Comfy folders, and live node choices. | Complete |
| Detect Subjects execution | Resolves and strictly executes the selected portable model through the same typed profile snapshot, without an OpenCV HOG substitution. | Complete |
| Frontend scan state | Keeps per-profile last-good runtime scans, rejects stale-profile reconciliation, preserves saved missing choices, and exposes retryable states. | Complete |

There is no separate custom detector catalog to maintain. The folders and model registrations visible to the selected Comfy profile are the source of truth. No additional P6.6.9 follow-up phase remains for the reported detector-list or Detect People issue.

Local acceptance for the completed flow should confirm:

1. **Refresh engines** lists the installed BBox and Segmentation detector files for the effective Comfy profile.
2. Switching **Person detector type** changes to the corresponding typed list.
3. **Detect people** runs the selected model or returns an actionable selected-model error; it never reports unrelated HOG boxes as that model's result.
4. Changing the Image backend cannot allow an older profile response to replace the current dropdown.
5. Saved custom choices remain visible and labeled if a later scan cannot currently discover them.

### Shared BiRefNet edge handoff

For box-only Comfy SAM runs, Neo can use an installed Comfy BiRefNet model after the selected-subject masks are combined:

```text
combined SAM mask
→ selection gate expand / feather
+ Comfy BiRefNet soft mask
→ multiply
→ final mask refinement
→ transparent PNG
```

This readiness is separate from core SAM readiness. When required nodes or a BiRefNet model are unavailable:

- **Fall back to SAM-only** keeps the Comfy SAM route and records the fallback;
- disabling fallback blocks the run with the exact missing asset.

Replay restores subject groups, selected states, correction points, detector preference, and SAM execution preference, then revalidates the source, Comfy connection, nodes, and installed models.


### Detector discovery and current-selection readiness

P6.6.3 merges person-detector choices from both the standard Ultralytics folders and the live Impact Pack `UltralyticsDetectorProvider` catalog. Nested detector folders and generic COCO YOLO filenames are supported; a generic `yolov8*.pt` checkpoint is treated as person-capable even when its filename does not contain `person`. Face-, hand-, eye-, mouth-, lip-, and head-only models are not selected automatically for **Detect people**.

The SAM status badge now describes the current selection rather than only the installed assets:

- **SAM assets ready** — a SAM backend is installed, but no subject has been selected yet;
- **SAM route ready** — the selected subjects can run through the resolved route;
- **Selection needs attention** — a selected subject lacks a box or contains point corrections while Native ONNX SAM is unavailable.

When only Comfy SAM is available, the editor disables Keep/Remove correction tools and directs the user to draw one box per subject. Existing correction points must be removed before the box-only Comfy route can run.

## Portable ComfyUI SAM discovery

Neo resolves the real ComfyUI application root before scanning `models/sams`. In portable Windows layouts, the configured wrapper can be one level above the actual application folder:

```text
ComfyUI_windows_portable/
└── ComfyUI/
    ├── custom_nodes/
    └── models/sams/
```

When Node Manager has a `custom_nodes` path, Neo treats that folder's parent as the authoritative ComfyUI root. Neo also merges the model choices reported by the live `SAMLoader` node through Comfy `/object_info`. This means a valid SAM checkpoint can be discovered from either the standard filesystem folder or Comfy's live loader catalog without exposing an absolute machine path in the UI.
