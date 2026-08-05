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
  - forge
  - comfyui
tags:
  - image
  - assets
  - embeddings
  - textual inversion
  - provider aware
  - selected profile
  - route aware
priority: 114
version: 2
updated: 2026-08-02
---

# Embeddings / Textual Inversion

**Embeddings / Textual Inversion** is a built-in Image **Assets** extension. It stores a canonical embedding identity and lets the selected Image provider decide how that identity is written into the submitted prompt.

```text
Image → Assets → Embeddings / Textual Inversion
```

It is not a LoRA loader and does not add a dedicated Comfy node. It is a prompt asset whose syntax depends on the provider.

## Provider formatting

Neo stores a plain canonical trigger:

```text
EasyNegative
```

At submission time it renders the provider syntax:

| Provider | Strength 1 | Strength 1.2 |
|---|---|---|
| **Forge Neo** | `EasyNegative` | `(EasyNegative:1.2)` |
| **ComfyUI** | `embedding:EasyNegative` | `(embedding:EasyNegative:1.2)` |

The visible positive and negative prompt fields are not permanently rewritten when a chip is added. Provider syntax exists only in the compiled request or Comfy workflow copy.

## Selected-profile catalog ownership

The currently selected Image profile is authoritative:

```text
Selected Forge profile → Forge embedding catalog
Selected Comfy profile → Comfy embedding catalog
```

Neo does not borrow an embedding catalog from another connected profile. The saved default Image profile is used only by older callers that do not provide a selected profile ID.

Catalog records sent to the browser contain portable names only. Absolute model roots and personal paths remain server-side.

## Core controls

| Control | Behavior |
|---|---|
| **Apply Embeddings/TI** | Enables the canonical embedding chips for the current workflow. |
| **Refresh** | Reloads the selected provider's embedding catalog. |
| **Scan Folder** | Optionally imports metadata from a local embeddings folder when provider discovery is incomplete. |
| **Search** | Filters saved and selected-provider records. |
| **Embedding** | Chooses a provider catalog or saved-library record. |
| **Canonical trigger** | Plain provider-neutral identity such as `EasyNegative`. Legacy `embedding:` prefixes and file extensions are normalized away. |
| **Target** | Applies to Positive prompt, Negative prompt, or both. Expert finish targets remain visible only where supported. |
| **Strength** | Sets prompt weighting from `0` to `2`. |
| **Add Embedding** | Adds or updates one canonical chip. It does not modify the visible prompt. |
| **CivitAI link** | Enriches metadata and previews; it does not install the model. |

## Identity normalization and duplicate handling

These values resolve to the same embedding identity:

```text
EasyNegative
embedding:EasyNegative
(EasyNegative:1.2)
(embedding:EasyNegative:1.2)
folder/EasyNegative.safetensors
<EMBEDDINGS_ROOT>/EasyNegative.pt
```

Neo deduplicates by canonical identity and prompt target. An existing weighted or unweighted Forge/Comfy variant is not appended a second time.

## Target behavior

- **Positive prompt** — style, subject, detail, or concept embeddings.
- **Negative prompt** — artifact/anatomy suppression embeddings.
- **Positive + negative** — compiles the same canonical asset into both prompt targets.
- **Finish positive / finish negative** — retained for expert/replay compatibility, but Forge finish-only TI routing remains fail-closed until a separate native finish-prompt contract exists.

## Route support

| Provider / route | State | Notes |
|---|---|---|
| Forge Neo SDXL/SD1.5 checkpoint | Available when the selected profile exposes the embedding catalog/capability | Uses plain Forge trigger syntax. |
| ComfyUI SDXL checkpoint | Available | Uses `embedding:` syntax in prompt text nodes. |
| ComfyUI SD1.5 checkpoint | Experimental | Same compile path, but visual parity should be checked per model. |
| Component, UNet, GGUF, Flux, Qwen, Z-Image, HiDream | Gated unless the active route explicitly validates TI | Neo does not assume classic textual inversion works with modern tokenizers. |
| Cloud/API Image profiles | Provider-gated | A word accepted by an API prompt is not proof that a local TI file was loaded. |

## Troubleshooting

**The list is empty:** verify the selected backend profile, refresh its Admin capability snapshot, then refresh the library. A manual folder scan is a fallback for metadata discovery, not permission to switch providers.

**The prompt field did not change:** correct. Phase 9 keeps the visible prompt clean and adds provider syntax during compilation.

**Forge generated without the expected effect:** confirm the embedding is installed in the selected Forge process and appears in that profile's `/sdapi/v1/embeddings` capability snapshot.

**Comfy did not load it:** confirm the embedding is in the selected Comfy installation's embeddings folder and the active route is a validated checkpoint prompt-token route.
