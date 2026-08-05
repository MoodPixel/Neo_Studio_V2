# Phase IP-3 — Built-In Sampling Preset Foundation

## Status
Implemented as the preset contract/runtime foundation. **IP-4 added `Default · Balanced` txt2img family rows and IP-5 now adds inherited workflow-aware overrides; this document remains authoritative for Provider Defaults / Clean Slate semantics.** IP-3 adds immutable built-in preset resolution, Provider Defaults, Empty · Clean Slate, context-aware matching, and submission validation. It **does not** add family quality/default numeric values or preset UI authoring.

## Authoritative files

- `neo_app/models/sampling_presets_builtin.json` — immutable built-in preset definitions.
- `neo_app/image/sampling_presets.py` — resolver, application semantics, Clean Slate validation, and job-boundary preparation.
- `neo_app/providers/schema.py` — applies sampling-preset preparation before IP-2 negative-prompt preparation.
- `neo_app/providers/base.py` — provider-neutral generation blocker for incomplete Clean Slate submissions.

Schema: `neo.image.sampling_presets.builtin.v1`

## Resolution key

Preset resolution is designed for the later family matrices and uses:

`preset_id + family + variant + loader + mode + intent`

Entries may use `*` selectors. The resolver scores more-specific matches above wildcard matches and fails closed if equally specific entries collide. This lets IP-4/IP-5 add one logical preset such as `default_balanced` with separate SDXL, FLUX, Klein, Krea, Qwen and Z route rows without duplicating resolver logic.

## Built-ins in IP-3

### Provider Defaults

- source: built-in,
- immutable/read-only,
- application mode: `delegate_provider`,
- values: `{}`.

Selecting/applying Provider Defaults removes every preset-managed sampling value. The Image `NeoJob` boundary repeats that cleanup so stale clients cannot send old steps/CFG/size values while claiming the provider-default preset is active.

Provider Defaults is the **only** IP-3 preset intentionally allowed to delegate missing sampling values to the selected provider/compiler.

### Empty · Clean Slate

- source: built-in,
- immutable/read-only,
- application mode: `clean_slate`,
- authoring template: true,
- values: `{}`.

Applying Clean Slate clears preset-managed sampling fields **once**. Subsequent manual values are preserved at job submission. IP-3 validates those manual values and blocks generation while required route controls remain missing.

This prevents both failure modes:

1. hidden provider/default contamination after choosing Empty;
2. erasing values the user typed after choosing Empty.

## Managed fields

IP-3 currently treats these as preset-owned sampling values:

- sampler / scheduler,
- width / height,
- steps,
- CFG / True CFG compatibility fields,
- FLUX/model guidance fields,
- denoise,
- seed/requested seed/actual seed.

Prompt text, negative prompt text, styles, LoRAs, extensions, ControlNet data, and other non-sampling state are not managed by this preset layer.

## Clean Slate completeness

Required manual fields are derived from IP-1 capability semantics rather than a global hardcoded checklist.

Examples:

- SDXL txt2img requires selectable sampler, scheduler, steps, CFG, width and height.
- SDXL img2img follows source resolution and requires denoise instead of width/height.
- FLUX requires FLUX Guidance but not its fixed sampler CFG.
- Krea 2 Turbo does not require family-forced steps or fixed Comfy CFG.
- Qwen accepts explicit `true_cfg` or the current compatibility `cfg` field.
- provider/profile-owned families fail closed under Clean Slate because Neo cannot know their complete manual sampling contract yet.

Incomplete state returns a validation message beginning with `Sampling settings are incomplete:` and providers block the run instead of silently restoring compiler defaults.

## NeoJob ordering

Image job preparation order is now:

1. IP-3 sampling preset preparation,
2. IP-2 negative-prompt eligibility,
3. provider validation/compile.

That ordering matters. Provider Defaults can remove stale CFG before IP-2 evaluates negative prompting, while Clean Slate manual CFG/True CFG remains available to IP-2.

## User presets

The future user namespace is reserved as:

`neo_data/image/sampling_presets`

IP-3 does not create or mutate user preset files. CRUD, duplication, rename/delete, and default selection remain IP-7 scope. Built-in files stay repository-packaged and immutable.

## Deferred work

IP-3 intentionally does not add:

- Default · Balanced numeric values,
- Quality/Fast numeric values,
- family native resolutions,
- workflow denoise defaults,
- Realistic / Anime intent overlays (implemented separately by IP-6 as metadata-only Output Intent),
- preset dropdown UI,
- user-preset CRUD.

Default · Balanced family txt2img values are implemented by IP-4, workflow-aware inheritance/denoise overrides by IP-5, and metadata-only Realistic / Anime Output Intent by IP-6. Quality/Fast variants and preset UI/user CRUD remain later phases.

## IP-7 authoring status

IP-7 activates the visible Sampling Preset UI and portable user authoring. The IP-3 special presets remain unchanged: `Provider Defaults` delegates provider ownership and `Empty · Clean Slate` remains a no-fallback authoring template. Repository built-ins stay immutable; user records live separately under `neo_data/image/sampling_presets`.

## IP-8 final release lock

This phase remains the authority for the behavior documented above. The complete sampling-preset program is finally release-locked by **IP-8** through `neo_app.image.sampling_preset_release_lock` and observed by `neo_app.image.sampling_preset_inspector`. IP-8 does not replace this phase's ownership or fabricate GPU/visual proof.
