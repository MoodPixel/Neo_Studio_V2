---
guide_id: video.output_inspector
title: Video Output Inspector
surface: video
scope: built_in
applies_to:
  - video_results
  - video_output_records
  - video_replay
  - video_lineage
  - video_extensions
  - video_finish
tags:
  - video
  - results
  - output inspector
  - replay
  - lineage
  - metadata
priority: 82
version: 3
updated: 2026-08-09
---

# Video Output Inspector

The Video Output Inspector is the Results-side view for understanding one saved Video output without reopening the live Generation parameter stack.

```txt
Results
  Generation History
  Video Output Inspector
```

The Inspector reads a normalized backend payload from:

```txt
GET /api/video/results/{result_id}/inspector
```

The current schema is `neo.video.output_inspector.v1`.

## What the Inspector shows

The normal Results view is organized around the selected output:

1. **Playback** — the active saved Video file or preview.
2. **Generation Setup** — provider/backend profile, family, loader, generation type, route, route status, recorded models, and timing.
3. **Prompt Recipe** — positive/negative prompt plus prompt-motion schedule or audio prompt metadata when recorded.
4. **Parameters** — high-signal settings first, with the complete saved parameter map available on demand.
5. **Sources** — source images, first/last frames, source video, MultiScene images, depth/motion/control inputs, audio, and result/file references when present.
6. **Executed Extensions** — extensions persisted on the selected result. Installed/currently-enabled extensions are never presented as though they participated in an older output.
7. **Lineage** — root generation through child continuation/finish outputs to the selected result.
8. **Replay** — validated recipe staging back into Generation. Replay never auto-runs.
9. **Expert Metadata** — normalized Inspector payload and raw saved Video record, shown only in Expert detail mode.

## Generation recipe vs selected finish output

Finish records such as interpolation, upscale, or repair are still inspected as the **selected output**, but they are not treated as generation families.

When a finish result has a valid ancestor chain, the Inspector finds the nearest real generation ancestor and uses that record as the replay recipe:

```txt
WAN/LTX generation
  -> Frame Interpolation
  -> SeedVR2 Upscale
  -> Repair
  -> selected output
```

The current finish result remains visible in playback, extension metadata, and lineage. Generation Setup / Prompt / Parameters / Sources use the ancestor recipe where required so replay does not pretend that `family=finish` is a runnable generation route.

If an ancestor is missing, the Inspector keeps the current result inspectable and reports the missing lineage reference rather than inventing one.

## Replay safety

**Load Recipe into Generation** stages the validated recipe only. It does not call Generate or Compile.

For local Video records, replay checks the saved route against the current canonical Video route matrix. Enabled and Experimental routes may be staged; Planned, unknown, or retired local routes are gated instead of silently rewritten to a different route.

For supported cloud Video records, the saved provider profile and provider-specific generation fields are restored where the current provider contract allows it.

Saved source references are checked best-effort. Missing source files are surfaced as warnings/gates according to the saved recipe rather than replaced with arbitrary current inputs.

After staging, the normal Video runtime still owns all execution checks:

```txt
staged recipe
  -> current provider/backend readiness
  -> current model/catalog availability
  -> current source requirements
  -> current surface-aware extension compatibility
  -> Generate
```

## Lineage rules

The output ledger stores child-to-parent/source references. The Inspector normalizes this into a human-readable root-to-selected chain.

Each node can expose:

- result id;
- category/operation;
- route/family/loader/generation type;
- created time;
- active output file;
- missing-record state.

Lineage must remain truthful. Do not synthesize missing ancestors or infer a finish operation as a generation route.

## Extension reporting

The Inspector reports `extensions.used` and persisted extension payload/settings from the saved result record only.

This is intentionally different from the live Extension workspace:

```txt
Live workspace       -> what is compatible/available now
Output Inspector     -> what this saved result says actually participated
```

For a finish child that replays through a generation ancestor, the selected child's extensions and ancestor generation extensions can be shown separately.

## Normal vs Expert detail

Normal/Guided detail should prioritize readable recipe and provenance information. Raw JSON is Expert-only.

Do not make users parse the original output-record schema merely to discover prompt, source, route, or lineage information. The backend Inspector adapter exists specifically to keep storage schema and UI view model separate.

## Regression locks

1. The Results **left rail** remains history + Inspector; it must not duplicate live Prompt/Parameters/Route Status inside Results. The persistent right generation rail remains visible beside it.
2. Selecting a history result refreshes the normalized Inspector for that result.
3. Playback represents the selected output, including finish children.
4. Finish children use a real generation ancestor for replay when one exists.
5. Unknown/retired local routes are gated, never silently rewritten.
6. Load Recipe stages only and never starts generation automatically.
7. Extension reporting is based on persisted result metadata, not current installation state.
8. Lineage is root-to-selected and surfaces missing references truthfully.
9. Expert raw metadata remains behind Expert detail mode.
10. The Inspector adapter remains separate from the persisted output-record schema so older/newer records can be normalized without forcing the UI to understand every historical shape.


## 2026-08-09 — Results parity with Image

- Video Results now mirrors the Image results flow more closely: **Results & Save Details → Saved Outputs → Output Inspector**.
- Output settings, replay-storage summary, and saved-output browsing now live above the Video Output Inspector.


## 2026-08-09 — Results storage presentation

The Results storage controls and Replay Storage Manager now use the same modern Video visual system as the Output Inspector and other current Video panels. The change is UI-only and does not alter the Video output settings or replay-storage APIs.

- Saved Outputs now uses the same modern Video card treatment as the updated Results storage controls, including styled filters and a polished empty state.
