---
guide_id: comfy_spaces.publish_gate
title: Comfy Spaces Publish Gate
surface: admin
scope: built_in
applies_to:
  - admin
  - comfy_spaces
tags:
  - comfyui
  - publishing
  - validation
  - portability
  - trust
priority: 84
version: 5
updated: 2026-10-07
---

# Comfy Spaces Publish Gate

**Admin → Comfy Spaces → Publish** turns a saved Builder draft into a versioned declarative Space package. Publishing does **not** run ComfyUI.

## Check publish readiness first

Use **Check publish readiness** on the saved draft revision. Neo deliberately recompiles from the saved Builder draft and frozen workflow/analysis data instead of trusting the Preview files.

The gate blocks publication when it finds issues such as:

- stale Builder revision;
- invalid manifest or Space UI contracts;
- invalid or missing reviewed bindings;
- confirmed Graph Anchors that no longer match the frozen import analysis;
- no exposed output;
- workflow provenance/hash mismatch;
- unresolved machine-specific portability paths;
- required nodes/models that were live-checked and are known missing;
- unsafe or missing presentation assets;
- trust settings that would permit executable Neo code or automatic dependency installation;
- an already-published package with the same Space ID and semantic version.

## Graph Anchors and runtime add-ons

Publishing stores only the author-confirmed `graph_anchors.json` metadata needed to identify reviewed attachment points. It does **not** bake a temporary runner Graph Add-on into `workflow.json`.

At run time, the selected Comfy runtime must still prove that the requested add-on nodes/models/socket types are available. A ControlNet or raw-node add-on can therefore be available on one runtime and unavailable on another without changing the published Space package.

## Dependency warnings

A Space may be authored for another Comfy installation, so Neo does not require every dependency to be live-checked on the authoring machine.

If dependency state was **not checked**, the gate warns instead of claiming readiness. The NCS-7 execution runtime always re-preflights the chosen Comfy runtime before a run can start.

If a selected runtime was checked and required nodes/models are **known missing**, publication is blocked until that draft/workflow is corrected or re-imported against an appropriate runtime.

## Published versions are immutable

Publication writes a versioned package to Neo's Comfy Spaces library. Neo will not overwrite an existing `Space ID + version` directory.

If you change the draft after publishing, update its version before publishing again. This prevents an already-published version from silently changing underneath future run history or shared references.

## What gets published

The current package contains the reviewed declarative authorities used by the NCS-7 execution runtime and the top-level Comfy Spaces runner:

```text
space_manifest.json
workflow.json
space_ui.json
graph_anchors.json
publication.json
```

Safe referenced presentation image assets may also be copied when present. Models, custom-node code, Python/JavaScript, generated media, and local run assets are not bundled.

`graph_anchors.json` contains only author-confirmed NCS-10A anchor definitions and explicitly has no topology-mutation or execution authority. `publication.json` records the Builder revision and package/provenance hashes used for the promotion, including the Graph Anchor sidecar hash. The published-package loader re-parses and re-hashes this sidecar before accepting a new-format package, so tampering is detected even though anchors are not yet used for execution.

## What Publish does not do

Publishing does not:

- submit the workflow to ComfyUI;
- upload or resolve user media for a run;
- install custom nodes or models;
- execute from Builder Preview;
- execute or modify the top-level Comfy Spaces library; publishing only promotes the immutable package that the library later consumes.

The separate NCS-7 backend runtime executes a published package only after rechecking the publication receipt, package hashes, selected-runtime dependencies, current bound values, and assets. NCS-8 owns persistent long-running progress/recovery on top of that execution authority, and the top-level NCS-9 Comfy Spaces workspace now exposes those published packages to users.
