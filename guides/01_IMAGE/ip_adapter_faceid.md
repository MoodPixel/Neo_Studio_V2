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
version: 1
updated: 2026-07-09
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

Model dropdowns are populated from Comfy `/object_info` and supported `extra_model_paths.yaml` scanning. Neo should not require hardcoded user paths.

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
| **FaceID model** | FaceID model file for FaceID mode. | Requires FaceID-compatible nodes and models. |
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
- Setting weight too high and overpowering the prompt.
- Expecting IP Adapter to run on Qwen/Flux/API routes when the live route is unsupported.

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
