---
guide_id: image.reference
title: Image Reference Workspace
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - reference
  - controlnet
  - ip_adapter
  - faceid
  - reference images
  - img2img
  - inpaint
  - outpaint
  - edit
tags:
  - image
  - reference
  - controlnet
  - ip adapter
  - faceid
  - identity
  - structure
  - control image
  - reference image
  - route aware
  - loader aware
priority: 119
version: 1
updated: 2026-07-09
---

# Image Reference Workspace

The Image **Reference** workspace owns reference-based generation controls. It is where Neo keeps tools that guide the image with external images, maps, identity references, structure references, and source-aware edit helpers.

Use this guide when the user asks about the **Reference** subtab, ControlNet, IP Adapter, FaceID, reference images, control images, generated maps, structural guidance, identity preservation, or why a reference extension is disabled.

## What belongs in Image → Reference

| Tool / area | Purpose | Guide |
|---|---|---|
| **ControlNet** | Guides structure using control images or generated maps such as canny, depth, pose, lineart, softedge, tile, or scribble. | `guides/01_IMAGE/controlnet.md` |
| **IP Adapter / FaceID** | Uses reference images for style, character, face, identity, or composition guidance. | `guides/01_IMAGE/ip_adapter_faceid.md` |
| **Preview action routing** | Sends a previous output into a reference tool from Preview/Results actions. | Use this guide plus the target extension guide. |

Reference tools are not the same as Assets tools. Assets tools store reusable model/prompt assets such as LoRAs and embeddings. Reference tools use images/maps to guide a specific generation job.

## Reference vs Generation vs Assets

| Workspace | Owns | Example |
|---|---|---|
| **Generation** | generation-time planning or graph modifiers | CFG Fix, LayerDiffuse, Style Stack, Wildcards, Scene Director |
| **Assets** | reusable model/prompt assets | LoRA Stack, LoRA Library, Embeddings / Textual Inversion |
| **Reference** | image/map/identity guidance | ControlNet, IP Adapter, FaceID |
| **Finish** | output finishing and repair | Upscale, ADetailer/repair-style finish passes when routed there |
| **Results** | saved output inspection and metadata | Output Inspector, replay, cleanup, delete |

## Route-aware rule

Reference tools are family-aware and loader-aware. The same ControlNet or IP Adapter card can be:

- **Ready** when the selected backend/family/loader/workflow has a validated graph patch;
- **Experimental** when the route is allowed but should be checked before batch work;
- **Planned / route gated** when the UI can preserve settings but should not execute;
- **Provider gated** when the backend/API/custom nodes cannot provide the required capability;
- **Unsupported** when the selected route should not use that extension.

Never tell the user that a Reference extension will run just because its card is visible. Check the live status badge and the active Image route first.

## General workflow

1. Pick the Image **Model Family**, **Main Model Type**, and **Workflow Mode**.
2. Open **Image → Reference**.
3. Choose the correct reference tool:
   - Use **ControlNet** for structure, pose, depth, edges, map-based composition, tile/detail, or mask/canvas control.
   - Use **IP Adapter / FaceID** for identity, face, character, style, or composition reference from one or more images.
4. Check the route status badge: Ready, Experimental, Planned, Provider gated, Unsupported, or Disabled.
5. Attach the needed image/reference assets.
6. Set strength and timing values.
7. Validate or generate.
8. Review Output Inspector metadata to confirm whether the extension actually patched the graph.

## Common user questions

### “Why is Reference disabled?”

Answer by checking:

- active backend profile;
- selected Model Family;
- selected Main Model Type / Loader;
- selected Workflow Mode;
- extension apply toggle;
- installed custom nodes and model dropdown readiness;
- whether the route state is Ready, Experimental, Planned, Provider gated, or Unsupported.

### “Should I use ControlNet or IP Adapter?”

Use **ControlNet** when the important thing is structure:

- same pose;
- same silhouette;
- same edge layout;
- same room/composition geometry;
- depth/perspective preservation;
- tile/detail restoration.

Use **IP Adapter / FaceID** when the important thing is reference identity or visual likeness:

- same face;
- same character;
- same outfit/style influence;
- same product/object style;
- same general composition from a reference image.

For strict character/person preservation, FaceID/IP Adapter often needs ControlNet or Scene Director too. FaceID helps identity; ControlNet helps body/pose/layout.

## Assistant behavior

When answering Reference questions:

1. Prefer this guide, `controlnet.md`, and `ip_adapter_faceid.md`.
2. Use the live Image snapshot for current route and enabled/disabled state.
3. Explain the visible fields in user language.
4. Mention route support only when relevant.
5. Do not dump payload JSON unless the user asks for raw debug data.
6. If the user asks for settings, give a safe starting range and explain what to adjust.
