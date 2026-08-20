---
guide_id: image.scene_director_inspector
title: Scene Director Inspector and Safety Status
surface: image
scope: built_in
applies_to:
  - image
  - scene_director
  - output_inspector
  - regional_prompting
  - regional_lora
tags:
  - scene-director
  - inspector
  - status
  - troubleshooting
priority: 103
version: 2
updated: 2026-08-16
---

# Scene Director Inspector and Safety Status

Scene Director includes read-only status information that helps explain whether a regional setup is ready, gated, or blocked. For the current family/loader/workflow matrix, use `scene_director_current.md`.

## What the Inspector tells you

The Inspector can summarize:

- the selected family, loader, and workflow;
- whether Scene Director is using the classic or modern engine;
- whether regional prompting is active;
- whether a region-targeted LoRA is active, waiting for compatibility checks, or unavailable;
- whether the selected route kept the provider's sampler and latent path intact;
- warnings or blockers that prevented Scene Director from modifying the workflow.

The Inspector is observational. It does not change generation settings.

## Common status meanings

### Available / ready

The current route supports Scene Director and the required runtime dependencies are present.

### Gated

The route is intentionally unsupported or planned-gated. Outpaint is currently planned-gated for Scene Director.

### Blocked

Neo found a safety or compatibility problem while preparing the regional workflow. It keeps the provider workflow from being silently replaced by an unsafe fallback.

### Regional LoRA preflight

Neo cannot fully confirm the LoRA-family match from metadata alone and requires the live runtime to resolve compatibility. An explicitly incompatible LoRA remains blocked.

## Safety behavior

Modern Scene Director should not silently:

- switch to another model family;
- add an unrelated extra sampler pass;
- convert a region-targeted LoRA into a global LoRA;
- route a modern family through the classic SDXL/SD1.5 engine;
- change the provider's sampler parameters or latent ownership;
- use a hidden repair/finish workflow as a fallback.

If a safe regional route cannot be prepared, Neo leaves the dependent capability unavailable and reports the reason.

## When to use the Inspector

Use it when:

- Scene Director is enabled but a region seems inactive;
- a regional LoRA is dimmed or blocked;
- a saved workflow was reopened after changing models/backends;
- a custom node or regional runtime was updated;
- you want to confirm which Scene Director engine owns the current route.

For installation and dependency recovery, see `scene_director_live_validation.md` (Scene Director Runtime Readiness and Troubleshooting).
