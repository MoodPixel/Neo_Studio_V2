---

> **SD-28.8 documentation note:** This is a phase-history document. For the current released Scene Director architecture, support matrix, proof semantics, and apply order, use `guides/01_IMAGE/scene_director_current.md`. Historical gates/statuses below describe this phase at the time it shipped and must not override the current guide.

guide_id: image.scene_director.sd28_5
title: Scene Director — SD-28.5 FLUX.2 Klein Support
surface: image
scope: built_in
updated: 2026-07-27
---

# Scene Director — SD-28.5 FLUX.2 Klein Support

SD-28.5 promotes **FLUX.2 Klein** to Neo's active lightweight Scene Director contract for **Generate, Img2Img, and Inpaint** on both **Safetensors / Components (`diffusion_model`)** and **GGUF** routes. Outpaint remains planned-gated.

## Execution contract

```text
provider FLUX.2 Klein MODEL + Qwen3 CLIP + Flux2 latent
        │
        ├── regional CLIPTextEncode → FluxGuidance → ConditioningSetMask
        │
        └── optional NeoRegionalLoRADelta MODEL wrapper
                        │
                        ▼
                existing provider sampler
```

Scene Director adds no KSampler/KSamplerAdvanced, does not enter V054, and does not use masked LoRA finish/refinement passes.

## 4B / 9B boundary

Neo treats Klein 4B and 9B as different adapter architectures. Runtime model identity is fail-closed primarily by transformer depth:

| Scale | Double blocks | Single blocks | Transformer hidden reference |
|---|---:|---:|---:|
| Klein 4B | 5 | 20 | 3072 |
| Klein 9B | 8 | 24 | 4096 |

The hidden-width value is diagnostic, not the family discriminator. This matters because the Klein 4B transformer is 3072-wide while its Qwen3-4B text encoder is 2560-wide. A FLUX.2 model whose block depth does not match either Klein signature is not accepted by the regional-LoRA runtime. This prevents FLUX.2 dev/unknown variants from entering the Klein adapter accidentally.

## Base vs distilled

The official FLUX.2 family distinguishes step/guidance-distilled Klein models from undistilled **Base** models intended for fine-tuning/LoRA training. Neo therefore does **not** auto-claim Base↔Distilled LoRA interchangeability.

- same scale + same declared kind: structurally compatible, runtime layer proof still required;
- 4B LoRA → 9B model or 9B LoRA → 4B model: rejected before trigger injection/runtime wrapper;
- same-scale Base ↔ Distilled: accepted only as `runtime_preflight_required` when metadata explicitly crosses the variant kind;
- missing scale/family metadata: runtime-preflight candidate, never automatically green.

## Sampler / conditioning integrity

FLUX.2 Klein keeps Neo's provider-owned conditioning contract:

- positive conditioning passes through `FluxGuidance`;
- KSampler CFG remains `1.0`;
- negative conditioning remains `ConditioningZeroOut`;
- Scene Director does not rewrite the provider/user step count;
- one sampler must remain one sampler.

For distilled Klein, 4 steps are the reference production profile; Base is a high-step training/fine-tuning model. These are diagnostics, not Scene Director sampler overrides.

## Regional prompt path

Every regional positive prompt is encoded with the active Klein CLIP/Qwen3 encoder, passed through a matching `FluxGuidance`, then attached to its region mask using `ConditioningSetMask(set_cond_area = mask bounds)`. Regional negative prompts are not independently injected because the Klein provider route uses zeroed negative conditioning.

## Regional LoRA adapter

Klein uses:

```text
flux2_klein_activation_delta_v1
```

LoRA Stack owns file/row selection. Scene Director consumes only rows explicitly targeted to Scene Director regions and inserts at most one `NeoRegionalLoRADelta` MODEL wrapper regardless of regional LoRA count.

Regional CLIP LoRA mutation remains suppressed. Region-targeted LoRA execution is model-side only.

## Spatial module policy

FLUX.2 Klein uses Comfy's FLUX double-stream then single-stream transformer. Neo only hooks linear modules with a provable image-token lane:

| Module path | Regional scope |
|---|---|
| `img_in` | image only |
| `double_blocks.*.img_attn.qkv` | image only |
| `double_blocks.*.img_attn.proj` | image only |
| `double_blocks.*.img_mlp.*` linear modules | image only |
| `single_blocks.*.linear1` | combined text + image; text token mask forced to zero |
| `single_blocks.*.linear2` | combined text + image; text token mask forced to zero |
| `final_layer.linear` | image only |
| text branches / time/vector/guidance embedders / modulation/AdaLN | excluded |

Unknown token layouts fail closed to an all-zero regional mask.

## Flux `linear1_qkv` partial target

Comfy can map a Flux LoRA key to a tuple such as a `linear1` weight plus an output narrow/slice for the QKV portion. Neo preserves that output slice. The LoRA delta is calculated for the mapped slice, expanded into a zero tensor matching the full linear output, and only the mapped activation feature range is modified before spatial masking.

This prevents a QKV-only LoRA from being treated as if it targeted the full `linear1` QKV+MLP output.

## GGUF

GGUF uses the same activation-delta runtime. Neo never rewrites quantized model weights. Live linear dimensions prefer `in_features` / `out_features`, and the selected loader is included in the runtime node/proof.

## Runtime proof

A live route is not called GPU-proven until the regional node reports all required proof fields healthy, including:

```text
lora_loaded = true
model_family_match = true
region_mask_bound = true
masked_delta_hook_active = true
delta_eval_attempted = true
delta_nonzero = true
global_model_mutation = false
sampler_count = 1
forward_hooks_removed = true
spatial_scope_filter_active = true
loader_supported = true
token_mask_scope_proven = true
```

Static tests establish the graph/runtime contract, not final-image leakage. Same-seed GPU A/B testing remains the proof for real output isolation.

## Failure policy

Klein regional LoRA never falls back to global `LoraLoader`, `LoraLoaderModelOnly`, V054, or a masked refinement sampler. Invalid scale, wrong family, non-Klein Flux2 transformer signature, unsupported LoRA structure, unresolved layers, or missing runtime node gate the LoRA lane fail-closed.

## Still gated

- Scene Director Outpaint
- Z-Image / Z-Image Turbo regional LoRA
- regional CLIP LoRA hard isolation
- unsupported/non-standard LoRA formats that do not resolve through the standard A/B activation-delta contract

## Superseded Z-Image status — SD-28.6

The SD-28.5 statement that Z-Image / Z-Image Turbo regional LoRA is gated is historical. SD-28.6 promotes both variants for native/components and GGUF Generate/Img2Img/Inpaint routes through `z_image_activation_delta_v1`, with an exact Z-Image NextDiT signature gate, ModelSamplingAuraFlow preservation, token-padding-safe regional masks, conservative Base↔Turbo LoRA preflight, and per-run runtime proof. Outpaint remains planned-gated.
