---
guide_id: comfy_spaces.builder_workspace
title: Comfy Space Builder Workspace
surface: admin
scope: built_in
applies_to:
  - admin
  - comfy_spaces
tags:
  - comfyui
  - builder
  - workflow-import
  - preview
priority: 82
version: 15
updated: 2026-10-07
---

# Comfy Space Builder Workspace

Use **Admin → Comfy Spaces** to turn a normal ComfyUI workflow JSON into an editable Neo Space draft. API-format workflow JSON is also accepted.

The Builder can analyze a workflow, shape the Neo interface, preview it, and publish a validated **declarative Space package**. Publishing is package-only: it does not run Comfy, upload run assets, or install dependencies.

## Before you import

Use the normal workflow JSON you save from ComfyUI. You do **not** need to understand or manually export API format for ordinary Builder use.

Neo detects the file shape and converts a normal editor workflow into its canonical execution graph internally. If you already have an API-format workflow, you can import that too.

Some older/custom workflows store widget values positionally without enough naming information in the file. Choose the relevant Comfy backend profile before importing; when a runtime is selected, Neo automatically reads that runtime's read-only `/object_info` metadata to map those values safely. There is no second Runtime-aware-analysis toggle in the normal Builder path.

Neo does not silently guess through unsupported graph constructs. Active and nested normal-Comfy subgraphs are now normalized natively with source lineage preserved. Recursive subgraphs and connected bypass semantics still produce a compatibility message instead of unsafe rewiring; API-format JSON remains supported but is no longer required merely because a workflow uses subgraphs.

## Builder steps

### 1. Import

Choose the ComfyUI workflow JSON you normally save and click **Import & create draft**.

Neo detects the workflow format, normalizes it when necessary, analyzes the graph, and creates a durable Builder draft. Importing does **not** execute the workflow.

### 2. Inspect

Review the workflow inventory and dependency information, including detected nodes, inputs/outputs, custom-node requirements, model requirements, runtime profile, portability warnings, and — when present — Comfy subgraph structure. Inspect reports active subgraph instances, nesting depth, reroute count, source subgraph names, flattened-node counts, and source group titles while keeping the execution graph separate from the original editor hierarchy. For model loaders, Neo uses the selected ComfyUI runtime's live combo values from `/object_info` as compatibility evidence. Exact values are preferred; normalized path separators and a single unique basename can reconcile old/extra relative folder prefixes. If the basename is ambiguous, Neo blocks rather than choosing one.

This is diagnosis and authoring evidence. The Builder does not install missing nodes or models automatically. After publication, the top-level Comfy Spaces runner can persist **Dependency Health** separately for each configured Comfy runtime; that health layer is runtime-local evidence and is not written into the immutable Space package.

Inspect also shows **Graph anchor** proposals when the selected runtime provides usable `/object_info` socket definitions. A proposal identifies the semantic role and the exact canonical source → target connection while retaining source-node/subgraph lineage. Check a proposal only when it is the attachment point you intend Neo to remember for graph composition. Confirmed anchors can now be used by the published Space runner for validated **per-run Graph Add-ons** such as ControlNet; the Builder itself still does not execute or patch the graph. Confirmation stores declarative metadata only; it does not edit the workflow. Ambiguous same-role candidates are never auto-confirmed. If runtime socket metadata is unavailable, Neo shows anchor analysis as unchecked instead of guessing.

### 3. Controls

Choose which detected workflow inputs should appear in Neo.

Depending on the detected value type, you can adjust things such as:

- exposed/not-exposed state;
- input ID;
- label and help text;
- section;
- compatible control style;
- single/multiple value mode where the type safely supports it;
- order;
- default behavior;
- advanced state;
- numeric range/step overrides where supported.

Every detected candidate remains visible in the Builder. Neo only recommends the initial exposure state. The recommendation is now **node-aware**: Neo considers the node's role as well as the field name, so a real generation `height` can start exposed while an annotation node's `height` stays optional; normal prompt fields can start exposed while `system_prompt`, device/dtype, output-path, and presentation-only settings normally do not. A not-exposed control is **not removed from the workflow**; its original template value remains intact, and you can expose it at any time if Neo's recommendation was wrong. The Controls screen also provides All / Exposed / Not exposed / Recommended filters plus Reset to Neo suggestions / Expose all / Expose none helpers. NCS-10A adds **Group controls** starters for Semantic role, Source node, Source subgraph, and Reset to Main. These starters reorganize sections only; they never change Comfy execution. You can continue renaming, reordering, merging, or collapsing the resulting sections afterward. Source-subgraph grouping follows each actual subgraph **instance path**, so repeated uses of the same reusable subgraph definition stay independently authorable; duplicate friendly names are disambiguated with their path.

You are not editing the Comfy graph directly. Neo keeps the normalized workflow frozen and compiles your reviewed choices into its declarative Space contracts.

### 4. Outputs

Choose which detected results the Space should expose. You can set labels, compatible result viewers, order, first/all collection behavior where supported, and the primary result.

### 5. Stages

Define explicit execution boundaries when the workflow should support Run All / Run Stage / Continue. Stages remain separate from presentation sections and Comfy bypass/mute.

### 6. Dynamics

Optionally define declarative visibility/enabled rules for inputs or sections. Conditions can read Space input values, enabled stages, selected Graph Add-ons, or active Variant state. You can also attach a **Repeater** or **Reference list** to an input that is explicitly authored as **Multiple values**.

Dynamic controls do not execute code or change graph topology. A repeatable stack-like value does not grant permission to chain topology add-ons; that still requires an explicit reviewed Graph Add-on contract.

### 7. Presentation

Set the Space name/description and organize controls into sections. This changes the Neo presentation, not the underlying workflow graph.

### 8. Preview

Save the draft, then compile Preview.

Preview validates the current draft and mounts the compiled manifest + Space UI through Neo's shared dynamic Space renderer. You can interact with normal text, numeric, toggle, select/search/model, JSON, slider/seed and advanced/collapsible controls to check the real Neo layout and control behavior. Result areas are rendered with the same shared output vocabulary used by the live top-level Comfy Spaces runner for text/image/video/audio/file results.

**No Comfy generation happens here.** Submit stays disabled. Builder Preview intentionally does not invoke the NCS-6 asset bus or NCS-7 execution runtime; media/file controls remain preview-only in Builder even though published Spaces can use them from the top-level **Comfy Spaces** runner. If you edit the draft afterward, the old preview is invalidated and must be compiled again.

### 9. Publish

Save the current draft, then run **Check publish readiness**. Neo recompiles the saved revision instead of trusting Preview files and checks the package fail-closed.

Publication is blocked when the saved draft has invalid bindings/UI references, no exposed output, broken workflow provenance/hash, unresolved portability issues, a known-blocked dependency preflight, unsafe presentation assets, or an existing package with the same Space ID and version.

If dependencies were never live-checked, Neo shows a warning rather than pretending they are ready. After publication you can refresh per-runtime **Dependency Health** from the top-level Space runner, but a later run must still preflight the selected runtime live before execution. A saved health snapshot never turns publication into a runtime guarantee.

When all blockers are clear, **Publish Space** copies the canonical workflow + manifest + Space UI + author-confirmed Graph Anchor sidecar + stage sidecar + dynamic-control sidecar into the Comfy Spaces library as an immutable version and records publication provenance. The anchor sidecar is metadata only and has no graph-mutation or execution authority. No workflow is executed. To publish another revision, bump the Space version instead of overwriting the published package.

## Multiple browser tabs

Builder drafts use revision checks. If two tabs open the same draft and one saves first, the older tab cannot silently overwrite the newer revision. Reload the draft before applying further edits.

## What the Builder does not do yet

The current Builder does not:

- run or queue Comfy workflows;
- start a runnable Space session from Builder Preview; the separate NCS-7 backend runtime now exists, but Builder Preview deliberately does not call it;
- install custom nodes or models;
- execute a published Space from Builder Preview; the top-level **Comfy Spaces** workspace owns that user-facing run experience;
- delete drafts from the Builder;
- execute the runner's per-run Graph Add-ons inside Builder Preview. Published Spaces may use Graph Add-ons from the top-level runner when confirmed anchors/runtime compatibility allow them;
- author arbitrary raw graph connection recipes; the runner's advanced raw-node lane is derived from live runtime schema and confirmed anchors;
- generate or execute arbitrary per-Space JavaScript from dynamic UI rules.

Those boundaries are deliberate: Builder stays an authoring/preview/publish surface. NCS-7 owns runtime submission, NCS-8 owns persistent progress/recovery, and the top-level NCS-9 **Comfy Spaces** workspace mounts the shared renderer as the user-facing runner instead of turning Builder Preview into it.


## Execution stages

The Builder has a dedicated **Stages** step. A stage is an execution boundary, not a presentation section and not a Comfy bypass toggle. Authors can define a label/order, required predecessor stages, target outputs, intermediate outputs, default/optional behavior, standalone eligibility, and a resource policy.

Stage target outputs must resolve to outputs already declared by the Space manifest. Dependency cycles and unknown stage/output references fail closed. Publishing stores reviewed stage metadata in the integrity-checked `stages.json` sidecar; the stored workflow remains unchanged. Conditional/dynamic presentation behavior is stored separately in integrity-checked `dynamic_controls.json`.


## Replacing an existing published workflow

Use **Replace a published workflow** instead of creating an unrelated Space when the Comfy graph evolves. Select the published source version, the replacement JSON, and a new semantic version. Review Neo's migration report, manually handle anything marked ambiguous/unmapped/review, save the draft, then use **Confirm migration review** before publishing. Any later draft edit makes that confirmation stale. Published version history remains immutable, and the version manager can explicitly activate an older version without overwriting files.
