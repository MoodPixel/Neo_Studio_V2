---
guide_id: comfy_spaces.universal_io_type_system
title: Comfy Spaces Universal Input and Output Types
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - controls
  - media
  - inputs
  - outputs
priority: 68
version: 9
updated: 2026-10-07
---

# Comfy Spaces Universal Input and Output Types

Comfy Spaces are designed to support mixed-media workflows instead of forcing every workflow into Image, Video, Voice, or Music.

Neo classifies imported workflow inputs and outputs into native Neo control/result types. **Admin → Comfy Spaces** now uses these descriptors to propose controls and result viewers that the Space author can review and override when the media kind supports it.

## Input controls

Examples of automatic mappings include:

```text
Prompt / multiline text  -> Text area
Normal text              -> Text field
Seed                     -> Seed control
Denoise / CFG / strength -> Slider when the range is sensible
Boolean                  -> Toggle
Small option list        -> Select
Large option list        -> Searchable select
Checkpoint / VAE / LoRA  -> Model selector
Image                    -> Image uploader/selector
Mask                     -> Mask uploader/selector
Video                    -> Video uploader/selector
Audio                    -> Audio uploader/selector
File                     -> File uploader/selector
Structured unknown value -> JSON editor
```

These are suggestions, not permission to expose every detected Comfy setting. The Space creator chooses which controls are visible in the current Builder.

## Output renderers

Neo also has a shared result vocabulary for Spaces:

```text
Text    -> Text viewer
Number  -> Numeric value
Boolean -> Boolean/status value
JSON    -> JSON viewer
Image   -> Image preview
Mask    -> Mask preview
Video   -> Video player
Audio   -> Audio player
File    -> File download
```

A single Space may return more than one media type.

## Media files are not local paths

Image, mask, video, audio, and file values are intended to use Neo-managed asset references rather than exposing local filesystem paths to the Space UI.

The type system declares this requirement, and NCS-4 gives the binding layer a portable `neo_asset_ref` envelope. NCS-6 now implements the neutral upload/media bus that owns those references, validates/stores their bytes, resolves them against one selected Comfy runtime, and can collect already-produced media back into Neo-owned assets. NCS-3/NCS-4 themselves still do not upload files or run workflows.

## Model selectors

When the workflow analyzer recognizes checkpoint, diffusion-model, VAE, text-encoder, LoRA, ControlNet, upscaler, or similar fields, Neo marks them as model selectors instead of generic dropdowns.

The connected Comfy runtime remains the source of truth for model availability. When a loader exposes a live combo catalog through `/object_info`, Neo uses that catalog as compatibility evidence: exact names are authoritative, normalized paths are accepted, and one unique basename can reconcile a saved extra/old folder prefix. Ambiguous basenames are never guessed, and a missing combo catalog remains Unchecked. Comfy Spaces do not automatically download or install models.

## Current availability

The universal type system now feeds the visible Admin Builder, workflow analysis/binding contracts, Neo's shared dynamic Space renderer, published Space UI packages, the NCS-6 neutral asset bus, NCS-7 execution runtime, NCS-8 progress layer, and the top-level NCS-9 **Comfy Spaces** workspace. Builder Preview renders the same control/result vocabulary without executing Comfy; the public runner reuses it for actual published-Space controls and results.


## Multiple values and dynamic collections

NCS-11 allows the author to mark supported inputs as **Multiple values**. Asset inputs may use a reference-list presentation over Neo asset references, while structured JSON inputs may use a bounded repeater. Neo does not infer array support for arbitrary scalar Comfy inputs. The published manifest multiplicity remains the binding contract, and dynamic collection UI compiles back into ordinary validated Space values.
