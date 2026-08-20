# Phase IP-6 — Realistic / Anime Output Intent Layers

Status: **Implemented**

IP-6 introduces a separate Image **Output Intent** layer with three immutable built-ins:

- `none` — neutral,
- `realistic` — Realistic / photographic intent,
- `anime_illustration` — Anime / Illustration intent.

The intent layer is deliberately separate from Sampling Presets. In IP-6 it is **metadata-only**: selecting Realistic or Anime / Illustration never injects prompt text, negative text, styles, LoRAs, embeddings, extensions, or sampling overrides.

## Runtime order

Image `NeoJob` preparation is now:

```text
IP-6 Output Intent normalization
        ↓
IP-3/IP-4/IP-5 Sampling Preset resolution/application
        ↓
IP-2 Negative Prompt Eligibility
        ↓
Provider validation / compile
```

Intent normalization runs first because the preset identity already contains the intent dimension:

```text
preset_id + family + variant + loader + mode + intent
```

IP-6 canonicalizes aliases before sampling resolution, but all current sampling rows still match `intent=*`. Therefore the selected intent does not alter any sampling value.

## Canonical intents and aliases

### None

Canonical id: `none`

Accepted aliases include `neutral`, `off`, `disabled`, and `default`.

### Realistic

Canonical id: `realistic`

Accepted aliases include `photo`, `photographic`, `photoreal`, `photorealistic`, and `realism`.

### Anime / Illustration

Canonical id: `anime_illustration`

Accepted aliases include `anime`, `illustration`, `illustrated`, and `anime_or_illustration`.

Aliases are authoring conveniences only. Runtime metadata always records the canonical id.

## Mutation boundary

Every IP-6 built-in intent declares the following effect structure and every field must remain empty:

```json
{
  "sampling_overrides": {},
  "prompt_additions": [],
  "negative_prompt_additions": [],
  "style_ids": [],
  "lora_ids": [],
  "embedding_ids": [],
  "extension_overrides": {}
}
```

Registry validation fails closed if any of those effects become non-empty.

This means Output Intent currently answers only:

> “What visual direction did the user select?”

It does **not** answer:

> “What prompt tokens or sampler values should Neo secretly add?”

## Sampling preset lock

`sampling_presets_builtin.json` remains the sampling authority. IP-6 adds the contract state:

```text
intent_specific_sampling_status = disabled_ip6
```

While that lock is active, any sampling preset entry with a non-wildcard `match.intent` is rejected by registry validation. Realistic and Anime therefore resolve the same `Default · Balanced` values as None for the same family/variant/loader/workflow.

Examples:

```text
SDXL + Balanced + None
SDXL + Balanced + Realistic
SDXL + Balanced + Anime / Illustration
```

all resolve the same sampling recipe.

The same rule applies to workflow overrides such as FLUX Fill, Klein Base/Distilled, Krea, Qwen, and Z-Image.

## Unknown intents

Unknown intent ids fail closed to `none`:

- generation is not blocked,
- no creative/sampling mutation is applied,
- a warning is recorded in `output_intent_resolution`,
- sampling resolution proceeds with canonical intent `none`.

This prevents stale future/user intent names from changing generation behavior unexpectedly.

## Runtime metadata

Image params now carry:

```text
output_intent
output_intent_resolution
```

`output_intent_resolution` records:

- requested intent,
- effective canonical intent,
- state (`neutral`, `advisory_only`, or `unknown_neutralized`),
- route context,
- zero-effect payload,
- warnings,
- `mutated_fields=[]`.

## Boundary

IP-6 does **not** add:

- prompt rewriting,
- automatic negative prompts,
- style selection,
- LoRA or embedding selection,
- intent-specific sampler/step/CFG/denoise values,
- Realistic/Anime model switching,
- the visible preset/intent UI.

A future phase may introduce tested intent-specific sampling differences, but that requires an explicit contract change and regression coverage. The visible selector and user preset authoring remain IP-7 scope.

## IP-7 selector status

IP-7 activates the visible Output Intent selector beside Sampling Preset. The semantic phase remains IP-6: None / Realistic / Anime-Illustration are still metadata-only, never captured into user sampling presets, and cannot mutate prompt/style/LoRA/extension or sampling values.
