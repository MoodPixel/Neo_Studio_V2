---
guide_id: comfy_spaces.top_level_surface
title: Comfy Spaces Workspace
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
tags:
  - comfyui
  - spaces
  - runner
  - generation
  - progress
priority: 90
version: 7
updated: 2026-10-07
---

# Comfy Spaces Workspace

**Comfy Spaces** is Neo Studio's top-level workspace for running published ComfyUI workflows as native Neo mini-apps. A published Space already defines its workflow, user-facing controls, outputs, and presentation, so the workspace opens directly to that Space instead of asking the user to configure Neo's generic Image routing first.

## Space library

Opening **Comfy Spaces** shows the published Space library. The library can search/filter available Spaces, mark favorites locally, show recent/active run state, and open a Space or an already-running job.

Only validated published package presentation data is exposed to this surface. The library API does not expose the stored workflow graph or publication receipt to the browser.

## Runner layout

A Space opens with its presentation header and the shared Neo Space renderer:

```text
Space header
        ↓
Controls (45%)  |  Results (55%)
        ↓
Validate · Generate · Pause · Stop
        ↓
Progress / status
        ↓
Recent runs
```

On narrow screens the controls and results stack vertically.

The controls/results are rendered from the same NCS-5C `space_manifest.json + space_ui.json` contracts used by Builder Preview. NCS-9 does not introduce a second renderer or generate per-Space frontend code.

## Native Neo runner presentation

The public runner applies a Neo-native presentation layer to those declarative controls instead of exposing browser-default form chrome. Text, numeric, searchable choice, model, slider, seed, toggle, textarea, and media/file controls share Neo's dark field/focus language. Media/file inputs appear as a dropzone rather than the browser's native **Choose File** control.

The split layout is top-aligned: when no Advanced controls exist, no empty Advanced grid row is reserved. The Results card begins beside the first control section and remains sticky on wider desktop layouts while long controls scroll.

Normal runner help is intentionally user-facing. Analyzer-only helper strings beginning with `node role:` are hidden in runtime mode, while real author/Comfy tooltips and descriptions remain visible. Builder Preview keeps the technical diagnostics for authors.

Declared runtime outputs use Neo result cards. Before a run they show a deliberate **No result yet** state; generated media can expose safe **Open** and contract-allowed **Download** actions.

## Live Comfy runtime catalogs

Choice controls that are backed by ComfyUI node combo inputs now use the **selected runtime's live `/object_info` catalog** in the public runner. This includes model/LoRA selectors and workflow choices such as sampler and scheduler when those node inputs expose a combo list.

The runner obtains those catalogs through the canonical Neo route `GET /api/comfy-spaces/runtime/options` with explicit Space/version/runtime coordinates. The older library-nested runtime-options path remains a compatibility alias, but both routes delegate to the same backend catalog service.

- `model_select` and `search_select` render as searchable Neo catalog pickers.
- ordinary `select` controls render the full live combo list as a native Neo dropdown.
- switching runtime refreshes the catalogs before the next run.
- a saved value that is no longer present is preserved visibly and marked unavailable instead of being silently replaced.
- when an older saved model path resolves uniquely to a live runtime catalog entry, Neo reports that runtime resolution but does not rewrite the published workflow package.
- if the selected runtime is offline or `/object_info` cannot provide a catalog, the picker is disabled and reports **Catalog unavailable**.

Typing in a searchable catalog control filters the runtime choices; arbitrary typed text is not committed as a new catalog value. The user must choose an option supplied by the selected Comfy runtime.

## Per-run Graph Add-ons

When the published Space contains author-confirmed Graph Anchors, the runner also asks the selected Comfy runtime for a **Graph Add-on catalog**. The catalog is rebuilt from that Space's confirmed anchors plus the runtime's live `/object_info`; it is not a saved second node database.

The **Per-run graph additions** panel has two lanes:

- **Add Module** — reviewed graph fragments. The initial module is ControlNet, which uses confirmed positive/negative conditioning anchors, a Neo-owned control image, and the runtime's actual ControlNet model/node choices.
- **Advanced · Add Raw Comfy Node** — only nodes Neo can prove are a safe single typed transform at one confirmed anchor. Nodes needing ambiguous/multiple required graph inputs are intentionally excluded.

Add-ons apply to the next **Validate / Generate** request only. They are not written into the published Space. Switching Comfy runtime clears selected add-ons because compatible nodes and models may differ.

Neo validates the exact confirmed edge again before rewiring the execution clone, validates add-on literal values against the live runtime schema, and rechecks dependencies after composition. If two selected add-ons claim the same anchor, validation fails instead of Neo inventing an ordering rule.

The browser never sends arbitrary Comfy graph JSON through this panel. Complex graph fragments belong to reviewed modules rather than the raw-node lane.

## Space Variants

The runner includes a **Reusable Space configuration** panel. Choose **Base Space · no Variant** to use the published defaults, or apply a saved Variant to restore its Graph Add-ons, stage defaults, and saved non-media values. Applying a Variant never edits the published workflow.

To save one, enter a Variant name and optionally enable **Save current non-media values**. Neo always pins the Variant to the exact open Space version and publication fingerprint. Media/file controls and add-on asset uploads are excluded deliberately, so those files are selected again per run.

A stale Variant is shown but cannot be applied when its exact base publication is unavailable or has changed. Neo does not silently retarget it. A selected Variant remains editable as a starting point; execution records distinguish the exact saved Variant from a modified run configuration.

Variants are not the same as generic UI Presets. A Variant may include graph topology and stage defaults; a future value-only Preset layer remains separate.


## Conditional and dynamic controls

If the published Space includes `dynamic_controls.json`, the runner evaluates it against the current Space values plus enabled-stage, selected-add-on, and active-Variant context. Inputs or sections can appear/disappear or enable/disable as those conditions change.

Repeaters are rendered only for manifest inputs explicitly authored as multiple values. Multi-reference media lists continue through the normal Neo asset bus. This layer is declarative only: it never converts hidden/disabled UI into Comfy bypass, graph rewiring, or stage skipping.

## No generic Image routing header

Comfy Spaces deliberately does **not** show Neo's generic Image workspace selectors such as:

- Model Family;
- Main Model Type;
- Workflow Mode;
- generic UI Presets.

Those concepts are already represented by the published Space and its bound Comfy workflow. When more than one compatible Comfy runtime is available, runtime choice is a compact runner setting rather than a second top-level workspace configuration layer.

## Execution controls

The runner follows Neo's familiar execution-control pattern:

- **Validate** — checks the selected runtime and current Space values without queueing a workflow;
- **Generate** — stages required assets and starts the published Space through NCS-7;
- **Pause** — currently shown disabled because NCS-7/NCS-8 do not provide a truthful Comfy pause/resume primitive;
- **Stop** — requests cancellation through the existing NCS-7 cancellation authority;
- **Progress** — shows the current run state using NCS-8 persistent progress.

Neo does not emulate pause by cancelling/restarting a graph.

## Media inputs and outputs

Image, mask, video, audio, and file controls use the NCS-6 asset/media bus. Browser files become Neo-owned `neo_asset_ref` values before execution. Raw local server paths are never accepted as Space values.

Declared results use the shared renderer. Scalar outputs are rendered directly; media/file outputs are collected into Neo-owned assets by NCS-6 before being shown to the user.

## Live progress and navigation safety

A running Space uses the same persistent NCS-8 `run_id` progress authority whether the user remains on the runner or navigates elsewhere.

The runner can show:

- queued/preparing/running/finalizing state;
- friendly current-node/stage text;
- real sampler step progress when Comfy reports it;
- completed-node counts;
- cancellation-requested state;
- reconnect/recovery state;
- terminal success, cancellation, or failure.

Exact node IDs/classes remain available under technical details rather than dominating the normal UI.

If the live progress websocket disconnects, Neo falls back to the existing NCS-7 history/queue reconciliation. The browser never receives the selected Comfy backend URL.

## Run history

The workspace reads recent Space runs from Neo's existing shared generation job registry. It does not create a second Space-specific scheduler or run database.

Historical runs can be selected to inspect their stored status/progress and declared results where those result references remain available. NCS-9 does not add persistence of complete user input values for one-click replay.

## Builder versus runner

**Admin → Comfy Spaces** remains the authoring surface:

```text
Import → Inspect → Controls → Outputs → Stages → Dynamics → Presentation → Preview → Publish
```

Builder Preview remains non-executing. Publishing creates the immutable declarative package consumed by the top-level **Comfy Spaces** workspace.

The public runner never edits the stored published workflow. Current controls, runtime-resolved model names, asset handoffs, selected Graph Add-ons, stage plans, and optional Variant overlays are resolved for the run while the stored published workflow remains unchanged.

## Trust boundaries

The top-level workspace does not:

- install models or custom nodes automatically;
- execute arbitrary Python/JavaScript from a Space package;
- expose raw published workflow JSON to the browser library API;
- expose publication receipts to the browser library API;
- silently fail over to another Comfy runtime;
- create a second GPU scheduler/job registry;
- execute Builder Preview;
- accept arbitrary browser-supplied graph JSON or auto-install an add-on dependency.

A run still revalidates the immutable package, optional Variant base fingerprint, selected runtime, required nodes/models, current values, Graph Add-ons, stage plan, and assets immediately before queueing.


## Stage-aware pipeline controls

When a published Space defines execution stages, the runner shows a pipeline panel above Graph Add-ons. Users can **Run All**, execute an individual **Run Stage**, toggle author-declared optional stages, or **Continue** into a dependent stage after a compatible predecessor run completes. The normal Generate control remains available for compatibility and follows the currently selected stage mode.

Continue is deliberately conservative: it requires the same Space/version and selected Comfy runtime, and the predecessor must have completed the required previous stage(s). Actual upstream reuse remains ComfyUI cache behavior rather than a Neo-side fake resume mechanism.
