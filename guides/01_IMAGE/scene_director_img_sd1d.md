# IMG-SD1D — Modern Subject Authority Conditioning Merge + Native Inpaint Parity

> **Superseded default behavior:** IMG-SD2 disables automatic modern subject/cast prompt mutation by default. This guide remains historical compatibility and inpaint-topology context. Native inpaint parity remains current.

**Status:** Current — 2026-08-15  
**Surface:** Image → Generations / Reference → Scene Director  
**Engine:** `lightweight_regional`  
**Families:** Krea 2 RAW/Turbo, FLUX.2 Klein, Z-Image Base/Turbo  
**Classic SDXL/SD1.5:** unchanged (`classic_v054`)

IMG-SD1D corrects the conditioning topology introduced by IMG-SD1C and restores Scene Director parity on native inpaint routes.

## Why SD1C needed a correction

SD1C encoded the subject-count contract as a separate, unmasked full-canvas conditioning lane:

```text
provider global conditioning
+ subject-authority conditioning
+ masked region 1
+ masked region 2
```

Physical Krea 2 testing showed that the styleless structural lane could compete with the user's photographic global prompt and materially change visual style. Modern semantic image models behave better when scene style and scene structure are encoded as one coherent global instruction.

## SD1D global conditioning contract

SD1D merges the compact structural suffix into the existing provider global `CLIPTextEncode` text before encoding:

```text
user global scene prompt
+ safe subject structure suffix
→ ONE provider global CLIPTextEncode
```

Example:

```text
Inside a hotel room at night, candid photo, photorealistic
+
exactly 2 visible subjects, one complete subject per character region,
every assigned character region occupied, no additional visible subjects
```

Regional identity/body/clothing/LoRA trigger terms remain in their own masked lanes. No regional trigger is copied into the global prompt.

The subject-authority merge is structural compiler metadata; Neo does not rewrite the visible user prompt field.

## Native inpaint ownership

Native inpaint uses `InpaintModelConditioning` to attach source-image/mask metadata to positive and negative conditioning. Scene Director must therefore combine its regional conditions **before** this wrapper.

Correct topology:

```text
provider global text + subject structure
→ provider positive conditioning
   + masked regional conditions
→ ConditioningCombine chain
→ InpaintModelConditioning.positive

provider negative / zero-negative policy
→ InpaintModelConditioning.negative

InpaintModelConditioning outputs 0 / 1 / 2
→ provider KSampler positive / negative / latent_image
```

The KSampler remains connected to the original `InpaintModelConditioning` outputs. Scene Director does not bypass the wrapper and does not replace the masked latent.

For Krea 2 Turbo, the zero-negative validator now traces through `InpaintModelConditioning` and proves the upstream source is still `ConditioningZeroOut` instead of incorrectly requiring the sampler's immediate negative node to be `ConditioningZeroOut`.

## Execution proof

Provider proof schema:

```text
neo.image.scene_director.execution_proof.img_sd1d.v3
```

Important fields include:

```text
subject_authority_merge_mode = merged_into_provider_clip_text
subject_authority_source_text_node_id
subject_authority_merge
subject_authority_node_ids = []
conditioning_rewire_location
sampler_conditioning_rewired
conditioning_wrapper_rewired
inpaint_conditioning_wrapper_node_id
inpaint_conditioning_wrapper_preserved
inpaint_conditioning_anchor_positive_ref
inpaint_conditioning_anchor_negative_ref
```

On native inpaint, expected proof is:

```text
conditioning_rewire_location = inpaint_model_conditioning_inputs
sampler_conditioning_rewired = false
conditioning_wrapper_rewired = true
inpaint_conditioning_wrapper_preserved = true
```

## Compatibility policy

- One provider sampler remains authoritative.
- Provider steps/CFG/sampler/scheduler/denoise/latent are preserved.
- Modern regional LoRA still uses `NeoRegionalLoRADelta`; no global LoRA fallback is allowed.
- Krea 2 Turbo and other zero-negative routes keep their native zero-negative policy.
- Prompt-vs-mask direction conflicts remain warning-only.
- SDXL/SD1.5 Classic V054 is unchanged.
- Non-Scene-Director Native/LanPaint inpaint behavior is unchanged.
