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
version: 8
updated: 2026-08-20
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
6. **Qwen3-TTS Phase 1 — complete audit only:** freeze the next-family capability/runtime contract without registering a worker or changing Voice behavior. Five released 12Hz roles are mapped; Voice Design protocol gaps, clone modes, live speaker/language discovery, isolated environment ownership, and unvalidated hardware admission are explicit.
7. **Qwen3-TTS Phase 2 — complete worker milestone:** isolated `qwen3_tts` worker, external environment/model roots, live model/voice/control discovery, lifecycle load/unload, async WAV jobs, CustomVoice, Base clone, and direct VoiceDesign dispatch. The manifest remains disabled, so normal product activation is still gated.
8. **Qwen3-TTS Phase 3 — complete registry/install milestone:** activate the managed manifest, local-only model registry, runtime/model install probes, explicit snapshot installer, family aliases, and conservative scheduler floors while keeping the normal Voice UI and VoiceDesign gateway task gated.
9. **Qwen3-TTS Phase 4 — UI/runtime activation + first physical generation:** installed/runtime-ready CustomVoice models can enter normal TTS; Language/Speaker map to common fields, 1.7B adds manifest-owned Voice Instruction, and model-control discovery remains non-starting. Real 0.6B CUDA/WAV generation succeeded physically and exposed a repeated-residency scheduler defect.
10. **Qwen3-TTS Phase 4.2 — resident VRAM admission hotfix:** split cold-load admission from lifecycle-confirmed same-model resident reuse, keep the 0.6B 8192 MB cold floor unchanged, release transient Qwen CUDA allocator cache after inference, expose admission diagnostics, and make terminal scheduler failures stop the Voice progress state. The post-hotfix repeated 0.6B physical retest passed.
11. **Qwen3-TTS Phase 4.3 — 1.7B VRAM calibration:** add an explicit diagnostic-only direct CUDA load/generation/unload tool after the conservative 12288 MB production floor blocked a GPU reporting 12287 MB total VRAM before model load. Physical calibration then succeeded: 4230 MB peak process reservation, 24.781 s model load, 17.992 s generation, no CUDA OOM.
12. **Qwen3-TTS Phase 4.4 — production VRAM admission calibration:** introduce generic split manifest semantics for `min_total_vram_mb` vs `cold_load_free_vram_mb`, admit the physically validated 12 GB-class 1.7B route with a 12000 MB capacity floor and 6144 MB cold-free floor, preserve Phase 4.2 resident reuse, and keep legacy `min_vram_mb` manifests compatible.
13. **Qwen3-TTS Phase 4.4.1 — provider-controls UI binding hotfix:** keep the persistent Script/Common Parameters rail TTS-scoped across workspace navigation, move clone-only provider controls into Reference, reject stale model-control responses after model switches, render long model text controls as multiline fields, and distinguish selected model/engine from the backend profile family without changing scheduler/runtime policy.
13. Onboard additional TTS/VC workers without adding Neo-facing backend profiles.

## Music is separate
The future Music backend should be a separate Neo Music Engine. Voice and Music may share architectural conventions but should not share one ML runtime or one provider service.

For the canonical locked field/schema rules, see `neo_system_records/03_PROVIDER_SYSTEM/VOICE_ENGINE_CONTRACT_VO_E1_20260811.md`. For the current physical stack, see `guides/05_VOICE/neo_voice_engine_gateway.md`, `guides/05_VOICE/neo_voice_engine_registry.md`, `guides/05_VOICE/neo_voice_engine_scheduler.md`, and the VO-E2/VO-E3/VO-E4/VO-E5 provider records.


For the Qwen-specific audit, isolated worker, active registry/install boundary, Phase 4 CustomVoice UI contract, Phase 4.2 resident-reuse scheduler rule, Phase 4.3 physical 1.7B calibration, Phase 4.4 split production VRAM admission, Phase 4.4.1 provider-control UI binding, and remaining clone/VoiceDesign gates, see `guides/05_VOICE/qwen3_tts.md`.
