---
guide_id: voice.external_runtime_root
title: External Voice Runtime Root
surface: voice
scope: built_in
applies_to:
  - voice_engine
  - local_backend
  - model_workers
  - runtime_storage
tags:
  - voice
  - runtime
  - virtualenv
  - storage
  - migration
priority: 72
version: 3
updated: 2026-08-21
---

# External Voice Runtime Root

**VO-E5A** moves mutable Voice Engine environments and gateway runtime data out of the Neo Studio source tree. Neo source stays portable; model-family runtimes can grow independently.

## Default layout

If Neo Studio is installed at:

```text
D:\Apps\Neo_Studio_V2\
```

the default Voice runtime is the sibling tree:

```text
D:\Apps\Neo_Runtime\voice\
  envs\
    gateway\
    chatterbox\
    qwen3\          # future
    voxcpm\         # future
  models\
  cache\
  temp\
  logs\
  state\
  outputs\
  legacy_backups\
```

`Neo_Studio_V2\neo_voice_engine\` and `Neo_Studio_V2\neo_integrations\` remain source code only. VO-E5A reserves the external `models/` and `cache/` locations but does not silently move existing Hugging Face/user caches or redownload model weights.

## Environment ownership

One environment is created per compatible **engine family**, not per checkpoint/model size.

For example:

```text
Chatterbox Turbo + Multilingual -> envs\chatterbox
Qwen3 0.6B + 1.7B variants      -> envs\qwen3
Gateway / supervisor            -> envs\gateway
```

The gateway environment remains lightweight and must not absorb model-specific Torch/Transformers stacks.

## Root overrides

Resolution precedence is:

1. `NEO_VOICE_RUNTIME_ROOT`
2. legacy explicit `NEO_VOICE_ENGINE_DATA`
3. `NEO_RUNTIME_ROOT\voice`
4. `<Neo project parent>\Neo_Runtime\voice`

`NEO_RUNTIME_ROOT` is the future shared root for engines such as Voice and Music. `NEO_VOICE_RUNTIME_ROOT` overrides only Voice.

Example:

```bat
set NEO_RUNTIME_ROOT=D:\AI_Runtime
```

Voice then resolves to:

```text
D:\AI_Runtime\voice
```

Or override only Voice:

```bat
set NEO_VOICE_RUNTIME_ROOT=E:\NeoVoiceRuntime
```

## Existing-install migration

Windows virtual environments are **not blindly relocated** because their scripts may contain absolute interpreter paths.

When VO-E5A setup finds a legacy root-level environment such as:

```text
Neo_Studio_V2\.venv-voice-engine
Neo_Studio_V2\.venv-chatterbox
```

it:

1. creates/reuses the new external target environment;
2. installs/verifies the required packages there;
3. only after successful verification, archives the old root-level environment under `legacy_backups`;
4. keeps the archive as rollback material instead of using it as an active runtime.

The old `neo_voice_engine_data` directory is also archived when possible. It contains gateway/runtime material, not Neo's final Voice Results.

## Current setup / start

First VO-E5A/VO-E5B migration or a fresh install:

```text
setup_chatterbox_backend.bat
setup_neo_voice_engine.bat
```

Normal use afterward:

```text
run_neo_voice_engine.bat
```

The gateway auto-starts the external Chatterbox worker when required. Direct diagnosis uses the developer-only `scripts/dev/chatterbox/run_chatterbox_backend.bat`. VO-E5B makes the Chatterbox setup device-aware: NVIDIA hosts receive an explicit CUDA PyTorch wheel lane inside `envs/chatterbox`, while non-NVIDIA hosts receive the explicit CPU lane. Phase 4.6 leaves model weights in the normal Hugging Face Hub cache selected by Admin → Models; it does not create a second Chatterbox model tree under `Neo_Runtime`.

## Manifest path scopes

Public manifests still cannot contain machine-specific absolute paths. VO-E5A adds a portable environment scope:

```json
{
  "environment": {
    "kind": "venv",
    "scope": "voice_runtime",
    "root": "envs/chatterbox",
    "python": "envs/chatterbox/Scripts/python.exe"
  }
}
```

The registry resolves these paths against the configured Voice runtime root and fail-closes unknown scopes or path escapes.

## Ownership boundary

- Neo owns final Voice Results under `neo_data/outputs/voice/`.
- Neo Voice Engine owns temporary worker/gateway runtime state under the external Voice runtime root.
- Worker environments are disposable/rebuildable runtime dependencies.
- Checked-in manifests/source remain machine-portable.

## Phase 4.6 model-storage boundary

`Neo_Runtime/voice/envs/` owns isolated Python runtimes, not Admin-managed model weights. Qwen legacy snapshots under `Neo_Runtime/voice/models/qwen3_tts` remain a compatibility exception with first precedence. New Qwen and Chatterbox repository-snapshot installs use the Hugging Face cache resolved by Admin. No setup script creates or mirrors HF `blobs / refs / snapshots` data.
