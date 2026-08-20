# Neo Studio Chatterbox Worker Adapter

This is the physical local Chatterbox runtime used by Neo Voice Engine as of **VO-E5**, with external runtime placement from **VO-E5A** and CUDA-safe installer behavior from **VO-E5B**.

## Architecture

```text
Neo Studio
  -> Voice · Neo Voice Engine  http://127.0.0.1:8790
      -> manifest/scheduler/supervisor
          -> Chatterbox worker http://127.0.0.1:8791
              -> Neo_Runtime/voice/envs/chatterbox
```

`voice.chatterbox` on `8791` remains available as a **non-default legacy direct diagnostic/fallback profile**. Normal Voice generation should use `voice.neo_engine` on `8790`.

## Why Chatterbox stays isolated

`chatterbox-tts` carries its own PyTorch / torchaudio / Transformers dependency requirements. Keeping it in `Neo_Runtime/voice/envs/chatterbox` prevents model dependencies from replacing packages in Neo Studio or the lightweight gateway environment.

## Setup

```text
setup_chatterbox_backend.bat
setup_neo_voice_engine.bat
run_neo_voice_engine.bat
```

The gateway auto-starts this worker when an executable Chatterbox job first arrives. You normally do **not** need to run `run_chatterbox_backend.bat` yourself.

For direct diagnosis only:

```text
run_chatterbox_backend.bat
```

## Physical endpoints

The worker binds to `127.0.0.1:8791` by default and implements:

```text
GET  /api/voice/health
GET  /api/voice/capabilities
GET  /api/voice/models
GET  /api/voice/voices
POST /api/voice/render
GET  /api/voice/jobs/{job_id}
POST /api/voice/jobs/{job_id}/cancel
GET  /api/voice/models/{model_id}/lifecycle
POST /api/voice/models/{model_id}/unload
```

A synchronous model-load endpoint is intentionally not required. Chatterbox model downloads/loading can exceed a normal control-request timeout, so first-use loading remains inside the asynchronous render job. The gateway scheduler passes its selected device through the private `_neo_execution` hint before lazy load.

## Models

- `chatterbox_turbo` -> `ChatterboxTurboTTS` (English, lower-compute route, paralinguistic tags)
- `chatterbox_multilingual` -> `ChatterboxMultilingualTTS(..., t3_model="v3")` (Multilingual V3 route)

## Reference clone safety

Neo remains clone-authorization authority. VO-E5 also revalidates authorization/QC/local-path rules at the gateway before forwarding a reference to this worker. The worker keeps its own Neo-reference-root restriction as defense in depth.

## VO-E5B installer device lane

The setup launcher explicitly owns PyTorch before installing Chatterbox dependencies. NVIDIA hosts default to the PyTorch 2.6 `cu124` wheel lane and must pass `torch.cuda.is_available()` before setup succeeds. `NEO_CHATTERBOX_CUDA_VARIANT=cu118|cu124|cu126` can override the supported CUDA lane for driver compatibility. Non-NVIDIA hosts use the explicit CPU wheel index.

This prevents the gateway scheduler from selecting CUDA while the isolated worker contains CPU-only Torch.

## Environment notes

Current PerTh releases still import `pkg_resources`, which Setuptools 82+ removed. The Chatterbox environment therefore pins `setuptools<82`; setup validates `perth.PerthImplicitWatermarker` before reporting success.

Environment controls:

- `NEO_CHATTERBOX_HOST` / `NEO_CHATTERBOX_PORT` — direct worker launcher binding;
- `NEO_CHATTERBOX_DEVICE=auto|cuda|cpu|mps` — standalone/default device selection;
- gateway `_neo_execution` — private per-job scheduler hint and current VO-E5 device authority when routed through Neo Voice Engine;
- `HF_TOKEN` — passed through when required by upstream model access;
- `NEO_CHATTERBOX_ALLOW_EXTERNAL_REFERENCE=1` — development-only override for the Neo reference-root guard.
