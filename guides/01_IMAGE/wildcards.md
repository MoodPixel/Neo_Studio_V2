---
guide_id: image.wildcards
title: Wildcards
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generations
  - wildcards
  - wildcard
  - prompt
  - queue variants
  - randomization
  - generate
  - img2img
  - inpaint
  - outpaint
tags:
  - image
  - wildcards
  - prompt
  - randomize
  - variants
  - seeded
  - provider neutral
priority: 108
version: 1
updated: 2026-07-09
---

# Wildcards

**Wildcards** is a prompt-only Image extension that expands tokens into text variants before generation.

It supports token syntax such as:

```text
__token__
__folder/token__
{soft|dramatic|cinematic}
```

Wildcards resolve before Style Stack.

## What it is for

- Creating prompt variation without rewriting the full prompt.
- Randomizing style, location, lighting, clothing, camera, colors, etc.
- Generating multiple queue variants from one prompt shell.
- Keeping token libraries organized under Neo runtime data.

## Fields

| Field / control | What it does | Advice |
|---|---|---|
| **Enable Wildcards** | Enables wildcard resolution for generation. | Disable if you want literal tokens to remain unchanged. |
| **Insert target** | Chooses whether inserted tokens go into the Positive Prompt or Negative Prompt. | Most tokens go into Positive Prompt; cleanup/avoidance lists can go into Negative Prompt. |
| **Preview count** | Number of preview resolutions to generate. | Use small values while testing. |
| **Queue variants** | Number of resolved variants to queue/generate. | Use carefully. Higher values can create many outputs. |
| **Refresh** | Reloads wildcard library files. | Use after editing/importing tokens. |
| **Export ZIP** | Exports the wildcard pack. | Good for backup or moving libraries. |
| **Auto-resolve on generation** | Automatically replaces tokens at generation time. | Keep on for normal wildcard use. Turn off if manually applying a preview. |
| **Use generation seed** | Resolves choices deterministically from the generation seed. | Keep on when you want replayable variants. Turn off for freer randomness. |
| **Root** | Shows runtime wildcard library root. | Normal root is `neo_data/extensions/image/wildcards/library`. |
| **Search tokens** | Searches token/path names. | Use this to find a token file quickly. |
| **Wildcard files** | Lists wildcard token files. | Selecting a file loads its values. |
| **Values preview** | Shows values inside the selected wildcard. | Confirms what the token can resolve into. |
| **Insert token** | Inserts the selected token into the chosen prompt field. | Inserts token syntax, not necessarily the resolved text. |
| **Preview resolve** | Shows resolved text variants without queueing generation. | Use before generating to avoid bad random combinations. |
| **Apply first result** | Applies the first preview result into the prompt. | Use when you like a preview and want fixed text instead of token syntax. |
| **Token name** | Edits the wildcard token path/name. | Example: `style/cinematic`. |
| **Format** | Chooses TXT, JSON, YAML, or YML. | TXT is simplest: one value per line. |
| **Wildcard values** | Values this token can resolve to. | Keep one idea/value per line for TXT. |
| **Save / Update** | Saves the token file. | Saves to runtime wildcard library under `neo_data`. |
| **Delete** | Deletes the selected wildcard token file. | Does not delete outputs. |
| **Import wildcard ZIP** | Imports a wildcard pack. | Merge adds/updates; replace overwrites the runtime library. |
| **Resolved preview** | Shows generated/resolved preview results. | Use this to check what will actually be sent to the prompt pipeline. |

## Storage

Runtime wildcard libraries live under:

```text
neo_data/extensions/image/wildcards/library
```

Supported file types:

```text
.txt, .json, .yaml, .yml
```

## Route support

Wildcards is provider-neutral and prompt-only.

| Route type | Support |
|---|---|
| Generate / Img2Img / Inpaint / Outpaint | Available when the prompt pipeline is active. |
| SDXL / SD 1.5 / Flux / Qwen / ZImage / HiDream | Available as prompt text resolution. |
| Comfy / Forge-like providers | Available as prompt text resolution. |
| API routes | Can apply if the API profile accepts prompt text. API-specific limitations still apply. |

## Important rules

- Wildcards runs before Style Stack.
- Wildcards changes prompt text only; it does not load models, LoRAs, ControlNet, or IP Adapter.
- Seeded resolution helps replay because the same generation seed can reproduce the same wildcard choices.
- If a token is missing, the Assistant should explain that the wildcard file/root/token may be absent rather than invent values.

## How to explain it to users

Good answer pattern:

```text
Wildcards lets you place tokens like __style/cinematic__ in the prompt, then Neo resolves them into actual text before generation. Use Preview resolve to check variants first, keep Use generation seed on for replayable results, and use Queue variants only when you want multiple prompt variations.
```
