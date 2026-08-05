---
guide_id: image.forge_neo_image_job_lifecycle
title: Forge Neo Image Job Lifecycle
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - runtime
  - admin
tags:
  - forge
  - forge-neo
  - jobs
  - progress
  - recovery
  - outputs
priority: 94
version: 3
updated: 2026-07-31
---

# Forge Neo Image Job Lifecycle

Neo wraps Forge Neo's synchronous A1111-compatible generation endpoints in a Neo-owned durable job lifecycle. This keeps the Image request handler responsive and gives Forge jobs the same visible queue, progress, preview, cancellation, output, and recovery contract used by the rest of Neo Studio.

## Supported executable routes

The lifecycle accepts every route that survives the selected profile's executable Forge policy. Current compiler ownership includes:

- SD 1.5/SDXL checkpoint txt2img, img2img, inpaint, and outpaint;
- Flux 1 and Flux.2 Klein txt2img plus experimental img2img;
- Krea 2 RAW/Turbo, Qwen Image, and Z-Image/Turbo txt2img;
- Qwen Image Edit 2509 img2img/edit with one main source; optional verified ImageStitch references.

Modern inpaint/outpaint, generic/unverified multi-source Qwen edit, Qwen Rapid AIO, Wan Image-surface routes, Hunyuan Image, HiDream, unknown families, and unsupported packaging combinations remain gated. The canonical matrix is in `forge_neo_complete_support.md`.

## Runtime flow

```text
Neo Image submission
  -> ForgeNeoProvider validation and compile
  -> durable request/state record
  -> one Forge worker for the profile
  -> synchronous /sdapi/v1/txt2img or /sdapi/v1/img2img
  -> /sdapi/v1/progress polling and preview translation
  -> Forge response image spool
  -> normal Neo_Data output import
  -> completed Image result
```

The worker sends `force_task_id=<neo_job_id>` so progress and cancellation checks can correlate the active Forge task with the Neo job.

## Durable runtime storage

Forge lifecycle records are runtime-only:

```text
neo_data/runtime/forge_neo/<profile_id>/jobs/<job_id>/
  request.json
  state.json
  outputs/
```

These paths are excluded from public releases. They may contain queued request data and temporary provider-owned output files, so they must never be committed or included in patch archives.

## Queue and concurrency policy

Forge's standard generation endpoints block until generation finishes and Forge serializes processing internally. Neo therefore uses one local worker per saved Forge profile.

- Additional jobs stay `queued`.
- Only the worker submits a blocking Forge request.
- The HTTP endpoint that accepted the Neo job returns immediately.
- The profile may configure `generation_timeout_seconds`; the shipped default is `3600` seconds.
- Pause and resume are unsupported because Forge does not expose a safe standard API contract for them.

## Progress and live preview

Neo polls:

```text
GET /sdapi/v1/progress
```

The response is translated into Neo runtime progress:

- percentage;
- ETA;
- status text;
- current Forge task ID;
- sanitized Forge state.

The Image preview endpoint requests `current_image` only when the UI asks for a preview. Preview base64 is returned to the browser but is not written into durable job-state JSON.

## Cancellation

For queued jobs, Neo cancels locally before submission.

For running jobs, Neo first checks Forge's `current_task`:

- matching task ID: Neo sends `POST /sdapi/v1/interrupt`;
- different task ID: Neo does not interrupt another user's or profile's task and records a warning;
- unavailable task metadata: Neo attempts the standard interrupt endpoint and records transport warnings if it fails.

A cancelled job never publishes returned images into Neo outputs.

## Output handoff

Forge returns images as base64 strings in the synchronous response. Neo:

1. validates and decodes each image;
2. detects PNG, JPEG, WebP, or BMP where possible;
3. writes a temporary provider-owned file under the Forge runtime job directory;
4. returns a normal provider output descriptor;
5. imports it through Neo's existing Image output persistence path into `Neo_Data`.

The durable state stores sanitized Forge `info` and request parameter metadata. Source images, masks, returned base64 data, credentials, and authorization headers are not copied into diagnostics.

## Restart and recovery behavior

### Queued job

A queued job remains on disk. When Neo starts and creates the profile manager, the worker resumes the queue.

### Completed response already spooled

The completed state and provider-owned output files remain available for Neo output-import recovery.

### Neo restarts during a blocking Forge request

The standard Forge API cannot replay the lost HTTP response. Neo therefore does not claim success and does not automatically duplicate the generation.

Neo marks the job:

```text
failed + recoverable
```

The recovery action then:

1. checks Forge progress;
2. waits if the original task ID is still active;
3. refuses to requeue while another task is active;
4. explicitly requeues the stored request only after Forge reports idle.

This can generate a second image if Forge finished the orphaned request after Neo lost the response. Recovery is therefore explicit rather than automatic.

## Admin and route readiness

Forge Admin exposes `neo_execution_adapter=true` only when the profile can reach the API and the generation, progress, and interrupt endpoint contract is present. That flag does not bypass the Image route matrix or extension gates.

A Forge job runs only when:

- the profile is enabled and valid;
- Forge was launched with `--api`;
- the execution lifecycle capability probe passes;
- the selected family/loader/mode route is live executable (`available` or `experimental_available`);
- required models, modules, settings, scripts, and sources are present;
- all enabled extensions have a supported Forge compiler mapping.

## Current limits

- No generic family activation merely because Forge lists a model.
- Modern-family inpaint/outpaint remains gated except SD 1.5/SDXL Neo-owned outpaint.
- Generic multi-source Qwen edit remains gated. E1 can transport extra Qwen Edit / Flux.2 Klein references only through the verified `ImageStitch Integrated` three-argument script contract.
- Extension execution is limited to provider-owned mappings validated for the selected route.
- No pause/resume.
- No automatic recovery of a synchronous standard-SDAPI response lost during Neo restart.
- Neo does not install, launch, update, or configure Forge command-line packages.

## Image interface capability overlay

Phase 4 adds a frontend overlay above this lifecycle. It uses the same profile and Admin discovery snapshot to populate Image controls, enforce resolution alignment, and gate unsupported extensions. Job submission and recovery semantics in this guide are unchanged.

## Phase 5 extension payloads

The durable Forge lifecycle submits the compiled Phase 5 payload unchanged, including native hires fields and verified `alwayson_scripts`. Queue, progress, cancellation, spooling, and recovery rules remain provider-level behavior and do not bypass extension validation. A recovered or requeued job is recompiled against its current selected-profile extension capability snapshot.

## Phase 6 Bridge lifecycle

The optional Bridge adds a Forge-owned durable job mirror. Neo still keeps a local record for UI continuity and output import, but progress, cancellation, history, and completed result recovery come from `/neo-api/v1/jobs/{job_id}` when the Bridge is selected.

The previous synchronous-response limitation still applies to **standard SDAPI mode**. In Bridge mode, a Neo restart can reattach to a Forge-owned queued/running job or fetch completed outputs. A Forge restart still terminates the underlying generation request and the Bridge marks the job recoverable rather than falsely completed.


## Phase 6.2 Forge live-preview browser polling

Forge standard SDAPI and the optional Bridge expose preview frames through Neo's provider-neutral HTTP preview route. Phase 6.2 wires the Image browser poller to that route for Forge jobs that do not have a ComfyUI WebSocket `client_id`.

The browser polls `/api/image/jobs/{profile_id}/{job_id}/preview` while the Forge job is queued or running, ignores duplicate frames, converts returned data URLs to temporary object URLs, and replaces the preview with the final persisted output when the job completes. ComfyUI WebSocket preview behavior is unchanged.

Forge must still emit `current_image` frames. In Forge Settings, live previews must be enabled and `show_progress_every_n_steps` must be greater than zero. No frame is expected while a checkpoint or VAE is still loading; preview normally begins after sampling starts. If Forge has not emitted a frame yet, generation and progress continue normally without treating preview absence as a failure.

A warning-state connection remains eligible for this lifecycle when the core API contract is healthy. Soft-optional Admin diagnostics, including `cmd_flags`, are not lifecycle blockers.

## Provider-aware native post-Hires lifecycle — 2026-08-02

Native selected-image Hires uses the same durable Forge Bridge queue, progress, preview, cancellation, result spool, and restart-recovery lifecycle as Bridge SDAPI jobs. Its request record stores `operation: native_txt2img_upscale` instead of an SDAPI endpoint. Neo's local mirror records the operation in the route snapshot and appends the returned output as a derived child of the selected source output.

The standard Forge job manager rejects native operations. This prevents an accidental attempt to send `firstpass_image` through `/sdapi/v1/txt2img`, whose public request model does not perform the native selected-image conversion used by Forge's UI handler.
