---
guide_id: prompt_captioning.overview
title: Prompt + Captioning Overview
surface: prompt_captioning
scope: built_in
applies_to:
  - prompt_captioning_workspace
  - prompt_captioning
  - prompt_builder
  - captioning
tags:
  - prompts
  - captioning
  - keywords
priority: 70
version: 12
updated: 2026-08-18
---

# Prompt + Captioning Overview

Prompt + Captioning stores reusable prompt outputs, captions, keywords, and source notes. The Assistant can index saved records so users can search and reuse previous prompt/caption work.


## Prompt & Captioning backends

Prompt Studio and Caption Studio use the same existing backend-profile selector. **KoboldCpp Local** remains the default local text backend. Neo also includes an optional **ComfyUI LLM / VLM** backend profile that points at a normal ComfyUI server, usually `http://127.0.0.1:8188`.

Neo uses **Connect/Test** to inspect the connected Comfy server and report whether the llama.cpp model loader, instruct node, optional parameter/unload helpers, main models, `mmproj` projectors, chat handlers, and Neo's bundled Prompt/Caption bridge nodes are available.

**Prompt Studio and Caption Studio can now execute through ComfyUI LLM / VLM.** Prompt text is returned through Neo's stable `NeoPromptCaptionTextOutput` history handoff. Caption images enter the Comfy workflow through `NeoPromptCaptionImageInput`, then pass to the detected llama.cpp VLM route. By default the VLM request uses `force_offload` so the model is released after inference.

A reachable ComfyUI server by itself is not enough. Prompt execution requires the llama.cpp loader + instruct nodes, at least one main model, and Neo's Text Output bridge. Caption execution additionally requires an image-capable instruct input, an `mmproj` projector, a usable chat handler, and Neo's Image Input bridge. See `comfy_llamacpp_backend.md` for setup and normal use.

Backend-specific model, projector, chat-handler, context, and VRAM controls appear only when the ComfyUI LLM/VLM backend is selected; the normal Prompt/Captioning controls remain shared across providers. When that backend shares the local GPU with Image or Video, Neo now serializes the Comfy GPU handoff so Prompt/Caption, Image, local Video, and Finish/SeedVR2 do not compete for the same local GPU slot.

For **Batch Captioning**, the ComfyUI LLM/VLM backend now uses a retained batch session: Neo holds the shared GPU lease once, keeps the VLM loaded between sequential images, and performs the configured unload/memory cleanup once when the batch ends. Single-image caption runs still use the normal per-request unload behavior.

**Backend readiness is now route-specific.** After Connect/Test, Neo reports Prompt Ready and Caption Ready separately, validates the required llama.cpp/Neo bridge nodes, checks the live model/mmproj/handler catalogs, and flags saved selections that no longer exist. Missing Prompt requirements block Prompt and Caption; missing VLM-only requirements can leave Prompt Ready while Caption remains gated.

**Startup/reconnect validation keeps that readiness current.** Saved Comfy LLM/VLM settings are restored when Neo starts, but an old saved Connected state is not trusted. Neo refreshes the current Comfy catalog in the background, rechecks saved selections, records the last successful validation time, and revalidates again before a real Prompt/Caption task is queued. If Comfy is restarted or comes back online later, the current live catalog replaces the stale session snapshot. Manual **Disconnect** pauses that automatic validation until Connect/Test is used again.

**Runtime recovery is also shared-GPU aware.** If Comfy disconnects, times out, restarts, loses history, rejects a workflow, runs out of VRAM, or fails an explicit memory cleanup, Neo returns an actionable error and keeps/reconciles the GPU guard according to what is actually known about the Comfy queue. A missing queue response is not treated as proof that nothing ran. Existing Comfy work discovered after a Neo restart is guarded until the backend queue clears.

## P23 profile engine

As of P23.4, the shared profile engine is active across Prompt Studio, Caption Studio, Dataset Preparation, and Save to Library, and canonical profiles persist through saved records, presets, history, snapshots, reuse, and replay. See `profile_engine_p23.md`; storage/replay migration details are in `storage_migration_p23.md`.

**Prompt Studio is text-first.** It supports Text → Image, text-only Image Edit Instruction, and Text → Video. Visual Treatment is separate from Prompt Format; Text → Video adds Motion Profile + Camera Behavior and produces a temporal prompt without claiming any image analysis.

**Caption Studio is image-first.** It produces grounded captions, Image → Recreation prompts, Image → Editing prompts, or Image → Video animation prompts through the vision backend. Dataset Preparation uses a strict/source-accurate training profile with optional trigger token; Save to Library keeps its own independent reusable profile.


## P23.4 storage + replay closure

P23.4 makes the P23 profile contract durable instead of UI-only. Older Prompt/Captioning records are normalized lazily when read, while new saves/imports persist `prompt_captioning.persistence.v2` metadata and a canonical `profile`.

- Prompt/Captioning snapshot exports use `prompt_captioning.library.v2`.
- Replay payloads use `prompt_captioning.replay_payload.v2`.
- Caption presets preserve the active Caption Studio profile.
- Prompt/caption history materializes the profile stored inside the execution payload.
- Library reuse includes the profile explicitly.
- Old style/mode fields remain readable and are not deleted.

Use the storage migration status/apply endpoints only if you want to physically rewrite older local JSON records; normal UI reads do not require that migration. Applied migrations are backup-first by default.

## 2026-08-09 Prompt Studio output-purity + Negative semantic guard

Prompt Studio treats provider prose wrappers as transport noise, not part of the usable prompt. Text-generation tasks still instruct the backend to return only the final prompt, and the provider boundary now recognizes explicit wrappers such as **“The final prompt is:”** / **“Final prompt:”**. When that marker exists, Neo keeps only the final deliverable after the last marker, strips balanced provider-added quote wrappers, and synchronizes the cleaned text into all output aliases/history so the discarded model description cannot leak back through `partial_text`. Responses without a clear final-prompt marker are left intact rather than heuristically truncating valid prompt prose.

Prompt Studio **Negative** now treats the source idea, current positive output, and positive custom instruction as **protected wanted content**. Negative output is accepted only when a term is a recognizable failure state/artifact or an explicit user-requested exclusion. Unqualified subject/style/mood/pose/camera/lighting descriptors copied or synonym-expanded from wanted content are removed. Failure-qualified terms such as `unrealistic proportions`, `lack of realism`, `incorrect camera angle`, `malformed hands`, and provider-artifact terms remain valid. Explicit custom exclusions (`avoid …`, `no …`, `without …`, `exclude …`) override positive-context protection.

If a provider returns only wanted-content descriptors and no safe exclusion terms, Neo fails closed with an empty Negative result plus a warning instead of placing contradictory positive content into the Negative box. The existing 32-term cap, duplicate/equivalent-term cleanup, and low-entropy Negative sampling envelope remain active.

## 2026-08-09 Prompt Studio first-load profile hydration hotfix

Prompt Studio is the default Prompt & Captioning workspace, so its manifest-backed controls must hydrate without requiring a visit to Caption Studio first. The shared `renderPromptCaptioningPanels()` boundary now starts `ensurePromptCaptioningProfileManifest()` before either Studio is rendered.

On a cold launch, the first paint may temporarily use the current-value fallback while the async manifest request is in flight. Once the manifest arrives, the loader automatically calls `render()`, replacing that fallback with the full selectable option lists. The in-flight guard prevents duplicate manifest requests.

Locked behavior:

- **Visual Treatment** must expose the manifest treatment list on first Prompt Studio entry, including Anime, Cinematic, Photorealistic, Watercolor, and other registered treatments.
- **Prompt Format** must expose the Prompt-Studio-selectable formats on first entry, including Natural Prompt, SD/SDXL Tags, Hybrid, and Structured.
- The user must not need to switch to Caption Studio and back to populate these selects.
- Profile hydration belongs to the shared Prompt & Captioning workspace bootstrap, not to a side effect of one Studio.

## 2026-08-09 submit-state integrity hotfix

Prompt Studio and Caption Studio treat the **visible form as authoritative at submit time**. The Prompt/Captioning state accessor preserves the mounted state-tree identity instead of rebuilding the whole tree on every read, preventing event handlers from writing into stale Prompt Builder / Captioning objects.

Before a provider request starts:

- Prompt Studio synchronizes the live source text, custom instruction, Visual Treatment, Prompt Format/task controls, provider profile, and sampling values, then snapshots the payload **before** the running-state rerender.
- Caption Studio synchronizes Purpose, Visual Treatment, Analysis Scope, Output Format, Grounding, task-specific controls, Detail Level, Length, provider profile, sampling values, and the visible Instruction field, then snapshots the payload **before** rerender.
- The main Prompt Studio **Generate** action always invokes `prompt_generate`; prior Enhance / Rewrite / Cleanup / Negative actions cannot latch onto the main Generate button.
- Negative generation uses a dedicated exclusion-only provider contract and may use the current positive output as context, but it must never echo or rewrite that positive prompt into the Negative output field.

These rules are submit-integrity invariants. Do not move the running-state `render()` ahead of the live-form synchronization/payload snapshot.


## 2026-08-09 batch image-marker output guard

Batch Captioning processes **one current image per provider request**. A local multimodal backend may nevertheless leak or repeat chat-template attachment markers such as `[Image 1]`, `[Image 2]`, or a token-truncated `[Image 3` after an otherwise valid caption. Those markers do not mean Neo discovered or submitted extra files.

The Batch Captioning contract now protects this boundary in three layers:

- Dataset Preparation and Save to Library explicitly tell the vision model that each request contains exactly one current image and must return exactly one caption/prompt for that image only. The model is told not to enumerate image indices, filenames, paths, or attachment labels.
- Batch sampling appends `\n[Image` / `\n[image` provider stop sequences while preserving any existing configured stop sequence, preventing the common marker tail from consuming the remaining token budget.
- Caption output is sanitized at the provider boundary before metadata/history/save operations. Standalone or trailing bracketed image-index markers are removed, including a missing closing bracket caused by token-limit truncation. Batch per-image handling repeats the sanitizer defensively before Dataset `.txt` or Library persistence.

Normal prose is not broadly rewritten: only standalone/trailing provider-style `[Image N]` markers are removed. If a backend returns **only** image markers and no useful caption text, Neo fails the caption item closed with `caption_internal_image_markers_only` rather than saving an empty/garbage caption.

A four-image regression fixture verifies exactly four items are processed/saved and that a contaminated final response containing a simulated `[Image 1]` through `[Image 38]` tail is stored as only its useful caption.

## 2026-08-09 negative-prompt repetition guard

Prompt Studio **Negative** is a specialized compact exclusion task, not a normal positive-prompt generation mode. The negative route now has three independent safeguards against local-model comma-list loops:

- A dedicated Negative system/user contract asks for **12–32 unique high-value exclusions**, forbids positive scene prose and synonym repetition, and tells the model to stop after the last useful term.
- Negative generation uses a conservative sampling envelope: temperature is capped at `0.35`, top-p at `0.85`, and output at `192` tokens even when the general Prompt Studio controls request a larger/more-random generation.
- Provider output is normalized before it reaches Prompt Studio history/results. Exact duplicates and a conservative set of equivalent artifact variants are collapsed while preserving the first useful wording, and the final list is hard-capped at **32 unique terms**.

The sanitizer does **not** invent replacement negative tags. It only cleans the provider output. If cleanup or truncation occurs, the normal Prompt Studio warning field records that Neo normalized the result.

This guard is intentionally scoped to `negative_prompt`; Generate, Enhance, Rewrite, Cleanup, and text-transform outputs keep their existing output guards and sampling behavior.

## ComfyUI LLM / VLM backend settings

When **ComfyUI LLM / VLM** is the selected Prompt or Caption backend, Neo shows provider-only controls for the live-discovered llama.cpp model, chat handler, context length, VRAM budget, and unload policy. Caption Studio also exposes the VLM projector and an advanced image-analysis/token section. These controls are hidden for other backends and save into the selected backend profile.


## ComfyUI LLM / VLM final status presentation

When **ComfyUI LLM / VLM** is selected, Prompt Studio and Caption Studio now show only the readiness for the active route instead of repeating the complete backend installation checklist. The route card shows the selected model components, whether the current live catalog is verified, and the shared-GPU policy.

Use **Admin → Backends → ComfyUI LLM / VLM** for the full grouped backend view. It summarizes Connection, Core nodes, Neo bridge, Prompt route, Caption route, and GPU policy, with detailed catalogs/timestamps collapsed under **Catalog & validation details**. Neo uses the concise states **Ready**, **Prompt only**, **Caption only**, **Needs setup**, **Offline**, **Validating**, and **Recovering** and presents one primary corrective action when setup is incomplete.
## Comfy VLM handler compatibility

When **ComfyUI LLM / VLM** is used for Caption Studio, Neo resolves the selected VLM to a compatible vision route. **Auto** prefers a known dedicated handler; when no dedicated handler fits and the updated Neo Comfy bridge supports Generic MTMD, the route can display **Auto → Generic MTMD**. This avoids treating an unrelated installed vision handler as compatible merely because it exists.

Generic MTMD still requires a matching `mmproj` and a model/chat-template combination supported by the llama.cpp build running inside ComfyUI. If no safe route is available, Caption Studio stays blocked and Admin shows the setup reason.

