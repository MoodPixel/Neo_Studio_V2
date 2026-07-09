---
guide_id: image.assets
title: Image Assets Workspace
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - assets
  - source assets
  - masks
  - canvas inputs
  - lora_stack
  - lora_library
  - embeddings_ti
  - textual inversion
  - reusable assets
tags:
  - image
  - assets
  - source image
  - mask
  - canvas
  - lora
  - embeddings
  - textual inversion
  - library
  - route aware
priority: 116
version: 1
updated: 2026-07-09
---

# Image Assets Workspace

The Image **Assets** workspace is the canonical place for reusable or source-driven image assets. It is not the same as the base **Generation** workspace.

Use Assets when the user asks about source images, masks, reusable LoRAs, LoRA metadata, Embeddings/Textual Inversion, or assets that should be carried into a generation payload.

## What belongs in Image → Assets

| Area | Purpose | Notes |
|---|---|---|
| **Source images** | Image 1 / Image 2 / Image 3 or other source lanes used by img2img/edit/reference workflows. | Which source lanes appear depends on Model Family, Main Model Type, Workflow Mode, and backend profile. |
| **Masks / canvas inputs** | Mask images, inpaint masks, outpaint canvas helpers, and source-canvas inputs. | Required only for routes such as Inpaint or Outpaint. |
| **LoRA Stack** | The active LoRA rows used for generation. | Assets is the canonical owner. LoRA rows may influence base or finish passes only when the selected route supports graph patching. |
| **LoRA Library** | Metadata browser/catalog for Comfy LoRAs. | Browse `LoraLoader.lora_name`, enrich with CivitAI, store triggers/prompts/previews, then add selected LoRAs to LoRA Stack. |
| **Embeddings / Textual Inversion** | Prompt-token asset browser and chip manager. | Adds tokens such as `embedding:name` to positive/negative prompts or queues prompt-token patch metadata. |

## Route-aware behavior

Assets can be visible even when a specific asset type cannot execute on the current route. The Assistant should explain the difference between:

- **available / ready** — the selected backend/family/loader/mode can use the asset path now;
- **experimental** — the path can be tried but should be validated;
- **route gated / planned gated** — Neo preserves the user's intent, but the active graph should not be patched;
- **provider gated** — the backend/API does not expose the needed capability;
- **unsupported** — the asset type does not apply to that selected route.

## Assistant behavior

When answering Image Assets questions:

1. Check the live Image snapshot for active workspace, family, loader, workflow mode, backend profile, selected source/mask files, LoRA rows, and Embeddings/TI chips.
2. Explain assets in terms of what the user can do: browse, select, add chips/rows, enrich metadata, and generate.
3. Do not promise that LoRAs or embeddings will affect output unless the route state is Ready or Experimental.
4. Do not treat LoRA Library metadata as an active LoRA. The LoRA must be added to LoRA Stack.
5. Do not dump raw payload JSON unless the user asks for debug details.

## Related guides

- `guides/01_IMAGE/lora_stack.md`
- `guides/01_IMAGE/embeddings_textual_inversion.md`
- `guides/01_IMAGE/image_parameters.md`
- `guides/01_IMAGE/image_model_families.md`
