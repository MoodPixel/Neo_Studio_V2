---
guide_id: image.generation_extensions
title: Image Generation Extensions
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generations
  - extensions
  - cfg_fix_dynamic_thresholding
  - layerdiffuse
  - style_stack
  - wildcards
  - scene_director
  - regional prompting
  - generate
  - img2img
  - inpaint
  - outpaint
  - edit
  - sdxl
  - sd15
  - flux
  - flux2_klein
  - qwen_rapid_aio
  - qwen_image_edit
  - qwen_image_edit_2509
  - z_image
  - z_image_turbo
  - hidream
tags:
  - image
  - generation extensions
  - extensions
  - cfg fix
  - dynamic thresholding
  - layerdiffuse
  - style stack
  - wildcards
  - scene director
  - regional masks
  - route aware
  - loader aware
priority: 118
version: 1
updated: 2026-07-09
---

# Image Generation Extensions

The Image **Generation** workspace documents generation-time extension cards such as CFG Fix, LayerDiffuse, Style Stack, Wildcards, and Scene Director. Asset-owned tools such as **LoRA Stack / LoRA Library** and **Embeddings / Textual Inversion** belong under **Image → Assets**. Reference-owned tools such as **ControlNet** and **IP Adapter / FaceID** belong under **Image → Reference**. Use `guides/01_IMAGE/image_assets.md`, `guides/01_IMAGE/lora_stack.md`, `guides/01_IMAGE/embeddings_textual_inversion.md`, `guides/01_IMAGE/image_reference.md`, `guides/01_IMAGE/controlnet.md`, and `guides/01_IMAGE/ip_adapter_faceid.md` for those areas.

Extensions are route-aware. A card can be visible, disabled, ready, experimental, or route-gated depending on:

```text
Backend profile + Model Family + Main Model Type / Loader + Workflow Mode + installed Comfy nodes
```

The Assistant should use this guide when the user asks what an extension does, why a card is disabled, why a field changed after switching model family, or whether an extension applies to the selected Image route.

Scene Director has a larger regional/multi-entity routing contract. Use `guides/01_IMAGE/scene_director.md` for the full Scene Director guide.

## Extension types

| Extension | Type | Mutates prompt? | Mutates Comfy graph? | Needs custom Comfy nodes? | Main use |
|---|---:|---:|---:|---:|---|
| **CFG Fix / Dynamic Thresholding** | sampler/model patch | No | Yes, when active route supports it | Yes: `sd-dynamic-thresholding` nodes | Reduces high-CFG overbaking / blown contrast. |
| **LayerDiffuse** | workflow replacement / external graph | Uses base prompt | Yes, replaces base workflow when enabled | Yes: ComfyUI LayerDiffuse nodes | Transparent RGBA assets, alpha masks, foreground/background compositing. |
| **Style Stack** | prompt-only extension | Yes | No | No | Add saved/manual style prompt and negative style text. |
| **Wildcards** | prompt-only extension | Yes | No | No | Resolve `__token__` or `{a|b|c}` variants before generation. |
| **Scene Director** | regional scene planner | Uses global + local region prompts | Yes, on V054-ready checkpoint routes | Yes: `NeoSceneDirectorV054` for active graph mutation | Region boxes, per-region prompts, Character Lock, Pair Pose, background lanes, and extension routing. |

## Prompt extension order

Prompt-only extensions run before the provider receives the final prompt.

```text
Wildcards → Style Stack → provider execution
```

This matters because wildcard tokens can expand into style words, embedding/TI trigger text, or LoRA trigger text before Style Stack and downstream metadata/replay records are built.

## Route-state meanings

| State | Meaning | User-facing explanation |
|---|---|---|
| **Enabled** | The user checked the extension/apply box. | The user wants this extension applied. |
| **Ready / Available** | Route can execute this extension. | The selected family/loader/workflow has a validated path. |
| **Experimental** | Route can attempt it but is not fully promoted. | Use with caution and validate output/metadata. |
| **Route gated / Planned gated** | Card may be visible, but execution is blocked. | The selected route does not yet have safe compiler/node support. |
| **Provider gated** | Backend/provider cannot expose the needed capability now. | Usually missing backend support, node discovery, or object_info capability. |
| **Unsupported** | The selected route should not use this extension. | Switch route/family/loader or disable the extension. |

## Family and loader awareness summary

| Extension | Best-supported routes | Experimental / guarded routes | Not a good fit |
|---|---|---|---|
| **CFG Fix** | SDXL + checkpoint + Generate | SD 1.5 + checkpoint + Generate | Most component/GGUF/API routes, Img2Img/Inpaint/Outpaint until explicitly promoted. |
| **LayerDiffuse** | SDXL checkpoint Generate; SDXL checkpoint Img2Img for image-conditioned modes | SD 1.5 checkpoint Generate | Flux/Qwen/ZImage/HiDream/Grok/API routes; Inpaint/Outpaint. |
| **Style Stack** | Provider-neutral Generate/Img2Img/Inpaint/Outpaint | N/A | It only edits prompt text; it cannot fix model/node readiness. |
| **Wildcards** | Provider-neutral Generate/Img2Img/Inpaint/Outpaint | N/A | It only resolves prompt tokens; it does not change models/nodes. |
| **Scene Director** | SDXL checkpoint Generate/Img2Img/Inpaint on ComfyUI with V054 node | SD 1.5 checkpoint Generate/Img2Img/Inpaint | Flux/Qwen/ZImage/HiDream/GGUF/component/API routes; Outpaint remains planned-gated. |


## Reference-owned extension note

Do not explain **ControlNet** or **IP Adapter / FaceID** as normal Generation cards. They are Image **Reference** tools:

| Reference tool | Guide |
|---|---|
| **ControlNet** | `guides/01_IMAGE/controlnet.md` |
| **IP Adapter / FaceID** | `guides/01_IMAGE/ip_adapter_faceid.md` |
| **Reference workspace overview** | `guides/01_IMAGE/image_reference.md` |

## Asset-owned extension note

Do not explain LoRA Stack, LoRA Library, or Embeddings/Textual Inversion as normal Generation cards. They are Image **Assets** tools:

| Asset tool | Guide |
|---|---|
| **LoRA Stack / LoRA Library** | `guides/01_IMAGE/lora_stack.md` |
| **Embeddings / Textual Inversion** | `guides/01_IMAGE/embeddings_textual_inversion.md` |
| **Assets workspace overview** | `guides/01_IMAGE/image_assets.md` |

## Assistant behavior

When answering extension questions:

1. Check the live Image snapshot first for active Model Family, Main Model Type, Workflow Mode, backend, and enabled extensions.
2. Explain the visible extension fields in user terms.
3. Mention route support only when relevant.
4. Do not tell the user an extension will execute just because it is visible. Use the route state.
5. Do not dump payload JSON unless the user asks for raw debug/payload details.
6. For prompt-only extensions, explain the final prompt effect rather than graph patching.
7. For workflow-patching extensions, explain what gets patched and what node/backend requirement gates execution.

## Scene Director quick note

Scene Director is visible as a Generation extension, but it is not just a prompt helper. It owns regional planning: canvas boxes, region cards, prompt rules, Character Lock, Pair Pose Authority, Background Space Authority, layout safety, presets, and region assignment for owner-extension rows/units. See `guides/01_IMAGE/scene_director.md` for the full field guide.
