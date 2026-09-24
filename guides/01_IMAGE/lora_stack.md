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
version: 9
updated: 2026-09-24
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
| **Strength** | Controls LoRA influence. Neo now accepts any numeric strength that the selected LoRA/provider route can handle. | Start around `0.6–0.9` for style/character LoRAs. Increase or decrease only when the specific LoRA documentation calls for it. |
| **Pass** | Chooses **Both passes**, **Base only**, or **Finish / redraw only**. | Both is normal. Finish-only is for later finishing/redraw paths and may be preserved without direct graph execution on gated routes. |
| **Target** | Shows global or Scene Director regional target. | LoRA Stack defaults to global. Regional assignment is owned by Scene Director → Advanced Region Control → Extension Routing. |
| **Focus** | Marks/selects the active row for library/details interaction. | Use this to inspect or edit the selected row metadata. |
| **Move up/down** | Reorders LoRA rows. | Order can matter because LoRAs patch in sequence. Put broad style LoRAs before specific detail/identity LoRAs when testing. |
| **Delete row** | Removes the row from the active stack. | Does not delete the LoRA file or library metadata. |

## LoRA Library fields

| Field / control | What it does | Advice |
|---|---|---|
| **Main folder** | Filters by the first folder under the provider LoRA root, for example `Krea2`, `Flux`, or `SDXL`. | Options come from the selected provider's real catalog paths; Neo does not hardcode category names. Selecting a main folder still includes LoRAs in its nested subfolders. |
| **Subfolder** | Narrows the selected main folder to its nested folder path, for example `Characters` or `Styles / Cinematic`. | Subfolder choices are rebuilt from the selected main folder. Choose **All subfolders** to see the complete main category. |
| **Search provider LoRAs** | Filters LoRA names reported by the selected Image profile after the current folder filters. | Forge uses its Extra Networks/shared catalog; Comfy uses `LoraLoader.lora_name`. |
| **Provider LoRA** | Selects a LoRA record from the active provider catalog. | Selection focuses metadata; use **Add selected LoRA to stack** to apply it. |
| **Preview carousel** | Shows saved/CivitAI/local preview images when available. | Useful to identify the LoRA before adding it. |
| **Positive triggers** | Trigger words that should usually be added to the positive prompt. | Append when the LoRA needs activation tokens. |
| **Positive keywords** | Extra positive words from metadata or CivitAI. | Use selectively; do not blindly dump every tag into the prompt. |
| **Negative keywords** | Negative prompt helpers from metadata/CivitAI. | Add when the LoRA needs quality/anatomy guardrails. |
| **Sample prompt** | Example prompt from metadata/CivitAI. | Use **Append Prompt** to add it or **Replace Prompt** when using it as the full baseline. |
| **Add selected LoRA to stack** | Creates/updates a LoRA Stack row from the selected library record. | This is the normal path from library browsing to generation use. |
| **Edit metadata / Save metadata** | Edits local metadata record. | Saves to Neo runtime data, not the original safetensors file. |
| **Notes** | Your manual LoRA notes, usage tips, preferred strengths, caveats, or reminders. | Stored separately from imported source descriptions; CivitAI pulls do not overwrite this field. |
| **CivitAI link** | URL for metadata enrichment. | Use a CivitAI model/model-version/download URL. Neo persists the source URL and shows a **CivitAI source ↗** chip when one is saved. |
| **CivitAI merge mode** | Controls how fetched metadata merges with local data. | **fill_missing** is safest. **overwrite_selected** is aggressive. |
| **Pull from CivitAI** | Fetches triggers, tags, prompts, previews, base model info, etc. | If CivitAI returns no usable metadata, Neo should report that honestly. |


### Folder filtering behavior

LoRA folder filters are derived from portable provider catalog names. For a Comfy catalog entry such as:

```text
Krea2/Characters/hero.safetensors
```

Neo exposes:

```text
Main folder: Krea2
Subfolder: Characters
```

A deeper path such as `Krea2/Styles/Cinematic/film.safetensors` is shown as main folder `Krea2` and subfolder `Styles / Cinematic`. Files directly in the LoRA root are grouped under **Root**. Absolute backend filesystem paths are not exposed to the browser for this feature.

The same Main folder/Subfolder state is used by the **Add LoRA** picker and the **LoRA Library** browser so users do not have to search a large flat list twice.


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
| **Krea 2 RAW / Turbo** | Components or GGUF | Experimental model-only LoRA patching. For Identity/Ostris edit engines, global rows are rewired upstream of the engine runtime. Separate mode keeps the dedicated engine LoRA between global rows and the edit patch; baked mode rewires global rows directly into the edit patch. |
| **Qwen Rapid AIO** | Bundled / GGUF | Experimental where route profile supports model/clip or model-only patching. |
| **Qwen Image Edit / 2509** | Components or GGUF | Experimental for source/edit workflows where supported. |
| **Qwen Image 2.1** | Components or GGUF | **Planned/gated in Q21-0.** Q21-5 targets model-only patching with exact Qwen-2.1 compatibility proof. |
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
- Identity Edit and Ostris Edit treat their dedicated edit LoRA as an **engine-owned** asset, not a normal global LoRA Stack row. Global Krea model-only rows may still be stacked upstream of the engine runtime.
- Do not add the same engine edit LoRA again as a normal global LoRA Stack row. If those edit weights are already merged into the selected Krea model, choose **Edit Weight Source → Baked into Model** so Neo does not apply the dedicated engine LoRA a second time.
- Generic **Krea 2 Ostris Edit** is now a separate Krea edit engine. It is distinct from the Krea 2 Turbo OpenPose ControlNet adapter even though both use Ostris node classes. The generic edit engine uses its own Edit Weight Source and KV Cache settings.
- **KV Cache** belongs to the Ostris edit training contract. Leave it Off unless the selected Ostris LoRA/model explicitly documents KV-cache training/export.
- A Krea edit route showing **Ready** confirms the runtime contract, not the visual quality of a particular community LoRA or merged checkpoint. For baked models, Neo intentionally trusts the user/model metadata rather than guessing from the filename.

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


## IMG-R17A open strength range hotfix — 2026-08-14

LoRA Stack strength is no longer hard-capped in the browser or backend serialization path.

What changed:

- row strength inputs and picker strength inputs no longer clamp to `-4…4`;
- saved library metadata keeps its declared `default_strength`, `min_strength`, and `max_strength` values instead of forcing them into `-4…4`;
- Forge prompt-tag preview and provider serialization preserve the requested numeric value;
- queue payload normalization still keeps the value numeric and rounded, but it no longer forcibly shrinks a request like `5`.

This matters for LoRAs that document unusual guidance such as `1.4`, `2.5`, or `5.0`. Neo now preserves that intent instead of silently flattening it.


## 2026-08-23 — Krea 2 LoRA format normalization (Phase 1)

Krea 2 RAW/Turbo keeps the normal **LoRA Stack** UX and the existing model-only `LoraLoaderModelOnly` graph path. Neo now adds a Krea-only compatibility preflight immediately before exact Comfy catalog binding so a locally visible Krea LoRA can be inspected without changing the saved LoRA identity.

### Supported Phase-1 input formats

| Detected format | Example key | Neo behavior |
|---|---|---|
| **Comfy/native** | `diffusion_model.blocks.0.attn.wq.lora_A.weight` | Pass through unchanged. |
| **Krea diffusers / Comfy-compatible** | `transformer_blocks.0.attn.to_q.lora_down.weight` | Pass through unchanged. |
| **PEFT/native** | `base_model.model.blocks.0.attn.wq.lora_A.weight` | Normalize automatically when Neo can resolve the selected LoRA on the same local Comfy filesystem. |
| **Kohya/native** | `lora_unet_blocks_0_attn_wq.lora_down.weight` | Fail closed in Phase 1 when locally inspectable. No speculative key rewrite is performed. |
| **Unknown / partially mappable** | other vocabularies | Fail closed when locally inspectable. |

The PEFT/native rewrite is semantic, not a blind prefix replacement. Neo maps Krea module vocabulary such as:

```text
base_model.model.blocks.N.attn.wq  -> transformer_blocks.N.attn.to_q
base_model.model.blocks.N.attn.wk  -> transformer_blocks.N.attn.to_k
base_model.model.blocks.N.attn.wv  -> transformer_blocks.N.attn.to_v
base_model.model.blocks.N.attn.wo  -> transformer_blocks.N.attn.to_out.0
base_model.model.blocks.N.mlp.*    -> transformer_blocks.N.ff.*
```

PEFT `lora_A` / `lora_B` tensor names become Comfy-compatible `lora_down` / `lora_up` names. Text-fusion and Krea input/time/final-layer adapters are translated through explicit maps as well.

### Safety and cache rules

- Neo rewrites **only the safetensors header keys**. Tensor bytes, tensor shapes, strengths, and LoRA math are unchanged.
- Normalization requires every PEFT adapter tensor to map and every A/B pair to be complete. Partial conversion is rejected before queueing.
- The normalized file is deterministic and cached beside the source LoRA under `_neo_krea2_normalized/`, keyed by the source SHA-256. Reusing the same source reuses the cache.
- Neo hides `_neo_krea2_normalized` cache entries from normal LoRA discovery so users keep seeing the original asset, not implementation copies.
- Saved/replay/public metadata keeps the **original portable LoRA name**. Only the provider-submitted runtime name points at the normalized cache file.
- If Neo cannot resolve the LoRA file locally (for example, a remote Comfy profile), it does **not** pretend to inspect or rewrite the remote filesystem. Existing exact-catalog submission is preserved and the compatibility report says inspection was unavailable.
- This phase applies only to Krea 2 / Krea 2 Turbo **global base/both** LoRA Stack rows. Scene Director regional rows, finish-only rows, other image families, and Krea 2 Identity Edit's dedicated engine-owned LoRA are not rewritten by this compatibility layer.

### UI diagnostics

For Krea 2 routes, LoRA Stack shows the most recent compatibility preflight result. Guided mode summarizes normalized/native/not-inspected/blocked counts; Expert mode can inspect the structured compatibility report. A locally inspectable unsupported format is blocked before Comfy queue submission instead of allowing hundreds of `lora key not loaded` warnings while presenting the LoRA as successfully applied.

## 2026-09-23 — large-catalog browser performance

The LoRA Library keeps the restored Main folder/Subfolder filters but avoids repeated catalog-wide matching work:

- the backend creates the saved/live provider catalog bridge once per browser request instead of merge-then-bridge duplication;
- the frontend builds exact and alias lookup maps once per reconciliation pass;
- saved metadata enrichment still wins for notes, CivitAI source, prompts, previews, and triggers;
- the live selected-provider catalog remains authoritative for exact runnable LoRA names and availability.

This changes lookup cost only; provider binding and execution behavior are unchanged.

## Qwen Image 2.1 LoRA / cache ordering — implemented through Q21-6B

The architecture target from Q21-0 is now executable through Q21-5/Q21-6B. Final model ordering is:

```text
Qwen Image 2.1 base model
→ compatible global model-only LoRA rows
→ QwenImage21Cache (if enabled)
→ sampler
```

Compatibility rules:

- bind the exact selected-provider LoRA enum at submission time;
- do not infer that Qwen Image, Qwen Edit 2509/2511, or Qwen Image 2.1 LoRAs are interchangeable;
- prefer `LoraLoaderModelOnly` for the initial implementation unless a real Qwen 2.1 asset proves text-encoder patching is required;
- preserve pass targeting for High-Res without loading the same LoRA twice onto an already patched model;
- fail closed when the runtime cannot establish a safe graph anchor or exact catalog asset.


## 2026-09-24 — Qwen Image 2.1 LoRA Stack (Q21-5)

`qwen_image_21` now publishes a compiler-owned **model-only** LoRA profile using `LoraLoaderModelOnly`.

- Safetensors/components: txt2img, img2img/edit, inpaint, and outpaint are experimental.
- GGUF: only the already-active txt2img/img2img/edit routes are experimental; masked GGUF remains unsupported.
- Qwen3-VL is not patched.
- Multiple global LoRAs retain normal Neo ordering and exact provider catalog binding.
- Use LoRAs trained for Qwen Image 2.1. Neo does not assume compatibility with older Qwen/Image-Edit, SDXL, Flux, or unrelated LoRAs.
- When High-Res Lab is enabled, LoRA is applied to the base model path first and Stage 2 reuses that already-patched model path; Neo must not load the same global LoRA a second time just because refinement is active.
- When Q21-6B `QwenImage21Cache` is enabled, the LoRA extension rewires the cache node input so the final graph remains `base → LoRA(s) → cache → route patch/sampler`; cache must never be inserted before the model-only LoRA stack.


## 2026-09-24 — LoRA Library H1 identity + CivitAI reconciliation

LoRA Library metadata now uses one additive identity contract across older recovery code and newer catalog/persistence code.

Durable identity is:

```text
provider_id + normalized full provider-relative catalog path
```

For example:

```text
comfyui:krea2/characters/hero.safetensors
```

Folder structure is part of identity. Two LoRAs with the same filename in different folders are separate records. The provider's exact catalog value is retained separately in `provider_catalog_name` for live loader binding.

Compatibility rule:

```text
canonical_lora_identity(catalog_value)
→ portable provider-neutral identity

canonical_lora_identity(provider_id, catalog_value)
→ durable provider-aware identity
```

Both forms are intentional and must remain available because old recovery/startup code and newer library/catalog code coexist.

**Save metadata** now reports success only after Neo verifies the record can be reloaded from `neo_data/extensions/lora_stack/library_index.json`. Successful CivitAI imports use the same verified persistence contract.

CivitAI enrichment accepts supported `civitai.com` and `civitai.red` model/model-version links. `fill_missing` remains the safest merge mode; imported source data does not overwrite the separate manual **Notes** field unless the merge policy explicitly targets that field.

If Save or CivitAI Pull receives a non-JSON server error, the LoRA Library now reports the HTTP status and a bounded response-text preview instead of showing a misleading `Unexpected token ... is not valid JSON` parser failure.
