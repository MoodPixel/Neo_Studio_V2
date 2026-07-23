---
guide_id: image.style_stack
title: Style Stack
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generations
  - style_stack
  - styles
  - prompt
  - negative prompt
  - generate
  - img2img
  - inpaint
  - outpaint
tags:
  - image
  - style stack
  - style
  - prompt
  - csv
  - provider neutral
priority: 109
version: 1
updated: 2026-07-23
---

# Style Stack

**Style Stack** is a prompt-only Image extension. It adds saved style text and manual style text to the final positive/negative prompts before the provider runs.

It does not patch Comfy graphs, does not need custom nodes, and does not make a model route ready by itself.

## What it is for

- Reusing art/style presets.
- Keeping positive and negative style language paired together.
- Building a stack of style chips from a searchable library.
- Importing/exporting style records by CSV.
- Adding manual style text without permanently editing saved style records.

## Fields

| Field / control | What it does | Advice |
|---|---|---|
| **Apply Style Stack** | Enables style prompt merging for the current generation. | Keep enabled only when you want selected/manual style text merged into prompts. |
| **Target pass** | Chooses where the style applies: **Both base + finish**, **Base only**, or **Finish / redraw only**. | Use **Both base + finish** for normal generations. Use Base/Finish only for advanced finish workflows. |
| **Refresh** | Reaches the backend with caching disabled, synchronizes bundled defaults into the runtime library, then reloads it. | Use after adding styles to the bundled CSV or importing/editing runtime records. |
| **Export CSV** | Exports the runtime style library. | Good for backup or moving styles between Neo installs. |
| **Category** | Filters style records by category/header. | Useful when the style library is large. |
| **Search styles** | Searches style name, positive prompt, or negative prompt. | Use keywords like cinematic, anime, watercolor, neon, etc. |
| **Style library** | Lists saved style records. | Selecting/double-clicking can focus/add a style chip depending on UI behavior. |
| **Active style chips** | The styles currently queued for prompt merge. | Remove chips you do not want before generation. |
| **Add selected** | Adds the selected style to active chips. | Multiple styles can stack, but too many may create muddy/conflicting prompts. |
| **Clear stack** | Removes active style chips. | Does not delete saved style records. |
| **Style name** | Name of the selected/editing style. | Use clean names and categories for easier search. |
| **Positive style prompt** | Positive text merged into the positive prompt. | Keep this style-focused, not the full subject prompt. |
| **Negative style prompt** | Negative text merged into the negative prompt. | Add style-specific cleanup terms only. |
| **Save / Update** | Saves the edited style record. | Saves to runtime style CSV under `neo_data`. |
| **Duplicate** | Copies a style record for variation. | Useful for variants like cinematic-light vs cinematic-dark. |
| **Copy text to prompts** | Copies the selected/editor style text into the main prompt fields. | Use when you want to manually edit the merged text. |
| **Delete** | Deletes the selected style record. Bundled defaults are tombstoned so Refresh does not restore them. | Does not delete generated outputs. Save the same name again to restore it intentionally. |
| **Manual positive style** | Temporary positive style text. | Good for one-off style additions without saving a style record. |
| **Manual negative style** | Temporary negative style text. | Good for temporary cleanup terms. |
| **Import CSV** | Imports style records. | Use merge to update/add; replace overwrites the runtime library. |

## Storage and synchronization

Runtime style records live under:

```text
neo_data/extensions/image/style_stack/generation_styles.csv
```

Bundled defaults live under:

```text
neo_extensions/built_in/image.style_stack/assets/default_generation_styles.csv
```

The two files have different authority:

- **Bundled CSV:** read-only defaults shipped with Neo.
- **Runtime CSV:** the user-owned library shown and edited by Style Stack.

When Style Stack loads or Refresh is clicked, Neo synchronizes them non-destructively:

- Newly bundled style names are added to the runtime library.
- Runtime-only custom styles are kept.
- User-edited matching styles are kept.
- An untouched default can receive a later bundled prompt update.
- Deleted bundled styles stay deleted through a tombstone.
- A backup is created before a synchronization changes the runtime CSV.

Synchronization state and backups live under:

```text
neo_data/extensions/image/style_stack/bundled_runtime_sync_state.json
neo_data/extensions/image/style_stack/backups/
```

The UI reports derived counts such as:

```text
Runtime 475 · Bundled 510 · Added 35 · Preserved 4 override(s)
```

The counts come from the current CSV files; they are not tied to an old fixed seed count.

> **First Phase 6B sync:** Neo cannot reconstruct bundled-style deletions made before tombstone tracking existed. A previously deleted bundled style may reappear once as a missing default. Delete it again through Style Stack and the new tombstone will keep it removed on later Refreshes. The pre-sync runtime backup remains available.

## Route support

Style Stack is provider-neutral and prompt-only.

| Route type | Support |
|---|---|
| Generate / Img2Img / Inpaint / Outpaint | Available when the prompt pipeline is active. |
| SDXL / SD 1.5 / Flux / Qwen / ZImage / HiDream | Available as prompt text merge. |
| Comfy / Forge-like providers | Available as prompt text merge. |
| API routes | Can apply if the API profile accepts prompt text. API-specific limitations still apply. |

## Important rules

- Style Stack runs after Wildcards.
- Style Stack appends/dedupes text; it should not replace the user's subject/composition prompt.
- Style Stack is not the same as LoRA. It changes prompt text only.
- Do not use Style Stack to explain missing model/node readiness because it has no backend node dependency.

## How to explain it to users

Good answer pattern:

```text
Style Stack lets you build reusable style chips that merge into your prompt. Pick a category/search style, add it to Active style chips, choose Target pass, then generate. It changes prompt text only; it does not load a LoRA or patch Comfy.
```
