---
guide_id: image.lora_stack
title: LoRA Stack and LoRA Library
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - assets
  - lora_stack
  - lora_library
  - lora
  - civitai
  - sdxl
  - sd15
  - flux
  - flux2_klein
  - krea2
  - krea2_turbo
  - qwen_rapid_aio
  - qwen_image_edit
  - qwen_image_edit_2509
  - z_image
  - z_image_turbo
  - hidream
tags:
  - image
  - assets
  - lora
  - lora stack
  - lora library
  - civitai
  - triggers
  - route aware
  - loader aware
priority: 115
version: 6
updated: 2026-08-07
---

# LoRA Stack and LoRA Library

The **LoRA Stack** and **LoRA Library** belong to **Image → Assets**.

LoRAs are reusable model assets. They should be explained as asset selection and asset routing first, not as normal base Generation parameters.

```text
Image → Assets → LoRA Stack / LoRA Library
```

## Stack vs Library

| Area | Purpose |
|---|---|
| **LoRA Stack** | Active LoRA rows requested for the next generation. Rows can target base/both/finish passes and can preserve Scene Director regional intent. |
| **LoRA Library** | Metadata/catalog browser for LoRA files: previews, triggers, keywords, sample prompts, CivitAI data, and local notes. |

LoRA Library metadata does **not** apply a LoRA by itself. To affect a generation, the LoRA must be added to **LoRA Stack** and the stack must be enabled.

## LoRA Stack fields

| Field / control | What it does | Advice |
|---|---|---|
| **Apply LoRA Stack** | Enables the active rows for this generation. | Keep off if no rows are needed. Enable after adding at least one valid LoRA row. |
| **Add LoRA** | Opens a searchable picker populated by the currently selected Image provider/profile. | Select one LoRA and strength, then add it. Multiple compatible LoRAs can still be stacked. |
| **Clean Empty/Disabled** | Removes rows that are empty or disabled. | Use this before saving/replaying a clean setup. |
| **Use** | Enables/disables a row without deleting it. | Good for A/B testing. |
| **LoRA** | Chooses the provider-neutral catalog name stored in the stack. | The dropdown is rebuilt from the selected provider. Missing entries are shown honestly and are never borrowed from another profile. |
| **Strength** | Controls LoRA influence. Values are clamped roughly from `-4` to `4`. | Start around `0.6–0.9` for style/character LoRAs. Lower if it overpowers the base model. |
| **Pass** | Chooses **Both passes**, **Base only**, or **Finish / redraw only**. | Both is normal. Finish-only is for later finishing/redraw paths and may be preserved without direct graph execution on gated routes. |
| **Target** | Shows global or Scene Director regional target. | LoRA Stack defaults to global. Regional assignment is owned by Scene Director → Advanced Region Control → Extension Routing. |
| **Focus** | Marks/selects the active row for library/details interaction. | Use this to inspect or edit the selected row metadata. |
| **Move up/down** | Reorders LoRA rows. | Order can matter because LoRAs patch in sequence. Put broad style LoRAs before specific detail/identity LoRAs when testing. |
| **Delete row** | Removes the row from the active stack. | Does not delete the LoRA file or library metadata. |

## LoRA Library fields

| Field / control | What it does | Advice |
|---|---|---|
| **Search provider LoRAs** | Filters LoRA names reported by the selected Image profile. | Forge uses its Extra Networks/shared catalog; Comfy uses `LoraLoader.lora_name`. |
| **Provider LoRA** | Selects a LoRA record from the active provider catalog. | Selection focuses metadata; use **Add selected LoRA to stack** to apply it. |
| **Preview carousel** | Shows saved/CivitAI/local preview images when available. | Useful to identify the LoRA before adding it. |
| **Positive triggers** | Trigger words that should usually be added to the positive prompt. | Append when the LoRA needs activation tokens. |
| **Positive keywords** | Extra positive words from metadata or CivitAI. | Use selectively; do not blindly dump every tag into the prompt. |
| **Negative keywords** | Negative prompt helpers from metadata/CivitAI. | Add when the LoRA needs quality/anatomy guardrails. |
| **Sample prompt** | Example prompt from metadata/CivitAI. | Use **Append Prompt** to add it or **Replace Prompt** when using it as the full baseline. |
| **Add selected LoRA to stack** | Creates/updates a LoRA Stack row from the selected library record. | This is the normal path from library browsing to generation use. |
| **Edit metadata / Save metadata** | Edits local metadata record. | Saves to Neo runtime data, not the original safetensors file. |
| **CivitAI link** | URL for metadata enrichment. | Use a CivitAI model/model-version/download URL. |
| **CivitAI merge mode** | Controls how fetched metadata merges with local data. | **fill_missing** is safest. **overwrite_selected** is aggressive. |
| **Pull from CivitAI** | Fetches triggers, tags, prompts, previews, base model info, etc. | If CivitAI returns no usable metadata, Neo should report that honestly. |


## Provider-aware catalog and serialization

Neo stores LoRA rows in a provider-neutral form:

```json
{
  "name": "characters/hero.safetensors",
  "strength": 0.8,
  "target": "both",
  "apply_to": "global"
}
```

The selected Image profile owns both catalog discovery and execution.

| Provider | Catalog authority | Execution serialization |
|---|---|---|
| **Forge Neo** | Live `/sdapi/v1/loras` when available, supplemented only by verified shared model paths referenced by that Forge process. | Compiles at submission into the positive prompt, for example `<lora:hero:0.8>`. The visible prompt is not modified. |
| **ComfyUI** | The selected profile's `LoraLoader.lora_name` choices. | Keeps the canonical row and applies it through compiler-owned LoRA loader nodes. No prompt tag is inserted. |

Rules:

- The currently selected Image profile takes priority over the saved default.
- Neo does not search another provider when the selected profile has no LoRAs.
- Switching providers preserves canonical LoRA rows but changes the displayed provider syntax.
- Existing Forge prompt tags are deduplicated against stack rows using path-, extension-, and case-insensitive identity matching.
- Absolute backend paths stay server-side. Browser records and saved public metadata use portable catalog names only.
- Forge supports global base/both rows. Regional and finish-only rows remain preserved but fail closed for direct Forge base generation.

## Route support

LoRA Stack is route-aware and loader-aware. It only mutates the graph when the compiler exposes safe model/clip patch points.

| Family | Loader | Workflow support |
|---|---|---|
| **SDXL** | Checkpoint | Available for Generate, Img2Img, Inpaint, Outpaint. |
| **SD 1.5** | Checkpoint | Experimental for Generate, Img2Img, Inpaint, Outpaint. |
| **Flux 1** | Components or GGUF | Experimental where compiler-owned LoRA patch profile exists. |
| **Flux 2 Klein** | Components or GGUF | Experimental, including edit routes where route matrix exposes them. |
| **Krea 2 RAW / Turbo** | Components or GGUF | Experimental model-only LoRA patching. With Identity Edit enabled, global rows are rewired before the dedicated Identity Edit LoRA and `Krea2EditModelPatch`. |
| **Qwen Rapid AIO** | Bundled / GGUF | Experimental where route profile supports model/clip or model-only patching. |
| **Qwen Image Edit / 2509** | Components or GGUF | Experimental for source/edit workflows where supported. |
| **ZImage / ZImage Turbo** | Components or GGUF | Experimental for non-edit image routes. |
| **HiDream** | Components or GGUF | Generate is experimental; image-conditioned modes are planned/gated. |
| **Cloud/API routes** | API model | Not a LoRA graph route unless the API/backend adds explicit LoRA support. |

## Important rules

- LoRA Stack is documented as an **Assets** tool. Do not describe it as a base Generation panel.
- LoRA Library metadata does not apply a LoRA by itself. The LoRA must be in the LoRA Stack and enabled.
- Regional LoRA targets are preserved in payload/replay, but Scene Director owns region assignment.
- If the route is gated, Neo may preserve the user's LoRA intent in metadata without mutating the graph.
- Forge prompt syntax is generated only during provider compilation; do not manually duplicate generated tags in the visible prompt.
- Do not mix SDXL LoRAs with incompatible model families unless the user is intentionally testing and understands the risk.
- Krea 2 Identity Edit's dedicated edit LoRA is an engine-owned required asset, not a normal LoRA Stack row. Global Krea model-only rows may still be stacked; Neo orders them before the dedicated edit LoRA so the custom model patch wraps the final model state.

## How to explain it to users

Good answer pattern:

```text
Go to Image → Assets. Use Add LoRA or LoRA Library to select from the active provider catalog, then add it to the stack. In LoRA Stack, enable the row, choose strength, and keep Pass on Both passes for normal generations. On your current route it is [ready/experimental/gated], so direct graph execution is [available/not available].
```


## Phase 19 exact Comfy catalog binding

Neo stores a portable LoRA identity but ComfyUI validates `lora_name` against the exact enum value published by the selected loader node in live `object_info`.

```text
Saved/replay identity:  Krea2/Style.safetensors
Live Comfy enum:        Krea2\Style.safetensors
Submitted graph value:  Krea2\Style.safetensors
```

The portable value is used for presets, replay, migration, and public metadata. It is never assumed to be safe for direct Comfy graph submission. Immediately before graph mutation Neo now resolves every explicitly enabled row against the exact live catalog belonging to the selected loader class:

- `LoraLoader` for model-and-CLIP routes;
- `LoraLoaderModelOnly` for model-only routes.

Matching may normalize slash direction and case only to find the provider entry. The graph always receives the original exact provider string. Basename-only fallback, adjacent-profile borrowing, and cross-loader catalog borrowing are forbidden.

An explicit LoRA request is fail-closed across Generate, Img2Img, Native Inpaint, LanPaint Inpaint, and Outpaint. If the exact entry, loader node, compiler patch profile, or graph anchor cannot be proven, Neo blocks before queueing instead of silently running the base workflow.

Successful execution metadata records:

- portable requested name;
- exact submitted provider name;
- loader class and node IDs;
- strength values;
- original and patched model/CLIP references;
- rewired consumers;
- provider/profile, family, loader, workflow mode, and inpaint engine;
- catalog verification and execution state.

Replay keeps only the portable identity and rebinds it against the current live provider catalog. A provider/profile switch or changed Comfy catalog therefore requires revalidation.


## Phase 8 LoRA Stack UX cleanup

The normal LoRA Stack card is intentionally focused on LoRA controls only.

- Do not show Native Inpaint/LanPaint architecture explanations inside the LoRA card. Engine independence remains a backend compatibility rule, not normal LoRA help copy.
- Do not render the full provider serialization string beside every LoRA row. The selected LoRA identity is already visible in the row and its summary.
- Provider serialization, route identity, LoRA loader mode, and catalog/debug details belong to **Expert** mode.
- Regional ownership guidance is reduced to Expert-only help; normal rows show only the chosen pass and target.
- Route-gated LoRA states use short artist-facing messages instead of matrix/route-key diagnostics.

This is presentation-only. Exact provider catalog binding, fail-closed execution, ordering, strength, pass, target, and replay behavior remain unchanged.


## CivitAI catalog reconciliation hotfix — 2026-08-08

LoRA Library metadata and live provider availability are separate authorities:

```text
CivitAI / saved library metadata
  → triggers, keywords, prompts, previews, notes, base-model metadata

Selected Image provider catalog
  → whether the LoRA is currently runnable and the exact provider catalog name
```

CivitAI Pull must never make an installed LoRA disappear merely because an enriched saved record contains stale `catalog_available` state. Neo now reconciles saved metadata with the live selected-provider catalog before building LoRA picker/stack options.

Rules:

- Records created directly from a live provider catalog are explicitly `catalog_available: true`.
- A matching live provider record wins for `catalog_available`, `catalog_name`, provider id/label, and catalog source.
- Saved metadata continues to win for enrichment fields such as triggers, keywords, previews, notes, sample prompts, and CivitAI metadata.
- CivitAI Pull refreshes the selected provider browser immediately after enrichment so the UI is rebound to current live catalog truth.
- Old saved records incorrectly carrying `catalog_available: false` are repaired in the active browser view whenever the selected provider still advertises that LoRA.
- Exact execution remains fail-closed against the live provider loader enum; this hotfix does not invent a LoRA or fall back to another provider.

For a LoRA that is installed and visible before CivitAI enrichment, the expected sequence is now:

```text
Live provider catalog
  → LoRA visible
  → CivitAI Pull enriches saved metadata
  → provider catalog is refreshed/reconciled
  → same LoRA remains visible and selectable
```
