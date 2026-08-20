---
guide_id: image.sampling_preset_inspector
title: Sampling Preset Inspector and Recovery
surface: image
scope: built_in
applies_to:
  - image
  - parameters
  - sampling_presets
  - output_inspector
tags:
  - sampling
  - presets
  - parameters
  - inspector
  - troubleshooting
priority: 101
version: 2
updated: 2026-08-16
---

# Sampling Preset Inspector and Recovery

Sampling presets help populate compatible sampling values for the selected Image family, loader, variant, and workflow. Your explicit Parameters remain authoritative where the route allows them.

## Preset choices

Neo can expose built-in presets, user presets, Provider Defaults, or Clean Slate depending on the current route.

- **Built-in preset** — applies a known set of values for the matching route.
- **User preset** — applies a saved user-authored preset for its compatible route.
- **Provider Defaults** — keeps the provider's normal behavior while preserving explicit values you set in Neo.
- **Clean Slate** — avoids silently inheriting a previous preset's values.

## Workflow-aware behavior

A preset is resolved against the current family, model variant, loader, and workflow. Switching from Generate to Img2Img/Edit/Inpaint/Outpaint can change which preset values are applicable.

Neo should not carry Txt2Img-only dimensions into a masked/source-image workflow just because the same preset name was used earlier.

## Negative prompt availability

Negative Prompt availability can change with the effective family/guidance route. If Neo disables Negative Prompt for the selected route, a preset does not override that restriction.

## Output Inspector

Output Inspector can show the effective sampling setup used for a generated result, including:

- route/family context;
- selected preset;
- inherited versus explicit sampling values;
- output-intent layer;
- negative-prompt eligibility;
- any preset warning or blocked state.

This is useful when a reopened or replayed result does not look like the current Parameters panel.

## User preset storage

User sampling presets are stored under:

```text
neo_data/image/sampling_presets
```

They are local runtime data and should be preserved when moving or upgrading Neo if you want to keep your custom presets.

## If a preset becomes unavailable

Check:

1. current family and model variant;
2. loader type;
3. workflow mode;
4. whether the selected backend/profile changed;
5. whether the saved user preset was created for a different route.

Choose a compatible preset or edit/save a new user preset for the current route. Neo should not force an incompatible preset onto the workflow.
