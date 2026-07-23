---
guide_id: admin.comfy_extra_model_paths
title: Comfy Extra Model Paths
surface: admin
scope: global
applies_to:
  - admin_models
  - node_manager
  - comfyui
  - comfyui_portable
  - image_extensions
  - video_extensions
tags:
  - admin
  - comfyui
  - models
  - extra_model_paths
  - portable
  - shared_models
priority: 116
version: 1
updated: 2026-07-23
---

# Comfy `extra_model_paths.yaml`

Use Comfy's `extra_model_paths.yaml` when models live outside the active Comfy installation. Neo reuses the Comfy roots already stored under **Admin → Extensions → Node Manager** and does not require a second personal path field.

The YAML remains local runtime configuration. Never commit drive letters, usernames, network shares, or machine-specific folders to Neo's public repository.

## Location

Comfy loads `extra_model_paths.yaml` from its application directory. Portable installations may also place it beside the portable wrapper when that is the path used to launch Comfy. Neo checks the saved Node Manager wrapper and the inner Comfy application root.

Typical portable shape:

```text
<COMFY_PORTABLE_ROOT>/
├── extra_model_paths.yaml
└── ComfyUI/
    ├── custom_nodes/
    └── models/
```

## YAML rules

- The top-level group name is arbitrary. `shared_models` is only an example.
- `base_path` may be absolute locally or relative to the YAML file.
- Every model-folder value must be a string. Use `|` for multiple folders.
- Keep `is_default` as a boolean, not a quoted path value.
- Keys are model-folder registry names. Core keys come from Comfy; custom nodes may register additional keys.
- Declare only folders that exist in your installation. Missing optional folders are harmless but add noise.
- Restart or refresh Comfy after changing the file, then refresh the relevant Neo model catalog.

Comfy currently reads each folder value as a newline-separated string and registers the key exactly as written. Core keys are defined by Comfy's `folder_paths.py`; custom-node keys become useful when the installed node registers or consumes that same folder name.

## Recommended shared-model template

Replace `<SHARED_MODELS_ROOT>` only in your local YAML. Remove entries for folders you do not use.

```yaml
shared_models:
    base_path: <SHARED_MODELS_ROOT>
    is_default: true

    # Core Comfy model folders
    checkpoints: checkpoints
    configs: configs
    diffusion_models: |
        diffusion_models
        unet
    text_encoders: |
        text_encoders
        clip
    clip_vision: clip_vision
    vae: vae
    vae_approx: vae_approx
    loras: loras
    embeddings: embeddings
    diffusers: diffusers
    controlnet: |
        controlnet
        t2i_adapter
    upscale_models: upscale_models
    latent_upscale_models: latent_upscale_models
    style_models: style_models
    gligen: gligen
    hypernetworks: hypernetworks
    photomaker: photomaker
    classifiers: classifiers
    model_patches: model_patches
    audio_encoders: audio_encoders
    background_removal: background_removal
    frame_interpolation: frame_interpolation
    geometry_estimation: geometry_estimation
    optical_flow: optical_flow
    detection: detection

    # Custom-node folders used by Neo when the matching nodes are installed
    ipadapter: ipadapter
    adetailer: adetailer
    ultralytics_bbox: ultralytics/bbox
    ultralytics_segm: ultralytics/segm
    onnx: onnx
    sams: sams
    BiRefNet: BiRefNet
    facerestore_models: facerestore_models
    SEEDVR2: SEEDVR2
```

Do not declare both a broad folder and typed subfolders unless that matches your real layout. For example:

- a single mixed detector library can use `adetailer: adetailer`;
- a typed Impact Pack layout can use `ultralytics_bbox` and `ultralytics_segm`;
- ONNX and SAM folders should use the exact names registered by the installed nodes.

## Key reference

### Core Comfy keys

| Key | Typical folder | Used by Neo for |
|---|---|---|
| `checkpoints` | `checkpoints` or `Stable-diffusion` | Checkpoint-based image routes |
| `configs` | `configs` | Legacy checkpoint configurations |
| `diffusion_models` | `diffusion_models`, `unet` | Component and diffusion-model routes |
| `text_encoders` | `text_encoders`, `clip` | CLIP/T5/Qwen and other text encoders |
| `clip_vision` | `clip_vision` | IP Adapter and vision conditioning |
| `vae` | `vae` | VAE loaders and route-owned VAE selection |
| `vae_approx` | `vae_approx` | TAESD/approximate preview VAEs |
| `loras` | `loras` | LoRA Stack |
| `embeddings` | `embeddings` | Embeddings / Textual Inversion |
| `diffusers` | `diffusers` | Diffusers-format model directories |
| `controlnet` | `controlnet`, `t2i_adapter` | ControlNet and adapter models |
| `upscale_models` | `upscale_models` | ESRGAN/RealESRGAN-style upscalers |
| `latent_upscale_models` | `latent_upscale_models` | Latent upscalers |
| `style_models` | `style_models` | Style-model loaders |
| `gligen` | `gligen` | GLIGEN loaders |
| `hypernetworks` | `hypernetworks` | Legacy hypernetworks |
| `photomaker` | `photomaker` | PhotoMaker nodes |
| `classifiers` | `classifiers` | Classifier-backed nodes |
| `model_patches` | `model_patches` | Model patch loaders |
| `audio_encoders` | `audio_encoders` | Audio/video conditioning |
| `background_removal` | `background_removal` | Core background-removal model folders |
| `frame_interpolation` | `frame_interpolation` | Frame interpolation models |
| `geometry_estimation` | `geometry_estimation` | Depth/geometry estimation models |
| `optical_flow` | `optical_flow` | Optical-flow models |
| `detection` | `detection` | Core detection models |

### Custom-node keys Neo recognizes or queries

| Key | Typical folder | Neo consumer |
|---|---|---|
| `ipadapter` | `ipadapter` | IP Adapter model dropdowns |
| `adetailer` | `adetailer` | Recursive BBox, Segmentation and ONNX detector discovery |
| `ultralytics_bbox` | `ultralytics/bbox` | Typed ADetailer BBox pool |
| `ultralytics_segm` | `ultralytics/segm` | Typed ADetailer Segmentation pool |
| `onnx` | `onnx` | ONNX detector provider choices |
| `sams` | `sams` | ADetailer and Background Removal SAM choices |
| `BiRefNet` | `BiRefNet` | Background Removal BiRefNet choices |
| `facerestore_models` | `facerestore_models` | CodeFormer / face restoration |
| `SEEDVR2` | `SEEDVR2` | SeedVR2 upscale models |

Custom nodes may register other keys. Use the exact folder name shown by Comfy's `/models` index or the node's documentation. Neo should not guess a personal folder name.

## Troubleshooting

### YAML found, but ADetailer folder missing

```text
config_files_found: 1
configured_detector_folders: 0
```

The YAML was found, but no supported detector key was declared. Add either:

```yaml
adetailer: adetailer
```

or the typed layout:

```yaml
ultralytics_bbox: ultralytics/bbox
ultralytics_segm: ultralytics/segm
```

Expected successful detector diagnostics include:

```text
config_files_found: 1
configured_detector_folders: 1 or more
existing_detector_folders: 1 or more
```

### Folder exists but dropdown remains stale

1. Restart or refresh Comfy so custom nodes register their folders.
2. Use the extension's **Refresh models** action in Neo.
3. Confirm the YAML key matches the folder key registered by Comfy.
4. Confirm `base_path` plus the relative value resolves to the real folder.
5. Check spelling and capitalization on case-sensitive hosts.

### Privacy boundary

Absolute paths are server-side only. Public diagnostics may expose counts, source labels and portable model values, but never the local `base_path`, drive letter, username or full model path.
