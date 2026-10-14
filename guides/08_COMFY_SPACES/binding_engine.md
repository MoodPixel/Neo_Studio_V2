---
guide_id: comfy_spaces.binding_engine
title: Comfy Spaces Binding Engine
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - bindings
  - workflow
  - inputs
  - outputs
priority: 66
version: 8
updated: 2026-10-07
---

# Comfy Spaces Binding Engine

Neo Studio uses an internal binding layer to let the current **Comfy Spaces** runner control a reviewed Comfy workflow without rewriting the stored workflow template.

The **Admin → Comfy Spaces** Builder is now available for reviewing detected controls/results and editing a Space draft. Users still do not edit raw node bindings manually: the Builder keeps candidate provenance and compiles the reviewed draft through this binding engine.

## Clone, then bind

A stored Space workflow remains an immutable template.

When Neo prepares values for a Space, the binding engine follows this rule:

```text
Stored workflow template
        ↓
deep clone
        ↓
validate reviewed Space bindings
        ↓
inject user/default values into the clone
        ↓
NCS-7 selected-runtime execution
```

The original stored workflow is not edited by the binding operation.

## Graph Anchors are not value bindings

NCS-10A adds `graph_anchors.json` as a separate author-confirmed metadata sidecar. An anchor describes a typed producer/consumer boundary Neo may use in a later graph-composition phase. It does not grant the binding engine permission to overwrite linked inputs, bypass nodes, or rewire the workflow.

The current binding rule therefore remains unchanged: ordinary Space controls may replace reviewed **literal** values only. NCS-10A anchor confirmation and section grouping are authoring metadata/presentation operations, not runtime binding operations.

## Safe input targets

Space controls may bind to literal Comfy node input fields such as:

```text
CLIPTextEncode.text
KSampler.seed
KSampler.steps
KSampler.denoise
EmptyLatentImage.width
```

Neo does not let a normal Space control overwrite an existing graph connection. If a target field already contains a Comfy node link, the binding is rejected instead of silently breaking the workflow graph.

Unknown input IDs, missing target nodes/fields, invalid value types, and missing required inputs also fail closed.

## Defaults and optional controls

If a Space input has a declared default, Neo may apply that default to the cloned workflow when the user does not provide a value.

If an input is optional and has no Space default, Neo leaves the workflow template's existing literal value unchanged for that run preparation.

## Images, video, audio, masks, and files

Media/file controls do not accept local filesystem paths directly from the Space UI.

NCS-4 introduces the portable asset-reference envelope:

```json
{
  "ref_type": "neo_asset_ref",
  "asset_id": "asset-001",
  "kind": "image"
}
```

The binding engine itself still does not upload the asset or turn it into a Comfy filename. The implemented NCS-6 neutral asset/media bus now owns that job: it validates Neo-owned asset records/bytes, resolves the reference against exactly one selected Comfy runtime, and passes only the resulting Comfy-ready value into the binding engine's `resolved_asset_values` seam.

If a media/file reference has not been resolved successfully, the binding plan remains **not ready for execution**. Raw filesystem paths remain invalid Space values.

## Output bindings

A Space output may point to a named Comfy result/history field, such as an `images` or video/file result field, or to an explicit output-slot envelope supplied by the execution layer.

NCS-4 can resolve `first` versus `all` collection behavior from already-supplied result data. It does not execute Comfy or copy generated media into Neo's output store.

Image, mask, video, audio, and file results are still marked as deferred **inside NCS-4 itself**; NCS-7 now consumes those resolved runtime coordinates and hands them to NCS-6 for collection.

## Current availability

The binding engine remains the internal authority behind reviewed bindings, and the visible Admin Builder now consumes it through the Builder service/compiler. It still does **not** provide:

- direct file/media transport inside the binding engine itself (NCS-6 owns the neutral bus);
- Comfy queue execution **inside the binding engine**; NCS-7 owns that separate runtime authority;
- public runner presentation or history ownership; the top-level **Comfy Spaces** workspace consumes this engine indirectly through NCS-7 rather than moving UI/runtime concerns into NCS-4.

Publishing reuses this binding validation as part of its fail-closed package gate. NCS-6 reuses the same seam after asset resolution, NCS-7 queues only the clone that passes this binding path plus live selected-runtime preflight, and the top-level Comfy Spaces runner invokes that existing runtime authority rather than binding graphs itself.
