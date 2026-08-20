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
version: 4
updated: 2026-08-11
---

# Neo Voice Engine Manifest Registry

VO-E3 establishes durable manifest ownership, VO-E4 consumes hardware/lifecycle metadata, VO-E5 activates Chatterbox, and **VO-E5A adds portable `voice_runtime` path scope for external worker environments**.

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

A managed installed `auto_start` worker may be executable while physically `stopped`; the supervisor can start it on demand. A missing/partial model is never executable merely because an HTTP worker responds.

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

## Current boundary

VO-E5 registers Chatterbox only. Qwen, VoxCPM, CosyVoice, RVC, Seed-VC and other future engines still require their own reviewed manifests/workers. Model downloading/Admin installation UI remains a later concern rather than registry responsibility.
