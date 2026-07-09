---
guide_id: image.embeddings_textual_inversion
title: Embeddings / Textual Inversion
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - assets
  - embeddings_ti
  - embeddings
  - textual inversion
  - prompt token
  - negative prompt
  - positive prompt
  - sdxl
  - sd15
tags:
  - image
  - assets
  - embeddings
  - textual inversion
  - prompt token
  - civitai
  - route aware
  - loader aware
priority: 114
version: 1
updated: 2026-07-09
---

# Embeddings / Textual Inversion

**Embeddings / Textual Inversion** is a built-in Image **Assets** extension. It manages prompt-token assets such as `embedding:name`.

Unlike a LoRA, Textual Inversion does not add a loader node to the Comfy graph. It works by inserting or preserving prompt tokens that the route's text encoder can understand.

## Where it lives

```text
Image → Assets → Embeddings / Textual Inversion
```

This card is an asset/prompt-token manager, not a Generation extension.

## Core fields

| Field / control | What it does | Advice |
|---|---|---|
| **Apply Embeddings/TI** | Enables Embeddings/TI chips for this workflow. | Keep disabled until at least one token/chip is selected. |
| **Scan Folder** | Scans a local embeddings folder and adds discovered records to Neo's Embeddings/TI browser. | Use this when the connected backend catalog does not list embeddings automatically. |
| **Refresh** | Refreshes Embeddings/TI records from the active backend/catalog. | Use after connecting ComfyUI or adding new embedding files. |
| **Records badge** | Shows how many Embeddings/TI records Neo can currently see. | `0 records` usually means the backend catalog/folder has not been scanned or no files exist. |
| **Embeddings folder** | Manual path to a folder such as `ComfyUI/models/embeddings`. | Use when auto-catalog discovery is unavailable. |
| **Search** | Filters the available Embeddings/TI records. | Useful when the folder contains many `.pt`, `.safetensors`, or similar TI files. |
| **Embedding** | Selects a discovered embedding record. | Selecting a record fills the prompt token when possible. |
| **Prompt token** | The actual token to insert, usually `embedding:name`. | If the token does not start with `embedding:`, Neo normalizes file-like names into that format. |
| **Target** | Chooses where the token should go. Normal mode exposes **Positive prompt** and **Negative prompt**. Expert mode can also show finish-pass targets. | Negative prompt is common for bad/anatomy embeddings. Positive prompt is for style/subject/detail embeddings. |
| **Strength** | Optional token weight. A strength of `1` emits `embedding:name`; other values emit weighted syntax like `(embedding:name:0.8)`. | Start at `1`. Lower if it overpowers the prompt. |
| **Add Embedding** | Adds the selected/manual token as a chip and attempts to append it to the selected prompt target. | Chips are also saved into metadata/replay payloads. |
| **Preview** | Shows a preview image if the embedding metadata contains one. | Not every embedding has a preview. |
| **CivitAI link** | URL used to pull metadata for the selected TI/embedding record. | Use a CivitAI model or model-version URL when available. |
| **Merge mode** | Controls how CivitAI metadata merges with local metadata. | `fill missing` is safest; overwrite modes are more aggressive. |
| **Pull from CivitAI** | Imports tags, prompts, preview data, and model details when available. | Metadata enrichment does not download or install the embedding model itself unless the backend implementation explicitly supports that later. |
| **Applied Embeddings** | Shows the current active chips. | Remove chips here when they should not affect the next generation. |
| **Status line** | Reports Ready, Disabled, Route gated, scan errors, CivitAI status, or library messages. | Read this before assuming the asset will affect generation. |

## Prompt-token behavior

Neo normalizes embedding tokens like this:

```text
my_bad_hands.pt → embedding:my_bad_hands
embedding:my_bad_hands → embedding:my_bad_hands
```

If strength is not `1`, Neo may format the token with weight:

```text
(embedding:my_bad_hands:0.8)
```

## Route support

Embeddings/TI support depends on text-encoder compatibility, not custom node discovery.

| Family / loader | Generate | Img2Img | Inpaint | Outpaint | Notes |
|---|---:|---:|---:|---:|---|
| **SDXL + checkpoint** | Ready | Ready | Ready | Ready | Main validated route. |
| **SD 1.5 + checkpoint** | Experimental | Experimental | Experimental | Experimental | Works like classic TI paths but should be validated per model. |
| **SDXL / SD1.5 component, UNet, or GGUF loaders** | Planned/gated | Planned/gated | Planned/gated | Planned/gated | Text encoder prompt-token compatibility is not promoted yet. |
| **Flux / Qwen / ZImage / HiDream component or GGUF routes** | Planned/gated | Planned/gated | Planned/gated | Planned/gated | Do not assume SD-style TI tokens work on modern text encoders. |
| **Cloud/API profiles such as Grok Imagine** | Unsupported/provider gated | Unsupported/provider gated | Unsupported/provider gated | Unsupported/provider gated | API prompt text may accept words, but Neo should not promise local TI embedding execution. |

## Common usage patterns

### Negative embedding

Use when a negative TI is designed to suppress artifacts:

```text
Target: Negative prompt
Token: embedding:bad-hands
Strength: 1
```

### Positive style/subject embedding

Use when the embedding is designed as a positive style/subject cue:

```text
Target: Positive prompt
Token: embedding:my-style
Strength: 0.8–1.0
```

## Assistant answer rules

When a user asks about Embeddings/TI:

- Say it belongs under **Image → Assets**.
- Explain that it is a prompt-token asset, not a LoRA and not a node-loader extension.
- Check the route state before saying it will execute.
- If the user has `0 records`, suggest connecting/testing ComfyUI, refreshing, or scanning `ComfyUI/models/embeddings`.
- If the user is using Qwen/Flux/ZImage/HiDream/Grok, say TI is currently gated/unsupported unless the live route explicitly says Ready.
