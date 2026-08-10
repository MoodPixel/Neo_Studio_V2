---
guide_id: image.parameter_truth
title: Image Parameter Truth
surface: image
scope: built_in
applies_to:
  - image_workspace
  - parameters
  - comfyui
  - comfyui_portable
  - forge
  - lanpaint
tags:
  - parameters
  - steps
  - cfg
  - sampler
  - scheduler
  - denoise
  - batch
  - seed
  - resolution
priority: 120
version: 2
updated: 2026-08-07
---

# Image Parameter Truth

Neo treats an explicit user-entered Image parameter as authoritative. Family defaults, sampling presets, route recommendations, and compiler defaults may fill a value only when the user did not submit that field.

## Core rule

The runtime order is:

1. read the current Parameters value;
2. normalize its type without changing its numeric meaning;
3. validate backend compatibility;
4. compile the same value into the workflow;
5. report the applied value in runtime metadata.

Neo must not silently replace an explicit value because another value is considered safer, more typical, faster, or more faithful to a family preset.

This contract covers the shared Image sampling controls: **Steps, CFG, Sampler, Scheduler, Denoise, Batch Count, Seed, Width, and Height**, plus route-specific guidance controls such as **Flux Guidance** when they are exposed.

## Defaults and presets

Defaults are fallback values, not runtime locks. A preset can intentionally populate controls when the user selects it, but the queue boundary does not reapply the preset over fields the user subsequently edits.

- **Provider Defaults** delegates only fields that are actually missing. Explicit values remain in the job.
- **Clean Slate** clears preset-owned values when selected and then preserves manual values.
- **Balanced or other numeric presets** fill missing values at submission; they do not overwrite explicit current values.

## Family behavior

Recommended family settings remain useful starting points, but they are not enforced over explicit user input.

- Krea 2 RAW and Turbo preserve manual Steps and CFG. Turbo defaults are used only when the fields are absent.
- Z-Image Base and Turbo preserve low or unusual Steps/CFG instead of clamping them into another variant's recommended range.
- Flux and Flux2 keep **Flux Guidance** and sampler **CFG** as separate editable controls. Neo does not force sampler CFG to `1` when the user supplies another value.
- Qwen native, GGUF, Edit, Rapid AIO, HiDream, SD checkpoints, Anima, and Ideogram routes preserve their submitted sampling values.
- LanPaint uses the main Parameters sampling values as the shared sampling source. LanPaint-specific crop, mask, stitch, thinking, and prompt-mode controls remain separate.
- Forge preserves explicit resolution and sampling values instead of silently snapping/clamping them. Unsupported values are validated rather than rewritten.
- LanPaint and source-derived Comfy image routes preserve Batch Count by repeating the encoded/masked latent when a batch greater than one is requested.

## Validation instead of substitution

If a backend has a hard requirement, Neo should surface a validation error or backend error. A recommendation may generate a warning. Neither is permission to mutate the user's request silently.

The only automatic value generation that remains normal is when the user deliberately requests an automatic value, such as a negative/random seed, or when a field is genuinely omitted and a default is needed.

## Runtime integrity trace

Neo now records these layers automatically through the **Image Parameter Integrity** contract. The trace starts from the UI state before route synchronization, records the client/API/NeoJob boundaries, and then verifies provider-applied values against the final Comfy graph or Forge request payload.

For ComfyUI and Forge, a concrete mismatch is blocked before provider queueing instead of being allowed to generate with a silently changed value. Fields that cannot be represented by a later provider boundary are marked unverified rather than guessed.

See **Image Parameter Integrity** for the tracked fields, boundary semantics, Output Inspector status card, and diagnostic JSON.

## Debugging

When a result appears to use a different parameter than the UI, check the **Parameter Integrity** card in Output Inspector. The trace records:

- UI before payload build;
- client payload;
- API received / normalized values;
- authoritative NeoJob values;
- provider `actual_params`;
- final workflow/request values when observable.

A proven concrete mismatch between those layers is a bug under this contract and is blocked before local provider queueing.

## Multi-KSampler stage parameters

Multi-KSampler Stage 2/3 settings follow the same Parameter Truth rule as Stage 1. Neo may provide initial suggestion values, but explicit stage Steps, CFG, Sampler, Scheduler, Denoise, and seed-policy choices are not rewritten at compile time. Unsupported custom-sampler routes fail validation instead of silently dropping the requested stages.

## Phase 6 Multi-KSampler transitions

The Parameter Truth rule also applies to Multi-KSampler Stage 2/3 and their inter-stage transforms. Explicit Stage Steps, CFG, Sampler, Scheduler, Denoise, seed policy, Sampler Engine, latent-upscale Scale, and latent-upscale Method are never replaced by family recommendations. Invalid values fail validation instead of being silently rewritten.
