---
guide_id: image.forge_neo_strict_ux_gating
title: Forge Neo Strict UX Gating
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
  - ux-gating
  - route-authority
  - model-classification
priority: 99
version: 3
updated: 2026-07-31
---

# Forge Neo Strict UX Gating

For the canonical current Forge setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`.

Phase 5 makes the selected Forge profile's executable route intersection the only normal-UI authority for family, loader, workflow, primary-model, field, and control visibility.

Schema:

```text
neo.provider.forge_ux_gating.v1
```

Implementation:

```text
neo_app/providers/forge_neo_ux_gating.py
neo_app/image/capability_overlays.py
neo_app/static/js/neo.js
```

## Authority chain

The browser must not reconstruct Forge support from the static route matrix. The backend publishes a sanitized selected-profile UX policy derived from:

```text
Forge route authority
∩ live model/module classification
∩ required settings/scripts/endpoints
∩ implemented workflow compiler
```

Only rows whose live intersection is `selectable=true` enter `ux_gating.executable_routes`.

## Selector rules

For a selected Forge profile:

- Model Family contains only families present in executable routes.
- Main Model Type contains only loaders executable for the selected family.
- Workflow Mode contains only modes executable for the selected family/loader pair.
- Primary Model contains only exact or conservatively ambiguous model names attached to the active executable route.
- Diagnostic, implementation-target, planned, provider-gated, and unsupported rows never enter normal selectors.
- There is no fallback to the backend-neutral family manifest or static route matrix.

When a profile refresh invalidates saved state, Neo deterministically coerces the stale family/loader/mode/model selection to the closest executable route. If the intersection is empty, route selectors and generation controls are hidden and generation is blocked.

## Route-owned controls

Each executable route publishes a field policy and control policy. Examples:

- Source image and denoise appear only for image-conditioned routes.
- Explicit mask input appears only for inpaint.
- Outpaint padding and canvas controls appear only for outpaint.
- Flux guidance appears only for Flux 1 and Flux.2 Klein.
- Clip Skip, Restore Faces, Tiling, and classic Forge inpaint controls appear only where the registered classic checkpoint compiler owns them.
- Comfy-only mask-grow, latent-capture, and inpaint-context controls remain hidden.
- The generic multi-source panel remains hidden on Forge. Stitch Images appears only for Qwen Image Edit 2509 img2img/edit or Flux.2 Klein img2img when the selected profile exposes the verified `forge.image_stitch.integrated.v1` contract.
- Forge GGUF hides Comfy's manual encoder-layout and node-role controls. The GGUF file is the primary Forge model, while required encoders and AE/VAE files are route-owned native modules.
- MMProj remains optional unless a future executable route explicitly declares it required.

Global provider field policy may narrow a route further, but it must not broaden the active route policy.

## Empty state

If no executable route exists, the Image workspace displays a Forge route empty state and adds a blocking readiness item:

```text
forge_executable_route
```

Connecting Forge is not enough. The selected profile must contain a supported primary model, required modules, enabled settings/scripts, and an implemented Neo compiler.

## Public-repository rules

The UX policy contains portable model names and sanitized route metadata only. It must not serialize absolute model paths, source-image paths, credentials, headers, runtime databases, or base64 images. GitHub is reference-only for this implementation and is not modified.

## Phase 6 regression protection

The executable-only selector contract is covered by `neo.provider.forge_validation.v1`. For every sanitized profile scenario, the validator requires the UX route-key set to exactly equal the live selectable route-key set. Diagnostic, missing-requirement, setting-disabled, provider-gated, planned, and unsupported rows must remain absent from normal selectors.

Run `python scripts/validate_forge_neo_phase6.py`; treat its result as offline contract evidence only, not physical GPU validation.


## E1 ImageStitch refinement

Phase E1 does not restore the generic multi-source panel for Forge. Instead, the existing Stitch Images control becomes visible only on Qwen Image Edit 2509 img2img/edit and Flux.2 Klein img2img when the selected profile publishes the verified `forge.image_stitch.integrated.v1` contract. Image 1 stays the main source; the Stitch pair supplies provider references. Missing or drifted script metadata hides this control and the route remains single-source.
