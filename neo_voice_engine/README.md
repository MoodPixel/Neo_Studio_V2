# Neo Voice Engine

`neo_voice_engine` is the standalone Voice gateway/supervisor introduced by **VO-E2**, made manifest-driven by **VO-E3**, GPU/resource-aware by **VO-E4**, connected to its first real model worker by **VO-E5**, and moved to an external runtime root by **VO-E5A**.

It remains a lightweight control plane. Chatterbox and future ML runtimes stay in isolated worker environments.

## Qwen3-TTS Phase 4 / 4.2 / 4.3 / 4.4 status

Qwen3-TTS now has an **enabled managed Voice Engine manifest** plus the isolated worker under `neo_integrations/qwen3_tts_adapter/`. Its external environment is `Neo_Runtime/voice/envs/qwen3_tts`, its local model registry is `Neo_Runtime/voice/models/qwen3_tts`, and the worker listens on `127.0.0.1:8792` when the gateway starts it.

Managed Qwen execution remains **local-only**: setup installs the isolated runtime but does not download model weights. Each Qwen model becomes `installed` only after runtime readiness and a complete local snapshot probe pass, including the bundled `speech_tokenizer`. The Qwen manifest uses `startup_policy: on_demand`, so health/catalog/control/registry reads never start the worker. Phase 4 exposes only executable CustomVoice models in normal TTS; 0.6B uses Language + Speaker and 1.7B additionally exposes manifest-owned Voice Instruction. The 0.6B CUDA/WAV path and Phase 4.2 repeated resident reuse are physically validated. Phase 4.3 then physically loaded/generated 1.7B CustomVoice on the RTX 3060 12 GB-class host with a 4230 MB peak process reservation and no CUDA OOM. Phase 4.4 converts that evidence into split 12000 MB total-capacity + 6144 MB cold-load-free admission while preserving exact-model resident reuse. Base clone and VoiceDesign remain gated. Chatterbox remains the default established family.

Use `setup_qwen3_tts_backend.bat`, then `download_qwen3_tts_model.bat --list` / `--status` and an explicit model ID. `run_qwen3_tts_backend.bat` remains a direct diagnostic launcher. See `guides/05_VOICE/qwen3_tts.md`.

## Current VO-E5A behavior

- serves the frozen VO-E1 `/api/voice/*` provider protocol on `127.0.0.1:8790`;
- owns gateway provider jobs (`nve_*`), progress, cancellation, and temporary output handoff;
- loads `neo.voice_engine.manifest.v1` documents and treats stable `model_id -> engine_id` ownership as manifest authority;
- ships active reviewed worker manifests for Chatterbox and Qwen3-TTS;
- supervises Chatterbox on `127.0.0.1:8791` from `Neo_Runtime/voice/envs/chatterbox`;
- keeps the Chatterbox worker cold until first executable work, then auto-starts it on demand;
- preserves Chatterbox lazy model download/load inside the worker job rather than blocking gateway startup;
- routes installed Chatterbox models and probe-ready Qwen model IDs through the gateway registry/scheduler;
- performs gateway-owned CPU/CUDA/VRAM admission before worker dispatch, distinguishes cold-load from lifecycle-confirmed resident reuse, and passes a private `_neo_execution` hint;
- preserves R6 clone authorization/QC/local-reference guards before a request reaches Chatterbox;
- copies completed worker audio into gateway-owned temporary output before Neo imports its final Voice Result;
- retains `voice.chatterbox` as a non-default legacy direct diagnostic/fallback profile;
- exposes registry and scheduler diagnostics at `GET /api/voice/registry` and `GET /api/voice/scheduler`.

## Setup order

For a fresh Chatterbox + Voice Engine installation:

```text
setup_chatterbox_backend.bat
setup_neo_voice_engine.bat
run_neo_voice_engine.bat
```

Normal Neo Voice traffic uses the default profile:

```text
Voice · Neo Voice Engine
http://127.0.0.1:8790
```

The gateway starts the Chatterbox worker on `127.0.0.1:8791` when an installed Chatterbox model is submitted. Direct diagnosis uses `scripts/dev/chatterbox/run_chatterbox_backend.bat`; both managed and diagnostic routes are local-only, and Admin → Models owns model acquisition.

## Dependency isolation

`Neo_Runtime/voice/envs/gateway` contains only lightweight gateway dependencies (FastAPI/Uvicorn). It must not absorb Torch, Transformers, Chatterbox, Qwen, VoxCPM, CosyVoice, or other model stacks.

Chatterbox resolves from `Neo_Runtime/voice/envs/chatterbox`. Future compatible model families receive their own engine-family environment under the same external `envs/` root.

## External runtime root

Default:

```text
<Neo parent>/Neo_Runtime/voice/
  envs/gateway/
  envs/chatterbox/
  envs/qwen3_tts/        # Qwen3-TTS isolated worker environment
  models/
    qwen3_tts/
  cache/
  temp/
  logs/
  state/
  outputs/
  legacy_backups/
```

Override the shared root with `NEO_RUNTIME_ROOT` or only Voice with `NEO_VOICE_RUNTIME_ROOT`. `NEO_VOICE_ENGINE_DATA` remains a legacy explicit alias. VO-E5A reserves `models/` and `cache/` but does not force-migrate existing upstream Hugging Face/user caches; model-family installers can adopt those roots explicitly in later milestones. Gateway output is temporary provider material; Neo Studio remains final Voice Result authority under `neo_data/outputs/voice/`.

## Manifest locations

Default:

```text
neo_voice_engine/manifests/*.json
```

Additional roots:

```text
NEO_VOICE_ENGINE_MANIFEST_DIRS
```

Public manifests must not contain machine-specific absolute paths, credentials, or secrets.

## Resource configuration

```text
NEO_VOICE_ENGINE_GPU_MAX_CONCURRENT_JOBS=1
NEO_VOICE_ENGINE_GPU_VRAM_RESERVE_MB=512
NEO_VOICE_ENGINE_GPU_PROBE_TIMEOUT_SECONDS=2
NEO_VOICE_ENGINE_SCHEDULER_WAIT_TIMEOUT_SECONDS=120
NEO_VOICE_ENGINE_MODEL_IDLE_UNLOAD_SECONDS=300
NEO_VOICE_ENGINE_WORKER_MAX_RESTARTS=2
NEO_VOICE_ENGINE_WORKER_RESTART_WINDOW_SECONDS=120
NEO_VOICE_ENGINE_WORKER_RESTART_BACKOFF_SECONDS=0.25
```

See:

```text
guides/05_VOICE/neo_voice_engine_contract.md
guides/05_VOICE/neo_voice_engine_gateway.md
guides/05_VOICE/neo_voice_engine_registry.md
guides/05_VOICE/neo_voice_engine_scheduler.md
guides/05_VOICE/external_voice_runtime.md
```
