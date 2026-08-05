# Phase IP-4 — Family Default Presets

## Status
Implemented. IP-4 owns the immutable `Default · Balanced` txt2img family bases. **IP-5 now inherits from these rows for img2img/edit/inpaint/outpaint instead of duplicating complete family presets.** Quality/Fast tiers and preset UI authoring remain later phases. IP-6 now provides Realistic / Anime as a separate metadata-only Output Intent layer without changing these family defaults.

## Authority

- `neo_app/models/sampling_presets_builtin.json` — immutable built-in rows.
- `neo_app/image/sampling_presets.py` — route resolver/application/validation.
- IP-1 remains capability semantics authority.
- IP-2 remains negative-prompt eligibility authority.
- IP-3 remains Provider Defaults / Empty Clean Slate foundation authority.

Schema remains `neo.image.sampling_presets.builtin.v1`. IP-4 introduced registry version `1.1.0`; IP-5 advanced the same registry to phase `IP-5`, version `1.2.0`, and IP-6 now advances it to `1.3.0` only to lock the intent dimension to metadata-only behavior while preserving these txt2img base values.

## Default · Balanced txt2img matrix

| Family | Sampler | Scheduler | Size | Steps | Sampler CFG / True CFG | Embedded / model guidance |
|---|---|---|---:|---:|---:|---:|
| SD 1.5 | `dpmpp_2m_sde_heun_gpu` | `karras` | 512×512 | 30 | CFG 4 | — |
| SDXL | `dpmpp_2m_sde_heun_gpu` | `karras` | 1024×1024 | 40 | CFG 4 | — |
| FLUX.1 Dev | `euler` | `simple` | 1024×1024 | 20 | sampler CFG 1 | Flux Guidance 3.5 |
| FLUX.2 Klein Distilled 4B/9B | `euler` | `simple` | 1024×1024 | 4 | sampler CFG 1 | Flux Guidance 1 |
| FLUX.2 Klein Base 4B/9B | `euler` | `simple` | 1024×1024 | 50 | sampler CFG 1 | Flux Guidance 4 |
| Krea 2 RAW | `euler` | `simple` | 1024×1024 | 52 | CFG 3.5 | — |
| Krea 2 Turbo | `euler` | `simple` | 1024×1024 | 8 | Comfy CFG 1 | — |
| Qwen Image / Edit no-source | `euler` | `simple` | 1328×1328 | 50 | True CFG 4 | — |
| Qwen Image Edit 2509 no-source | `euler` | `simple` | 1328×1328 | 40 | True CFG 4 | model guidance 1 |
| Qwen Rapid AIO | Provider profile | Provider profile | Provider | Provider | Provider | Provider |
| Z-Image Base | `euler` | `simple` | 1024×1024 | 35 | CFG 3.5 | — |
| Z-Image Turbo | `euler` | `simple` | 1024×1024 | 9 | Comfy CFG 1 | — |

Qwen presets write both `true_cfg` and compatibility `cfg` because current Comfy compilers still consume `cfg`, while IP-1/IP-2 treat the semantic control as True CFG.

## Safety boundaries

### Klein Base vs Distilled
There is no generic FLUX.2 Klein `Default · Balanced` fallback. The preset resolves only when the route context identifies a registered Base or Distilled variant. This prevents few-step distilled values from leaking into Base models.

### FLUX.1 Krea
The FLUX.1 Dev row matches only `variant=dev`. `krea_dev` does not inherit the normal FLUX.1 Dev Balanced recipe. Krea 2 RAW/Turbo are separate visible families and have their own rows.

### Rapid AIO
`Default · Balanced` is available as a logical built-in, but uses `delegate_provider` with `{}` values. AIO checkpoint/profile sampling remains provider-owned instead of pretending one numeric recipe fits every bundle.

### Unreviewed families
HiDream, Wan and Hunyuan receive no fake Balanced rows in IP-4.

## Workflow boundary

IP-4 itself is the txt2img family-base phase. IP-5 now adds source/canvas-aware img2img/edit/inpaint/outpaint overrides that inherit these bases, drop inherited width/height, and apply route-specific denoise or FLUX Fill semantics.

## Negative prompt interaction

Preset application still runs before IP-2 eligibility:

1. resolve/apply IP-4 family base or IP-5 inherited workflow sampling preset,
2. evaluate IP-2 effective negative prompt,
3. provider validate/compile.

Therefore Qwen Balanced True CFG 4 activates its negative lane, while FLUX/Klein/Krea Turbo/Z Turbo remain governed by their family/route negative policy rather than by a numeric value alone.

## Deferred

- Default · Quality / Fast,
- img2img/inpaint/outpaint/edit inheritance and denoise values,
- FLUX Fill vs GGUF workflow overrides,
- intent-specific sampling changes for Realistic / Anime (IP-6 currently records intent metadata only),
- preset dropdown and user CRUD.

## IP-7 UI status

IP-7 now exposes the IP-4 family `Default · Balanced` rows in the Image Parameters preset selector when the active family / variant / loader / workflow can resolve them. Built-in family values remain read-only; Duplicate creates a separate My Preset instead of editing the built-in registry.

## IP-8 final release lock

This phase remains the authority for the behavior documented above. The complete sampling-preset program is finally release-locked by **IP-8** through `neo_app.image.sampling_preset_release_lock` and observed by `neo_app.image.sampling_preset_inspector`. IP-8 does not replace this phase's ownership or fabricate GPU/visual proof.
