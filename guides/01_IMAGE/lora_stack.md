---
guide_id: image.lora_stack
title: LoRA Stack and LoRA Library
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generations
  - assets
  - reference
  - finish
  - lora_stack
  - lora_library
  - lora
  - civitai
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
  - lora
  - lora stack
  - lora library
  - civitai
  - triggers
  - route aware
  - loader aware
priority: 113
version: 1
updated: 2026-07-09
---

# LoRA Stack and LoRA Library

The **LoRA Stack** controls which LoRA files are requested for a generation. The **LoRA Library** manages metadata for the selected LoRA files, such as previews, triggers, keywords, sample prompts, and CivitAI details.

These are connected but not the same:

| Area | Purpose |
|---|---|
| **LoRA Stack** | Adds active LoRA rows to the generation payload and, when route-ready, patches the Comfy graph. |
| **LoRA Library** | Browses Comfy `LoraLoader.lora_name` choices and stores metadata/prompt helpers for LoRA records. |

## LoRA Stack fields

| Field / control | What it does | Advice |
|---|---|---|
| **Apply LoRA Stack** | Enables the active rows for this generation. | Keep off if no rows are needed. Enable after adding at least one valid LoRA row. |
| **Add LoRA Row** | Adds another LoRA slot. | Use multiple rows only when the LoRAs are compatible. Too many can muddy style/identity. |
| **Clean Empty/Disabled** | Removes rows that are empty or disabled. | Use this before saving/replaying a clean setup. |
| **Use** | Enables/disables a row without deleting it. | Good for A/B testing. |
| **LoRA** | Chooses the LoRA file/name from the connected Comfy catalog or library record. | If a LoRA shows missing from Comfy, connect/test Comfy or verify the file is in the backend LoRA folder. |
| **Strength** | Controls LoRA influence. Values are clamped roughly from -4 to 4. | Start around 0.6–0.9 for style/character LoRAs. Lower if it overpowers the base model. |
| **Pass** | Chooses **Both passes**, **Base only**, or **Finish / redraw only**. | Both is normal. Finish-only is for later finish/redraw paths and may be preserved without direct graph execution. |
| **Target** | Shows global or Scene Director regional target. | LoRA Stack defaults to global. Regional assignment is owned by Scene Director’s extension routing. |
| **Focus** | Marks/selects the active row for library/details interaction. | Use this to inspect or edit the selected row metadata. |
| **Move up/down** | Reorders LoRA rows. | Order can matter because LoRAs patch in sequence. Put broad style LoRAs before specific detail/identity LoRAs when testing. |
| **Delete row** | Removes the row. | Does not delete the LoRA file or library metadata. |

## LoRA Library fields

| Field / control | What it does | Advice |
|---|---|---|
| **Search Comfy LoRAs** | Filters LoRA names from the connected Comfy backend catalog. | Connect/test Comfy first so `LoraLoader.lora_name` choices are populated. |
| **Comfy LoRA** | Selects a LoRA catalog record. | Selection focuses metadata; use **Add selected LoRA to stack** to apply it to generation. |
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

## Route support

LoRA Stack is route-aware and loader-aware. It only mutates the graph when the compiler exposes safe model/clip patch points.

| Family | Loader | Workflow support |
|---|---|---|
| **SDXL** | Checkpoint | Available for Generate, Img2Img, Inpaint, Outpaint. |
| **SD 1.5** | Checkpoint | Experimental for Generate, Img2Img, Inpaint, Outpaint. |
| **Flux 1** | Components or GGUF | Experimental where compiler-owned LoRA patch profile exists. |
| **Flux 2 Klein** | Components or GGUF | Experimental, including edit routes where route matrix exposes them. |
| **Qwen Rapid AIO** | Bundled / GGUF | Experimental where route profile supports model/clip or model-only patching. |
| **Qwen Image Edit / 2509** | Components or GGUF | Experimental for source/edit workflows where supported. |
| **ZImage / ZImage Turbo** | Components or GGUF | Experimental for non-edit image routes. |
| **HiDream** | Components or GGUF | Generate is experimental; image-conditioned modes are planned/gated. |
| **Cloud/API routes** | API model | Not a LoRA graph route unless the API/backend adds explicit LoRA support. |

## Important rules

- LoRA Library metadata does not apply a LoRA by itself. The LoRA must be in the LoRA Stack and enabled.
- Regional LoRA targets are preserved in payload/replay, but Scene Director owns region assignment.
- If the route is gated, Neo may preserve the user's LoRA intent in metadata without mutating the graph.
- Do not mix SDXL LoRAs with incompatible model families unless the user is intentionally testing and understands the risk.

## How to explain it to users

Good answer pattern:

```text
Use LoRA Library to find and enrich the LoRA, then use Add selected LoRA to stack. In LoRA Stack, enable the row, choose strength, and keep Pass on Both passes for normal generations. On your current route it is [ready/experimental/gated], so direct graph execution is [available/not available].
```
