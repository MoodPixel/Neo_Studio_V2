---
guide_id: comfy_spaces.overview
title: Comfy Spaces Overview
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - workflows
  - spaces
  - mixed-media
priority: 80
version: 26
updated: 2026-10-08
---

# Comfy Spaces Overview

Comfy Spaces turn a ComfyUI workflow into a clean Neo-style mini-app without rebuilding that workflow as a separate Neo extension. Authors import, review, preview, and publish a Space in **Admin → Comfy Spaces**. Users run published Spaces from the top-level **Comfy Spaces** workspace.

A Space keeps the Comfy execution graph underneath while Neo supplies the interface, assets, execution controls, results, and long-running progress experience on top. One workflow may mix several media types, for example:

```text
Prompt → Image generation → Upscale → Image-to-video → Video output
```

A Space therefore does not have to be only an Image tool or only a Video tool. Its visible inputs and results can include text, numbers, choices, images, masks, video, audio, and files.

## Current availability

The complete NCS-1 through NCS-9 runtime backbone plus **graph-aware authoring, per-run Graph Add-ons, stage-aware execution, Space Variants, value-only Presets, declarative dynamic controls, and persistent dependency health** is now present:

- normal ComfyUI workflow import and API-workflow support, including native nested Comfy subgraph normalization with preserved source lineage;
- runtime-aware graph analysis and dependency checks;
- universal media-neutral input/output descriptions;
- safe clone-and-bind execution preparation;
- Admin Builder drafts, shared preview renderer, and immutable publication;
- neutral Neo-owned asset/media handoff;
- selected-runtime published-Space execution;
- navigation-safe long-running progress/recovery;
- top-level published Space library and runner;
- runtime-schema-backed Graph Anchor proposals with explicit author confirmation;
- semantic-role, source-node, and source-subgraph control grouping starters that remain presentation-only;
- selected-runtime **Per-run Graph Add-ons** that can temporarily insert reviewed real-Comfy-node modules such as ControlNet at author-confirmed anchors;
- an advanced raw-node lane that only lists runtime nodes Neo can prove are safe single typed transforms at a confirmed anchor.
- explicit **Run All / Run Stage / Continue** execution stages with declared dependencies, target outputs, optional-stage defaults, and post-stage resource policies;
- reusable **Space Variants** pinned to one exact published Space/version, capable of remembering Graph Add-ons, stage defaults, and optional non-media values without copying the workflow;
- declarative **conditional/dynamic controls** for value/stage/add-on/Variant-aware visibility/enabled state plus bounded repeaters/reference lists, with no per-Space JavaScript;
- persistent per-runtime **Dependency Health** with ready/blocked/stale/unreachable state, runtime inventory-change detection, missing nodes/models, package hints, stage readiness, and Graph Add-on readiness while live queue-time preflight remains authoritative.

Builder Preview remains deliberately non-executing. The top-level **Comfy Spaces** workspace is the user-facing execution surface.

## Authoring flow

```text
Import normal ComfyUI workflow JSON
        ↓
Detect format + normalize when needed
        ↓
Review every detected input/output candidate
        ↓
Accept or override Neo's exposure suggestions
        ↓
Review/confirm safe Graph Anchor proposals
        ↓
Auto-group by semantic role/node/subgraph or organize manually
        ↓
Define stages and conditional/dynamic controls when needed
        ↓
Preview the Neo interface
        ↓
Check publish readiness
        ↓
Publish immutable Space version
```

Neo keeps every detected input candidate available to the author. Node-aware recommendation logic only suggests the initial exposed/not-exposed state; it never deletes a candidate because Neo thinks it is unimportant.

Normal ComfyUI workflows may also contain reusable/nested subgraphs. Neo preserves that source hierarchy for authoring evidence while flattening only the execution copy into stable canonical node ids. This allows complex modern Comfy workflows to become Spaces without requiring a separate API export. NCS-10A uses that lineage for node/subgraph-aware grouping and typed Graph Anchor proposals. The public runner can now use those **author-confirmed anchors** for per-run Graph Add-ons without rediscovering topology by guesswork.

Publication is package-only and fail-closed. Neo revalidates the saved draft, refuses known blockers and existing-version overwrites, and writes the immutable package without running Comfy or installing dependencies.

## Running a published Space

The top-level runner deliberately opens directly to the Space's own controls. It does **not** show the Image workspace's generic **Model Family**, **Main Model Type**, **Workflow Mode**, or generic **UI Presets** controls.

Runtime-backed choice controls are populated from the selected ComfyUI runtime itself. Model and LoRA controls use searchable catalog pickers, while smaller combo choices such as schedulers use the runtime's actual dropdown values. Neo preserves stale saved values with a warning rather than silently replacing them, and disables catalog pickers if the selected runtime cannot provide `/object_info`.

The runner reuses Neo's shared dynamic Space renderer and familiar execution pattern:

```text
Space header
        ↓
Controls | Results
        ↓
Validate · Generate · Pause · Stop
        ↓
Progress / current stage
        ↓
Recent runs
```

`Pause` is currently visible but disabled because the current Comfy execution contract does not provide a truthful pause/resume primitive. Neo does not fake pause by cancelling/restarting a graph.

When several Comfy runtimes are available, runtime selection remains a compact runner setting. The selector can also show the saved Dependency Health state for each configured runtime so you can see which environment was last known ready, blocked, stale, or unchecked before switching. Execution still resolves to exactly one selected runtime; Neo does not silently fail over during a run.

## Dependency Health

Published Spaces keep runtime-local compatibility evidence per exact Space version and Comfy backend profile. The runner can show missing node/model details, package/module hints, stage-specific readiness, Graph Add-on readiness, and whether the runtime dependency inventory materially changed since the previous refresh. Use **Refresh Health** for an explicit recheck.

Health is advisory rather than execution authority. A saved Ready snapshot can become stale or the runtime can change after it was written, so every Validate/Generate path still reads the selected runtime's live `/object_info` and validates the actual per-run execution clone before queueing. Corrupt local health evidence degrades to Not checked/Stale and does not make a valid published Space unloadable.

Neo never installs missing custom nodes or models automatically from health evidence. See **Comfy Spaces Dependency Health** for the detailed troubleshooting contract.

## Assets, execution, and progress

Uploaded media is represented by `neo_asset_ref`, stored under Neo ownership, and handed to exactly one selected Comfy runtime through the NCS-6 asset bus. Raw local filesystem paths are not accepted as public Space values.

NCS-7 re-verifies the immutable published package, selected-runtime nodes/models, assets, and current bindings before queueing an execution clone through Neo's existing shared Comfy GPU/job infrastructure. The published `workflow.json` itself is never mutated.

NCS-8 gives every run persistent `run_id` progress, current-node/step telemetry, history/queue fallback, navigation-safe reconnect, startup recovery, and cancellation feedback. The NCS-9 runner consumes those same records instead of creating a second scheduler or progress store.

## Current graph-editing boundary

Neo can now import complex normal ComfyUI workflows with supported nested subgraphs, preserve their source hierarchy, expose reviewed controls, propose typed runtime-backed Graph Anchors, and execute the published Space. Authors can confirm anchors and reorganize controls by semantic role/source node/source subgraph.

In the top-level runner, **Per-run Graph Add-ons** may temporarily change the execution topology when both the published anchor metadata and the selected runtime's live `/object_info` prove the attachment is compatible. The initial reviewed module is ControlNet. Advanced users can also choose a raw Comfy node, but Neo only lists nodes that can be proven as a single typed transform at one confirmed insert-between anchor. Complex multi-input nodes are not exposed through that raw lane.

The published `workflow.json` remains immutable. Add-ons exist only on the cloned graph for the current Validate/Generate request, and switching runtimes clears them because node/model availability may differ. Neo also rejects two add-ons claiming the same anchor instead of guessing an order.

## Space Variants

The runner can save the current Space setup as a **Variant**. A Variant is not a copied workflow and is not a new published Space version. It records an exact base `space_id` + version + publication fingerprint, then stores only reusable declarative overlays such as selected Graph Add-ons, stage defaults, lightweight presentation metadata, and optionally saved non-media input values.

Uploaded image/mask/video/audio/file inputs and Graph Add-on asset references are never persisted into a Variant. Re-select those assets for the next run. If the exact published base is removed or its publication receipt changes, the Variant becomes **stale/blocked** instead of silently moving to another version. Explicit migration belongs to the versioning/migration workflow.

A **Preset** remains a different concept: value-only configuration for an unchanged topology. A Variant may carry topology/stage overlays, so Neo does not label or treat Variants as presets.

Presentation sections still only organize controls. Collapsing, hiding, or regrouping a section does **not** bypass/mute Comfy nodes. Execution stages are explicit `stages.json` contracts, and reusable Space Variants are separate runtime-local overlays; neither is inferred from UI section state.


## Conditional and dynamic controls

Authors can use the Builder **Dynamics** step to make an input or presentation section visible/enabled only when another Space value, enabled execution stage, selected Graph Add-on, or active Variant state matches a declarative condition. Repeatable JSON values and asset reference lists are available only after the underlying input is explicitly authored as **Multiple values**.

These rules remain presentation/value-availability metadata. They do not bypass/mute nodes, modify bindings, infer stage execution, or chain Graph Add-ons. Published metadata lives in integrity-checked `dynamic_controls.json`, and Builder Preview/public runner use the same shared evaluator without per-Space JavaScript.

## Spaces are not Neo Extensions

A Comfy Space is declarative workflow data. It does not receive permission to run custom Python or JavaScript inside Neo.

Neo Extensions remain the executable plugin system for features that genuinely need custom Neo code. Comfy Spaces instead describe a workflow, visible controls/results, requirements, and presentation.

## Custom node safety

A Space may reference Comfy custom nodes that are already installed in the selected Comfy runtime. Those custom nodes are executable code inside ComfyUI and should be trusted separately.

Importing or publishing a Space does not silently install custom nodes, models, Python packages, or other executable dependencies. Neo reports missing requirements so the user can decide what to install/configure in the connected Comfy environment.

## Workflow files

For normal use, choose the **regular workflow JSON saved from ComfyUI**. Neo detects the editor-format `nodes` / `links` graph and converts supported workflows into its canonical execution graph internally. Existing API-format workflow JSON is accepted directly as well.

Most users therefore do not need to know the difference between the two formats.

Neo still fails closed on workflow features it cannot map safely. Older/custom nodes with unnamed positional widgets may require a selected ComfyUI runtime so Neo can read `/object_info`; unsupported graph features should be simplified or supplied as an API-format export rather than guessed.

## Portability

Published Space packages keep the canonical execution graph and declarative presentation contract rather than machine-specific absolute paths. Model compatibility is checked against the selected runtime, while user media moves through Neo-managed assets.

This keeps Spaces easier to move between Neo installations without bundling model weights or silently installing executable custom-node code.


## Runtime catalog route

Published Space combo controls resolve live choices from the selected ComfyUI runtime through Neo's canonical `GET /api/comfy-spaces/runtime/options` route. Comfy `/object_info` remains authoritative; Neo does not maintain a parallel model/sampler catalog.


## Versions and workflow replacement

Published Space versions are immutable. When the underlying Comfy workflow changes, use the Admin Builder's **Replace a published workflow** flow to compare the new graph against an existing published version. Neo migrates only uniquely compatible controls, outputs, Graph Anchors, stages, and dynamic references; ambiguous mappings remain for explicit review. Publishing creates a new version, while rollback only changes which immutable version is active. Existing Variants remain pinned until explicitly migrated.

## NCS-14 value-only Presets

Use the runner **Presets** panel to save current non-media input values, apply a saved Preset as an overlay to current inputs, or delete it. Applying a Preset does **not** change the selected Variant, Graph Add-ons, stage settings or media selections. Presets are pinned to the exact published Space version/fingerprint and stale Presets are disabled. For graph or stage configuration, use a Variant. See `guides/08_COMFY_SPACES/value_only_presets.md`.
