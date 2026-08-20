---

> **SD-28.8 documentation note:** This is a phase-history document. For the current released Scene Director architecture, support matrix, proof semantics, and apply order, use `guides/01_IMAGE/scene_director_current.md`. Historical gates/statuses below describe this phase at the time it shipped and must not override the current guide.

guide_id: image.scene_director.sd28_4
title: Scene Director — SD-28.4 Krea 2 RAW + Turbo Full Support
surface: image
scope: built_in
updated: 2026-07-27
---

# Scene Director — SD-28.4 Krea 2 RAW + Turbo Full Support

SD-28.4 promotes **Krea 2 RAW** and **Krea 2 Turbo** from the SD-28.3 experimental runtime foundation to Neo's active lightweight Scene Director contract for **Generate, Img2Img, and Inpaint** on both **Safetensors / Components (`diffusion_model`)** and **GGUF** routes. Outpaint remains planned-gated.

## Execution contract

Krea does not enter the classic SDXL/SD1.5 V054 repair engine. Its route is:

```text
provider Krea MODEL + CLIP + latent
        │
        ├── Scene Director masked regional prompts
        │
        └── optional NeoRegionalLoRADelta MODEL wrapper
                        │
                        ▼
                existing provider sampler
```

The lightweight route adds **zero KSampler/KSamplerAdvanced nodes**. It does not run Character Lock repair samplers, midpoint repair, end refinement, background repaint, or masked LoRA finish passes.

## RAW and Turbo sampler integrity

| Family | Required runtime behavior |
|---|---|
| Krea 2 RAW | Preserve the provider/user RAW sampler profile. Neo does not silently convert RAW into Turbo. Official/reference defaults remain 52 steps and CFG 3.5 for diagnostics only. |
| Krea 2 Turbo | Preserve the Comfy Turbo contract: **8 steps, CFG 1.0, `ConditioningZeroOut` negative**. If a compiled graph violates that profile, Scene Director fails closed and does not mutate the graph. |

## Regional prompting

Each active region is encoded with the provider Krea CLIP and attached through `ConditioningSetMask` with `set_cond_area = mask bounds`. The global provider conditioning remains the canvas base unless Prompt Authority is **Scene Director only**.

## Regional LoRA

LoRA Stack still owns LoRA selection and file identity. A LoRA row becomes regional only when its `apply_to` target resolves to a Scene Director region. LoRA Stack must not globally patch that row.

For Krea, Scene Director inserts one `NeoRegionalLoRADelta` MODEL wrapper regardless of the number of valid regional LoRA routes. The wrapper clones the active Comfy ModelPatcher and injects masked activation deltas during the existing denoising run.

### Compatibility policy

- Krea RAW and Turbo share the Krea 2 LoRA architecture for this route contract.
- A row declaring Krea RAW/Krea2 may target Turbo, and a Krea Turbo-declared row may target RAW.
- A row explicitly declaring another family such as SDXL is rejected **before** the regional wrapper and before trigger words are added to the regional prompt.
- A row with no family metadata is allowed only as a runtime-preflight candidate; live Krea layer resolution must still succeed.
- Regional CLIP LoRA mutation is not claimed. Region-targeted rows execute model-side only; CLIP delta is suppressed.

## Spatial scope policy

Krea 2 mixes text and image tokens inside its transformer blocks. SD-28.4 therefore applies a module-scope filter:

| Krea module path | Regional LoRA scope |
|---|---|
| `first` | image tokens only |
| `blocks.*` | combined sequence, but text-token mask values are forced to zero |
| `last.linear` | combined sequence, but text-token mask values are forced to zero |
| text-fusion / text-MLP / timestep-only paths | excluded |

Unknown token layouts fail closed to an all-zero regional LoRA mask. Neo never substitutes an average/global mask.

## GGUF

GGUF is part of the SD-28.4 Krea support contract because regional LoRA operates on live module activations rather than rewriting quantized weights. Linear dimensions are resolved through module `in_features` / `out_features` before any tensor-weight fallback. The loader identity is passed into `NeoRegionalLoRADelta` and recorded in runtime proof.

This is still subject to **per-run runtime proof** on the installed Comfy/Krea/GGUF stack; compile-time metadata does not fabricate GPU validation.

## Runtime proof

A successful regional LoRA execution requires all of the following:

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

Until the live run produces those fields, the graph is considered **armed / runtime-proof pending**, not GPU-proven hard isolation.

## Failure policy

SD-28.4 never falls back from a failed regional LoRA route to a global `LoraLoader`, `LoraLoaderModelOnly`, V054, or a masked refinement sampler. Regional prompting may still execute while the LoRA lane is gated.

## Still gated

- Krea 2 Outpaint Scene Director execution
- FLUX.2 Klein regional LoRA
- Z-Image / Z-Image Turbo regional LoRA
- regional CLIP LoRA hard-isolation claims
- non-standard LoRA formats that do not resolve through the supported Krea layer contract

## Superseded Klein status — SD-28.5

SD-28.5 promotes FLUX.2 Klein beyond the adapter-gated status recorded in this phase. Klein now uses `flux2_klein_activation_delta_v1` for 4B/9B native/components and GGUF Generate/Img2Img/Inpaint routes with family-specific double-stream/single-stream spatial filtering, FluxGuidance profile preservation, 4B/9B compatibility checks, Comfy partial-QKV mapping support, and per-run runtime proof. Z-Image regional LoRA remains adapter-gated.

## Superseded Z-Image status — SD-28.6

The SD-28.4/SD-28.5 note that Z-Image regional LoRA is adapter-gated is historical. SD-28.6 promotes Z-Image Base/Turbo through the separate `z_image_activation_delta_v1` family adapter with NextDiT signature proof, padding-safe token masks, ModelSamplingAuraFlow preservation, native/GGUF support, and per-run runtime proof.
