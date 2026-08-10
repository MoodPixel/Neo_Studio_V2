---
guide_id: prompt_captioning.profile_engine_p23
title: Prompt + Captioning Profile Engine (P23)
surface: prompt_captioning
scope: built_in
applies_to:
  - prompt_studio
  - caption_studio
  - batch_dataset
  - batch_library
tags:
  - profiles
  - grounding
  - visual_analysis
  - prompts
  - captioning
priority: 85
version: 8
updated: 2026-08-09
---

# Prompt + Captioning Profile Engine (P23)

## 2026-08-09 — Prompt output-purity and Negative semantic invariant

Prompt Studio provider output has two additional execution invariants:

1. **Final-prompt purity.** Prompt generation/enhance/rewrite/transform contracts must begin directly with the usable prompt/instruction and must not prepend model analysis, a source description, or wrapper labels such as `The image shows`, `Here is the prompt`, or `The final prompt is`. Because local models can still violate that contract, the service boundary may extract the suffix after the last explicit `Final prompt` marker. This cleanup is marker-driven only; arbitrary prose without a marker is not truncated. If cleanup changes the resolved output, `partial_text` must be synchronized to the clean output before history/result aliasing.

2. **Negative semantic separation.** `negative_prompt` receives source/positive/custom text only as protected wanted context. A contextual Negative term must either carry recognizable failure/exclusion semantics or be explicitly requested by the user through `no`, `avoid`, `exclude`, `without`, or equivalent instruction. Positive subject, identity, wardrobe, pose, mood, style, camera, lighting, scene, and quality descriptors — including close protected-concept synonyms — must not be emitted as negative terms. Failure-qualified variants remain allowed.

The semantic guard is deliberately fail-closed: if every provider term is positive-context leakage, return an empty Negative result with a warning rather than inventing a replacement negative prompt or sabotaging the positive request. Existing repetition/alias cleanup and the 32-term hard cap remain downstream invariants.

Validation: `tests/test_prompt_captioning_prompt_output_purity_guard.py`.

## 2026-08-09 — first-load manifest hydration invariant

The canonical profile manifest must be requested from the **shared Prompt & Captioning workspace render boundary** before Prompt Studio or Caption Studio is chosen. Prompt Studio is the default workspace and therefore cannot depend on Caption Studio being rendered first to populate manifest-backed selects.

`ensurePromptCaptioningProfileManifest()` remains single-flight. During a cold asynchronous load, manifest-backed selects may briefly render their current-value fallback; successful hydration must trigger a normal `render()` so the full option set appears automatically.

This specifically protects Prompt Studio **Visual Treatment** and **Prompt Format** on first entry while retaining the same manifest source used by Caption Studio and Batch profiles.

## Current status — P23.4

P23.1 established the shared profile and visual-analysis foundation. P23.2 activated it for **Caption Studio**, **Batch → Dataset Preparation**, and **Batch → Save to Library**. P23.3 migrated **Prompt Studio** to the same canonical profile engine and added a text-first **Text → Video** prompt compiler. P23.4 closes the migration by making canonical profiles durable across saved records, presets, history, snapshots, reuse, and replay.


## Submit-state integrity — 2026-08-09

The canonical profile is not only a storage/replay contract; it is now explicitly a **submit-time live-form contract**. Prompt Studio and Caption Studio must synchronize visible profile controls into the canonical state and build the outgoing payload before any running-state rerender. This prevents Visual Treatment, Prompt Format, Purpose, Analysis Scope, Output Format, Grounding, Detail Level, Length, and free-text instructions from falling back to stale/default values during execution.

Prompt/Captioning state reads must preserve the live object identity during a mounted render cycle. Re-normalizing by replacing `state.promptCaptioning` on every read is prohibited because bound UI handlers retain references to nested state objects. UI snapshots are normalized at initialization/restore boundaries instead.

Negative Prompt remains a Prompt Studio operation, but its provider instruction contract is exclusion-only. The current positive output may be supplied as context for deriving blockers; it must not be restated as desired positive scene text.

## Batch one-image / one-caption boundary — 2026-08-09

`batch_dataset` and `batch_library` are per-item vision tasks. The worker may scan many files, but `_caption_for_batch_image()` rebuilds a single-image payload for each item and the provider contract must treat that request as **one current image only**. Internal multimodal attachment labels such as `[Image 1]` are never valid batch-caption output.

The profile compiler therefore adds a one-image/one-caption rule to both batch surfaces and forbids image enumeration, attachment labels, filenames, and paths. Batch sampling also appends newline image-marker stop sequences without replacing existing stops. Provider output is normalized before persistence; standalone/trailing `[Image N]` markers (including a truncated missing `]`) are stripped, while ordinary inline prose is preserved. Marker-only responses fail closed instead of being saved.

This is an output-integrity rule, not a change to folder scanning, batch counts, category persistence, Dataset file transfer, or the single-image Caption Studio profile model.

## One profile contract

Prompt Studio, Caption Studio, Dataset Preparation, Save to Library, image-edit prompt generation, and video-prompt generation share one canonical profile contract:

- Purpose
- Visual Treatment
- Grounding
- Analysis Scope
- Output Format

Task-specific extensions are also defined centrally:

- Target Media
- Prompt Task
- Edit Intent
- Preservation Policy
- Motion Profile
- Camera Behavior

The single source of truth is `neo_app/prompt_captioning/profile_manifest.json`. The frontend/backend may read the same manifest through `GET /api/prompt-captioning/profile-manifest` rather than maintaining duplicate option lists.

## Surface defaults

- Prompt Studio: general purpose, source-accurate treatment, transformative grounding, natural prompt, Image / Text-to-Image.
- Caption Studio: general purpose, source-accurate treatment, balanced grounding, full-image descriptive caption.
- Dataset Preparation: strict grounding + source-accurate treatment + dataset-caption format are locked profile defaults.
- Save to Library: balanced grounding with source-accurate treatment and image-generation-prompt output by default.

## Backward compatibility

P23.1 does not delete legacy payload fields such as `style`, `output_style`, `caption_mode`, `component_type`, `caption_style`, or preset `target_use`. `normalize_prompt_captioning_payload()` derives a canonical `profile` alongside legacy data. Known legacy values map to canonical IDs; unknown free-text values are preserved as `Custom` values rather than discarded.

The normalized payload contract is now version 2 and includes `payload.profile`. Result metadata and replay payloads retain the canonical profile so later UI/preset migration does not lose intent.

## Grounding policy

Grounding has three canonical levels:

- **Strict** — directly visible or explicitly supplied facts only; uncertainty is omitted rather than guessed.
- **Balanced** — factual with cautious broad visual interpretation when supported.
- **Transformative** — permits requested generation/style language while keeping concrete source facts grounded.

Universal visual-fidelity invariants prevent unsupported names, relationships, occupations, brands, specific locations, nationality/ethnicity/exact-age claims, unreadable text claims, and off-frame inventions.

## User instruction authority

The shared instruction compiler places a user instruction above task-profile defaults. Purpose, scope, visual treatment, and output format may guide the task but must not expand beyond an explicit user request. Grounding/fidelity invariants remain hard constraints.

## Visual Analysis contract

P23.1 adds a provider-neutral visual-analysis schema with these fields:

- subjects
- appearance
- pose
- expression
- clothing
- environment
- composition
- camera
- lighting
- visual_style
- visible_text
- actions_interactions
- objects
- uncertainties

The schema is intentionally provider-neutral. Caption Studio, Dataset, Library, image editing, and Image-to-Video prompt generation can consume the same structured analysis without hard-coding KoboldCpp/Qwen/VLM provider details.

P23.2 now consumes the profile/analysis request when building Caption Studio vision messages. The selected VLM still returns only the requested final caption/prompt; Neo records the provider-neutral visual-analysis request in execution metadata rather than exposing hidden reasoning.

## Caption Studio tasks — P23.2

Caption Studio is image-first and exposes four canonical tasks:

- **Caption Image** — grounded caption/analysis with selectable Purpose, Visual Treatment, Analysis Scope, Output Format, and Grounding.
- **Image → Recreation Prompt** — source-faithful image-generation prompt; optional user instruction may request intentional changes.
- **Image → Editing Prompt** — edit instruction with Edit Intent + Preservation Policy. An edit instruction is required.
- **Image → Video Prompt** — temporal animation prompt with Motion Profile + Camera Behavior, grounded on the source image as the first-frame state.

`Caption mode` is now presented as **Analysis Scope**. The duplicate visible `Component type` control is removed from Caption Studio; Neo derives its legacy value internally for compatibility. Existing caption-format shortcut buttons remain but now set the canonical profile instead of invoking separate prompt logic.

## Batch isolation — P23.2

Batch no longer inherits hidden single-image caption style, output style, selected preset, or sampling state. Dataset and Library keep independent profiles, instructions, and conservative sampling defaults.

### Dataset Preparation

Dataset keeps these locks:

- Grounding = **Strict**
- Visual Treatment = **Source Accurate / Neutral**
- Output Format = **Dataset Caption**
- Target Media = Image
- Prompt Task = Dataset Preparation

The user selects Dataset Purpose and Analysis Scope and may supply an optional LoRA **Trigger Token**. Dataset sampling is capped at temperature `0.30` and top-p `0.85`.

### Save to Library

Library keeps an independent profile with selectable Purpose, Visual Treatment, Analysis Scope, Output Format, and Grounding. Its batch temperature is capped at `0.55` to reduce unsupported invention while retaining useful descriptive flexibility.

## Shared Instruction authority — P23.2

Batch Shared Instruction is now mode-specific (`datasetInstruction` / `libraryInstruction`) and is reread from the visible textarea immediately before Preview or Run. The outgoing canonical `inputs.caption_instruction` and nested compatibility `caption_settings.instruction` carry the same value. Switching Dataset ↔ Library preserves each instruction independently.

## Prompt Studio — P23.3

Prompt Studio is the **text-first** Prompt & Captioning surface. It does not inspect source images. Image-aware recreation/edit/animation prompting remains in Caption Studio.

Prompt Studio exposes **Target Media** plus a task filtered for that media:

- **Image → Text → Image** — build an image-generation prompt from a text idea.
- **Image → Image Edit Instruction** — build a source-safe edit instruction from text only. Neo explicitly does not pretend it can see the source image.
- **Video → Text → Video** — build a temporal generation prompt from a text idea.

The old mixed **Prompt Style** selector is no longer the primary Prompt Studio contract. P23.3 separates:

- **Visual Treatment** — how the result should look, such as Cinematic, Photorealistic, Anime, Watercolor, etc.
- **Prompt Format** — how Text → Image prompt text is formatted, such as Natural Prompt, SD/SDXL Tags, Hybrid, or Structured.

Legacy `style` values remain compatibility aliases. Cinematic/Anime/Photoreal map to Visual Treatment; `sdxl_tags`/`descriptive` map to Prompt Format.

### Text → Video contract

Text → Video uses the selected local text backend; no vision model is required. The compiler keeps the user idea authoritative and asks the model to organize it temporally around:

1. starting state
2. requested action/motion
3. transitions and continuity
4. selected Motion Profile
5. selected Camera Behavior
6. ending state

The compiler explicitly rejects gratuitous cuts, extra people/objects/locations/actions, and continuity changes not requested by the user. Prompt Studio result metadata reports an empty `visual_analysis_request` because no image was inspected.

### Text-only Image Edit

Image Edit Instruction exposes **Edit Intent** and **Preservation Policy**. Because Prompt Studio has no image input, the generated instruction may use only source details supplied in text. It must not claim unseen clothing, pose, camera, background, identity, or other source-image facts.

### Handoff

Prompt Studio generation results can be appended/replaced into the matching Neo generation workspace:

- Image prompts/edit instructions → Image positive prompt
- Text → Video prompts → Video positive prompt

The cross-tab handoff contract allows `video.positive_prompt` / `video.negative_prompt`; P23.3 adds direct Prompt Studio buttons for the positive Video prompt path.

### Presets and saved prompts

New Prompt presets and saved Prompt records retain the canonical profile. Older presets without a profile still load through the legacy style migration path. The visible preset-details form no longer asks the user to maintain a second Style setting; the current Prompt Studio profile is captured automatically.


## Persistence and migration — P23.4

P23.4 introduces `prompt_captioning.persistence.v2`. Profile-bearing local records now expose the same canonical `profile` whether they were created before or after P23.

Profile-bearing libraries are:

- saved prompts
- prompt history
- prompt presets
- saved captions
- caption history
- caption presets
- caption batch results
- result metadata / replay payloads

Legacy fields are retained for compatibility. Neo does not delete `style`, `target_use`, `caption_mode`, `component_type`, `output_style`, or `caption_style`; it materializes the canonical profile alongside them. This makes migration non-destructive and keeps older exports readable.

P23.4 also extends the legacy alias table for historical Caption Studio values such as `person_only`, `face_only`, `outfit_only`, `pose_only`, `location_only`, and `sd_prompt`. Unknown free-text legacy style values still resolve to `Custom` rather than being discarded.

### Lazy compatibility vs physical migration

Normal library reads are **lazy-migrated** in memory, so old records work immediately without rewriting user files. New saves, updates, duplicates, and imports write the canonical profile.

For users who want to materialize profiles into existing local JSON files, Neo exposes:

- `GET /api/prompt-captioning/storage/migration-status` — read-only migration count/report.
- `POST /api/prompt-captioning/storage/migrate` — explicit migration. The default request is a dry run. Set `dry_run=false` to apply. Existing files are backed up by default before a changed file is written.

The migration is idempotent: after a successful apply, another dry run should report `needs_migration = 0`.

### Library export/import

Prompt/Captioning library snapshots are now `prompt_captioning.library.v2` and include:

- `profile_schema_version`
- `persistence_schema_version`
- canonical profiles in profile-bearing records
- per-library migration reports

Imports still accept older snapshot shapes. Legacy incoming records are normalized before write, and existing IDs/timestamps remain protected according to the normal merge rules.

## Replay and reuse — P23.4

Replay payloads are now `prompt_captioning.replay_payload.v2`. The replay profile is canonicalized even when the source metadata was written before P23.

Saved-record reuse now carries `reuse_payload.profile` explicitly instead of relying on the caller to rediscover task intent from legacy fields. This keeps Image vs Video target media, Prompt Task, Visual Treatment, Motion Profile, Camera Behavior, Edit Intent, and Preservation Policy intact across library reuse.

Prompt/Captioning history records now materialize their nested execution `payload.profile` at the top level. Caption presets also save the canonical profile both at `record.profile` and `record.settings.profile`.

## UI load behavior — P23.4

Prompt Studio and Caption Studio load canonical profiles from saved records/presets when available. For older records they fall back to the historical fields and immediately reconcile them into the P23 profile. Caption Studio no longer loses Image → Video or Image → Edit task metadata when loading a saved caption/preset.

## Negative Prompt compact-output invariant — 2026-08-09

The `negative_prompt` tool remains outside the normal positive Prompt Task compiler because its output contract is fundamentally exclusion-only. Its runtime invariant is now:

1. positive/source text is **context only** and must never be rewritten as desired scene prose;
2. output contains only relevant comma-separated failure modes/exclusions;
3. target size is **12–32 unique terms**;
4. repeated terms and conservative equivalent variants are normalized after provider return;
5. final output is capped at **32 unique terms**;
6. generation is bounded to `temperature <= 0.35`, `top_p <= 0.85`, and `max_tokens <= 192`.

The post-provider cleanup layer is deliberately conservative. It handles exact repeats plus known equivalent artifact wording (for example color-fringing/chromatic-aberration, color-bleed variants, pixelization/pixelation, and JPEG/compression-artifact variants). It does not perform broad semantic rewriting and does not add terms that the provider did not produce.

Do not remove this post-provider guard solely because a model prompt says “do not repeat.” Local backends can still enter repetition loops even under a correct instruction contract, so Negative requires both generation-time and deterministic output-time protection.
