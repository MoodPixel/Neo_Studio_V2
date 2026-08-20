---
guide_id: voice.chatterbox_backend
title: Chatterbox Local Voice Backend
surface: voice
scope: built_in
applies_to:
  - voice_generation
  - voice_reference
  - voice_clone
  - backend_profiles
tags:
  - voice
  - chatterbox
  - tts
  - voice cloning
  - local backend
  - multilingual
priority: 68
version: 5
updated: 2026-08-11
---

# Chatterbox Local Voice Backend

**VO-R13** added Neo's physical Chatterbox runtime. **VO-E5** placed it behind Neo Voice Engine, **VO-E5A** moved its isolated environment out of the Neo Studio source tree, and **VO-E5B** hardens the installer so NVIDIA hosts cannot silently end up with a CPU-only PyTorch build. Chatterbox now runs from `Neo_Runtime\voice\envs\chatterbox`; the default Neo-facing Voice profile is now `voice.neo_engine` on `127.0.0.1:8790`, while the worker remains on `127.0.0.1:8791`.

## Setup and start

From the Neo Studio project root on Windows:

1. Run `setup_chatterbox_backend.bat` once.
2. Run `setup_neo_voice_engine.bat` once if the gateway environment is not already prepared.
3. Start `run_neo_voice_engine.bat`.
4. Open Neo and use the default **Voice · Neo Voice Engine** profile at `http://127.0.0.1:8790`.
5. Generate normally or use an authorized clone-ready reference. The gateway auto-starts the Chatterbox worker on `127.0.0.1:8791` when needed.

`run_chatterbox_backend.bat` and the non-default **Voice · Chatterbox (Legacy Direct)** profile remain available for diagnosis/fallback. Neo's frontend still talks only to Neo.


## VO-E5A runtime location

By default the worker environment is created at `<Neo parent>\Neo_Runtime\voice\envs\chatterbox`. The gateway lives at `...\envs\gateway`. Existing root-level `.venv-chatterbox` / `.venv-voice-engine` folders are rebuilt externally and then archived under `Neo_Runtime\voice\legacy_backups` after successful verification; Windows venvs are not blindly relocated. See `guides/05_VOICE/external_voice_runtime.md`.

## Runtime behavior

- Neo submits the existing R4/R6 provider contract to Neo Voice Engine; the gateway creates an `nve_*` job and dispatches the manifest-owned request to Chatterbox.
- Chatterbox `POST /api/voice/render` still returns its own asynchronous worker job immediately; the gateway polls it and maps completion/cancellation back to the provider-facing job.
- Chatterbox model weights load lazily. First use can remain in `loading_model` while files download and the checkpoint initializes.
- The adapter keeps only one Chatterbox model resident at a time and clears the previous model/CUDA cache when switching families.
- Completed worker audio is first copied into gateway-owned temporary output, then Neo imports it into Neo-owned Voice output storage before marking the R4/R6 job complete.
- The adapter never writes a fake/silent success output.

## Models

### Chatterbox Turbo

Neo ID: `chatterbox_turbo`

Use for English TTS / clone work when lower compute and faster startup/generation matter. Turbo supports native paralinguistic tags such as laugh/chuckle-style tags. The current Turbo implementation does **not** use CFG weight, min-p, or exaggeration. Turbo reference audio must be longer than five seconds.

### Chatterbox Multilingual V3

Neo ID: `chatterbox_multilingual`

The physical adapter explicitly loads the current V3 checkpoint. It supports the Chatterbox multilingual language set and zero-shot reference cloning. Its native expressive controls include exaggeration and CFG/pace guidance.

## Reference / clone safety boundary

VO-R6 remains the authority before provider submission. The physical adapter also checks the transport:

- request mode must be `voice_clone`;
- Neo must pass `authorization_confirmed=true`;
- reference QC must be `usable` or `usable_with_warnings`;
- the local reference path must exist under `neo_data/outputs/voice/reference/` by default.

`NEO_CHATTERBOX_ALLOW_EXTERNAL_REFERENCE=1` exists only for explicit development/testing and should not be used for normal Neo operation.

## Common settings mapping

- Language: Turbo accepts English; Multilingual validates the supported language code.
- Long text: the adapter respects Neo's split toggle / max chunk size and concatenates generated chunks before provider completion.
- Speaking rate: when not `1.0`, the adapter uses FFmpeg `atempo` so pitch is not changed by naïve resampling.
- Output: WAV is native. MP3 is created with FFmpeg when requested.
- Seed: the current R8 Chatterbox seed control is applied before synthesis.
- Historical `expression_strength` is applied as Multilingual exaggeration only. It is intentionally not translated for Turbo because Turbo does not support that parameter.
- Historical `reference_strength` is **not** remapped to a different Chatterbox parameter; silently pretending CFG is a speaker-similarity slider would violate the provider-control contract.

A later provider-control refinement can make the advanced controls model-family-specific without changing this physical runtime boundary.

## VO-E5B CUDA installer behavior

`setup_chatterbox_backend.bat` owns the Chatterbox PyTorch lane before installing the remaining adapter requirements. This prevents a fresh/rebuilt external worker environment from appearing healthy while exposing only CPU Torch to the VO-E4 scheduler.

- If `nvidia-smi` is available, setup defaults to PyTorch **2.6.0 + torchaudio 2.6.0** from the official **CUDA 12.4 (`cu124`)** wheel index.
- Advanced compatibility override: `NEO_CHATTERBOX_CUDA_VARIANT=cu118|cu124|cu126`. Unsupported values fail closed.
- If NVIDIA is not detected, setup uses the official CPU wheel index explicitly.
- Setup validates the selected Torch/torchaudio versions before Chatterbox dependency installation and re-checks them afterward. If dependency resolution changes the selected runtime, setup repairs it from the explicit PyTorch index.
- On an NVIDIA host, setup does not report success unless `torch.cuda.is_available()` is true and Torch reports a CUDA runtime. This matches the gateway scheduler contract: an NVIDIA/CUDA admission must not be forwarded to a CPU-only worker.

The CUDA choice changes only the isolated external `Neo_Runtime\voice\envs\chatterbox` environment; Neo Studio's main venv and the lightweight gateway environment remain untouched.

## Dependency compatibility: PerTh / Setuptools

Chatterbox requires its bundled PerTh watermarking path to initialize successfully. Current PerTh releases still import the legacy `pkg_resources` module, while Setuptools 82+ removed `pkg_resources`. The isolated Chatterbox environment is therefore pinned to `setuptools<82` until upstream PerTh no longer requires that legacy module.

`setup_chatterbox_backend.bat` now enforces the compatibility pin and verifies that `perth.PerthImplicitWatermarker` is callable before declaring setup complete. The adapter health endpoint also reports `perth_watermarker` readiness. Neo does not silently substitute `DummyWatermarker`; preserving the upstream watermarking contract is preferred over hiding the dependency fault.

## Troubleshooting

- **Neo Voice Engine offline:** start `run_neo_voice_engine.bat`, then Connect/Test the default Voice profile again.
- **Chatterbox worker will not auto-start:** rerun `setup_chatterbox_backend.bat`; use `run_chatterbox_backend.bat` only to diagnose the worker directly on `8791`.
- **`Failed to load chatterbox_turbo: 'NoneType' object is not callable`:** this is the known PerTh / `pkg_resources` compatibility failure. Stop the adapter, rerun `setup_chatterbox_backend.bat` so the external Chatterbox environment receives `setuptools<82`, then restart the adapter.
- **`Neo Voice Engine requested CUDA but CUDA is not available to the Chatterbox worker`:** rerun the VO-E5B `setup_chatterbox_backend.bat`. On NVIDIA hosts it repairs Torch/torchaudio from the explicit CUDA wheel lane and refuses to finish until CUDA is visible. If your driver requires another supported PyTorch 2.6 lane, set `NEO_CHATTERBOX_CUDA_VARIANT=cu118`, `cu124`, or `cu126` before rerunning setup.
- **Dependency missing/incompatible:** rerun `setup_chatterbox_backend.bat`. It changes only the external `Neo_Runtime\voice\envs\chatterbox` environment.
- **First generation looks stuck on model loading:** keep the adapter console open; first use downloads/initializes model weights.
- **Turbo non-English request:** select Chatterbox Multilingual V3.
- **Turbo clone reference error:** use a clean reference longer than five seconds.
- **Rate/MP3 error:** ensure FFmpeg is available on PATH.
