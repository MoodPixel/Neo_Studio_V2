---
guide_id: comfy_spaces.long_running_progress
title: Comfy Spaces Long-Running Jobs and Live Graph Progress
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - jobs
  - progress
  - recovery
  - websocket
priority: 87
version: 3
updated: 2026-10-07
---

# Comfy Spaces Long-Running Jobs and Live Graph Progress

Neo Studio uses the NCS-8 runtime layer for **navigation-safe, long-running Comfy Space jobs**. The top-level Comfy Spaces runner now consumes this persistent progress contract; Builder Preview still does not execute workflows.

## What NCS-8 adds

A published Space run no longer depends on one browser page staying open to know what the job is doing.

Neo stores run lineage and the latest progress snapshot in its existing generation job registry, while a backend observer listens to ComfyUI's live execution events when available.

```text
published Space run
        ↓
NCS-7 selected-runtime execution
        ↓
Comfy prompt_id + unique client_id
        ↓
NCS-8 backend progress observer
        ├─ websocket execution events
        ├─ current node / node class
        ├─ sampler step progress
        ├─ cached/completed nodes
        └─ queue state
        ↓
persistent Neo job record
        ↓
run_id-based progress snapshot / event cursor
```

A browser can leave the current **Comfy Spaces** workspace and later reconnect using the same Neo `run_id`. It does not need direct access to the Comfy backend URL or its filesystem.

## Live graph progress

When the selected ComfyUI runtime provides websocket execution events, Neo understands the core progress families used by current ComfyUI runtimes, including:

- workflow execution start;
- cached nodes;
- current executing node;
- step progress (`value / max`);
- richer `progress_state` node state;
- node completion;
- execution success;
- execution error/interruption;
- queue/status updates.

Neo stores only small progress facts. Binary websocket preview frames are **not** persisted in the run registry.

## Human-readable current-node state

The runtime keeps a compact graph plan made from node IDs, class types, optional Comfy titles, dependencies and stable graph order. It does not persist the user's bound prompt/media values in that progress plan.

That plan lets Neo present useful state such as:

```text
Sampling · 12/30
Decoding output
Encoding video
Finalizing output
```

The API also keeps the exact current node ID/class available for technical diagnostics without forcing those details into the normal progress display.

## Progress percentage is intentionally honest

Not every Comfy node reports a meaningful numeric percentage. Neo therefore distinguishes the source of a displayed percentage:

- `phase` — broad queue/preparing/finalizing state;
- `graph_estimate` — position/completed-node estimate across the graph;
- `node_step` — a node is reporting real step/max progress;
- `terminal` — completed/cancelled/failed state.

Neo does not claim that graph-position estimates are exact GPU-time estimates.

## Websocket is telemetry, not completion authority

A websocket can disconnect or become quiet while a Comfy prompt is still running. NCS-8 therefore does **not** make websocket liveness authoritative.

If live progress drops:

1. the run remains persistent;
2. Neo marks the observer reconnecting;
3. NCS-7 history/queue polling is used to reconcile actual backend state;
4. the observer reconnects when possible;
5. the shared GPU lease remains governed by the existing NCS-7 lifecycle rules.

This prevents a progress-socket failure from being mistaken for a failed generation.

## Startup/restart recovery

During Neo startup, NCS-8 scans persisted non-terminal Comfy Space jobs. Jobs that still have selected-runtime, client and prompt lineage are reconciled through NCS-7 history/queue state and receive a new progress observer.

Already terminal jobs remain historical records and are not re-run.

Recovery never submits a duplicate prompt.

## Cancellation feedback

Cancellation remains an NCS-7 execution action, but NCS-8 now records the user-visible transition:

```text
running
   ↓
cancel_requested
   ↓
waiting for Comfy interruption confirmation
   ↓
cancelled
```

Neo does not release the shared GPU lease merely because the user clicked Cancel. It waits for the same backend confirmation/recovery rules used by NCS-7.

## Run history/progress API foundation

The top-level Comfy Spaces runner uses the NCS-8 runtime endpoints below for current run history, progress, reconnect, polling, cancellation, and recovery:

```text
GET  /api/comfy-spaces/runtime/runs
GET  /api/comfy-spaces/runtime/runs/{run_id}
GET  /api/comfy-spaces/runtime/runs/{run_id}/progress
WS   /api/comfy-spaces/runtime/runs/{run_id}/progress/ws
POST /api/comfy-spaces/runtime/runs/{run_id}/poll
POST /api/comfy-spaces/runtime/runs/{run_id}/cancel
POST /api/comfy-spaces/runtime/recover
```

The Neo progress websocket is backed by the persistent registry. The browser reconnects to Neo by `run_id`; it is not handed a ComfyUI base URL/client websocket directly.

## Current product boundary

The top-level NCS-9 **Comfy Spaces** workspace now renders this progress/recovery state. NCS-8 still does **not** add:

- a second Space renderer;
- automatic custom-node/model installation;
- runtime fallback to another Comfy profile;
- persisted binary live previews;
- generated Python/JavaScript per Space;
- a second job database or scheduler.

NCS-9 reuses the NCS-5C renderer, NCS-7 execution service, and this NCS-8 run/progress contract rather than creating parallel authorities.
