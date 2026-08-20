# Phase IP-5 — Workflow-Aware Sampling Presets

Status: **Implemented**

IP-5 extends the IP-4 `Default · Balanced` txt2img family bases into workflow-aware `img2img`, `edit`, `inpaint`, and `outpaint` entries. It does this through inheritance rather than duplicating complete family presets.

## Resolution contract

Preset identity remains:

```text
preset_id + family + variant + loader + workflow + intent
```

Each non-txt2img Balanced entry that owns explicit sampling values declares:

```json
{
  "inherit": {"preset_id": "default_balanced", "mode": "txt2img"},
  "drop_fields": ["width", "height"],
  "values": {"denoise": 0.75}
}
```

The resolver materializes the txt2img family base first, removes inherited fields listed in `drop_fields`, then applies workflow-local values. Cycles or missing parents fail closed.

## Resolution ownership

IP-5 does not carry txt2img width/height into source workflows:

- `txt2img` → explicit family canvas from IP-4.
- `img2img` / `edit` / `inpaint` → source/auto resolution.
- `outpaint` → expanded canvas/auto resolution.

Applying a Balanced source-workflow preset clears stale managed `width`/`height` so the route's source/canvas contract remains authoritative.

## Balanced workflow strengths

| Family | Img2Img | Edit | Inpaint | Outpaint |
| --- | ---: | ---: | ---: | ---: |
| SD 1.5 | 0.65 | — | 0.72 | 1.0 |
| SDXL | 0.65 | — | 0.72 | 1.0 |
| FLUX.1 Dev Components | 0.65 | — | Fill semantics | Fill semantics |
| FLUX.1 Dev GGUF | 0.65 | — | 1.0 | 1.0 |
| FLUX.2 Klein Base / Distilled | 0.75 | 0.75 | 0.75 | 1.0 |
| Krea 2 RAW | 0.75 | — | 0.75 | 1.0 |
| Krea 2 Turbo | 0.75 | — | 0.75 | 1.0 |
| Qwen Image Edit | 0.85 | 0.85 | 0.85 | 1.0 |
| Qwen Image Edit 2509 | 0.85 | 0.85 | 0.85 | 1.0 |
| Qwen Rapid AIO | Provider profile | Provider profile | Provider profile | Provider profile |
| Z-Image Base | 0.75 | — | 0.75 | 1.0 |
| Z-Image Turbo | 0.75 | — | 0.75 | 1.0 |

The `1.0` outpaint values are Neo's initial Balanced full-synthesis starting point for newly expanded masked canvas areas. They are not presented as an official universal model recommendation and remain subject to live quality tuning.

## FLUX loader-aware split

### Components / Safetensors

`family=flux + variant=dev + loader=diffusion_model + inpaint/outpaint` resolves through Neo's internal FLUX.1 Fill route. Balanced therefore overrides the inherited normal Dev values with:

```text
Euler / Simple
50 steps
Sampler CFG 1
Fill Guidance 30
Denoise 1.0
```

This mirrors the FLUX.1 Fill reference recipe while keeping Fill hidden as an internal route rather than a visible normal family.

### GGUF

`family=flux + variant=dev + loader=gguf + inpaint/outpaint` does **not** borrow Fill Guidance 30. It inherits the normal FLUX.1 Dev Balanced base:

```text
Euler / Simple
20 steps
Sampler CFG 1
Flux Guidance 3.5
```

and only applies masked-route denoise 1.0.

## Klein isolation

Klein workflow entries inherit only from the matching explicit Base or Distilled txt2img row:

- Distilled → 4 steps / Guidance 1.
- Base → 50 steps / Guidance 4.

An unresolved Klein kind still has no `Default · Balanced` match. IP-5 does not introduce a generic Klein fallback.

## Qwen

Qwen workflow rows preserve the IP-4 True-CFG family bases:

- Qwen Image Edit → 50 steps / True CFG 4.
- Qwen Image Edit 2509 → 40 steps / True CFG 4 / model guidance 1.

The current compatibility `cfg` field remains alongside `true_cfg` until the provider compiler is migrated to a distinct True-CFG input contract.

## Provider Defaults and Clean Slate

IP-5 does not change the special presets:

- **Provider Defaults** continues to strip preset-managed values at submission and delegate to the provider/compiler.
- **Empty · Clean Slate** still clears sampling values once for authoring and never silently inherits Balanced or provider values.

Qwen Rapid AIO `Default · Balanced` remains provider-delegated for every supported workflow because a single universal numeric AIO recipe is not trustworthy.

## Boundary

IP-5 itself adds no preset UI, user preset CRUD, Quality preset, or Fast preset. IP-6 now provides Realistic / Anime as a separate metadata-only Output Intent layer; it does not alter these workflow sampling values.

## IP-7 UI + user authoring

IP-7 exposes the workflow-aware resolver through the Image Parameters preset UI and recalculates availability when family, variant/model, loader, or workflow changes. My Presets are route-scoped by the same family + variant + loader + workflow identity and never inherit Output Intent. The IP-5 inheritance/Fill/GGUF semantics are unchanged.
