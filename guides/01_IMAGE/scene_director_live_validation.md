# Scene Director — Live GPU Validation Checklist

**Applies to:** Krea 2 RAW/Turbo, FLUX.2 Klein, Z-Image/Z-Image Turbo  
**Purpose:** qualify a specific ComfyUI + model + loader + LoRA combination after the static SD-28 release contract has passed.

Static tests establish routing and graph safety. They do **not** prove visible spatial isolation on a real GPU run. Use this checklist whenever a backend/model/LoRA combination is being promoted from “supported, proof pending” to “runtime proven.”

## Test fixture

Keep the comparison controlled:

- fixed seed;
- same model, VAE, text encoder, scheduler, sampler, steps, CFG/guidance and denoise;
- same resolution;
- same source image/mask for Img2Img/Inpaint;
- same Scene Director region geometry and feather;
- no unrelated LoRA/ControlNet/IPAdapter changes between A and B;
- save Neo Inspector metadata for every run.

Recommended scene: two or three visually different subjects with non-overlapping regions and one neutral background area.

## A/B sequence

### A — regional prompt only

Run the scene with the regional LoRA row disabled. Save image and Inspector metadata.

Expected:

- one provider sampler;
- regional prompt lanes active;
- no `NeoRegionalLoRADelta` wrapper when no regional LoRA is requested;
- release lock locked;
- GPU LoRA proof absent/not applicable.

### B — one regional LoRA

Enable one LoRA on one subject region only.

Expected runtime proof:

- `lora_loaded=true`;
- `model_family_match=true`;
- `region_mask_bound=true`;
- `masked_delta_hook_active=true`;
- `delta_eval_attempted=true`;
- `delta_nonzero=true`;
- `global_model_mutation=false`;
- `sampler_count=1`;
- `forward_hooks_removed=true`;
- token/spatial scope proof true where the family adapter exposes it.

### C — 3+ regional LoRAs

Assign separate compatible LoRAs to at least three regions.

Expected:

- one `NeoRegionalLoRADelta` model wrapper;
- one provider sampler;
- no standard LoRA-loader node created for the region rows;
- every accepted route remains bound to its owning mask;
- no legacy two-route limit.

### D — wrong-family LoRA

Assign a deliberately incompatible LoRA.

Expected:

- LoRA rejected or failed closed;
- no global LoRA fallback;
- no finish-pass sampler;
- owning trigger terms are not injected when compatibility is explicitly rejected;
- regional prompting can remain active if otherwise valid;
- release lock remains healthy.

## Fixed-seed leakage measurement

For A and B, compare pixels outside the selected region. This is a **visual/effect measurement**, not part of the compile-time support claim.

Record:

- mask used for the region;
- optional guard band around the feathered boundary;
- mean absolute pixel difference outside the guard band;
- maximum outside-region difference;
- percentage of outside-region pixels above a chosen difference threshold;
- a visual difference image.

Do not interpret `runtime_gpu_proven=true` as mathematical proof that every final outside-region pixel is invariant. It proves the masked-delta runtime actually executed as designed; downstream transformer mixing can still create indirect changes.

## Family checks

### Krea 2 RAW

- provider/user RAW sampler profile remains unchanged;
- regional negative conditioning works;
- Krea LoRA key resolution succeeds;
- non-Krea family metadata is rejected.

### Krea 2 Turbo

- 8 steps;
- Comfy CFG 1;
- zero-negative policy;
- Scene Director does not silently convert the graph to RAW values.

### FLUX.2 Klein

Run 4B and 9B separately.

- model scale is correctly identified;
- 4B ↔ 9B LoRA crossing is blocked;
- same-scale Base ↔ Distilled crossing stays preflight unless runtime compatibility is proved;
- `FluxGuidance` remains in the regional conditioning path;
- partial `linear1_qkv` mappings do not expand into unrelated output features.

### Z-Image Base

- `ModelSamplingAuraFlow` remains in the provider model chain;
- Base steps/CFG are preserved;
- regional negative conditioning works;
- padded text/image tokens receive zero regional-LoRA mask values.

### Z-Image Turbo

- Neo's current provider contract remains 9 KSampler steps / CFG 1 / zero-negative;
- padded tokens remain zero-masked;
- Base ↔ Turbo LoRA crossing remains runtime-preflight unless specifically proved.

## Loader checks

Repeat relevant tests for:

- `diffusion_model` / components / safetensors;
- GGUF.

For GGUF, verify that regional execution operates through live activation/module dimensions and does not attempt to rewrite quantized base weights.

## Failure acceptance criteria

A backend/model combination must remain **proof pending or blocked** if any of these occur:

- delta never evaluates;
- delta is always zero when a compatible LoRA should be active;
- family/model signature does not match;
- regional mask cannot be proven;
- global model mutation is observed;
- sampler count changes;
- hooks are not cleaned up;
- unknown token geometry causes non-zero regional execution;
- release lock reports a blocker.

Do not bypass these states by enabling a classic, global-LoRA, crop-refine or finish-pass fallback.
