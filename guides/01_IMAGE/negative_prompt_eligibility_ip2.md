# Phase IP-2 — Negative Prompt Eligibility Engine

## Status
Implemented. IP-2 converts IP-1 negative-prompt capability metadata into runtime states and an effective provider payload. It does **not** add sampling presets or alter family sampler/step defaults.

## Authoritative files

- `neo_app/image/negative_prompt_eligibility.py` — backend resolver and payload preparation.
- `neo_app/providers/schema.py` — Image `NeoJob` boundary that applies the resolver before providers receive a job.
- `neo_app/static/js/negative_prompt_eligibility.js` — browser UX mirror; backend remains authoritative.
- `neo_app/models/sampling_guidance_capabilities.json` — IP-1 semantic source.

Schema: `neo.image.negative_prompt_eligibility.v1`

## Runtime states

### `ACTIVE`
The route supports negative conditioning and the submitted CFG/True-CFG value is in the normal active range. A missing activation value also remains ACTIVE because IP-2 must not invent a preset value.

### `WEAK`
For `cfg_gated` routes, guidance is greater than `1.0` but lower than `1.5`. The negative prompt is still sent, but the UI warns that influence may be weak.

### `INACTIVE_CFG`
For `cfg_gated` routes, effective CFG/True CFG is `<= 1.0`. The user text is retained, while the provider-facing negative becomes empty.

### `DISABLED_FAMILY`
The current family contract explicitly does not execute a negative branch. Current examples: Krea 2 Turbo and Z-Image Turbo.

### `DISABLED_ROUTE`
The selected Neo route deliberately zeroes/ignores negative conditioning. Current examples: FLUX.1 and FLUX.2 Klein routes. `flux_guidance` never activates negative prompting.

### `PROFILE_CONTROLLED`
Neo does not guess. Qwen Rapid AIO, HiDream, Wan, Hunyuan, unknown families, and future provider-owned profiles retain the user negative value for the provider/profile to interpret.

## Threshold rule

For IP-1 `cfg_gated` routes:

- `CFG / True CFG <= 1.0` → `INACTIVE_CFG`
- `1.0 < CFG / True CFG < 1.5` → `WEAK`
- `CFG / True CFG >= 1.5` → `ACTIVE`

`1.5` is the UX weak-range boundary, **not** the mathematical activation cutoff.

## Qwen True-CFG rule

Qwen semantic aliases are authoritative when explicitly submitted. Therefore:

- `true_cfg=4`, stale `cfg=1` → ACTIVE
- `true_cfg=1`, stale `cfg=4` → INACTIVE_CFG

Current Comfy compatibility may still store True CFG in the `cfg` field when no explicit `true_cfg` value exists.

## FLUX rule

`flux_guidance` is embedded/model guidance. It is not True CFG. A value such as `flux_guidance=3.5` cannot enable the negative field or cause the backend to send the user negative prompt on routes that IP-1 marks `disabled_by_route`.

## User-value retention

IP-2 separates authoring state from execution state:

- `params.negative_prompt_input` — untouched user text,
- `params.effective_negative_prompt` — value providers may execute,
- `params.negative_prompt_eligibility` — full state/proof object,
- `params.negative_prompt_suppressed` — whether non-empty user text was intentionally suppressed.

The top-level `NeoJob.negative_prompt` becomes the effective provider value. This prevents stale clients and replay payloads from reactivating a disabled negative lane while preserving the text for later route/CFG changes.

## Browser behavior

The Image helper observes route/guidance changes and:

- disables but does not clear the field for `INACTIVE_CFG`, `DISABLED_FAMILY`, and `DISABLED_ROUTE`,
- keeps the field enabled with a warning for `WEAK`,
- keeps profile-controlled routes editable with an informational warning,
- only re-enables a field if IP-2 itself disabled it, so unrelated UI locks are not overridden,
- exposes `window.NeoNegativePromptEligibility.evaluate()` and `.preparePayload()` for core Image migration work.

The browser implementation is a UX mirror. Server-side `NeoJob` preparation is the execution authority.

## Scope boundary

IP-2 does not:

- choose sampler/scheduler/steps/CFG defaults,
- decide Realistic/Anime creative intent (IP-6 now records this separately without changing negative eligibility),
- enable advanced True-CFG FLUX workflows,
- change Krea/Z Turbo model semantics,
- guess provider-profile negative behavior.

Those boundaries remain for later preset/profile phases.

## IP-3 job-boundary ordering

IP-3 sampling-preset preparation now runs immediately before IP-2 inside the Image `NeoJob` boundary. This is intentional: Provider Defaults removes stale guidance values before IP-2 evaluates them, while manual values entered after Empty · Clean Slate remain available to IP-2. IP-2 remains the sole authority for negative-prompt ACTIVE/WEAK/disabled execution state.

## IR-4 live UI recovery

The IP-2 backend contract remains unchanged. IR-4 reconnects its browser mirror to the real Image DOM, adds Qwen True-CFG labeling through the existing CFG control, and locks non-destructive negative-prompt greying. See `cfg_negative_prompt_live_ux_ir4.md` / `CFG_NEGATIVE_PROMPT_LIVE_UX_IR4.md`.
