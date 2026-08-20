---
guide_id: voice.neo_voice_engine_contract
title: Neo Voice Engine Contract
surface: voice
scope: built_in
applies_to:
  - voice_generation
  - voice_reference
  - voice_clone
  - backend_profiles
  - voice_engine
tags:
  - voice
  - tts
  - voice cloning
  - voice engine
  - backend architecture
  - local backend
  - worker isolation
  - provider contract
priority: 69
version: 4
updated: 2026-08-11
---

# Neo Voice Engine Contract

**VO-E1** freezes the API boundary for the combined local Voice backend. **VO-E2 implements the lightweight gateway/supervisor physically, VO-E3 makes model/worker identity manifest-driven, VO-E4 adds GPU/resource admission plus model lifecycle/recovery, and VO-E5 migrates the tested Chatterbox worker behind that boundary.** Neo now uses `voice.neo_engine` as the default Voice profile while preserving `voice.chatterbox` as a non-default direct diagnostic fallback.

## Why this exists
Neo's current Chatterbox integration proved that heavy model dependencies should stay outside Neo's primary Python environment. The long-term Voice architecture extends that rule to every model family:

```text
Neo Studio
    |
    | one selected Voice backend profile
    v
Neo Voice Engine gateway
    |
    +-- Chatterbox worker (isolated runtime)
    +-- Qwen worker       (isolated runtime)
    +-- VoxCPM worker     (isolated runtime)
    +-- CosyVoice worker  (isolated runtime)
    +-- VC worker(s)      (isolated runtime)
```

The gateway gives Neo one stable API while workers remain replaceable.

## What stays inside Neo
Neo continues to own:
- Backend Profile selection;
- common Voice settings;
- reference upload, authorization and QC;
- Voice Profile Assets;
- Dialogue and Batch orchestration;
- Results/replay/project lineage;
- final output storage;
- Finish processing.

The engine only performs model execution and temporary output handoff.

## Stable v1 endpoints

```text
GET  /api/voice/health
GET  /api/voice/capabilities
GET  /api/voice/models
GET  /api/voice/voices
GET  /api/voice/controls?model_id=...&mode=...
GET  /api/voice/registry                 # VO-E3 diagnostic extension
GET  /api/voice/scheduler                # VO-E4 resource/lifecycle diagnostics
POST /api/voice/render
GET  /api/voice/jobs/{provider_job_id}
POST /api/voice/jobs/{provider_job_id}/cancel
GET  /api/voice/jobs/{provider_job_id}/output
```

The paths intentionally match the current Voice adapter grammar so existing R4/R6 generation can migrate without inventing another frontend/backend protocol.

## One backend does not mean one environment
The gateway/supervisor should be lightweight. Models with conflicting Torch, Transformers, CUDA, phonemizer, or native-library requirements can run in separate workers/virtual environments.

A worker crash should fail its provider job. It must not crash Neo Studio or require rebuilding Neo's main `.venv`.

## Model routing
Neo selects a public `model_id`. Every model catalogue record also declares its internal `engine_id`.

The gateway resolves:

```text
model_id -> engine_id -> worker -> physical model
```

Neo must not infer the worker from model names, and adding a new engine should not require a new Neo Backend Profile.

## Current generation compatibility
The gateway accepts the current request schemas:
- TTS: `neo.voice.provider_generation_request.v1`
- Clone: `neo.voice.provider_clone_request.v1`

The gateway returns a distinct provider job ID and Neo continues to maintain its own durable local job ID.

Public provider job states stay simple:
- queued
- running
- completed
- failed
- cancelled

Detailed work such as model download/loading belongs in `progress.stage`.

## Output rule
The provider may expose completed audio at the same-host job output endpoint. Neo downloads/copies it and only then marks the local Voice job complete.

Provider files are temporary. Neo-owned Results under `neo_data/outputs/voice/` remain final authority.

## Clone rule
Reference cloning still requires the existing R6 authorization and QC checks. A local path may be used only for a same-machine/loopback gateway and must remain under an explicitly configured Neo reference root.

## Future capabilities
The v1 vocabulary reserves future tasks such as voice design and voice conversion, but VO-E1 does not activate them in the current UI. They become current only after dedicated implementation/validation milestones.

## Migration plan
1. **VO-E1 — complete:** protocol/ownership contract freeze.
2. **VO-E2 — complete:** standalone gateway/supervisor, provider jobs, temporary output handoff, worker compatibility seam.
3. **VO-E3 — complete:** manifest-driven worker/model identity, install/hardware/license/source metadata, fail-closed conflicts, registry diagnostics.
4. **VO-E4 — complete:** GPU-aware admission, model residency/load/unload/eviction policy, bounded managed-worker recovery.
5. **VO-E5 — complete:** register the tested Chatterbox service as the first active manifest-owned worker, route the default `voice.neo_engine` profile through the gateway, and retain the direct Chatterbox profile only as legacy fallback.
6. **Next model-family milestone:** add additional workers such as Qwen3-TTS through the same manifest/worker contract instead of creating new Neo-facing provider stacks.
7. Onboard additional TTS/VC workers without adding Neo-facing backend profiles.

## Music is separate
The future Music backend should be a separate Neo Music Engine. Voice and Music may share architectural conventions but should not share one ML runtime or one provider service.

For the canonical locked field/schema rules, see `neo_system_records/03_PROVIDER_SYSTEM/VOICE_ENGINE_CONTRACT_VO_E1_20260811.md`. For the current physical stack, see `guides/05_VOICE/neo_voice_engine_gateway.md`, `guides/05_VOICE/neo_voice_engine_registry.md`, `guides/05_VOICE/neo_voice_engine_scheduler.md`, and the VO-E2/VO-E3/VO-E4/VO-E5 provider records.
