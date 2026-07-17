---
guide_id: image.adetailer
title: Image ADetailer
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - adetailer
  - selective_repair
  - face_repair
  - hand_repair
  - manual_boxes
  - detailer_passes
tags:
  - image
  - finish
  - adetailer
  - repair
  - faces
  - hands
  - detailer
  - impact pack
priority: 111
version: 3
updated: 2026-07-18
---

# Image ADetailer

**ADetailer** is the built-in Image → Finish selective repair tool. Use it when the generated image is mostly good, but a local region needs repair: face, eyes, hands, person, clothing, object, product detail, or a manually drawn target.

ADetailer is a finish-stage extension. It should run after the base generation and after other structure/style/reference tools. If High-Res Lab is active, ADetailer should repair the high-res output, not the earlier base decode.

## Supported route shape

| Route | State |
|---|---|
| ComfyUI / ComfyUI Portable + SDXL checkpoint + Generate/Img2Img/Inpaint | Available |
| ComfyUI / ComfyUI Portable + SDXL checkpoint + Outpaint | Planned/gated |
| ComfyUI / ComfyUI Portable + SD 1.5 checkpoint + Generate/Img2Img/Inpaint | Experimental |
| ComfyUI / ComfyUI Portable + SD 1.5 checkpoint + Outpaint | Planned/gated |
| Flux/Qwen/ZImage/HiDream component or GGUF routes | Unsupported/gated in the current ADetailer graph contract |
| xAI Grok / cloud API routes | Not a local Comfy ADetailer graph |

ADetailer depends on local detection/detailer nodes and detector assets. Impact Pack/SEGS-style paths and detector models must be installed and discoverable.

## Detector model discovery

The ADetailer model picker merges configured backend sources without exposing custom path fields:

1. recursive scanning of the standard Comfy folders under `ComfyUI/models/ultralytics/`;
2. compatibility scanning of `ComfyUI/models/adetailer/`;
3. detector folders registered through Comfy `extra_model_paths.yaml`;
4. the live model choices exposed by Impact Pack's detector-provider nodes through Comfy `/object_info`.

The live catalog is authoritative for nested/custom models because its scoped values are the exact choices accepted by the active Comfy nodes. Filesystem discoveries remain available as an offline fallback. ADetailer intentionally shows the complete detector catalog. Person-only preference and face-only rejection belong to Background Removal's **Detect people** action and must not narrow the normal ADetailer picker.


## Apply ADetailer to a completed Inpaint result

From **Image → Results → Output Inspector**, select the completed output file and click **Apply ADetailer Pass**.

Neo will:

1. use that completed output as the new source image;
2. clear the old inpaint mask;
3. run a forced Img2Img-derived finish pass;
4. apply the current ADetailer detector/pass settings;
5. save the repaired image as a new derived output.

The workspace can remain visually on Results or Inpaint while the finish pass runs. The execution mode is forced to Img2Img internally so the old mask is not required or reused.

Use the ADetailer panel before clicking the action when you need to change detector, confidence, denoise, prompts, manual boxes, or pass order.

## Image-source ownership

ADetailer has one pixel-source contract:

- during a normal Generate run, it repairs the generated/current finish output;
- during an explicit post-output pass, it repairs the selected output carried by the Img2Img source lane;
- an IP Adapter reference image is identity/style conditioning only;
- a ControlNet image is structural conditioning only.

ADetailer never selects the first available `LoadImage` node. A post-output pass resolves only the declared source image connected to the base VAE encoding lane. If that owned lane is missing, Neo blocks the finish patch instead of borrowing an IP Adapter, ControlNet, mask, or other extension asset.

When a user returns to clean Generate/txt2img after staging a completed output, Neo clears ADetailer's temporary `preview_action_source`, `staged_preview_source`, `detailer_output_pass`, and derived source-image state before submission. Detector/pass settings remain enabled and reusable; only the stale post-output ownership state is removed.

## Header and state chips

| Chip | Meaning |
|---|---|
| **Enabled / Disabled** | Whether ADetailer is applied to this generation/finish run. |
| **Available / Experimental / Route gated / Unsupported** | Whether the current route can support the tool. |

Disabled does not mean broken. It means the extension exists but is not currently applied.

## Shared defaults

Shared defaults affect the whole detailer stack and are inherited by pass cards unless a pass overrides them.

| Field | Meaning |
|---|---|
| **SAM preset / SAM model** | Segmentation model selection for mask refinement. |
| **Detector provider** | Usually Ultralytics or ONNX. |
| **Custom classes / notes** | Optional detector class hints such as person, face, hand, clothing. |
| **Confidence** | Detection confidence threshold. Higher is stricter. |
| **Top-K** | Limits how many detected targets are kept. 0 means no explicit cap. |
| **BBox grow** | Expands or shrinks the detected box before detail pass. |
| **Mask blur** | Softens mask edge to avoid hard seams. |
| **Denoise** | Strength of local repair. Low preserves; high changes more. |
| **Steps** | Detail pass sampler steps. |
| **CFG cap** | Caps prompt strength for repair. Lower values reduce overcorrection. |
| **Use main prompts** | Reuses the main positive/negative prompt as context. |
| **Force inpaint pass** | Routes repair through an inpaint-style pass even if the base mode was not inpaint. |

## Detector model scan

The Detector model list is built from the active Comfy backend rather than a Neo-specific custom path field. Neo merges:

- the local-only **Admin → Models → Paths → ComfyUI models root** setting, which is the authoritative filesystem source for URL-only Comfy profiles;
- `ComfyUI/models/ultralytics/bbox/`;
- `ComfyUI/models/ultralytics/segm/`;
- nested detector folders under the standard Ultralytics tree;
- `ComfyUI/models/adetailer/` for existing ADetailer-compatible model layouts;
- detector folders registered through Comfy `extra_model_paths.yaml`, including `ultralytics`, `yolo`, `detectors`, `adetailer`, `detailer`, and type-specific aliases;
- live Impact Pack `UltralyticsDetectorProvider` and `ONNXDetectorProvider` choices.

Set **ComfyUI models root** to the parent models directory, not directly to its `adetailer` child. Neo derives `adetailer`, `ultralytics/bbox`, `ultralytics/segm`, `onnx`, and `sams` beneath that root. It also reads Comfy's registered `/models` folder endpoints so a URL-only local profile is not limited to the small list returned by one provider node.

The selected Detector type controls the dropdown. BBox, Segmentation, and ONNX lists remain separate so a face bounding-box model is not shown as a valid segmentation choice. Refresh models after Comfy starts or after changing model-path configuration. If live Comfy discovery is temporarily unavailable, the backend keeps filesystem models and records safe source/count/error diagnostics without returning absolute machine paths.

The loaded status reports total BBox, Segmentation, ONNX, and SAM choices. When diagnostics are available, it also reports **folder files** separately from **Comfy registered** choices. This distinction makes a folder-resolution problem visible without exposing the absolute folder path.

Face, Hands, Person, and Custom detector targets do not filter the model list. They describe the repair intent. The selected Detector type alone chooses the BBox, Segmentation, or ONNX pool.

### Path privacy boundary

ADetailer model discovery may use absolute filesystem paths on the server, but the browser model scan exposes only model values and portable role paths. Do not add a developer drive, username, Comfy root, detector root, or SAM root to the response payload, UI placeholder, manifest, or tracked defaults. Local Node Manager and Admin model-path values belong under ignored `neo_data/` storage.

### Comfy execution bridge

The model scan and Comfy execution have different folder contracts. Existing ADetailer installations often keep detector files directly under `ComfyUI/models/adetailer`, while Impact Pack loads Ultralytics detectors from `ComfyUI/models/ultralytics/bbox` or `ComfyUI/models/ultralytics/segm` and ONNX detectors from `ComfyUI/models/onnx`.

Immediately before Neo builds and queues an ADetailer graph, the execution bridge checks only the enabled detector models selected in that run:

- a selected flat BBox detector is copied into the matching relative path under `ultralytics/bbox`;
- a selected flat segmentation detector is copied into the matching relative path under `ultralytics/segm`;
- a selected flat ONNX detector is copied into the matching relative path under `onnx`;
- an existing non-empty native target wins and is never overwritten;
- the source file remains in `adetailer` and is never moved or changed;
- copies use a temporary sibling plus atomic replace so Comfy cannot observe a partial model;
- absolute, drive-qualified, and parent-traversal selections are rejected before any copy.

This bridge is execution-time only. Opening or refreshing the model scan never writes into the models directory. It runs only when Neo can establish local/shared filesystem access for the active Comfy profile. A remote URL-only profile is reported as non-local and is not staged. A model with no flat-folder source remains non-blocking because Comfy may already register it through its own folders or `extra_model_paths.yaml`.

Bridge metadata reports only safe states and relative model values: `staged`, `already_ready`, `not_local_source`, `remote_url_only`, or `blocked`. Absolute source and target paths remain server-side. A rejected path or failed copy creates a blocking extension validation item, so the provider stops before posting the workflow to Comfy.

### Detector execution validation

Filesystem discovery alone does not prove that an Impact Pack provider currently accepts a detector value. Before compiling an extension workflow, Neo now:

1. stages only the enabled selected detectors into the active Comfy model root;
2. requests fresh Comfy `/object_info` after staging;
3. reads the live choices for `UltralyticsDetectorProvider` or `ONNXDetectorProvider`;
4. uses the exact accepted choice, including its `bbox/`, `segm/`, or provider-specific nested scope;
5. blocks before queue when the active provider still does not accept the selection.

The resolver is filename-agnostic. Models such as `face_yolo11s.pt`, `face_yolov8s.pt`, person detectors, hand detectors, and custom detectors follow the same contract; none is whitelisted in Neo.

If a newly installed model is visible in the filesystem catalog but Comfy does not yet expose it as a provider choice, use **Refresh Nodes** or restart Comfy, then use **Refresh models** in ADetailer. Neo reports `adetailer_detector_not_accepted_by_comfy_provider` instead of sending a workflow that Comfy will reject with `Value not in list`.

## Model scan refresh and recovery

ADetailer scans models automatically the first time the panel opens for an Image backend profile. Each profile has its own temporary runtime scan result; model lists are not saved inside the generation recipe and do not carry across profiles as stale draft data.

Use **Refresh models** after installing/removing a detector or changing `extra_model_paths.yaml`. Refresh bypasses browser caching and allows only one request for the current profile at a time. A transient request failure receives one automatic retry.

Model scan states are explicit:

| State | Meaning |
|---|---|
| Scanning / Refreshing | A request is running. The last successful options remain visible. |
| Models loaded | The status shows BBox, Segmentation, ONNX, and SAM counts plus available source labels. |
| No BBox/Segmentation/ONNX models found | The selected type has no models in the successful catalog; other type pools may still be populated. |
| Refresh failed | Check/start Comfy and refresh again. A previous successful catalog is retained. |
| Saved · not in current BBox/Segmentation/ONNX scan | The saved detector is preserved for review but is not currently reported by that detector type. Select a current model before queueing. |

Switching the Image backend automatically resolves a separate scan. A successful backend **Connect/Test** also triggers a new silent scan. A response from the previously selected profile cannot replace the active profile's dropdown or detector selection.

## Detailer pass cards

ADetailer supports one primary pass plus optional additional passes.

| Pass field | Meaning |
|---|---|
| **Mode** | Face, hands, person, or custom repair target. |
| **Detector type/model** | Detection path/model for this pass. |
| **Target order** | Which targets are repaired first: auto, left-to-right, largest-first, etc. |
| **Start index / Count** | Which detected targets to repair. Useful for multi-face images. |
| **Min / Max area** | Filters detections by size. |
| **Target mode** | Auto detect or manual boxes. |
| **Reference lock** | Optional identity/style/control reference policy. |
| **Positive / Negative prompt** | Pass-specific repair prompts. |

## Reference Lock

Reference Lock is a **conditioning policy**, not an image-source selector. ADetailer always repairs the generated/current finish output (or the explicitly selected post-output source). IP Adapter and ControlNet images remain upstream conditioning assets and are never substituted as ADetailer's pixels.

| Mode | Required active dependency | Behavior |
|---|---|---|
| **Off** | None | Runs the repair with the normal upstream graph. Active extensions still affect generation normally. |
| **Soft identity** | FaceID IP Adapter | Keeps FaceID conditioning on the repair model and caps repair denoise at 0.30. |
| **Strong identity** | FaceID IP Adapter | Keeps FaceID conditioning and caps repair denoise at 0.20. |
| **Face only** | FaceID IP Adapter + a Face pass | Repairs the detected generated face with the FaceID-conditioned model and caps denoise at 0.25. It does not feed the FaceID reference image into ADetailer. |
| **Style only** | Standard IP Adapter | Confirms standard IP Adapter conditioning is active for the repair pass. |
| **Follow ControlNet** | ControlNet | Confirms ControlNet conditioning is active while the detail pass uses the sampler's patched prompt conditioning. |
| **Legacy IP-Adapter / FaceID** | Any IP Adapter unit | Compatibility policy for older saved recipes. |
| **Legacy both** | IP Adapter + ControlNet | Compatibility policy requiring both upstream extensions. |

Reference Lock does not force the SEGS route. SEGS is selected only when targeting features such as multiple targets, manual boxes, ordering, or area filters require it. If a dependency is missing or **Face only** is used on a non-face pass, Neo shows a warning and safely runs that pass without claiming that the lock was applied.

The IP Adapter unit's own weights and timing remain authoritative. Reference Lock does not hardcode a detector filename, reference path, IP Adapter model, or personal filesystem location.

## FaceID generation followed by ADetailer

Use this order when the goal is a new prompted image with the reference person's
identity and a final face repair:

1. In **Image → Reference → IP Adapter / FaceID**, enable one FaceID unit.
2. Select the FaceID model, matching preset, CLIP Vision model, provider, and
   reference portrait.
3. Keep the reference weight moderate so the prompt still controls clothing,
   pose, background, and scene.
4. In **Image → Finish → ADetailer**, enable a Face pass and select a detector
   accepted by the active Comfy detector-provider list.
5. Set **Reference Lock** to **Face only** and use conservative face denoise.
6. Generate normally. IP Adapter conditions the model first; ADetailer detects
   and repairs the generated face afterward.

The FaceID portrait is never ADetailer's pixel source. If the output resembles
the reference image's original pose/background instead of following the prompt,
check for an overly high IP Adapter weight and confirm only one durable reference
is present. If Comfy reports `Value not in list` for the detector, refresh the
ADetailer model list and select the exact live provider value; do not rename or
hardcode a detector path in Neo.

For **Apply ADetailer Pass** on a completed output, the selected output becomes
the Img2Img-owned pixel source. The FaceID reference remains conditioning only.

## Manual boxes and visual target picker

Use manual boxes when detection fails or the target is not a normal face/hand/person.

Manual boxes can be written as:

```text
xywh:120,80,300,300,#1
xyxy:120,80,420,380,#2
12%,10%,28%,28%,#3
```

The visual target picker can use the current output/source, detect targets, add canvas boxes, remove targets, sync canvas/text, and export/import snapshots. Per-target prompts are compiled with `[SEP]` chunks.

## Recommended starter settings

For face repair:

```text
Mode: face
Confidence: 0.30–0.45
BBox grow: 8–16
Mask blur: 4–12
Denoise: 0.10–0.25
Steps: 8–16
CFG cap: 5–8
Use main prompts: On
Force inpaint: On
```

For hands or difficult local repairs, denoise may need to be higher, but warn the user that identity/detail drift can increase.

## Assistant rules

When the user asks about ADetailer:

- explain it as Image → Finish selective repair;
- check route support and detector/SAM readiness before promising execution;
- recommend lower denoise for identity/face preservation;
- use manual boxes when auto detection misses the region;
- do not suggest it for cloud/API-only execution unless a local Comfy finish backend is connected.
