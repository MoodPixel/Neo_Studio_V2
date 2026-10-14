---
guide_id: comfy_spaces.workflow_import_analysis
title: Comfy Space Workflow Import and Analysis
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - workflow-import
  - dependencies
  - object-info
priority: 70
version: 14
updated: 2026-10-07
---

# Comfy Space Workflow Import and Analysis

Neo Studio accepts the **normal ComfyUI workflow JSON you save from the editor**. ComfyUI API-format JSON is also supported.

Normal Save/workflow JSON is the preferred user-facing import path. Neo converts it internally into the canonical execution graph used by the rest of Comfy Spaces; users do not normally need to export a separate API-format file.

## Current availability

Workflow import and graph analysis are available through **Admin → Comfy Spaces**. The Builder can import a normal ComfyUI saved workflow or an API-format workflow, inspect the detected graph and dependencies, and turn the analysis into an editable draft.

The Builder can preview and publish validated declarative Space packages. NCS-10A proposes typed **Graph Anchors** from canonical topology plus the selected runtime's `/object_info`, and builds a source-node/subgraph-aware authoring map for control grouping. Confirmed published anchors can now be consumed by the top-level runner for NCS-10B per-run Graph Add-ons; import/analysis itself remains topology-read-only. Import, preview, anchor review, grouping, and publication themselves do not execute the workflow. Published Spaces run from the top-level **Comfy Spaces** workspace through the separate NCS-7 execution authority after selected-runtime preflight; Builder Preview remains non-executing.

## What happens to a normal ComfyUI workflow

A normal ComfyUI workflow commonly contains top-level `nodes` and `links`, editor layout information, and saved widget values. Neo keeps the received source JSON in import staging, then creates a separate canonical execution graph for analysis and later packaging.

Conceptually:

```text
Normal ComfyUI Save JSON
        ↓
Format detection
        ↓
Safe editor → execution-graph normalization
        ↓
Canonical workflow.json
        ↓
Graph Analyzer / Builder
```

Neo currently understands both modern named saved widget values and older positional widget values when it can determine their names safely. When you select a ComfyUI runtime, Neo automatically performs the read-only `/object_info` schema lookup during import so older positional widgets and custom-node inputs can be resolved without a second expert toggle. If no runtime is selected, workflows that already carry enough named widget information can still import normally.

Neo deliberately treats the selected ComfyUI runtime as the **node-schema authority**. The runtime's `/object_info` response is used for real input ordering, socket types, selectable values, output definitions, package/module hints, and custom-node availability instead of maintaining a second hard-coded catalog inside Neo. This follows the same separation ComfyUI itself uses: the saved editor graph carries layout/widget state, while the executable prompt needs node input semantics.

If a workflow references a custom node that is not loaded by the selected runtime, Neo reports the missing node class and asks you to open/check that workflow in ComfyUI and install or enable the node pack. Neo does **not** auto-install custom-node code.

### Native subgraph compatibility

Normal ComfyUI workflows may contain `definitions.subgraphs`, subgraph instances, nested subgraphs, exposed input/output ports, groups, and modern reroute points. Neo now understands this structure directly. The original editor JSON is preserved as the source record, while Neo creates a separate flattened execution representation for analysis, binding, publication, and runtime submission.

The flattening is deterministic and lineage-aware:

```text
normal Comfy editor graph
        ↓
subgraph-aware normalization
        ├─ exposed ports resolved
        ├─ nested instances expanded
        ├─ modern reroute metadata tolerated
        ├─ stable execution node ids generated
        └─ source node/subgraph lineage retained
        ↓
canonical workflow.json
```

Builder **Inspect** reports the number of active subgraph instances, maximum nesting depth, reroute count, subgraph names, flattened node counts, and preserved source group titles. Detected nodes/controls also retain their original source node id plus subgraph path, so Neo can keep complex workflow controls tied to their real source hierarchy instead of treating every flattened node as unrelated.

Neo still fails closed rather than guessing semantics it cannot reproduce safely. Recursive subgraph definitions and connected bypass behavior remain blocked. A disabled/collapsed Neo UI section does **not** mean the related Comfy nodes are bypassed. API-format JSON remains accepted, but active/nested subgraphs no longer require authors to export a special API workflow first.

### Graph Anchors and the authoring map

NCS-10A adds a **read-only graph understanding layer** on top of the canonical workflow. When a Comfy runtime is selected and `/object_info` is available, Neo can propose typed attachment points such as `MODEL`, positive/negative `CONDITIONING`, `LATENT`, `IMAGE`, `MASK`, `VAE`, text encoder, `VIDEO`, and `AUDIO`.

Anchor proposals are derived from the actual producer → consumer edge plus live Comfy socket definitions. Each proposal retains canonical node IDs and NCS-9.3 source lineage, including the original source node and nested subgraph path. Neo does **not** auto-confirm proposals. Multiple plausible candidates for the same semantic role remain visibly ambiguous until the Space author confirms the intended anchor in Builder Inspect.

If `/object_info` is unavailable, Neo does not guess socket semantics from node names or labels: anchor analysis reports `not_checked` and produces no typed proposals. The authoring map can still group controls from preserved source lineage because that operation is presentation-only.

The authoring map offers starting layouts by **semantic role**, **source node**, **source subgraph**, or **manual** grouping. Applying one of those layouts changes `space_ui` sections only. It does not bypass, mute, insert, remove, or rewire Comfy nodes.

## What Neo will inspect

When a workflow is imported, Neo can inspect it without running it and identify:

- workflow nodes and connections;
- source and final/terminal nodes;
- literal settings that could become Neo controls;
- prompt, seed, steps, CFG, denoise, dimensions, sampler/scheduler, and similar settings;
- image, mask, video, and audio source inputs;
- checkpoint, VAE, text-encoder, LoRA, ControlNet, and other common model selectors;
- likely image, video, audio, text, or file outputs.

These are suggestions only. Neo attaches a native control descriptor to **every detected candidate** (for example textarea, seed, slider, model selector, image/video/audio uploader). Candidates are never deleted because Neo thinks they look internal. Instead, Neo recommends an initial exposure state using **node role + field role + runtime metadata**, not the field name alone. For example, `KSampler.steps` can be recommended while `AddLabel.height` remains optional even though both look like common numeric controls; `system_prompt`, device/dtype settings, output filenames, annotation fields, and similar implementation controls normally start not exposed. All candidates remain reviewable in **Admin → Comfy Spaces → Controls**, and the Space author has final authority. Reviewed bindings are validated against the frozen canonical workflow rather than being authored as arbitrary node edits in the browser.

## API-format workflows are still supported

If you already have a ComfyUI API-format workflow, Neo accepts it directly. Internally, Comfy Spaces use a canonical node-ID graph with `class_type` and `inputs` fields because that is the stable execution representation used by later binding/runtime layers.

The difference is now a Neo implementation detail rather than a requirement users must understand before opening the Builder.

## Runtime dependency check

When a ComfyUI backend profile is selected for import, Neo automatically compares the normalized graph against that exact runtime by reading its `/object_info` catalog. Neo does not silently switch runtimes.

This can report:

- referenced node classes that are available or missing;
- whether a node appears to come from Comfy core or a custom-node package;
- live input/output socket information;
- selected model availability when the Comfy node exposes its model list as a selectable catalog; Neo first trusts exact catalog values, then can reconcile normalized separators or one **unique filename/basename** match for saved workflows that carry an extra/old relative folder prefix; ambiguous filename matches fail closed instead of being guessed;
- machine-specific absolute model/file paths that must be remapped before the Space can become portable.

Neo does not automatically switch to another backend profile if the selected one is missing something. The selected runtime's `/object_info` combo values are compatibility evidence, not install authority. If a model field has no usable live combo catalog, Neo reports it as **Unchecked** rather than falsely Ready. If live dependency checking is skipped, publication can proceed with an explicit warning, but later execution still requires runtime preflight and must resolve the target runtime's accepted model value again before queueing.

## What import does not do

Import analysis does **not**:

- execute the workflow;
- install custom nodes;
- download models;
- run Python or JavaScript from the Space inside Neo;
- publish the workflow by itself;
- modify the user's source JSON in place.

Comfy custom nodes remain executable code inside the connected Comfy runtime and should be installed/trusted separately.

## Universal type handoff

Analysis reports include `neo.comfy_space.type_system.v1` descriptors for detected inputs and outputs. This keeps workflow analysis independent from any one media surface: a hybrid workflow can expose a prompt textarea and image uploader while rendering its final result as a video player. NCS-4 provides the separate `neo.comfy_space.binding_engine.v1` clone/binding contract, NCS-6 owns neutral asset transport, and NCS-7 now owns selected-runtime execution/history/output collection.
