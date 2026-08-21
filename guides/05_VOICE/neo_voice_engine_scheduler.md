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
version: 6
updated: 2026-08-21
---

# Neo Voice Engine GPU Scheduler + Model Lifecycle

VO-E4 created the resource-control layer. **VO-E5 exercises it with Chatterbox, Qwen3-TTS Phase 4.2 adds explicit cold-load vs resident-reuse admission for heavyweight managed workers, Phase 4.3 measures 1.7B CUDA usage, and Phase 4.4 adds a split total-capacity/cold-free-VRAM production contract.**

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

Chatterbox does not require a synchronous acquisition `/load` operation. Phase 4.6 moves weight acquisition to Admin → Models, so scheduler admission sees only models whose local snapshot probe passes. The worker still constructs the selected model lazily from that verified local path during an async job, while explicit unload remains supported.

## CUDA telemetry

The gateway does not import Torch/NVML. It uses local `nvidia-smi` when available and exposes telemetry through:

```text
GET /api/voice/scheduler
```

If CUDA telemetry is unavailable, execution may continue only through a manifest-declared CPU fallback.

## Admission rules

1. CPU-only models do not require a GPU lease.
2. CUDA-capable models prefer a device satisfying the model's cold-load free-VRAM requirement after the configured reserve.
3. Legacy manifests continue to use `min_vram_mb` as the cold-load free-VRAM floor and `recommended_vram_mb` as the reservation target. New manifests may additionally declare `min_total_vram_mb`, `cold_load_free_vram_mb`, and `recommended_total_vram_mb` to separate GPU capacity class from current free-memory admission.
4. When `cold_load_free_vram_mb` is present it supersedes `min_vram_mb` for cold admission; when it is absent, legacy semantics are unchanged. `min_total_vram_mb` is checked against the device's reported total VRAM before free-memory admission.
5. A scheduler residency record alone never proves that model VRAM is already paid for. Resident reuse requires a healthy managed worker plus a read-only lifecycle response confirming the exact same `model_id` is resident on the expected CUDA device.
6. Confirmed same-model resident work uses `admission_mode=resident_reuse`: the full model minimum is not charged a second time, the lease reserves `0` duplicate model MB, and only the configured generic GPU safety reserve must remain available.
7. If lifecycle confirmation is missing/stale, reports another/unloaded model, or changes before dispatch, Neo falls back/fails closed as cold-load work rather than assuming resident headroom.
8. GPU-exclusive work is serialized according to `NEO_VOICE_ENGINE_GPU_MAX_CONCURRENT_JOBS`.
9. CPU fallback occurs only when the manifest explicitly allows it.
10. Unsafe CUDA-only work fails before worker submit.
11. External workers are never killed for VRAM reclamation.


### 1.7B calibration and Phase 4.4 production policy

Phase 4.3 measured the actual `qwen3_tts_17b_custom_voice` path on the target RTX 3060. The direct calibration loaded and generated successfully with 12287 MB total VRAM, 11255 MB free before load, 4230 MB peak process reservation during generation, and 7105 MB free after generation. Model load took 24.781 seconds and generation took 17.992 seconds; no CUDA OOM occurred.

Phase 4.4 converts that evidence into a production contract without claiming a lower untested hardware class:

```text
min_total_vram_mb          = 12000
cold_load_free_vram_mb     = 6144
recommended_total_vram_mb  = 16384
legacy/free floor           = 6144
```

The 6144 MB free floor is deliberately above the observed 4230 MB peak model reservation and is evaluated **after** Neo's separate generic GPU reserve (512 MB by default). The 12000 MB total-capacity floor means the physically validated 12 GB class can be admitted while 8 GB-class compatibility remains unclaimed. Once the exact model is confirmed resident, Phase 4.2 `resident_reuse` semantics still apply and the cold-load floor is not charged again.

### Cold-load vs resident-reuse example

For Qwen3-TTS 0.6B, the manifest cold-load floor remains 8192 MB. Once that exact model is confirmed resident, a later generation does **not** need another 8192 MB of free VRAM merely to reuse the same weights. It needs the normal configured safety reserve (default `NEO_VOICE_ENGINE_GPU_VRAM_RESERVE_MB=512`) plus any other scheduler reservations. This fixes resident-model VRAM double counting without lowering the cold-load requirement.

`GET /api/voice/scheduler` now exposes `last_admission` plus per-lease admission fields including `admission_mode`, `observed_free_vram_mb`, and `effective_free_vram_mb`. These diagnostics are intended to make repeated physical tests auditable without importing Torch/NVML into the gateway.

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

A managed `startup_policy: on_demand` worker that has never been launched may remain `stopped` without making gateway health degraded. Legacy `auto_start: true` manifests normalize to the same policy. Discovery never starts it; first executable work starts it only after install/model admission passes.

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

## Phase 4.6 acquisition boundary

Scheduler lifecycle is execution lifecycle, not download lifecycle. For managed Chatterbox and Qwen routes, missing-model state fails before dispatch; scheduler/controller code must not repair that state by fetching weights. Admin → Models owns install/repair, and the worker remains local-only.
