---

> **SD-28.8 documentation note:** This is a phase-history document. For the current released Scene Director architecture, support matrix, proof semantics, and apply order, use `guides/01_IMAGE/scene_director_current.md`. Historical gates/statuses below describe this phase at the time it shipped and must not override the current guide.

guide_id: image.scene_director.sd28_6
title: Scene Director — SD-28.6 Z-Image + Z-Image Turbo Support
surface: image
scope: built_in
updated: 2026-07-27
---

# Scene Director — SD-28.6 Z-Image + Z-Image Turbo Support

SD-28.6 promotes **Z-Image Base** and **Z-Image Turbo** to Neo's active `lightweight_regional` Scene Director contract for **Generate, Img2Img, and Inpaint** on both **Safetensors / Components (`diffusion_model`)** and **GGUF** routes. Outpaint remains planned-gated.

## Execution boundary

```text
provider Z-Image MODEL
        │
        ├── regional CLIPTextEncode → ConditioningSetMask
        │
        └── optional NeoRegionalLoRADelta MODEL wrapper
                         │
                         ▼
               ModelSamplingAuraFlow
                         │
               DifferentialDiffusion   (Inpaint when present)
                         │
                         ▼
                 existing KSampler
```

Scene Director must not add another KSampler/KSamplerAdvanced, enter V054, mutate global model weights/CLIP, or use a masked regional-LoRA finish pass.

## Z-Image architecture lock

Z-Image Base and Turbo share the same 6B transformer architecture. Neo proves the runtime is the Z-Image `Lumina2/NextDiT` route before regional LoRA execution by checking:

- `dim = 3840`
- `in_channels = 16`
- `n_heads = 30`
- 30 main `layers`
- 2 `noise_refiner` blocks
- 2 `context_refiner` blocks
- `patch_size = 2`

A generic Lumina2 model that does not match this signature is blocked rather than treated as Z-Image.

The transformer signature cannot distinguish Base weights from Turbo weights because both variants share the architecture. The selected Neo route owns the Base/Turbo label; runtime proof records that this variant identity is route-declared rather than inferred from tensor shape.

## Base ↔ Turbo LoRA policy

Neo does not claim automatic LoRA interchangeability merely because Base and Turbo share architecture.

- Base LoRA → Base: structurally compatible; live layer resolution still required.
- Turbo LoRA → Turbo: structurally compatible; live layer resolution still required.
- Base ↔ Turbo: accepted only as `runtime_preflight_required` when metadata explicitly crosses variants.
- Non-Z-Image family metadata: rejected before regional trigger text or runtime wrapper insertion.
- Unknown family metadata: runtime preflight candidate.

## Provider sampler lock

### Z-Image Base

Scene Director preserves the provider/user sampler profile. Neo's current reference profile is 35 steps / CFG 3.5, with the normal base floor at 28 steps / CFG 2.5. Lower values produce diagnostics only; Scene Director does not silently rewrite them.

Base negative conditioning stays encoded through `CLIPTextEncode`.

### Z-Image Turbo

Neo's current provider contract is locked to:

```text
steps = 9
CFG = 1.0
negative = ConditioningZeroOut
```

If the incoming Turbo graph does not preserve this profile, Scene Director fails closed before regional mutation.

Both Base and Turbo must preserve `ModelSamplingAuraFlow` in the MODEL chain.

## Regional prompt path

Z-Image Base uses masked positive and masked regional negative conditioning. Z-Image Turbo uses masked positive conditioning and preserves its zero-negative provider policy.

Regional prompts continue to use `ConditioningSetMask(set_cond_area = mask bounds)` and the existing Scene Director rectangle/feather authority. No family-specific extra sampler is introduced.

## Regional LoRA adapter

Z-Image uses:

```text
z_image_activation_delta_v1
```

LoRA Stack owns LoRA selection. Scene Director consumes only rows explicitly targeted to Scene Director regions and inserts at most one `NeoRegionalLoRADelta` MODEL wrapper regardless of the number of regional LoRAs.

Regional CLIP LoRA remains suppressed. Execution is model-side activation delta only.

## Spatial module policy

Comfy's Z-Image NextDiT first refines image and caption streams separately, then concatenates them for the 30 main blocks.

| Module path | Regional scope |
|---|---|
| `x_embedder` | unpadded image tokens only |
| `noise_refiner.*.attention.qkv/out` | padded image tokens only |
| `noise_refiner.*.feed_forward.w1/w2/w3` | padded image tokens only |
| `layers.*.attention.qkv/out` | combined caption + image; caption mask forced to zero |
| `layers.*.feed_forward.w1/w2/w3` | combined caption + image; caption mask forced to zero |
| `final_layer.linear` | combined caption + image; caption mask forced to zero |
| `context_refiner.*`, `cap_embedder`, timestep/AdaLN, norm-only paths | excluded |

## Token-padding safety

Z-Image may pad caption and image token sequences to `pad_tokens_multiple`.

Neo mirrors only the **sequence lengths**, never the content:

- logical image region mask occupies only real image patch tokens;
- image padding tokens receive zero;
- caption tokens receive zero;
- caption padding tokens receive zero;
- unknown sequence lengths receive an all-zero mask;
- omni/reference-latent token stacks are blocked in this phase rather than guessed.

This prevents valid padded Z-Image graphs from being accidentally disabled while still failing closed against region leakage.

## GGUF

GGUF uses the same activation-delta runtime and does not rewrite quantized weights. Live linear dimensions prefer `in_features` / `out_features`, and the loader is included in runtime proof.

## Runtime proof

A live run is not called regional-LoRA proven unless all standard proof fields pass:

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

Static tests establish the graph/runtime contract. Final-image leakage still requires same-seed live GPU A/B validation.

## Failure policy

Z-Image regional LoRA never falls back to global `LoraLoader`, `LoraLoaderModelOnly`, V054, or a masked refinement sampler. Wrong family metadata, generic/non-Z Lumina2, unsupported LoRA structure, zero safe layer matches, unknown token layouts, unsupported reference-token stacks, or a missing runtime node fail closed.

## Still gated

- Scene Director Outpaint
- regional CLIP LoRA hard isolation
- non-standard LoRA formats not represented by the current standard A/B activation-delta contract
- Z-Image omni/reference-latent regional-LoRA token layouts

---

## SD-28.7 release note

SD-28.7 does not change the Z-Image family adapter described above. It adds the shared Scene Director release lock and Inspector v2. Z-Image Base/Turbo therefore keep the exact SD-28.6 model/padding/sampler contracts while gaining fail-closed post-compile invariant checks and explicit GPU-proof-pending UX.
