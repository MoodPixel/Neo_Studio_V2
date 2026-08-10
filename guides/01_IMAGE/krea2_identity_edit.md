---
guide_id: image.krea2_identity_edit
title: Krea 2 Identity Edit
surface: image
scope: built_in
applies_to:
  - image_workspace
  - img2img
  - inpaint
  - outpaint
  - krea2
  - krea2_turbo
  - diffusion_model
  - gguf
  - lora
  - identity_edit
tags:
  - image
  - krea 2
  - identity edit
  - image editing
  - reference image
  - gguf
  - safetensors
  - qwen3-vl
priority: 117
version: 2
updated: 2026-08-08
---

# Krea 2 Identity Edit

Neo exposes **Krea 2 Identity Edit v1.2** as an opt-in edit engine inside the existing Krea 2 RAW and Krea 2 Turbo image routes. It does not replace Neo's existing Krea 2 source-latent/mask/canvas adapters, and it is not a separate Krea model family.

The upstream node pack is `comfyui-krea2edit` and the recommended current weight is `krea2_identity_edit_v1_2.safetensors`. The user-supplied model page for this integration is `https://civitai.com/models/2761113/krea-2-identity-edit?modelVersionId=3139172`.

## What it is for

The v1.2 model/node workflow is intended for instruction-based, appearance-preserving editing. Useful trained use cases include:

- identity/face-likeness preserving restaging;
- recolor, attribute, scene, and style changes;
- character reference sheet use and creation;
- head / face / eye / person replacement;
- object/person removal and replacement;
- garment try-on;
- inpainting;
- outpainting;
- two-reference scene + subject edits.

This is a community Krea 2 editing workflow, not an official new Krea family owned by Neo.

## Required Comfy dependencies

Install the custom node pack into ComfyUI and restart. In Neo, this can be done from **Admin → Image → Node Manager → Install GitHub** using:

```text
https://github.com/lbouaraba/comfyui-krea2edit.git
```

Required runtime pieces:

- a ComfyUI build with native Krea 2 support;
- Krea 2 RAW or Krea 2 Turbo diffusion model;
- Qwen3-VL-4B text encoder loaded through `CLIPLoader(type=krea2)`;
- Qwen Image VAE;
- `krea2_identity_edit_v1_2.safetensors` in the Comfy LoRA catalog;
- custom nodes `Krea2EditModelPatch` and `Krea2EditGroundedEncode`;
- core `LoraLoaderModelOnly` and `EmptySD3LatentImage`.

Neo validates the current v1.2 socket contract. An older node build that exposes the same class names but lacks the two-reference, pixel-fit, or `target_latent` sockets is blocked instead of being submitted optimistically.

## Native / SafeTensor workflow

```text
UNETLoader(Krea 2)
  -> LoraLoaderModelOnly(Identity Edit LoRA)
  -> Krea2EditModelPatch
  -> KSampler.model

LoadImage(Image 1)
  -> VAEEncode
  -> Krea2EditModelPatch.source_latent

LoadImage(Image 1)
  -> Krea2EditModelPatch.source_image
  -> Krea2EditGroundedEncode.image

CLIPLoader(Qwen3-VL-4B, type=krea2)
  -> Krea2EditGroundedEncode(prompt)
  -> KSampler.positive

CLIPLoader(Qwen3-VL-4B, type=krea2)
  -> Krea2EditGroundedEncode(empty prompt, same image)
  -> KSampler.negative

EmptySD3LatentImage
  -> KSampler.latent_image
  -> Krea2EditModelPatch.target_latent
```

The graph deliberately uses both edit-conditioning paths:

1. **appearance path** — clean VAE source tokens through `Krea2EditModelPatch`;
2. **semantic path** — the source image is visible to Qwen3-VL through `Krea2EditGroundedEncode` while it reads the instruction.

Using stock `CLIPTextEncode` instead of the grounded encoder is not the supported Identity Edit graph.

## GGUF workflow

GGUF changes only the first diffusion-model loader:

```text
UnetLoaderGGUF / LoaderGGUF
  -> LoraLoaderModelOnly(Identity Edit LoRA safetensors)
  -> Krea2EditModelPatch
```

Everything else stays native/safetensors:

- Qwen3-VL-4B through `CLIPLoader(type=krea2)`;
- Qwen Image VAE;
- Identity Edit LoRA;
- grounded encoder and model patch nodes.

Neo therefore treats Krea 2 GGUF Identity Edit as **transformer-only GGUF**. Do not select a GGUF Qwen3-VL encoder for this route.

## Neo controls

Choose Krea 2 RAW or Turbo, select Components or GGUF, then use an image mode and set **Krea 2 Edit Engine → Krea 2 Identity Edit v1.2**.

The engine card is mounted in **Parameters** only for `img2img`, `inpaint`, and `outpaint`. It is intentionally absent from `txt2img`. M17.1 fixed a frontend mounting bug where the parameter profile contained the Identity Edit fields but the fixed Parameters renderer did not insert them into the visible surface, especially obvious on GGUF routes.

| Control | Meaning | Starting point |
|---|---|---|
| **Identity Edit LoRA** | Dedicated model-only edit LoRA. Required when the engine is enabled. | `krea2_identity_edit_v1_2.safetensors` |
| **Identity Edit LoRA Strength** | LoRA model strength. | `1.0` |
| **Reference Fit** | Source-to-target geometry. | `fit` |
| **Identity Reference Boost** | Pull toward the final/subject reference appearance. | `4.0` for strong likeness, then tune |
| **Scene Reference Boost** | First-reference boost in two-image edits. | `1.0` |
| **Grounding Resolution** | Resolution shown to Qwen3-VL for semantic grounding. | `768`; v1.2 trained range is roughly 384–768 |

`target_latent` is always wired to the same `EmptySD3LatentImage` used by KSampler. This lets the node pre-encode the pixel-fit source before sampling and avoids the known VRAM/offload slowdown that can occur when source VAE encoding starts mid-sampler.

## One reference vs two references

Single-reference edit:

```text
Image 1 = image being edited / appearance reference
```

Two-reference edit uses the training order:

```text
Image 1 = scene / composition / main edit canvas
Image 2 = subject / identity reference
```

Image 2 is connected to `source_latent_b`, `source_image_b`, and grounded `image_b`. Neo intentionally caps this engine at two source images. Image 3 is not routed into Krea 2 Identity Edit.

## Inpaint behavior

Identity Edit owns the generation graph, so Neo does **not** inject generic `InpaintModelConditioning`, `SetLatentNoiseMask`, or `DifferentialDiffusion` into it.

Neo runs the trained instruction edit against the full source, then uses the user's inpaint mask as the final `ImageCompositeMasked` commit boundary. This keeps the edit model's training-matched target-noise path intact while preventing unmasked generated pixels from replacing the original image.

Identity Edit and LanPaint cannot be stacked on the same Krea masked job. Selecting Identity Edit uses the Identity Edit family graph.

## Outpaint behavior

Krea 2 Identity Edit v1.2 learned outpainting through its **centered `fit` reference geometry**. Neo therefore keeps Image 1 clean and creates a larger `EmptySD3LatentImage` target; it does not feed blank `ImagePadForOutpaint` pixels into the clean appearance-token path.

The Outpaint Left / Right / Top / Bottom controls determine the requested **target size**. The v1.2 reference itself is centered inside that target. If padding is asymmetric, Neo warns that the source cannot be side-anchored exactly by this trained graph. Use balanced padding when exact source placement matters.

For outpainting, prefer Euler/ODE-style sampling. The upstream v1.2.4 advisory warns that SDE noise can disrupt the reference-copy channel.

## Sampling guidance

These are starting points, not forced values. **Parameter Truth remains authoritative.**

- Turbo: roughly 8 steps / CFG 1 is the fast general-edit path.
- RAW: use stronger real guidance for removals or stubborn destructive changes; upstream suggests around CFG 3 and ~20 steps as a useful starting point.
- Keep generation at or below roughly 2 MP for the trained range; larger targets can duplicate/bleed source content.
- `grounding_px` lower values can improve edit adherence; higher values can increase identity/likeness emphasis.
- Denoise remains user-owned, but Identity Edit samples from a fresh `EmptySD3LatentImage`; it is not the same source-latent blend semantics as Neo's legacy Img2Img adapter.

## Negative conditioning

Identity Edit always uses a second `Krea2EditGroundedEncode` with:

```text
prompt = ""
image = same source image(s)
```

That matches the trained unconditional path. A normal user negative prompt is not applied by this engine; Neo records a warning when one is present rather than silently implying that it reached the Identity Edit negative branch.

## LoRA Stack interaction

The dedicated Identity Edit LoRA is part of the Krea 2 Identity Edit engine and is separate from the general **Image → Assets → LoRA Stack**.

If global model-only Krea LoRAs are also enabled, the compiler-owned patch profile rewires them upstream:

```text
base Krea model
  -> global LoRA Stack rows
  -> dedicated Identity Edit LoRA
  -> Krea2EditModelPatch
  -> sampler
```

The Qwen3-VL CLIP branch remains unpatched by Krea model-only LoRAs.

## Validation status

Neo's local validation proves graph construction, route ownership, current-node socket checks, model/LoRA wiring, two-reference ordering, and legacy Krea regression compatibility. It does **not** prove visual quality or physical GPU behavior. Run real Krea 2 images on the target Comfy/RunPod environment before treating the route as physically validated.
