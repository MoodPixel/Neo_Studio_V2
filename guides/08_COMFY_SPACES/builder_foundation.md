---
guide_id: comfy_spaces.builder_foundation
title: Comfy Spaces Builder Foundation
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - builder
  - drafts
  - preview
priority: 67
version: 10
updated: 2026-10-07
---

# Comfy Spaces Builder Foundation

Neo Studio's Builder draft and presentation contracts now power the visible **Admin → Comfy Spaces** Builder preview.

This guide explains the foundation beneath that screen. For the practical authoring flow, see **Comfy Space Builder Workspace**.

## What is already implemented internally

After a ComfyUI workflow has been imported and analyzed, Neo can create an independent Builder draft containing frozen copies of the **normalized canonical workflow** and its analysis. The import may start from the normal ComfyUI Save/workflow JSON or an API-format workflow; normal editor workflows are converted before this draft boundary.

That means a draft does not depend on the temporary import record remaining forever.

Neo can also compile that authoring state into a draft preview package:

```text
workflow.json
space_manifest.json-style preview
space_ui.json-style preview
```

The workflow is still not run during this process.

## Why Neo separates runtime, presentation, and anchor metadata

A Comfy Space now keeps three declarative concerns separate:

- the **manifest** describes trusted workflow bindings, requirements, and runtime behavior;
- the **Space UI** describes how those approved inputs/results should look in Neo;
- the **Graph Anchor sidecar** stores only author-confirmed typed attachment metadata discovered by NCS-10A.

Keeping them separate lets Neo preserve useful UI details such as sliders, searchable model selectors, sections, result viewers, and media controls without allowing presentation data to change the underlying Comfy graph. The anchor sidecar also carries explicit `topology_mutation_authority: false` and `execution_authority: false`; future graph add-ons must use a separate contract rather than smuggling topology changes through a binding or UI section.

## Draft safety

Builder drafts record the workflow/analysis hashes they were created from. If those frozen files are changed outside the Builder, Neo rejects the draft instead of silently compiling a different graph. Draft saves also carry a revision number so the current Builder can detect an older browser tab trying to overwrite newer edits.

Builder drafts now also persist the author's Graph Anchor confirmation choices against the frozen analysis proposals. A confirmed anchor must still exist with the same semantic role at compile time; unknown or role-mismatched confirmations fail closed.

When draft settings change, Neo invalidates any earlier compiled preview. A new preview is created only after the edited controls/results pass compatibility and binding checks.

Imported machine-specific paths are not copied through as if they were portable. The fail-closed publish gate now blocks unresolved portability issues before a package can enter the Space library.

## What is still unavailable

The current Admin Builder can edit draft controls/outputs/presentation, compile the contracts, mount them through the shared interactive Space renderer, and publish a validated declarative package. Builder Preview remains presentation/control-state only and does not submit workflows.

The top-level **Comfy Spaces** workspace now reuses that same renderer for published Spaces, while NCS-6 owns assets, NCS-7 owns selected-runtime execution, and NCS-8 owns persistent live progress/recovery. No second renderer, scheduler, or per-Space generated frontend/backend implementation is introduced.
