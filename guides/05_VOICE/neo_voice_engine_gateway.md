---
guide_id: voice.neo_voice_engine_gateway
title: Neo Voice Engine Gateway / Supervisor
surface: voice
scope: built_in
applies_to:
  - voice_engine
  - voice_generation
  - voice_clone
  - local_backend
  - backend_architecture
tags:
  - voice
  - tts
  - gateway
  - supervisor
  - workers
  - local backend
  - isolated runtime
priority: 70
version: 5
updated: 2026-08-11
---

# Neo Voice Engine Gateway / Supervisor

VO-E2 created the lightweight gateway, VO-E3 made it manifest-driven, VO-E4 added GPU/resource control, VO-E5 migrated the tested Chatterbox runtime behind it, and **VO-E5A moves mutable Voice runtimes out of the Neo Studio source tree**.

## Current topology

```text
Neo Studio Voice runtime
   |
   | selected default profile: voice.neo_engine
   v
Neo Voice Engine :8790
   |
   +-- manifest registry
   +-- GPU/resource scheduler
   +-- async nve_* jobs + cancellation
   +-- temporary output handoff
   +-- worker supervisor
          |
          +-- Chatterbox :8791
              managed / auto-start on demand
              Neo_Runtime/voice/envs/chatterbox
              chatterbox_turbo
              chatterbox_multilingual
```

`voice.chatterbox` on `8791` remains enabled but non-default as a legacy direct diagnostic/fallback route. On existing installs, Neo seeds the new gateway profile into the runtime profile store and migrates only the historical direct-Chatterbox default; an explicitly different/custom Voice default is preserved.

## Setup

For the current Chatterbox-backed deployment:

```text
setup_chatterbox_backend.bat
setup_neo_voice_engine.bat
run_neo_voice_engine.bat
```

The normal Neo profile is:

```text
Voice · Neo Voice Engine
http://127.0.0.1:8790
```

The gateway environment installs only FastAPI/Uvicorn under `Neo_Runtime/voice/envs/gateway`. Chatterbox remains isolated under `Neo_Runtime/voice/envs/chatterbox`.

## Cold worker behavior

Gateway startup does not eagerly start or load Chatterbox. A managed worker that has not yet been launched reports `stopped` / `auto_start=true`, which is a valid idle state rather than a backend failure.

On first executable Chatterbox request the gateway:

1. resolves the manifest-owned model;
2. performs scheduler admission;
3. starts/probes the worker when needed;
4. passes a private execution hint;
5. submits the normal async Chatterbox job;
6. polls/cancels through the worker seam;
7. copies completed audio into gateway temporary output;
8. returns the existing provider-job/output contract to Neo.

Chatterbox model weights still lazy-download/load inside the async worker job. VO-E5 intentionally avoids a synchronous first-use `/load` call because model acquisition may exceed normal HTTP control timeouts.

## External runtime root

Default runtime layout is `<Neo parent>/Neo_Runtime/voice/`, containing `envs/`, `models/`, `cache/`, `temp/`, `logs/`, `state/`, `outputs/`, and `legacy_backups/`. The gateway environment is `envs/gateway`; Chatterbox is `envs/chatterbox`. These are runtime/provider artifacts. Neo remains final Voice Result authority under `neo_data/outputs/voice/`.

Use `NEO_RUNTIME_ROOT` to relocate the shared Neo runtime or `NEO_VOICE_RUNTIME_ROOT` to relocate Voice only. Existing `NEO_VOICE_ENGINE_DATA` remains a backward-compatible explicit override.

## Environment overrides

```text
NEO_VOICE_ENGINE_HOST
NEO_VOICE_ENGINE_PORT
NEO_RUNTIME_ROOT
NEO_VOICE_RUNTIME_ROOT
NEO_VOICE_ENGINE_DATA  # legacy explicit alias
NEO_VOICE_ENGINE_REFERENCE_ROOT
NEO_VOICE_ENGINE_ALLOW_LOCAL_REFERENCE_PATHS
NEO_VOICE_ENGINE_WORKER_POLL_SECONDS
NEO_VOICE_ENGINE_WORKER_START_TIMEOUT_SECONDS
NEO_VOICE_ENGINE_WORKER_HTTP_TIMEOUT_SECONDS
NEO_VOICE_ENGINE_MAX_CONCURRENT_JOBS
NEO_VOICE_ENGINE_MANIFEST_DIRS
NEO_VOICE_ENGINE_GPU_MAX_CONCURRENT_JOBS
NEO_VOICE_ENGINE_GPU_VRAM_RESERVE_MB
NEO_VOICE_ENGINE_GPU_PROBE_TIMEOUT_SECONDS
NEO_VOICE_ENGINE_SCHEDULER_WAIT_TIMEOUT_SECONDS
NEO_VOICE_ENGINE_MODEL_IDLE_UNLOAD_SECONDS
NEO_VOICE_ENGINE_WORKER_MAX_RESTARTS
NEO_VOICE_ENGINE_WORKER_RESTART_WINDOW_SECONDS
NEO_VOICE_ENGINE_WORKER_RESTART_BACKOFF_SECONDS
```

## Chatterbox migration guarantees

- no Chatterbox ML dependency moved into Neo or the external gateway environment;
- `chatterbox_turbo` and `chatterbox_multilingual` are manifest-owned public IDs;
- undeclared worker models remain ignored;
- R6 clone authorization/QC/reference-root rules are revalidated at the gateway;
- the gateway scheduler selects CPU/CUDA before worker submit;
- cancellation and direct-audio-on-complete polling remain supported;
- current Chatterbox synthesis code was adapted, not rewritten;
- legacy direct profile remains available for diagnosis.

## Diagnostics

```text
GET /api/voice/health
GET /api/voice/registry
GET /api/voice/scheduler
GET /api/voice/models
GET /api/voice/capabilities
```

See `neo_voice_engine/manifests/chatterbox.json`, `guides/05_VOICE/neo_voice_engine_registry.md`, `guides/05_VOICE/neo_voice_engine_scheduler.md`, and `guides/05_VOICE/external_voice_runtime.md`.

## Next milestone

With the first real worker accepted end-to-end, the next model-family milestone can add Qwen3-TTS behind the same manifest/worker boundary rather than creating another Neo-facing backend.
