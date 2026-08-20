---
guide_id: voice.neo_voice_engine_scheduler
title: Neo Voice Engine GPU Scheduler + Model Lifecycle
surface: voice
scope: built_in
applies_to:
  - voice_engine
  - voice_generation
  - voice_clone
  - local_backend
  - backend_architecture
  - gpu_scheduling
  - model_lifecycle
tags:
  - voice
  - tts
  - scheduler
  - gpu
  - vram
  - model lifecycle
  - worker recovery
priority: 72
version: 2
updated: 2026-08-11
---

# Neo Voice Engine GPU Scheduler + Model Lifecycle

VO-E4 created the resource-control layer. **VO-E5 now exercises it with the first real manifest-owned worker: Chatterbox.**

## Execution order

```text
Neo provider request
  -> resolve model_id -> engine_id
  -> scheduler admission
  -> managed worker start/readiness
  -> optional lifecycle prepare
  -> worker async submit
  -> worker poll/output
  -> gateway temporary output
  -> release lease / idle lifecycle
```

## Chatterbox VO-E5 behavior

The Chatterbox manifest declares CPU/CUDA support and VRAM guidance. The gateway selects the execution device before submit and injects the private `_neo_execution` hint. The worker applies that hint before its lazy model load.

Chatterbox does not expose a synchronous first-use `/load` operation in VO-E5. This is intentional: first-time model download/loading can exceed normal control-call timeouts. Its lifecycle remains `implicit` until generation loads the model, while explicit unload is supported.

## CUDA telemetry

The gateway does not import Torch/NVML. It uses local `nvidia-smi` when available and exposes telemetry through:

```text
GET /api/voice/scheduler
```

If CUDA telemetry is unavailable, execution may continue only through a manifest-declared CPU fallback.

## Admission rules

1. CPU-only models do not require a GPU lease.
2. CUDA-capable models prefer a device satisfying minimum VRAM after the configured reserve.
3. `recommended_vram_mb` is a preferred reservation target; `min_vram_mb` is the admission floor.
4. GPU-exclusive work is serialized according to `NEO_VOICE_ENGINE_GPU_MAX_CONCURRENT_JOBS`.
5. CPU fallback occurs only when the manifest explicitly allows it.
6. Unsafe CUDA-only work fails before worker submit.
7. External workers are never killed for VRAM reclamation.

## Lifecycle / eviction

Optional worker operations:

```text
GET  /api/voice/models/{model_id}/lifecycle
POST /api/voice/models/{model_id}/load
POST /api/voice/models/{model_id}/unload
```

Workers need not implement every operation. Chatterbox implements lifecycle + unload but intentionally omits synchronous load.

Gateway safe unload:

```text
POST /api/voice/models/{model_id}/unload
```

Only idle, evictable models can be reclaimed. The gateway may stop a managed idle worker when policy permits and the worker unload API is unavailable; it never stops an external worker for reclamation.

## Managed-worker cold state and recovery

A managed `auto_start` worker that has never been launched may remain `stopped` without making gateway health degraded. It will start on first executable request.

Actual crashes use bounded recovery:

```text
NEO_VOICE_ENGINE_WORKER_MAX_RESTARTS=2
NEO_VOICE_ENGINE_WORKER_RESTART_WINDOW_SECONDS=120
NEO_VOICE_ENGINE_WORKER_RESTART_BACKOFF_SECONDS=0.25
```

A failed synthesis is not automatically replayed. Once the restart budget is exhausted the worker becomes `recovery_exhausted`.

## Error vocabulary

- `hardware_unavailable`
- `gpu_oom`
- `scheduler_timeout`
- `model_busy`
- `worker_recovery_exhausted`

These remain structured failures under the existing public job-state vocabulary.
