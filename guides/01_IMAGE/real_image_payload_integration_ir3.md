# IR-3 — Real Image Payload Integration

## Status
Implemented against the uploaded Neo Studio V2 build used for live UI testing, layered after IR-1 and IR-2.

## Purpose
IR-3 joins the unified Image Preset selector to the real generation boundary. IR-1 created one visible selector and IR-2 established browser-side authority handoff; IR-3 makes that authority survive `Generate -> /api/image/generate -> NeoJob -> provider` without a second interpretation layer.

## Submission contract
`buildImageJobPayload()` now writes the active preset authority into `job.params`:

- built-in selections submit `sampling_preset_id`;
- Manual / No Preset and User Presets omit `sampling_preset_id`;
- `output_intent` is submitted separately and remains `none` by default in IR-3;
- `_neo_sampling_preset_submission` records privacy-safe browser provenance for diagnostics.

The selector remains the single visible preset control. IR-3 adds no Output Intent control and no additional Sampling Preset control.

## Authority mapping

### Manual / No Preset
- no `sampling_preset_id` is submitted;
- visible manual sampling values are submitted normally;
- IP-8 release lock reports `not_applicable` rather than pretending a preset is active.

### User Preset
- no built-in `sampling_preset_id` is submitted;
- the captured workspace values are submitted as manual values;
- browser provenance records `selector_source=user` only for diagnostics.

### Provider Defaults
- submits `sampling_preset_id=provider_defaults`;
- the authoritative NeoJob preset resolver removes preset-managed sampling fields before provider validation/compile;
- stale browser sampler/steps/CFG/resolution values therefore cannot survive as provider overrides.

### Default · Balanced
- submits `sampling_preset_id=default_balanced`;
- NeoJob re-resolves the current family + variant + loader + workflow instead of trusting only browser-applied numbers;
- the final Inspector and release lock report the resolved backend contract.

### Empty · Clean Slate
- submits `sampling_preset_id=empty_clean_slate`;
- browser fallback values historically created by `buildImageJobPayload()` are stripped unless the user actually authored that managed field in `imageDraft`;
- incomplete Clean Slate therefore remains incomplete and is blocked by existing provider validation rather than accidentally passing with fake 1024/28/etc. defaults.

## One prepared NeoJob
`/api/image/generate` now constructs one `NeoJob` and reuses it for:

1. provider execution;
2. job registry payload;
3. saved Image job context;
4. runtime sampling Inspector/release-lock proof.

This removes the old split where provider execution received the prepared preset payload but registry/context bookkeeping could retain the earlier browser payload.

## Runtime proof
The generation response exposes privacy-safe authoritative metadata under `runtime` when available:

- `sampling_preset_inspector`;
- `sampling_preset_release_lock`;
- `sampling_preset_submission`.

The Inspector remains contract proof only. It does not claim GPU visual-quality proof.

## Output Intent
IR-3 carries `output_intent` through the real payload but does not add another UI control. Current Image authoring defaults it to `none`. Realistic / Anime intent selection remains deferred until a compact UI placement is deliberately designed.

## Next phase
IR-4 repairs the live CFG and Negative Prompt UI semantics using the actual Image DOM ids from the uploaded source-of-truth build.
