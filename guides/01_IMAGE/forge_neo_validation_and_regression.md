---
guide_id: image.forge_neo_validation_and_regression
title: Forge Neo Readiness and Troubleshooting
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - models
  - admin
tags:
  - forge
  - forge-neo
  - troubleshooting
  - readiness
  - connection
priority: 100
version: 3
updated: 2026-08-16
---

# Forge Neo Readiness and Troubleshooting

For normal setup and the current workflow matrix, start with `forge_neo_complete_support.md`.

Neo only shows Forge routes that the **selected Forge profile can actually report as usable**. A model family or feature can therefore disappear when Forge is reachable but a required model, module, setting, script, or Bridge capability is missing.

## Refresh Forge after changing models or extensions

After installing, removing, renaming, or updating Forge models/modules/extensions:

1. Restart Forge when the Forge change requires it.
2. Open **Admin → Backends → Image**.
3. Select the Forge / Forge Neo profile.
4. Run the available **Connect/Test** or **Refresh Forge Admin** action.
5. Return to **Image** and reselect the family, loader, workflow, and model.

Old capability snapshots are intentionally not treated as proof that a newly changed Forge installation can still run the same route.

## If a model family is missing

Check these in order:

- the intended Forge profile is selected;
- Forge is connected with API access enabled;
- the primary model is visible to that Forge installation;
- required text encoders and VAE/AE modules are available for modern families;
- the selected loader matches the model package type, such as checkpoint, components/safetensors, or GGUF;
- any route-specific Forge setting is enabled;
- Forge was refreshed after the installation changed.

Neo does not borrow a model or module from another Image backend to make a Forge route appear.

## If a control or extension is missing

Features such as ControlNet, ADetailer, ImageStitch references, Forge Couple, native post-Hires, or other Forge extension-backed controls appear only when the selected profile reports the required live capability.

If the feature was recently installed or updated:

1. restart Forge;
2. refresh the Forge profile in Neo Admin;
3. reopen the Image workflow;
4. check the control tooltip or Output Inspector diagnostic text for the missing requirement.

## Connected with warnings

A **Connected with warnings** state can still be usable. It normally means Forge's core generation API is reachable but one or more optional discovery or extension capabilities are unavailable. Neo gates the affected feature instead of disabling every Forge route.

## If generation is blocked before queueing

Neo fails closed when the selected route and the live backend no longer agree. Common causes include:

- a model or encoder was renamed or removed;
- a required Forge script/extension is missing;
- a route-specific setting is disabled;
- a Bridge-only operation is selected without a compatible Bridge;
- a stale selection survived a backend/profile change;
- the chosen workflow is not supported for that family/loader combination.

Refresh the profile first. If the route still stays unavailable, switch only to an option Neo exposes as ready rather than forcing a hidden/unsupported combination.

## What to include when asking for help

Share:

- Forge build/version;
- Neo version/source snapshot;
- selected Image profile;
- family, loader, workflow mode, and model filename;
- required encoder/VAE filenames for modern families;
- Bridge status if the feature depends on it;
- the visible Neo error/disabled reason;
- GPU and VRAM when the failure happens during model loading or generation.

Redact credentials and unnecessary personal filesystem paths.
