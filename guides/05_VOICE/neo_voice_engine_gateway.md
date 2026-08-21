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
version: 7
updated: 2026-08-21
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


## Qwen3-TTS Admin/HF runtime binding — Phase 4.5.7

Qwen selected-model admission now uses one offline runtime resolver shared by the manifest install probe and the isolated worker. Resolution order is **complete legacy Neo Runtime snapshot → authoritative Admin-managed Hugging Face cache snapshot → `model_not_installed`**. The HF path is accepted only when the Phase 4.5.5 requested-revision/materialization/content probe returns `installed`.

This does not add any download behavior to the gateway or worker. Managed Qwen launch remains `NEO_QWEN3_TTS_LOCAL_ONLY=1`, so generation cannot fall through to a remote repository ID. Existing users with Phase 3 local snapshots keep those paths unchanged and first in precedence.

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

Chatterbox model objects still load lazily inside the async worker job, but Phase 4.6 removes model acquisition from generation. The selected model must first pass the local Admin/Hugging Face snapshot probe; the worker receives a local snapshot path and uses `from_local()`. Managed generation is offline/local-only and never invokes an upstream model download.

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

Qwen3-TTS reuses this same manifest/worker boundary: Phase 2 added the isolated worker, Phase 3 activated managed local-only install/model state, and Phase 4 exposes installed CustomVoice through the normal TTS surface. Qwen uses `startup_policy: on_demand`: gateway health/catalog/control refresh remains read-only, and the worker may start only after runtime + selected-model readiness passes. Phase 4.4.2 hardens provider-control discovery further: manifest-owned control metadata is read directly from the already-loaded registry before model resolution, install/executable checks, worker probes, or worker startup. Unsupported model modes return an authoritative empty control contract. Dynamic worker control discovery remains only as a compatibility fallback for supported modes without manifest-owned definitions. Opening the Voice UI therefore does not wake `:8792`.

## Phase 4.6 Voice model lifecycle unification

The gateway now applies one acquisition/execution principle to its executable Admin-managed Voice families:

- **Qwen CustomVoice:** legacy complete Neo Runtime snapshot first for compatibility, then authoritative Admin HF snapshot.
- **Chatterbox Turbo / Multilingual V3:** authoritative Admin HF snapshot only; existing upstream-created HF cache materializations are valid if the local probe passes.
- Worker setup owns dependencies only.
- Admin → Models owns model acquisition/repair.
- Registry install probes and physical loaders consume the same local truth.
- Generate never downloads missing weights.

The Chatterbox managed worker starts with `NEO_CHATTERBOX_LOCAL_ONLY=1`, `HF_HUB_OFFLINE=1`, and `TRANSFORMERS_OFFLINE=1`. A missing/partial snapshot therefore fails before synthesis instead of silently reaching the network.
