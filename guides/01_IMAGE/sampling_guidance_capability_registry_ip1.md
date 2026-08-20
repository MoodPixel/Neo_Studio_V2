# Phase IP-1 — Sampling + Guidance Capability Registry

## Status
Implemented as a contract-only foundation. IP-1 does **not** add presets, mutate the Image UI, change provider compiler defaults, or decide negative-prompt eligibility for a numeric CFG value.

## Why this exists
Neo previously had sampling facts distributed across the family manifest and provider compilers. Those files mix route support, compiler defaults, and model-specific semantics. The preset work needs a stable answer to a different question:

> For this family + variant + loader + workflow, what do the controls *mean* and who owns them?

IP-1 establishes that answer without choosing "best" values.

## Authoritative files

- `neo_app/models/sampling_guidance_capabilities.json` — data contract.
- `neo_app/image/sampling_guidance_registry.py` — normalization and resolver.

Schema: `neo.image.sampling_guidance_capabilities.v1`

## Ownership boundary

The registry owns:
- sampler/scheduler/step control ownership (`selectable`, `fixed`, `provider_profile`, etc.),
- guidance kind and user-facing field meaning,
- negative-prompt policy metadata,
- resolution ownership per workflow,
- denoise ownership per workflow,
- family/variant/loader/workflow semantic overrides.

The registry does **not** own:
- route availability — still owned by the route matrix/provider compile router,
- recommended sampler/scheduler/steps/CFG/resolution — deferred to IP-4/IP-5 presets,
- runtime negative-prompt active/weak/inactive state — deferred to IP-2,
- model file compatibility or discovery.

## Guidance kinds

### `classic_cfg`
Used where Comfy's sampler CFG directly controls positive-vs-negative conditioning, including SD 1.5, SDXL, Krea 2 RAW, and Z-Image Base.

### `embedded_guidance`
Used where a model-specific guidance embedding is separate from sampler CFG, including FLUX.1 and FLUX.2 Klein. `flux_guidance` must never be treated as evidence that a negative prompt is active.

### `true_cfg`
Used by Qwen semantic contracts. Current Neo Comfy workflows store the effective value in KSampler `cfg`, but the registry labels the concept `True CFG` and records `true_cfg` as an alias so later UI/backend work can use the correct terminology without changing the current compiler in IP-1.

### `none`
Used for family routes where guidance/negative CFG is intentionally absent, such as Krea 2 Turbo and Z-Image Turbo.

### `provider_profile`
Used when Neo cannot safely generalize across model/profile variants, including Qwen Rapid AIO, HiDream, Wan and Hunyuan.

## Negative-prompt policies

IP-1 records policy only. **IP-2 now enforces it** through `neo_app/image/negative_prompt_eligibility.py`; this IP-1 guide remains the capability-history record.

### `cfg_gated`
Contract metadata:
- hard activation threshold: `> 1.0`,
- UX weak range: `> 1.0` and `< 1.5`,
- normal UX range: `>= 1.5`.

### `disabled_by_family`
The family/variant intentionally does not consume a user negative prompt in its current Neo route. Krea 2 Turbo and Z-Image Turbo use this state.

### `disabled_by_route`
The current Neo compiler route zeroes or ignores negative conditioning even if the architecture might support a different advanced workflow elsewhere. FLUX.1 and FLUX.2 Klein use this state in IP-1.

### `profile_controlled`
The selected provider/model profile must declare the behavior. Neo must not guess.

## Important family contracts

### SD 1.5 / SDXL
- classic CFG,
- negative prompt is CFG-gated,
- explicit txt2img canvas,
- source-owned image workflows.

### FLUX.1
- sampler CFG is neutral/fixed at 1 in current Neo semantics,
- `flux_guidance` is embedded model guidance,
- negative prompting is disabled by current route semantics,
- component Dev inpaint/outpaint resolves the semantic override `flux_fill_internal`, while GGUF remains the generic FLUX route.

### FLUX.1 Krea
Detected from explicit variant aliases or a Krea-marked model identity. It preserves the selected Krea model and records zero-negative / CFG-1 semantics.

### FLUX.2 Klein
- one Qwen3/Flux2 architecture contract,
- embedded `flux_guidance`,
- sampler CFG fixed at 1 for semantic purposes,
- negative prompt disabled by current Neo route,
- Base/Distilled and 4B/9B variant names normalize independently so later preset defaults can differ without changing guidance semantics.

### Krea 2
RAW and Turbo are separate family contracts:
- RAW: classic CFG + encoded negative,
- Turbo: no guidance + zeroed negative + family-owned step semantics.

### Qwen
Qwen Image and Qwen Image Edit 2509 are labeled with `true_cfg` semantics. 2509 also declares a secondary `model_guidance` capability as `declared_not_enforced_by_ip1`; IP-1 does not modify the compiler to add it.

Qwen Rapid AIO remains profile-controlled.

### Z-Image
- Base: classic CFG + encoded negative,
- Turbo: no CFG guidance + zeroed negative.

### HiDream / Wan / Hunyuan
These remain variant/provider/profile controlled. The registry intentionally refuses to borrow SDXL, FLUX, Qwen, or Z-Image assumptions.

## Resolution policy vocabulary

- `explicit_canvas` — txt2img owns explicit width/height.
- `source_or_auto` — img2img/edit/inpaint follows source or provider auto-size.
- `expanded_canvas_or_auto` — outpaint follows the expanded source canvas.
- `provider_profile` — provider/model profile owns size semantics.

No numeric native size is selected by IP-1.

## Denoise policy vocabulary

- `full_generation`
- `strength_controlled`
- `masked_strength_controlled`
- `provider_profile`

Numeric strengths are deferred to workflow presets.

## Fail-closed behavior

Unknown family, unknown profile, or provider-gated semantics do not fall back to SDXL. The resolver returns provider/profile ownership with warnings.

A loader/mode outside a family's declared semantic context is diagnostic only. IP-1 does not replace route validation or compilation.

## Downstream enforcement

IP-2 consumes this registry and implements the Negative Prompt Eligibility Engine with states `ACTIVE`, `WEAK`, `INACTIVE_CFG`, `DISABLED_FAMILY`, `DISABLED_ROUTE`, and `PROFILE_CONTROLLED`. The user's typed negative value is retained even while the effective provider negative is inactive. See `guides/01_IMAGE/negative_prompt_eligibility_ip2.md`.

## IP-3 preset-foundation note

IP-3 now owns preset resolution/application mechanics through `neo_app/image/sampling_presets.py`. IP-1 remains the semantic source used to derive Clean Slate requirements. Family numeric defaults are still not stored in this registry; those remain IP-4/IP-5 scope.
