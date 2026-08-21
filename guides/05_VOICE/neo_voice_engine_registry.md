---
guide_id: voice.neo_voice_engine_registry
title: Neo Voice Engine Manifest Registry
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
  - registry
  - manifest
  - workers
  - models
  - license
  - hardware
priority: 71
version: 8
updated: 2026-08-21
---

# Neo Voice Engine Manifest Registry

VO-E3 establishes durable manifest ownership, VO-E4 consumes hardware/lifecycle metadata, VO-E5 activates Chatterbox, and **VO-E5A adds portable `voice_runtime` path scope for external worker environments**. Qwen3-TTS Phase 4 now consumes the reviewed manifest for installed CustomVoice UI/control exposure while keeping Base clone and VoiceDesign gated.

## Core rule

```text
public model_id
    -> manifest engine_id
        -> supervisor worker
            -> isolated physical runtime
```

For manifest-owned engines, worker discovery may enrich runtime status/voices/controls but cannot invent a public model ID or change model ownership.

## Schema and roots

Every active manifest uses:

```text
schema_id = neo.voice_engine.manifest.v1
```

References:

```text
neo_voice_engine/schema/manifest_v1.schema.json
neo_voice_engine/schema/manifest_v1.example.json
neo_voice_engine/manifests/*.json
```

Additional roots use `NEO_VOICE_ENGINE_MANIFEST_DIRS`.

## First active manifest: Chatterbox

`neo_voice_engine/manifests/chatterbox.json` declares:

- `engine.id = chatterbox`;
- managed loopback worker `127.0.0.1:8791`;
- `voice_runtime`-scoped `envs/chatterbox` runtime;
- `auto_start=true`;
- install probes for the worker environment/adapter;
- stable models `chatterbox_turbo` and `chatterbox_multilingual`;
- TTS + reference-clone tasks;
- language/output/reference metadata;
- CPU/CUDA/VRAM admission metadata;
- unload/idle lifecycle policy;
- source/license metadata.

The manifest does **not** install Chatterbox. Missing required probes produce `not_installed`, and execution fails before worker dispatch.

## Repository safety

Checked-in manifests must not contain:

- absolute workstation paths;
- home-directory paths;
- tokens/passwords/API keys/cookies;
- user-specific model directories.

Machine-specific overrides belong outside portable manifest authority. Environment paths may use `scope: project` or `scope: voice_runtime`; both remain relative and escape attempts fail closed.

## Install vs runtime state

Manifest install state remains separate from worker state:

- `external`
- `installed`
- `partial`
- `not_installed`

A managed installed worker with `startup_policy: on_demand` may be executable while physically `stopped`; the supervisor can start it only at the executable-work boundary. Legacy manifests with `auto_start: true` normalize to the same on-demand policy for compatibility. A missing/partial runtime or model is never executable merely because an HTTP worker responds.

## Fail-closed identity rules

- duplicate `manifest_id` -> reject conflict;
- duplicate `engine_id` -> reject conflicting ownership;
- duplicate public `model_id` -> remove that public ID from executable catalogue;
- undeclared worker model -> ignore/report;
- request engine/model mismatch -> reject.

No first-one-wins routing.

## Registry endpoint

```text
GET /api/voice/registry
GET /api/voice/registry?refresh=true
```

Schema:

```text
neo.voice_engine.registry.v1
```

Commands and environment secrets are not exposed through public supervisor payloads.

## Scheduler consumption

VO-E4/VO-E5 consume manifest fields such as:

- `hardware.cpu`
- `hardware.cuda`
- `hardware.min_vram_mb`
- `hardware.recommended_vram_mb`
- `hardware.gpu_exclusive`
- `hardware.allow_cpu_fallback`
- `lifecycle.evictable`
- `lifecycle.idle_unload_seconds`
- `lifecycle.unload_strategy`

The scheduler does not alter registry identity authority.

## Qwen3-TTS Phase 3+ active manifest + Phase 4 UI metadata

Qwen3-TTS now ships an **enabled** `neo_voice_engine/manifests/qwen3_tts.json`. The manifest owns the five reviewed Qwen model IDs, managed worker `qwen3_tts`, port `8792`, isolated runtime path, conservative scheduler floors, and local model-source metadata.

The registry now supports optional manifest `install.probe_id` hooks. Qwen uses two probes:

```text
qwen3_tts_runtime_env
qwen3_tts_model_snapshot
```

The runtime probe requires the isolated Python environment plus the setup verification marker. The model probe requires a local snapshot with `config.json` and a complete weight set; sharded index files are checked against every referenced shard.

Qwen managed worker env sets `NEO_QWEN3_TTS_LOCAL_ONLY=1`, so registry activation cannot trigger an implicit first-use model download. Normal model acquisition is explicit through **Admin → Models**; legacy direct-download wrappers are developer-only under `scripts/dev/qwen3_tts/`.

Phase 4 extends the model manifest with optional `provider_controls`. For Qwen CustomVoice the manifest owns Language and Speaker mappings to the common Voice fields, plus the 1.7B-only Voice Instruction control. The manifest parser/schema and registry preserve these definitions so `GET /api/voice/controls` can answer them **without contacting or starting the worker**. CustomVoice models use `voice-ui-requires-executable`; Base/VoiceDesign keep `voice-ui-gated`.
Phase 4.4 extends generic hardware metadata with optional split CUDA admission fields: `min_total_vram_mb`, `cold_load_free_vram_mb`, and `recommended_total_vram_mb`. When present, `cold_load_free_vram_mb` becomes the cold-load free-memory threshold and `min_total_vram_mb` gates the validated device capacity class. Legacy manifests that only declare `min_vram_mb` / `recommended_vram_mb` preserve their existing semantics. Qwen3-TTS 1.7B CustomVoice is the first manifest to use this split contract.


## Current boundary

Chatterbox remains the validated/default Voice worker family. Qwen3-TTS is also manifest-owned; installed/runtime-ready CustomVoice models may appear in normal TTS, while missing/partial Qwen models remain absent and do not start workers during discovery. Base clone and VoiceDesign are still product-gated. VoxCPM, CosyVoice, RVC, Seed-VC and other future engines still require their own reviewed manifests/workers.

## Phase 4.6 — Chatterbox model probes

The Chatterbox manifest now declares `chatterbox_runtime_env` for the isolated environment and `chatterbox_model_snapshot` for each executable model. Registry publication uses those probes, so a declared Chatterbox model is not executable merely because its Python environment exists. The runtime/model probe resolves the Admin catalog record, local Hugging Face requested revision and content contract. This mirrors the Qwen rule that registry visibility/execution is derived from local installed truth rather than first-use download behavior.
