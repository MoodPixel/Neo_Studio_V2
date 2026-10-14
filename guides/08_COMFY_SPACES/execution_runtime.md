---
guide_id: comfy_spaces.execution_runtime
title: Comfy Spaces Execution Runtime
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - execution
  - runtime
  - jobs
  - outputs
priority: 86
version: 8
updated: 2026-10-07
---

# Comfy Spaces Execution Runtime

Neo Studio's NCS-7 execution engine is the backend execution authority for **published Comfy Spaces**. The top-level **Comfy Spaces** workspace now calls this runtime; Builder Preview remains non-executing.

## What happens when a published Space runs

Neo does not submit the stored workflow file directly.

```text
Published Space version
        ↓
Recheck package + publication receipt
        ↓
Reverify confirmed Graph Anchor + execution-stage sidecar integrity when present
        ↓
Resolve an optional exact-version Space Variant overlay
        ↓
Inspect the selected ComfyUI runtime
        ↓
Recheck base required nodes + current model choices
        ↓
Clone the stored workflow
        ↓
Apply selected per-run Graph Add-ons at confirmed anchors
        ↓
Resolve Neo media/file assets + bind current Space values
        ↓
Resolve the selected stage plan and prune to declared target outputs + ancestors
        ↓
Reanalyze the actual composed clone + dependencies
        ↓
Apply selected-runtime model-name reconciliation to the clone only
        ↓
Acquire Neo's shared Comfy GPU lease
        ↓
Queue the clone in the selected ComfyUI runtime
        ↓
Read Comfy history
        ↓
Resolve only the outputs declared by the Space
        ↓
Copy media/file outputs back into Neo-owned assets
```

The published `workflow.json` remains unchanged.

Newly published graph-aware Spaces may include `graph_anchors.json`. The runtime/package loader re-parses that sidecar and verifies its publication-receipt hash before treating the package as valid. The sidecar is still metadata rather than an executable graph patch.

When a run selects a Graph Add-on, NCS-10B uses only **author-confirmed anchors** from that verified sidecar plus the selected runtime's live `/object_info` to compose an execution clone. The base package is never rewritten.

## Stage-aware execution

Newly published stage-aware Spaces may include `stages.json`. The published-package loader re-parses that sidecar, verifies its receipt hash, and validates stage dependencies/output references before it is allowed to influence execution.

NCS-10C exposes three explicit run modes:

- **Run All** selects the configured/default-enabled stage set, pulls required dependencies, and submits one execution slice targeting the union of those stage outputs. It is not a hidden sequence of multiple `/prompt` calls.
- **Run Stage** selects one stage plus its declared dependency closure and submits only the target outputs and graph ancestors needed for that boundary.
- **Continue** requires a completed compatible predecessor run on the same Space version and Comfy runtime. Neo then submits the requested next-stage slice normally; ComfyUI decides whether unchanged upstream work can be reused from cache. Neo does not fabricate a resume token or claim cache reuse when Comfy cannot provide it.

Stages never translate to Comfy node bypass/mute state. UI sections remain presentation only. Optional stages are enabled/disabled in the execution plan, and stage dependencies are explicit author-defined metadata.

A stage may request a post-run resource policy: `automatic`, `keep_loaded`, `free_memory`, or `unload_models`. The explicit memory-release policies use Comfy's memory endpoint only after the stage run has reached a terminal completed state.

## Variant resolution

NCS-10D resolves an optional `variant_id` before Graph Add-on composition and stage planning. The Variant must still point to the exact requested published Space/version and its saved publication fingerprint must match the current `publication.json`. Missing or changed bases fail closed as stale; Neo never silently retargets a Variant to a newer Space version.

Runtime-effective values begin with the published Space defaults, then the Variant's saved non-media values are overlaid, followed by any explicit per-run edits. Variant Graph Add-ons and stage defaults are used when the run does not explicitly replace them. Run/preflight records retain `variant_id` and whether the request was an exact Variant run or a modified starting point.

Variants do not persist media/file Space inputs or structured Graph Add-on asset references. Those assets remain per-run NCS-6 inputs and must be selected again. A Variant with an asset-requiring module such as ControlNet therefore provides the reusable module/model/strength/anchor configuration, while the control image is supplied for the actual run.

## Persistent dependency health versus live preflight

NCS-12 adds runtime-local **Dependency Health** snapshots so Neo can report likely compatibility before Generate. Snapshots are stored per exact published Space/version/runtime and may include runtime inventory fingerprints, missing node/model details, stage readiness, Graph Add-on readiness, and package/module hints.

These snapshots are intentionally **not execution authority**. Validate/Generate still reads the selected runtime's live `/object_info` and reanalyzes the actual per-run clone after Variant/Add-on/stage composition. A Ready snapshot therefore cannot override a live queue-time blocker. Stale, missing, corrupt, or unreachable health evidence also does not make the immutable published Space itself invalid.

Neo never auto-installs dependencies from health evidence. Refreshing health is an explicit diagnostic action; Neo does not continuously poll every configured Comfy runtime in the background.

## Selected runtime only

A run uses the ComfyUI backend profile selected for that run. Neo does not silently retry on another Comfy server if that profile is missing a node, model, or connection.

Before queueing, Neo reads the selected runtime's `/object_info` again. This is execution-time validation, so a Space that was publishable on one machine can still be blocked on another machine when the required runtime dependencies are not available there.

## Graph Add-ons are composed before normal binding

A per-run Graph Add-on is validated and composed server-side before Neo binds the normal Space controls. This ordering lets the existing NCS-4 binding engine keep ownership of published Space values while the topology overlay stays a separate concern.

After composition Neo analyzes the modified clone again. Missing inserted node classes, incompatible runtime sockets/catalog values, stale confirmed edges, or conflicting anchor claims therefore block preflight before queue submission.

The initial reviewed ControlNet module inserts a runtime-native image loader, ControlNet loader, and two-conditioning apply node. The advanced raw-node lane is narrower: it only accepts a runtime node that Neo can prove has one compatible graph input, one compatible continuing output, and only safe literal controls for its other required inputs.

Added nodes carry Graph Add-on lineage into run records so progress and diagnostics can identify which temporary module/node introduced them.

## Model names are reconciled at run time

Portable workflows sometimes save a model under a relative path that differs from the selected Comfy installation's accepted loader value.

When the selected runtime proves one unambiguous match, Neo may use that accepted value in the **execution clone**. It does not rewrite the published Space package.

Ambiguous model matches still fail closed.

## Assets stay Neo-owned

Image, mask, video, audio, and file inputs use `neo_asset_ref` values. NCS-6 verifies the Neo-owned bytes and transports them to the selected Comfy runtime before NCS-7 binds the execution clone.

When Comfy finishes, declared media/file results are copied back into Neo-owned output assets. Result values therefore point back to Neo content URLs instead of exposing Comfy or local filesystem paths.

## Shared GPU and queue safety

Comfy Space runs use Neo's existing shared Comfy GPU lifecycle instead of introducing a second scheduler. If another Neo/Comfy job owns that local GPU resource, a Space run waits according to the configured lease behavior or fails as busy rather than overlapping work.

If `/prompt` times out after Comfy may already have accepted the request, Neo keeps a recovery guard on the GPU lease instead of assuming the queue call failed. The same fail-safe rule applies if Comfy returns a prompt ID but Neo-side tracking fails afterward.

## Run status and cancellation

The runtime records each Space run in Neo's shared generation job registry. NCS-7 owns execution/terminal authority, while NCS-8 now layers persistent progress snapshots, graph/node events, run listing, reconnect/recovery and navigation-safe progress streaming on the same records.

A queued prompt can be removed from Comfy's pending queue. A running prompt can request interruption, but Neo does not immediately free the shared GPU lease; it waits until Comfy confirms the prompt has actually stopped or finished.

NCS-8 now adds that richer persistent job/progress foundation. Live Comfy websocket telemetry is treated as progress-only evidence; NCS-7 history/queue remains the fallback/terminal authority when the websocket is quiet or disconnected.

## Result contract

A Space only returns outputs that were explicitly reviewed and published in its manifest. Neo does not dump every Comfy history artifact into the Space result.

Text/number/boolean/JSON values are returned directly. Image, mask, video, audio, and file results are imported through the neutral asset bus and returned as Neo asset references/content URLs.

## Current product boundary

The top-level **Comfy Spaces** workspace now provides the public library/runner and calls this execution authority. NCS-7 still does **not** add or permit:

- automatic model or custom-node installation;
- generated Python/JavaScript per Space;
- runtime fallback to a different Comfy profile;
- direct execution from Builder Preview;
- arbitrary client-defined graph JSON/connections;
- a second Space-specific GPU scheduler/job registry;
- automatic Variant migration/retargeting to a different published Space version;
- persisted media/file values or Graph Add-on asset references inside a Variant;
- value-only Preset semantics (that remains a separate contract).

NCS-8 remains persistent jobs/progress/recovery authority, and NCS-9 is the user-facing host that reuses the shared renderer plus these runtime contracts.
