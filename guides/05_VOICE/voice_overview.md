---
guide_id: voice.overview
title: Voice Tab Overview
surface: voice
scope: built_in
applies_to:
  - voice_workspace
  - voice
  - voice_generation
  - voice_assets
  - voice_reference
  - voice_finish
  - voice_results
tags:
  - voice
  - tts
  - voice cloning
  - reference audio
  - surface architecture
  - workspace apps
  - common settings
  - provider routing
  - capability routing
  - generation runtime
  - results
  - voice profile assets
  - provider-specific controls
  - finish processing
  - dialogue
  - multi-speaker
  - batch
  - script import
  - batch queue
priority: 65
version: 18
updated: 2026-08-14
---

# Voice Tab Overview


## Public user summary

For normal use, Voice should be explained without internal milestone identifiers:

- **Generation** — provider-routed TTS using the selected Voice backend.
- **Reference / Clone** — upload authorized reference audio and clone only when the selected backend supports it.
- **Assets** — save reusable Voice Profile Assets.
- **Dialogue** — map speakers to provider voices or authorized references and generate a stitched multi-speaker result.
- **Batch** — import TXT/MD/CSV/JSON/SRT and process items with bounded concurrency.
- **Results** — review the shared Voice result registry.
- **Finish** — non-destructive local audio finishing for Neo-owned outputs.

The default normal route is **Neo Voice Engine**. The currently validated worker family is **Chatterbox**, while the direct Chatterbox profile remains a diagnostic fallback. Internal VO-R/VO-E identifiers belong in engineering records and should not be shown as normal UI copy.

Voice is an active top-level Neo Studio surface. **VO-R1** reinstated the five-workspace shell, **VO-R2** established the provider-neutral common TTS draft, **VO-R3** made the selected Voice Backend Profile authoritative for provider/model/voice/capability routing, **VO-R4** activated the canonical provider-routed TTS generation runtime with durable job state and Neo-owned audio persistence, **VO-R5** added the shared-registry Preview + Results system, and **VO-R6 — Reference / Clone** activates Neo-owned reference-audio assets plus capability-gated voice-clone generation through the same current runtime/result lineage. **VO-R7 — Voice Profiles / Assets** added current reusable Voice Profile Assets that safely capture common Voice settings and optional authorized reference lineage without becoming backend-routing authority. **VO-R8 — Provider-Specific Controls** activates selected-profile, capability- and mode-scoped synthesis controls without expanding the VO-R2 common contract. **VO-R9 — Finish** activates provider-independent, capability-gated local post-processing for completed Neo-owned Voice outputs with non-destructive child-result lineage in the shared generation registry. **VO-R10 — Dialogue / Multi-speaker** added current deterministic speaker parsing, same-backend speaker mapping, real R4/R6 child synthesis, and FFmpeg-stitched combined Dialogue results. **VO-R11 — Batch** now adds safe TXT/MD/CSV/JSON/SRT import, bounded current-child orchestration, per-item retry, manifest history, and Batch lineage on normal playable Results. **VO-R12A — Voice Surface Layout Parity + Navigation Flattening** then realigned the Voice workspace shell with the Image command-strip grammar, moved workspace / mode / runtime actions into the top command area, added a visible progress lane, and removed the extra nested per-workspace wrapper cards so each workspace now exposes its real extension blocks more directly. **VO-R12B — Voice Subtab Visual Modernization** adds one Voice-scoped modern form/card design layer across Generation, Assets, Reference, Finish, and Results so flattened workspace content uses Neo-styled dark inputs, selects, file pickers, disabled/focus states, action rows, result cards, and responsive field grids instead of native-browser control rendering. **VO-R13 — Chatterbox Physical Backend** now turns the existing Chatterbox adapter target into a real isolated local service: the shipped `voice.chatterbox` profile defaults to `127.0.0.1:8791`, Admin Connect/Test probes that selected endpoint, R4/R6 submit asynchronous provider jobs, and real completed audio is returned for Neo-owned persistence without placing Chatterbox's pinned ML stack inside Neo's main virtual environment. **VO-E1 — Neo Voice Engine Contract Freeze** freezes the combined-backend boundary, **VO-E2 — Gateway / Supervisor** implements it as a lightweight FastAPI service, **VO-E3 — Manifest Registry** makes engine/model identity durable, **VO-E4 — GPU Scheduler + Model Lifecycle** adds pre-dispatch resource control, and **VO-E5 — Chatterbox Migration** now proves the architecture end-to-end: `voice.neo_engine` on `127.0.0.1:8790` is the default Voice profile, its first active manifest owns `chatterbox_turbo` and `chatterbox_multilingual`, and the gateway auto-starts the isolated Chatterbox worker on `127.0.0.1:8791` only when required. **VO-E5A — External Voice Runtime Root** now moves the gateway and worker environments to the sibling `Neo_Runtime/voice/envs/` tree, adds a configurable shared/Voice-specific runtime root, and archives legacy root-level venvs only after the rebuilt external environments verify successfully. R2/R3/R4/R6/R8/R10/R11 remain Neo-side authorities; R13 remains the physical Chatterbox worker implementation underneath the new gateway. The direct `voice.chatterbox` profile remains enabled only as a non-default diagnostic/fallback route.

## Current workspace structure

Voice follows Neo's current creative-surface architecture:

- **Generation** — active provider-routed single-voice TTS, capability-gated VO-R10 Dialogue / Multi-speaker, and VO-R11 Batch. Batch imports TXT/MD/CSV/JSON/SRT, keeps one selected backend profile authoritative for the whole batch, and dispatches only current R4 TTS / R6 Clone / R10 Dialogue child jobs with bounded concurrency.
- **Assets** — active VO-R7 Voice Profile Asset library for reusable common Voice settings and optional clone-ready reference selection. Historical V7 saved profiles remain compatibility-only.
- **Reference** — active VO-R6 reference upload, authorization, QC, selection, and capability-gated clone generation.
- **Finish** — active VO-R9 provider-independent post-processing for completed Neo-owned Voice outputs. Normalize, silence trim, noise cleanup, loudness targeting, WAV/MP3 conversion, split, and merge are capability-gated by the local FFmpeg runtime. Historical VO-V14 Finish remains compatibility-only.
- **Results** — active current TTS + reference-clone + Dialogue + Finish ledger plus VO-R11 Batch-child lineage. Batch parents remain manifest-only orchestrators; playable child results retain batch/item/attempt/parent lineage without creating a second audio-result database.

On desktop the selected workspace owns the **left rail** and the persistent current draft remains in the **right rail** as **Script + Audio Preview + Common Parameters + Provider Controls**. As of **VO-R12A**, the top Voice command strip now mirrors Image-tab structure: workspace selector, selected-profile readout, workflow-mode selector, readiness strip, Validate / Generate / Pause / Stop action row, and progress bar. The previous extra wrapper cards for `Generation workspace`, `Assets workspace`, and similar shell-level nesting were removed so each workspace renders its actual current extension/runtime blocks directly. **VO-R12B** keeps that flattened structure but applies a shared modern Voice visual system to the direct workspace blocks: dark filled form controls, custom select affordances, rounded section cards, consistent field spacing, modern file upload buttons, reference/checkbox rows, disabled/focus styling, and polished result/profile cards. This visual layer is scoped to Voice and does not alter runtime payloads or backend routing. Switching workspace app must not silently change backend profile, model, voice, language, selected reference asset, or the rest of the common draft. Applying a Voice Profile Asset may update the draft but never auto-switches the active backend profile.

## VO-R2 common settings authority

Canonical endpoints:

- `GET /api/voice/base-contract`
- `POST /api/voice/base-contract/validate`

The common contract remains exactly:

1. `script`
2. `language`
3. `model_id`
4. `voice_id`
5. `speaking_rate`
6. `output_format`
7. `split_long_text`
8. `max_chunk_chars`
9. `punctuation_cleanup`

Provider-native fields are not promoted into this contract. VO-R4 TTS and VO-R6 clone generation both consume this common draft, with reference data supplied through a separate R6 asset contract.

## VO-R3 selected-profile routing authority

Canonical routing endpoint:

- `GET /api/voice/provider-routing?profile_id=<selected Voice profile>`

The selected enabled Voice Backend Profile is the single routing authority for:

- provider identity
- model family/runtime identity
- selected-profile model catalog
- selected-profile voice/speaker catalog
- selected-profile capability flags
- common-field visibility/enabled/fixed-value state
- backend health/readiness context

Explicit invalid, disabled, or non-Voice profile IDs fail closed. Neo must not silently substitute Chatterbox, the default profile, or another Voice provider. Backend discovery may augment only the same selected profile.

`provider_default` remains semantic: it resolves to the selected profile's own default model/voice.

## VO-R4 canonical TTS generation runtime

Current runtime endpoints:

- `POST /api/voice/generate`
- `GET /api/voice/generation/jobs/{job_id}`
- `GET /api/voice/generation/jobs`

Current TTS flow:

`VO-R2 common draft -> VO-R3 selected-profile route -> backend readiness gate -> provider adapter request -> Neo durable generation registry -> provider completion/poll -> Neo-owned audio import -> current Results handoff`

### Generate readiness

The current Generate action is available only when:

- the selected Voice profile routes successfully
- the selected profile advertises TTS capability
- its backend is reachable
- a non-empty script exists
- selected model/voice values belong to that profile's catalog or use semantic `provider_default`

A disconnected backend does not create a generation job or output file.

### Provider adapter contract

VO-R4 uses the selected profile's `voice_runtime` contract. Current local Voice profiles default to:

- generation path: `/api/voice/render`
- async poll path: `/api/voice/jobs/{provider_job_id}`

The frontend never calls Chatterbox, Kokoro, or Fish Speech directly.

**VO-R13 / VO-E5 / VO-E5A / VO-E5B physical Chatterbox:** `setup_chatterbox_backend.bat` creates the isolated external `Neo_Runtime/voice/envs/chatterbox` environment and now explicitly installs/verifies the correct PyTorch device lane (CUDA on detected NVIDIA hosts; CPU otherwise). `setup_neo_voice_engine.bat` creates `.../envs/gateway`. Normal use starts only `run_neo_voice_engine.bat`; the gateway supervises the Chatterbox worker on demand through `neo_voice_engine/manifests/chatterbox.json`. `run_chatterbox_backend.bat` remains a direct diagnostic/fallback launcher. First-use model download/loading still occurs behind the asynchronous provider-job contract. See `guides/05_VOICE/chatterbox_backend.md`, `guides/05_VOICE/neo_voice_engine_gateway.md`, and `guides/05_VOICE/external_voice_runtime.md`.

The provider may respond with immediate audio, embedded/base64 audio, a provider-local output file, a same-provider-host output URL, or an asynchronous provider request/job ID. Neo normalizes those outcomes behind the current runtime contract.

## Durable job and output rules

VO-R4/VO-R6 use Neo's shared file-backed generation job registry for current Voice jobs.

Current states include:

- `queued`
- `running`
- `completed`
- `failed`

Async provider request IDs are stored separately from Neo's local job ID. Temporary poll transport failures remain recoverable; provider-terminal failures become failed jobs.

**Neo must not mark a current Voice job completed until real provider audio has been persisted under Neo-owned Voice storage.**

Current render destination:

- `neo_data/outputs/voice/render/`

Metadata sidecars are written under:

- `neo_data/outputs/voice/metadata/`

Provider completion without retrievable audio is a failed job. The current runtime must not create silent placeholder WAV files as proof of success.

## VO-R5 Preview + Results authority

Canonical endpoints:

- `GET /api/voice/results`
- `GET /api/voice/results/{job_id}`
- `GET /api/voice/results/{job_id}/replay`
- `GET /api/voice/results/{job_id}/download`
- `POST /api/voice/results/{job_id}/open-folder`

Current Results do **not** create a second result database. The ledger is a read model over Neo's shared durable generation registry. Historical `GET /api/voice/history` remains compatibility-only and is not current Results authority.

Selecting a result drives the persistent Audio Preview and the Results inspector. Output actions are limited to validated Neo-owned Voice files.

Replay never auto-switches the active backend profile. Same-profile TTS replay can restore exact common settings. Cross-profile TTS replay restores portable common settings and resets Model/Voice to `provider_default` for the active profile.

## VO-R9 provider-independent Finish

Current Finish endpoints:

- `GET /api/voice/finish-runtime/capabilities`
- `POST /api/voice/finish-runtime/process`
- `POST /api/voice/finish-runtime/split`
- `POST /api/voice/finish-runtime/merge`
- `GET /api/voice/finish-runtime/history`
- `GET /api/voice/finish-runtime/jobs/{job_id}`

Finish is intentionally independent from the active TTS/clone provider. The selected Voice backend profile does not decide whether an already-generated Neo-owned audio file can be normalized, trimmed, cleaned, converted, split, or merged. The local processing capability contract decides that.

### Source and output rules

- Only completed current Voice results with validated files below `neo_data/outputs/voice/` may be Finish sources.
- Finish never modifies the source file in place. Every operation creates a new child job/output.
- Current Finish jobs use the same durable generation job registry as TTS and clone. There is no second R9 Finish database.
- Finish child jobs preserve source job IDs, original common settings, provider-control snapshot, route/reference/profile-asset lineage, and the Finish operation/settings used.
- Results treats `voice_finish`, `voice_finish_split`, and `voice_finish_merge` as current result modes.
- Replay from a Finish result restores the underlying TTS/clone generation recipe rather than treating Finish settings as generation parameters.

### Capability and execution truth

R9 uses the local FFmpeg/FFprobe runtime where required. Current operations are:

- normalize (`loudnorm`)
- trim edge silence (`silenceremove`)
- noise cleanup (`afftdn`)
- loudness target (`loudnorm`)
- WAV/MP3 conversion
- real time-based splitting
- real multi-source merge

If a required binary/filter is missing, the operation is capability-gated and Neo returns a readable unavailable/setup state. **R9 never fabricates placeholder audio.** In particular, it does not create silent proof WAVs, copy WAV bytes into a file merely named `.mp3`, or create fake split/merge artifacts.

Historical `POST /api/voice/finish`, `/api/voice/finish/split`, `/api/voice/finish/merge`, and `GET /api/voice/finish/history` remain VO-V14 compatibility routes and are not current Finish UI authority.

Dialogue was subsequently released under VO-R10 and Batch under VO-R11. Historical V12/V13/V14 execution paths remain compatibility-only.

## VO-R6 Reference / Clone

### Current reference endpoints

- `POST /api/voice/references/upload`
- `GET /api/voice/references`
- `GET /api/voice/references/{reference_id}`
- `POST /api/voice/references/{reference_id}/analyze`
- `POST /api/voice/references/{reference_id}/attest`

### Current clone endpoints

- `POST /api/voice/clone/generate`
- `GET /api/voice/clone/jobs/{job_id}`

Historical `/api/voice/reference/*` and `/api/voice/clone` routes remain compatibility-only. They are not current Reference/Clone UI authority.

### Reference asset ownership

A Voice reference is a **Neo-owned reusable asset**, not a provider setting. Physical reference files remain under:

- `neo_data/outputs/voice/reference/`

VO-R6 deliberately reuses the established V6 physical reference/index storage instead of creating a duplicate binary store. The current R6 layer normalizes those stored records into `neo.voice.reference_asset.v1`, adds current authorization/readiness semantics, and leaves historical V6 schemas/routes intact for compatibility.

Changing the active Voice backend does not mutate or re-home the stored reference. The selected backend only determines whether that asset can currently be used for clone generation.

### Authorization lock

A newly uploaded current reference requires explicit confirmation that the user is authorized to use that voice reference for cloning. Neo records an R6 attestation alongside the reference record.

Legacy V6 references can remain visible, but **they are not clone-ready by default**. They must receive a current authorization attestation before R6 clone execution can use them.

The attestation is an authorization declaration, not identity verification. Neo does not claim to determine who the speaker is or whether an external legal right actually exists.

### QC and clone readiness

A current reference becomes clone-ready only when all of these are true:

1. the file exists below Neo-owned `neo_data/outputs/voice/reference/`
2. the R6 authorization attestation is confirmed
3. QC resolves to `usable` or `usable_with_warnings`

Existing WAV QC can inspect duration, sample rate, channels, clipping, rough level, and related warnings. Non-WAV deep inspection remains limited by the existing reference analyzer.

The Reference workspace owns upload, list, selection, authorization state, QC display/re-analysis, and **Generate Clone**.

### Provider capability gate

Clone generation is allowed only when the selected VO-R3 profile advertises **both**:

- `voice_clone`
- `reference_audio`

and the selected backend is reachable.

Current profile policy:

- **Chatterbox** — current VO-R13 physical local TTS + reference-clone adapter at the selected profile URL; Turbo and Multilingual V3 are physically mapped while R6 remains clone-authorization authority.
- **Fish Speech** — current TTS runtime plus capability-gated reference-clone adapter contract.
- **Kokoro** — current TTS runtime; reference/clone explicitly unsupported and fail-closed.

Capability truth remains selected-profile-owned. Neo never falls back to another Voice backend merely because that backend supports cloning.

### Reference provider handoff

For the current local-HTTP adapter contract, clone submission sends a controlled reference block containing the Neo-owned reference path plus QC/transcript metadata. Current transport is:

- `reference_transport = neo_owned_local_path`

R6 intentionally does not embed large reference audio as base64 in the JSON request. Remote/cloud providers that require an upload/token step need a dedicated future transport adapter rather than bypassing this contract.

### Clone job lifecycle

Current clone flow:

`selected Neo reference -> authorization/QC readiness -> VO-R3 clone/reference capability gate -> VO-R2 common script settings -> selected-profile clone adapter -> shared durable generation registry (mode=voice_clone) -> real provider audio -> Neo-owned output -> current Results`

Clone completion uses the same real-audio lock as TTS. Provider completion without retrievable audio fails. Async clone requests remain pollable through the shared registry and current clone job endpoint.

Clone metadata records the selected `reference_id`, reference path/label, QC state, authorization state, selected profile/provider/model/voice, and provider/local job lineage.

### Results and replay lineage

Current Results accepts both:

- `tts`
- `voice_clone`

Clone results expose reference lineage in the inspector. Replay never auto-switches Backend Profile.

If the active profile still advertises `voice_clone + reference_audio`, replay may reselect the same Neo-owned `reference_id` and return the user to Reference. If the active profile cannot clone, Neo loads only portable common settings and clears clone-reference state rather than pretending the replay is executable.

## Current release boundary

VO-R6 proves:

- current Neo-owned reference asset list/detail/upload/analyze/attest authority
- explicit authorization before current clone readiness
- legacy V6 references fail closed until re-attested
- path-safe reference ownership under the Voice reference root
- selected-profile clone/reference capability gating
- no automatic provider fallback or profile switching
- current clone jobs in the shared generation registry using `mode=voice_clone`
- real-audio completion lock with no placeholder clone output
- current Results support for TTS + clone jobs
- selected reference lineage in result inspection and replay
- provider-independent reference assets with provider-dependent execution readiness
- historical V6/V7/V9 clone/reference services preserved only as compatibility code

VO-R6 did **not** release dialogue, batch, saved-profile authoring/mutation, provider-native tuning controls, or Finish execution. VO-R7 subsequently activated the current Voice Profile Asset library, and VO-R8 activates synthesis-only provider-specific controls.

## VO-R7 Voice Profiles / Assets

### Current asset endpoints

- `GET /api/voice/profile-assets`
- `POST /api/voice/profile-assets`
- `GET /api/voice/profile-assets/{asset_id}`
- `PATCH /api/voice/profile-assets/{asset_id}`
- `DELETE /api/voice/profile-assets/{asset_id}`
- `POST /api/voice/profile-assets/{asset_id}/apply`

Current reusable records use `neo.voice.profile_asset.v1`. The asset identity is `asset_id`; `backend_profile_id` separately records which selected Voice backend profile originally authored the asset. This separation is intentional because the historical V7 saved-profile store used `profile_id` for the reusable profile itself, which conflicts with the current R3 meaning of `profile_id` as backend-profile routing authority.

### What a current Voice Profile Asset stores

A current asset stores only reusable provider-neutral/common Voice settings:

- language
- model selection
- voice/speaker selection
- speaking rate
- output format
- split-long-text state
- maximum chunk size
- punctuation cleanup
- optional R6 `reference_id` when that reference is currently authorized and clone-ready

The asset **does not store the script** and does not store provider-native controls such as expression strength, seed, reference strength, backend-native extras, dialogue mapping, or Finish parameters.

### Backend authority and apply policy

Voice Profile Assets are reusable configuration assets; they are **not routing profiles**. Applying an asset never changes the selected Voice Backend Profile.

- **Same backend profile:** Neo may restore the exact model/voice selection when those catalog entries still exist. Stale selections fail safely to that backend's `provider_default`.
- **Different active backend profile:** Neo applies only portable common fields, resets Model and Voice to `provider_default`, normalizes any fixed language rule through current R3 capabilities, and leaves the active backend unchanged.
- **Reference-backed asset:** the reference is reselected only if the active backend still advertises both `voice_clone` and `reference_audio` and the R6 reference remains clone-ready. Otherwise the reference is omitted with a compatibility warning.

### Legacy V7 compatibility

Historical `neo.voice.profile.v7` records and `/api/voice/profiles*` routes remain readable compatibility infrastructure. They are not auto-promoted into current R7 assets and are never auto-applied. R7 reports their presence only as legacy compatibility context.

### Runtime and Results lineage

When a current asset is applied and then used to generate TTS or clone audio, VO-R4/R6 job runtime stores the asset lineage separately from provider routing. VO-R5/R7 Results exposes the originating asset ID/name and application mode. Result replay may restore the asset ID as lineage, but it does not silently apply the asset or switch backend profiles.



## VO-R8 Provider-Specific Controls

Canonical endpoint:

- `GET /api/voice/provider-controls?profile_id=<selected Voice profile>&mode=tts|voice_clone`

Schema: `neo.voice.provider_controls.v1`.

VO-R8 activates provider-specific **synthesis** controls without expanding the VO-R2 common contract. The selected Voice Backend Profile declares `provider_controls` definitions with control type, capability requirement, supported generation modes, default, and validation metadata. Neo filters that schema through the active profile capabilities and requested mode before it reaches the UI.

Current rules:

- R2 common fields remain the only canonical common settings.
- Provider controls live separately in the draft as `provider_controls.tts` and `provider_controls.voice_clone`.
- TTS/clone requests submit normalized values only under the nested `provider_controls` block; they are never flattened into common `params`.
- Unsupported, stale, unknown, or out-of-range control values fail closed before provider submission.
- `backend_native` JSON is sandboxed: reserved routing/common/output keys are rejected and arbitrary nested execution payloads are not accepted.
- Same-profile replay may restore exact provider controls. Cross-backend replay clears them.
- Voice Profile Assets intentionally continue to exclude provider-native controls, so applying an R7 asset cannot smuggle backend-specific tuning across providers.
- Runtime-saved backend profiles that predate R8 inherit provider-control definitions from their provider template when the field is absent; explicit profile overrides remain authoritative.

Examples in the current built-in profile templates include Chatterbox expression/reference strength and seed, Kokoro seed, and Fish Speech expression/reference strength, seed, pause handling, artifact cleanup, tags, prosody, and guarded backend-native extras. **The frontend does not hardcode those provider names or control lists**; it renders the selected profile's returned R8 contract. VO-R13 adds one physical-truth caveat for Chatterbox: Turbo does not accept exaggeration/CFG/min-p and `reference_strength` has no exact native Chatterbox equivalent, so the physical adapter does not silently reinterpret those values. Multilingual may use the historical expression value as its native exaggeration control; model-family-specific R8 control presentation remains a later refinement.

Finish is active under VO-R9. Dialogue / Multi-speaker is active under VO-R10. Batch is active under VO-R11.

## VO-R10 Dialogue / Multi-speaker

Canonical current endpoints:

- `GET /api/voice/dialogue-runtime/capabilities`
- `POST /api/voice/dialogue-runtime/parse`
- `POST /api/voice/dialogue-runtime/generate`
- `GET /api/voice/dialogue-runtime/jobs/{job_id}`

Current schema/runtime authority is `neo.voice.dialogue_plan.v1` plus `neo.voice.dialogue_runtime_job.v1`.

VO-R10 rules:

- Dialogue supports explicit `[Speaker]` blocks and `Speaker: line` syntax. Neo does not infer emotions/actions from the script.
- One selected VO-R3 backend profile owns the entire Dialogue parent job. Speaker mappings never auto-switch providers.
- A speaker may use a current provider voice, a VO-R7 Voice Profile Asset, or an R6 authorized clone-ready reference asset.
- Provider voice turns reuse the current R4 TTS runtime. Reference-backed turns reuse the current R6 clone runtime. Provider-specific tuning remains in the existing R8 `provider_controls.tts` / `provider_controls.voice_clone` blocks.
- The parent Dialogue job is stored in the shared generation registry as `mode=voice_dialogue`; each turn preserves its child job ID/source lineage.
- Combined Dialogue audio is created only by real FFmpeg stitching of completed Neo-owned child audio. Missing/failed child audio or missing FFmpeg fails the Dialogue; current R10 never writes silent placeholder turns or a fake combined WAV.
- VO-R5/R10 Results expose Dialogue plan, speaker assignments, child turn jobs, stitch metadata, and replay-safe mapping state.
- Replay never changes the active backend profile. Speaker/model/voice/profile-asset/reference mappings are revalidated against the currently selected backend before another Dialogue run.
- VO-R9 Finish can use a completed `voice_dialogue` combined result as a normal Neo-owned Voice source.
- Historical VO-V12 `/api/voice/dialogue` and stub dialogue manifests remain compatibility-only and are not mounted as current runtime authority.

## VO-R11 Batch

Canonical current endpoints:

- `GET /api/voice/batch-runtime/capabilities`
- `POST /api/voice/batch-runtime/import`
- `POST /api/voice/batch-runtime/{batch_id}/run`
- `GET /api/voice/batch-runtime/{batch_id}`
- `GET /api/voice/batch-runtime/{batch_id}/poll`
- `POST /api/voice/batch-runtime/{batch_id}/retry-item`
- `GET /api/voice/batch-runtime/history`

Current schema authority is `neo.voice.batch_runtime.v1` plus `neo.voice.batch_history.v1`.

VO-R11 rules:

- Batch is a **Generation mode**, not a sixth Voice workspace. The five-workspace surface architecture remains unchanged.
- Import accepts TXT, Markdown, CSV, JSON, and SRT. Imported rows may choose `tts`, `voice_clone`, or `voice_dialogue`.
- One selected VO-R3 backend profile owns the entire Batch. Imported `profile_id`, `backend_profile_id`, `provider_id`, `runtime`, and `family` fields are ignored with warnings and cannot route individual rows to another backend.
- Imported provider-native controls are also ignored. Trusted R8 `provider_controls.tts` / `provider_controls.voice_clone` come only from the current selected-profile Batch submission state.
- Batch itself is provider-agnostic orchestration. A provider does **not** need a native batch API. Neo dispatches normal current R4 TTS, R6 Clone, or R10 Dialogue jobs with bounded concurrency from 1–4.
- The Batch parent is stored in the shared generation registry as `mode=voice_batch`, but it has no playable audio output. Audio authority remains with the normal child jobs.
- Each child receives `neo.voice.batch_lineage.v1` metadata including Batch ID/name, parent job ID, item ID/index/title, and attempt. Results displays that lineage on the playable child result.
- Failed/cancelled items can be retried individually. Retry creates a new child attempt and never overwrites the prior child job/output.
- A terminal Batch parent is immutable. Re-running the same terminal Batch cannot overwrite existing children; import a new Batch or retry failed items.
- Batch manifest/history files live below `neo_data/outputs/voice/batch/`. They are orchestration/history authority only, not a second audio Results database.
- Historical VO-V13 `/api/voice/batch/*` remains compatibility-only and is not mounted as current Batch authority.
- Current R11 never fabricates per-item audio. Child completion still inherits the real-audio requirements of R4/R6/R10.

## VO-E1 / VO-E2 / VO-E3 / VO-E4 / VO-E5 Neo Voice Engine foundation

VO-E1 froze the backend contract, VO-E2 implemented the lightweight gateway/supervisor, VO-E3 made worker/model routing manifest-driven, VO-E4 added scheduler/lifecycle control, and VO-E5 migrates Chatterbox as the first real worker while preserving Neo's existing Voice orchestration ownership.

Current physical routing:

```text
Neo Voice runtime
  -> selected default profile: voice.neo_engine
  -> Neo Voice Engine :8790
  -> chatterbox manifest / scheduler / supervisor
  -> Chatterbox worker :8791 / Neo_Runtime/voice/envs/chatterbox
  -> gateway temporary audio
  -> Neo-owned Voice Result
```

- `voice.neo_engine` is now seeded/default and targets `127.0.0.1:8790`;
- `voice.chatterbox` remains enabled but non-default for direct diagnosis/fallback;
- `neo_voice_engine/manifests/chatterbox.json` owns `chatterbox_turbo` and `chatterbox_multilingual`;
- a cold managed worker reports `stopped` and auto-starts on first executable job instead of appearing failed;
- the gateway still contains no Chatterbox/Torch stack; Chatterbox remains isolated under `Neo_Runtime/voice/envs/chatterbox`;
- VO-E4 scheduler admission chooses CPU/CUDA before worker submit and forwards only a private `_neo_execution` hint;
- Chatterbox lazy model loading remains inside the async generation job; no blocking first-use gateway `/load` is required;
- clone authorization, QC, local reference transport and path guards are revalidated at the gateway before worker dispatch;
- final audio persistence remains Neo-owned; gateway output is temporary provider material;
- manifest identity stays fail-closed and future engines must integrate as new workers/manifests rather than new Neo-facing stacks.

See `guides/05_VOICE/neo_voice_engine_contract.md`, `guides/05_VOICE/neo_voice_engine_gateway.md`, `guides/05_VOICE/neo_voice_engine_registry.md`, `guides/05_VOICE/neo_voice_engine_scheduler.md`, and the VO-E1 through VO-E5 provider records.

## Next implementation phase

- Add the next reviewed TTS family (recommended: Qwen3-TTS) as a separate manifest-owned worker behind Neo Voice Engine.
- Do not create a new Neo-facing backend profile per model family; Neo should continue selecting `voice.neo_engine` while engine/model choice is resolved inside the gateway.
- Keep each incompatible ML stack isolated and preserve the same registry/scheduler/job/output contract proven by Chatterbox.
