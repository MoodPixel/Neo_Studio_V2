---
guide_id: voice.qwen3_tts
title: Qwen3-TTS Integration Status
surface: voice
scope: built_in
applies_to:
  - voice_engine
  - qwen3_tts
  - voice_generation
  - voice_clone
  - voice_design
  - local_backend
tags:
  - voice
  - tts
  - qwen3-tts
  - voice cloning
  - voice design
  - custom voice
  - worker isolation
  - model registry
priority: 72
version: 14
updated: 2026-08-21
---

# Qwen3-TTS Integration Status

## Phase 4.6.3 — Physical lifecycle closure

Post-Phase-4.6 Qwen generation is confirmed working on the Windows/NVIDIA host after the legacy/no-redownload compatibility layer and the Phase 4.6.2 polling/VRAM diagnostics fix were applied. This closes the physically exercised managed runtime path for an existing local Qwen installation: Neo can keep the model in `Neo_Runtime`, prefer it over an optional Admin HF copy, and generate without downloading weights.

The host was intentionally **not** required to click Repair/Install HF copy simply to create duplicate model data. The Admin HF repository-snapshot path remains available for new installations or explicit optional migration, while legacy-local support remains permanent. The successful generation report proves the runtime path; it does not assert that every optional HF-install or controlled background-poll focus scenario was re-run physically.

## Phase 4.6.2 — Busy-GPU diagnostics and polling stability

The Phase 4.4 production values for `qwen3_tts_17b_custom_voice` are unchanged. A 12 GB-class GPU is admitted only when it meets the 12000 MB total-capacity floor and currently has at least 6144 MB safely free for a cold load after Neo's configured GPU reserve. Therefore a simultaneous Video/Comfy workload can correctly block a Qwen cold load even though the same 1.7B model was physically validated on the host when the GPU had sufficient free VRAM.

When that happens the scheduler now reports `admission_reason=insufficient_free_vram`, observed free VRAM, effective safe VRAM after reservations, and an actionable message explaining that another GPU workload may be holding memory. Exact-model resident reuse from Phase 4.2 remains unchanged.

The browser also no longer allows unrelated background pollers to force full Voice workspace rerenders. This protects the model selector and Script/Provider Controls focus while Video/Admin/Image/Prompt jobs continue in the background. Same-surface polling defers repaint while an editable control has focus.

## Phase 4.6.1 — Legacy Voice Model Compatibility / No-Redownload Migration

A complete historical Qwen snapshot under `Neo_Runtime/voice/models/qwen3_tts` is a permanent supported runtime source. Neo does not require that snapshot to be copied into the Hugging Face cache and does not require a second model download or re-download. Runtime precedence remains **complete legacy Neo Runtime snapshot → verified Admin-managed HF cache snapshot → unavailable**.

Admin now reports runtime availability separately from the Admin HF-copy state. Therefore a machine may legitimately show:

```text
Voice runtime: Legacy Neo Runtime · ready
Admin HF copy: Not installed / Cache problem
Migration: not required
```

When that happens the normal action is to keep using the legacy model. **Install HF copy** is optional and may use network data; it is never required for generation. Managed Generate remains local-only and never downloads weights.

Qwen3-TTS **Phase 4 activates the first normal Voice-tab path for CustomVoice**, **Phase 4.2 hardens repeated resident-model generation**, **Phase 4.3 measures the real 1.7B CUDA footprint**, **Phase 4.4 converts that physical evidence into production admission policy**, **Phase 4.4.1 fixes the provider-control UI binding exposed by the normal 1.7B test**, and **Phase 4.4.2 hardens the provider-control transport so manifest settings remain discoverable without model resolution or worker contact**. The isolated worker remains on `127.0.0.1:8792`; normal Voice work is still started only on demand after runtime/model readiness and scheduler admission pass.

Only installed/runtime-ready **0.6B CustomVoice** and **1.7B CustomVoice** are eligible for the normal TTS model selector. Base clone models and VoiceDesign remain intentionally gated for later dedicated UI phases.

## Current architecture

```text
Neo Studio Voice UI
  ↓
voice.neo_engine :8790
  ↓
active qwen3_tts manifest
  ↓
Qwen3-TTS worker :8792
  ↓
Neo_Runtime/voice/envs/qwen3_tts/
  ↓
runtime source resolver
  ├─ existing Neo_Runtime/voice/models/qwen3_tts snapshot
  └─ authoritative Admin-managed Hugging Face cache snapshot
```

The worker keeps **one Qwen model resident at a time**.

### Registered is not the same as running

The active Qwen manifest uses an explicit managed startup policy:

```text
enabled: true
startup_policy: on_demand
auto_start: false   # legacy eager/implicit flag is not used for Qwen
```

Neo may therefore know about Qwen and report install/model state without launching the worker. These read-only operations never spawn `:8792`:

- gateway startup;
- `/health`;
- model/voice/capability refresh;
- registry/install-status reads.

The worker launch boundary is executable work only:

```text
manifest registered
  + runtime probe = installed
  + selected model probe = installed
  + scheduler admission
  -> ensure worker :8792 on demand
```

A missing or partial runtime returns `dependency_missing`; a missing or partial selected model returns `model_not_installed`. Both fail before process launch or worker contact.

## Upstream runtime contract

Neo remains aligned to the reviewed official Qwen3-TTS implementation and `qwen-tts==0.1.1` runtime. The isolated environment prevents Qwen's pinned Transformers/Accelerate stack from changing the Neo Studio or Voice Engine gateway environments.

Released Neo model IDs:

| Neo model ID | Upstream role | Gateway task status |
|---|---|---|
| `qwen3_tts_06b_custom_voice` | 0.6B CustomVoice | **Voice TTS UI active when runtime + snapshot are executable**; Speaker + Language; no Voice Instruction |
| `qwen3_tts_17b_custom_voice` | 1.7B CustomVoice | **Voice TTS UI active when runtime + snapshot are executable**; Speaker + Language + Voice Instruction |
| `qwen3_tts_06b_base` | 0.6B Base / clone | Registry active; `voice_clone` transport exists; normal clone UI still gated |
| `qwen3_tts_17b_base` | 1.7B Base / clone | Registry active; `voice_clone` transport exists; normal clone UI still gated |
| `qwen3_tts_17b_voice_design` | 1.7B VoiceDesign | Registry-declared; gateway/UI task activation still gated |

The Phase 3 manifest marks `qwen3_tts_06b_custom_voice` as the preferred Qwen model for the later first managed physical test.

## Admin Model Guide runtime binding — Phase 4.5.7

Admin → Models now installs and authoritatively verifies the 0.6B and 1.7B CustomVoice repositories in the normal Hugging Face cache, and **Phase 4.5.7 binds that verified cache state into the Qwen Voice runtime**. Runtime resolution is offline/read-only and follows a fixed precedence:

```text
1. complete legacy Neo_Runtime/voice/models/qwen3_tts snapshot
2. authoritative Admin-managed Hugging Face cache snapshot
3. model_not_installed
```

A Hugging Face cache snapshot is executable only when the Phase 4.5.5 requested-revision/materialization/content probe returns `installed`. `partial`, `stale`, `corrupt`, `unverified`, and `not_installed` cache states never become a model path. The gateway install probe, worker `/api/voice/models`, worker model registry, and the actual model loader all use the same runtime resolver, so Admin Installed and Voice executable state can no longer disagree for the two catalog-declared CustomVoice models.

Existing Phase 3 local snapshots remain first priority for compatibility and are not moved or redownloaded. Base clone and VoiceDesign still have no Admin repository-snapshot catalog records, so they retain legacy/local-only behavior until their dedicated surface/catalog activation.

Managed Voice launch still sets `NEO_QWEN3_TTS_LOCAL_ONLY=1`. Therefore a missing model fails as `model_not_installed` **before `Qwen3TTSModel.from_pretrained()` receives a repository ID**. Voice Generate does not download weights. The old repo-ID fallback remains reachable only when local-only mode is deliberately disabled for development/tests, not through the normal managed Neo route.

Phase 4.5.4 storage preflight remains deliberately conservative: authoritative installed-state classification does not yet compute exact missing-blob transfer bytes, so no partial-cache download-size credit is granted.

## Runtime/install policy — Phase 4.5.8

Qwen managed routing is now **local-only**.

```text
NEO_QWEN3_TTS_LOCAL_ONLY=1
```

A missing model must fail as not installed. The gateway/worker must not silently begin a multi-gigabyte Hugging Face download during a generation request.

### Runtime setup

```bat
setup_qwen3_tts_backend.bat
```

Successful setup writes:

```text
Neo_Runtime/voice/envs/qwen3_tts/.neo_qwen3_tts_ready
```

The Voice Engine registry uses that verified marker together with the isolated Python path as the Qwen runtime-install probe.

### Model installation

For normal users, model installation is owned by **Admin → Models**. Install the supported CustomVoice repository snapshot there after `setup_qwen3_tts_backend.bat` completes. Admin uses the Phase 4.5 repository-snapshot pipeline, Hugging Face cache resolver, disk preflight, snapshot installer, and authoritative installed-state probe.

The user-facing repository root intentionally exposes **only** `setup_qwen3_tts_backend.bat` for Qwen-specific setup. Direct download/test/worker BAT wrappers are developer diagnostics under:

```text
scripts/dev/qwen3_tts/
```

The developer direct downloader still writes a full snapshot into the legacy compatibility path:

```text
Neo_Runtime/voice/models/qwen3_tts/<neo_model_id>/
```

That path is retained for debugging/backward compatibility only. It is not the normal Admin/HF-cache install path. Existing complete legacy snapshots and upstream repository-directory names remain recognized so upgrades do not force redownloads.

## Setup / BAT ownership — Phase 4.5.8

Setup and model acquisition are now deliberately separated:

```text
setup_qwen3_tts_backend.bat
  → isolated worker code/dependencies only

Admin → Models
  → supported CustomVoice model weights in HF cache

Voice Generate
  → executes already-installed local snapshots only
```

The setup BAT no longer creates the legacy Qwen model directory merely as a side effect and no longer advertises direct download/test/diagnostic wrappers. Historical Windows wrappers were moved to `scripts/dev/qwen3_tts/` so a fresh repository root exposes one clear Qwen setup entry point. Python tests/audits and diagnostic Python scripts remain in source control.

## Model completeness probe

Phase 3 does not treat a folder name as proof that a model is installed.

A local snapshot must contain the model, text/processor assets, and the **bundled 12 Hz speech tokenizer** required by the released Qwen checkpoint. Neo currently verifies:

- root `config.json`;
- root `tokenizer_config.json`, `vocab.json`, `merges.txt`, and `preprocessor_config.json`;
- a complete root model weight set;
- `speech_tokenizer/config.json` and `speech_tokenizer/preprocessor_config.json`; and
- a complete `speech_tokenizer` weight set.

If an HF sharded weight index exists, Neo reads the index and verifies that all referenced shards exist. Missing root weights, processor files, or bundled speech-tokenizer files therefore report `partial` instead of `installed`. The standalone `Qwen3-TTS-Tokenizer-12Hz` repository is not treated as a separate generation-model install; each released generation snapshot already carries the tokenizer assets it needs.

Install states are:

```text
not_installed
partial
installed
```

The gateway combines:

1. Qwen isolated-runtime readiness;
2. worker source-file presence; and
3. local model snapshot completeness.

A model is executable only when the combined install state is `installed` and the managed worker is eligible for on-demand start. Registry state now records the runtime and model components separately (`runtime_state` / `model_state`) so a fresh runtime install, partial runtime, and missing model produce distinct fail-closed diagnostics.

## Worker diagnostics

Normal managed Voice work goes through `run_neo_voice_engine.bat`. Direct Qwen worker launch and historical preflight/calibration wrappers are developer-only and live under `scripts/dev/qwen3_tts/`; they are not part of normal setup instructions.

Phase 3 direct worker endpoints include the existing Phase 2 endpoints plus:

```text
GET /api/voice/model-registry
```

`GET /api/voice/models` also reports each Qwen model's local install state.

## Phase 4 CustomVoice UI contract

Phase 4 keeps the common Voice draft authoritative instead of duplicating normal routing fields inside a Qwen-only card:

```text
Language / Locale  -> common Voice `language`
Voice / Speaker    -> common Voice `voice_id`
Voice Instruction -> model-scoped provider control; 1.7B only
```

The Qwen manifest owns the model-specific control contract. Provider-control discovery reads that manifest through the Voice Engine gateway and **does not start the Qwen worker**. The frontend does not hardcode a Qwen Voice Instruction widget: it renders the returned control definition generically.

The nine current built-in speaker IDs are filtered by the selected Qwen CustomVoice model, so Qwen speakers do not leak into Chatterbox selections and vice versa. The Language field becomes a selector for the Qwen-supported language set while a CustomVoice model is active.

```text
0.6B CustomVoice -> Language + Speaker; upstream instruct disabled
1.7B CustomVoice -> Language + Speaker + natural-language Voice Instruction
```

Missing or partial Qwen model installs carry `voice-ui-requires-executable` and are omitted from the normal model selector. The Base and VoiceDesign models keep `voice-ui-gated`.

## Clone contract

### ICL clone

```text
reference audio: required
reference transcript: required
x_vector_only_mode: false
```

### Speaker-embedding-only clone

```text
reference audio: required
reference transcript: optional
x_vector_only_mode: true
```

Both remain subject to Neo's existing authorization, QC, `neo_owned_local_path`, and reference-root safety checks.

## Voice Design

The isolated worker implements `generate_voice_design()` directly. The model is now registry-declared, but `neo_voice_engine.jobs` protocol v1 still activates only `tts` and `voice_clone`; the normal Voice UI also has no dedicated Voice Design source grammar yet.

Therefore **VoiceDesign remains deliberately non-executable through the normal gateway job protocol in Phase 3**.

## Scheduler policy

Phase 2's zero-valued hardware placeholders were removed before manifest activation.

Phase 3 began with conservative unmeasured managed-admission floors. Phase 4.4 now supersedes the 1.7B CustomVoice floor with a split, physically calibrated contract while leaving 0.6B unchanged:

```text
0.6B CustomVoice
  legacy cold-load min_vram_mb       = 8192 MB
  recommended_vram_mb                = 10240 MB

1.7B CustomVoice
  min_total_vram_mb                  = 12000 MB
  cold_load_free_vram_mb             = 6144 MB
  min_vram_mb                        = 6144 MB  # legacy/free-floor compatibility
  recommended_vram_mb                = 6144 MB  # scheduler reservation estimate
  recommended_total_vram_mb          = 16384 MB
```

CPU capability remains declared, but automatic CPU fallback is disabled for managed Qwen jobs. The 1.7B values are derived from the successful RTX 3060 calibration described below; they do **not** claim 8 GB-class support.

## Family aliases

Qwen model IDs are now known to the Neo Voice capability layer and `neo_voice_engine` runtime alias. This prevents a Qwen model ID from being normalized back to the historical `chatterbox_turbo` family when it is inspected through backend APIs.

The normal Voice surface now consumes installed CustomVoice records from the selected `voice.neo_engine` catalog. Model-specific provider controls are resolved only after a concrete Qwen model is selected; other Voice families continue to use their existing profile-scoped contracts.

## Physical validation path

The historical Phase 4 first milestone was **0.6B CustomVoice normal TTS**. Phase 4.5.8 keeps that diagnostic tooling available for developers but the normal-user retest path is now the Admin-managed route that Phase 4.5.9 will validate end to end:

1. Run `setup_qwen3_tts_backend.bat` to install/verify the isolated worker runtime.
2. Start Neo Studio and open **Admin → Models**.
3. Install **Qwen3-TTS 0.6B CustomVoice** and confirm its authoritative state becomes **Installed**.
4. Start `run_neo_voice_engine.bat` if the Voice Engine is not already running.
5. In **Voice → Generation**, select `Qwen3-TTS 0.6B CustomVoice`, use `English` with `Ryan` or `Aiden`, enter a short script, and Generate.
6. Confirm worker/runtime diagnostics report `huggingface_cache_snapshot` when no complete legacy snapshot is present.

The old non-loading preflight remains available only as `scripts\dev\qwen3_tts\test_qwen3_tts_06b_custom_voice.bat`; it is not a normal setup requirement.

The first physical Windows/NVIDIA test has now succeeded for `qwen3_tts_06b_custom_voice` on an RTX 3060 12 GB host: the preflight passed, the model loaded through the normal managed Voice route, real WAV output reached Neo, and multiple short/longer conversational-emotion generations completed. The fourth pre-hotfix generation then exposed a scheduler bug: the full 8192 MB **cold-load** floor was being applied again while the same model was already resident, so the model's own VRAM allocation reduced the reported free VRAM and caused a false `gpu_oom` admission failure. No measured peak-VRAM claim is recorded from that run.

## Phase 4.2 resident-model VRAM admission

Phase 4.2 separates initial model admission from reuse of an already loaded model:

```text
Cold load
  -> no confirmed matching resident model
  -> require manifest min_vram_mb
  -> 0.6B remains 8192 MB minimum
  -> load model

Resident reuse
  -> scheduler record suggests resident
  -> managed worker is already healthy
  -> read-only lifecycle confirms the exact same model is resident
  -> require only the generic configured GPU safety reserve
  -> do not reserve/charge the full model VRAM again
  -> do not reload the model
```

The default resident-reuse safety reserve remains the existing `NEO_VOICE_ENGINE_GPU_VRAM_RESERVE_MB` policy (512 MB unless configured otherwise). This is **not** a new Qwen minimum and does not weaken the 8192 MB cold-load floor.

Scheduler residency is never trusted by itself. If the worker lifecycle reports unloaded/another model, is unavailable, or changes before dispatch, Neo falls back/fails closed instead of using resident-reuse headroom for an unsafe cold load. Scheduler diagnostics now report admission mode plus observed/effective free VRAM so physical retesting can distinguish `cold_load` from `resident_reuse`.

After each Qwen inference, the worker also releases transient unused CUDA allocator cache while keeping the model weights resident. This improves repeat-run headroom without forcing a model reload.

The post-hotfix physical repeat test has now passed on the target host: repeated 0.6B generations remained stable after the resident-reuse fix. Phase 4.2 is therefore physically closed for the tested 0.6B path.

## Phase 4.3 1.7B VRAM calibration

The installed `qwen3_tts_17b_custom_voice` checkpoint did not reach model loading through the normal production route on the target RTX 3060. The manifest still requires `min_vram_mb=12288`, while the physical GPU reports `12287 MB` total VRAM. The scheduler correctly fails closed before worker/model load, but that 1 MB policy mismatch does **not** tell us whether the model itself fits in 12 GB.

Phase 4.3 therefore adds a controlled diagnostic:

```bat
scripts\dev\qwen3_tts\test_qwen3_tts_17b_custom_voice.bat
```

This launcher explicitly warns that it bypasses normal scheduler admission for **one direct calibration run only**. It uses the isolated Qwen environment and installed local 1.7B snapshot, then measures CUDA memory around model load, one short Voice-Instruction generation, and unload. It writes a JSON report and, on success, a calibration WAV under:

```text
neo_data/outputs/voice/calibration/
```

The report captures total/free/used VRAM, process allocated/reserved and peak CUDA memory, model-load/generation/cleanup timings, OOM status, and post-unload reclamation. A CUDA OOM is a valid physical result and is cleaned up/reported; it does not automatically lower scheduler policy.

Phase 4.3 deliberately left production 1.7B policy unchanged until the physical JSON could be reviewed. That calibration then succeeded on the RTX 3060 12 GB-class host:

```text
GPU total VRAM                  12287 MB
free before load                11255 MB
free after load                  7127 MB
free after generation            7105 MB
process peak allocated           4148 MB
process peak reserved            4230 MB
model load time                  24.781 s
generation time                  17.992 s
CUDA OOM                         false
free VRAM reclaimed on unload    3788 MB
```

The successful run proves the prior `12288 MB` single floor was a policy mismatch rather than a model-fit failure. It also gives enough headroom evidence to calibrate normal 12 GB-class admission without guessing.

## Phase 4.4 production VRAM admission

Phase 4.4 adds generic manifest fields that separate **GPU capacity class** from **free VRAM required immediately before a cold load**:

```text
min_total_vram_mb
cold_load_free_vram_mb
recommended_total_vram_mb
```

Legacy manifests that only declare `min_vram_mb` continue to behave exactly as before. For `qwen3_tts_17b_custom_voice`, production policy is now:

```text
min_total_vram_mb          = 12000
cold_load_free_vram_mb     = 6144
min_vram_mb                = 6144
recommended_vram_mb        = 6144
recommended_total_vram_mb  = 16384
allow_cpu_fallback          = false
```

Why 6144 MB: the measured 1.7B process peak reservation was 4230 MB, so a 6144 MB cold-load floor leaves roughly 1.9 GB of additional model-side headroom before the gateway's separate 512 MB generic safety reserve is applied. This is a conservative production value derived from one successful physical calibration, **not an experimentally proven minimum**.

Why 12000 MB total: the only physically validated capacity class is the 12 GB-class RTX 3060 that reports 12287 MB. Neo therefore admits that class while intentionally keeping 8 GB-class GPUs unvalidated.

Resident reuse from Phase 4.2 remains authoritative after the first load: once the exact 1.7B model is confirmed resident, later generations do not recharge the 6144 MB cold-load floor and only require the generic configured safety reserve.

## Phase 4.4.1 provider-controls UI binding

The first normal 1.7B production-path UI inspection exposed a surface-state problem rather than a model/runtime problem. The persistent right rail (Script, Common Parameters, Provider Controls) remains the TTS generation draft across every Voice workspace, but its Provider Controls panel previously switched to `voice_clone` whenever the active workspace was **Reference**. That could hide the 1.7B model-scoped **Voice Instruction** and show `Clone turn controls` instead.

Phase 4.4.1 makes the ownership explicit:

```text
persistent Script/Common Parameters rail -> tts provider controls
Reference workspace                    -> voice_clone provider controls
```

Qwen 1.7B Voice Instruction remains contract-driven: `neo.js` does not hardcode a Qwen instruction field. Generic long text controls render as a textarea, so the manifest's `voice_instruction` (`max_length=1000`) appears as a multiline field with its supplied placeholder. Qwen 0.6B still has no instruction control.

Provider-control refresh also captures the selected model ID and discards stale asynchronous responses if the model changes before the request completes. Runtime badges now identify the selected model/engine separately from the backend profile family, avoiding the misleading impression that a selected Qwen model is `chatterbox_turbo`.

The normal Generation payload remains nested and separate from the spoken script:

```text
common_settings.script                     -> spoken text
provider_controls.voice_instruction        -> delivery instruction
```

Phase 4.4.1 changes no VRAM/scheduler values.

## Phase 4.4.2 provider-control contract transport hardening

The physical retest after Phase 4.4.1 exposed a second issue below the UI layer: Neo Studio could ask the Voice Engine for the correct Qwen model-scoped control contract, but the gateway control route still called full model resolution/catalog refresh before reading static manifest metadata. Neo Studio also capped that local control lookup to 3 seconds. A slow refresh or temporary gateway delay could therefore return an unavailable/empty contract even though the 1.7B manifest still declared `voice_instruction`.

Phase 4.4.2 changes the discovery path to:

```text
Qwen model selected
  -> Neo Studio /api/voice/provider-controls
  -> Voice Engine /api/voice/controls
  -> already-loaded manifest registry model
  -> provider_controls metadata
  -> no model resolution, install gate, worker probe, or worker startup
```

For manifest-owned models, provider-control metadata is now read directly from the registry first. A mode the model does not support is returned as an **authoritative empty contract** without worker contact. Dynamic-control worker fallback remains available only for supported modes that do not have an authoritative manifest control definition.

The Neo Studio bridge no longer applies the old hard 3-second cap; it uses the configured local gateway timeout, bounded to 1–30 seconds. Transport failures remain explicit `unavailable` contracts instead of being presented as though the selected model simply has no extra controls. The Voice UI now distinguishes **loading**, **controls unavailable**, and **authoritative no-extra-controls** states. A temporary discovery failure also preserves the user's existing provider-control draft instead of erasing it; unavailable controls are simply omitted from serialization until discovery succeeds again.

Normal Generation always requests the TTS control contract. Clone-control discovery is now conditional: it is requested only when the Reference/clone workflow actually needs it and the selected model supports `voice_clone`. Therefore a Qwen CustomVoice TTS model no longer triggers an unnecessary clone-control lookup.

For Qwen CustomVoice, the expected result remains:

```text
0.6B CustomVoice -> Language + Speaker; no Voice Instruction
1.7B CustomVoice -> Language + Speaker + Voice Instruction
```

Phase 4.4.2 changes no Qwen model files, inference behavior, scheduler values, VRAM admission, resident reuse, Base-clone gating, VoiceDesign gating, or Chatterbox execution behavior.

## Still gated after Phase 4.4.2

Current integration does **not**:

- expose Qwen Base clone models in the normal Reference / Clone UI;
- activate Qwen VoiceDesign in gateway protocol v1 or add a Voice Design source grammar;
- activate streaming;
- auto-download model weights;
- claim 8 GB-class 1.7B compatibility;
- add quantization or hidden CPU fallback;
- change Chatterbox routing/defaults;
- make catalog/health discovery start the Qwen worker.

## Next milestone

After applying Phase 4.4.2, rerun the normal managed 1.7B CustomVoice path with the visible Voice Instruction textarea for several consecutive generations to physically verify **cold_load → resident_reuse** under the Phase 4.4 policy and corrected UI binding. After that, the planned generic model-management follow-up should move Voice models toward Admin Model Manifest / Hugging Face snapshot-cache ownership before Base cloning is broadened. **Base voice cloning** then remains the next Qwen capability milestone, with explicit ICL vs x-vector-only controls. VoiceDesign should remain a separate later surface because it has a different request/source grammar.
