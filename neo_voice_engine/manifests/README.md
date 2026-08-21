# Neo Voice Engine manifests

VO-E3 established `neo.voice_engine.manifest.v1`; VO-E4 consumes its hardware/lifecycle metadata; **VO-E5 ships the first active reviewed worker manifest**.

## Active manifests

```text
chatterbox.json
qwen3_tts.json
```

The Chatterbox manifest registers:

- engine: `chatterbox`;
- managed loopback worker: `http://127.0.0.1:8791`;
- isolated external runtime: `voice_runtime://envs/chatterbox`;
- models: `chatterbox_turbo`, `chatterbox_multilingual`;
- tasks: TTS + authorized reference cloning;
- CPU/CUDA admission metadata;
- lifecycle/idle-unload policy;
- source/license metadata without claiming unverified commercial rights.


The Qwen3-TTS manifest registers `engine=qwen3_tts`, worker port `8792`, five reviewed Qwen model IDs, local-only managed launch, and probe-driven install state. Phase 4 allows executable CustomVoice models into normal TTS; Base/VoiceDesign remain gated. Phase 4.4 makes 1.7B CustomVoice the first model to use split CUDA admission metadata (`min_total_vram_mb`, `cold_load_free_vram_mb`, `recommended_total_vram_mb`) while older manifests continue using legacy `min_vram_mb` semantics.

The gateway does not install Chatterbox packages. `setup_chatterbox_backend.bat` remains responsible for the worker environment under the external Voice runtime root. If required install probes are absent, the model remains declared but `not_installed` and execution fails before worker dispatch.

Additional manifest directories can be supplied with `NEO_VOICE_ENGINE_MANIFEST_DIRS` using the platform path separator (`;` on Windows, `:` on Linux/macOS).

Public manifests must not contain user-specific absolute paths, tokens, cookies, API keys, or other secrets.

See `../schema/manifest_v1.schema.json`, `guides/05_VOICE/neo_voice_engine_registry.md`, and `guides/05_VOICE/neo_voice_engine_scheduler.md`.

## VO-E5A environment path scopes

Worker `environment.root` and `environment.python` stay relative in checked-in manifests. `environment.scope` controls the trusted base:

- `project` — resolve under the Neo Studio source root (legacy/default for manifest v1);
- `voice_runtime` — resolve under the configured external Voice runtime root.

Use `voice_runtime` for managed model-family venvs. Absolute paths and `..` traversal remain invalid in public manifests.


## Qwen3-TTS Phase 3+ / Phase 4 CustomVoice

`qwen3_tts.json` is now an **enabled managed manifest**. It owns the five reviewed Qwen3-TTS model IDs and the managed worker on port `8792`. The worker uses `voice_runtime://envs/qwen3_tts` with `startup_policy: on_demand`; `auto_start` remains false for Qwen. Install `probe_id` values let the registry distinguish an installed isolated runtime from a complete local model snapshot. Registry/health/catalog discovery never starts the Qwen worker; only executable work that has passed runtime + selected-model admission may start it.

Managed Qwen jobs set `NEO_QWEN3_TTS_LOCAL_ONLY=1`; model weights are therefore never silently acquired on first generation. Phase 4.5.7 resolves a complete legacy `Neo_Runtime/voice/models/qwen3_tts/` snapshot first, then an authoritative Admin-managed Hugging Face cache snapshot. The model probe verifies the bundled `speech_tokenizer` assets before either source is executable. Phase 4 adds model-owned `provider_controls`: Language/Speaker map to common Voice fields for CustomVoice, while 1.7B also declares Voice Instruction. These controls are served from the manifest without starting the worker. Missing/partial CustomVoice stays hidden; Base/VoiceDesign remain `voice-ui-gated`.

### Installation probes

Managed manifests may declare an optional lowercase `probe_id` in worker/model `installation` blocks. Neo resolves those through its install-probe registry in addition to normal path checks. Unknown probe IDs fail closed. Qwen Phase 3 currently defines `qwen3_tts_runtime_env` and `qwen3_tts_model_snapshot`.

## Phase 4.6 Voice model lifecycle

`chatterbox.json` now uses two install authorities: `chatterbox_runtime_env` verifies the external worker venv plus `.neo_chatterbox_ready`; `chatterbox_model_snapshot` resolves/validates the corresponding Admin repository-snapshot record in the local Hugging Face cache. Managed Chatterbox workers are started with local-only/offline environment flags, and generation never downloads weights.

The Chatterbox catalog IDs intentionally match the Voice Engine public IDs. Turbo and Multilingual V3 are installable because Neo has physical loaders for them; unsupported upstream variants are not advertised as executable installs.
