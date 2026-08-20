# IMG-SD2 — Modern Regional LoRA Isolation Core

**Status:** current for FLUX.2 Klein / Z-Image; Krea 2 runtime superseded by IMG-SD3  
**Date:** 2026-08-15  
**Applies to:** historical Krea 2 SD2 design plus current FLUX.2 Klein / Z-Image Base/Turbo routes; see IMG-SD3 for Krea 2 current execution  
**Does not replace:** SDXL / SD1.5 Classic V054

## Product intent

Modern checkpoints already understand ordinary multi-subject composition, relationships, camera language, scene semantics, and natural prompt structure. Scene Director therefore should not add heavy SDXL-era scene-control machinery unless the user explicitly needs it.

The primary modern use case is narrower:

> Load multiple LoRAs, assign each LoRA to a region/character, and prevent those LoRAs from globally mixing identities across the image.

The modern Scene Director UI is consequently Basic-only and LoRA-isolation-first.

## Default prompt authority

IMG-SD2 makes the provider/user prompt authoritative and leaves it unchanged by default.

```text
User global prompt
    ↓ unchanged
Provider CLIP/text encode

Optional Region 1 prompt → masked local reinforcement
Optional Region 2 prompt → masked local reinforcement

Region 1 LoRA → regional model-side isolation mask
Region 2 LoRA → regional model-side isolation mask
                     ↓
               one provider sampler
```

Default modern behavior:

- no hidden subject-count bridge;
- no hidden cast/gender bridge;
- no automatic `exactly one subject` suffix in region prompts;
- no automatic prompt-direction rewrite;
- regional prompt may be empty when a region has an assigned LoRA Stack row;
- global scene composition remains owned by the selected checkpoint and user prompt.

The historical IMG-SD1C/IMG-SD1D subject-authority implementation remains only behind explicit `strict_cast_control=true` compatibility intent. The current modern UI sends `strict_cast_control=false`.

## Regional LoRA ownership

LoRA Stack remains the asset/selection owner. Scene Director owns spatial execution for rows assigned to Scene Director regions.

For each assigned row:

1. preserve the LoRA Stack row UID;
2. remove that row from normal/global LoRA execution;
3. resolve the owning Scene Director region;
4. pass the route to one `NeoRegionalLoRADelta` node;
5. reuse the provider model/sampler/latent path;
6. never fall back to a global `LoraLoader` when regional execution cannot be proved.

A modern graph can therefore have LoRA-only regions with zero `ConditioningSetMask` nodes when no local prompt is supplied.

## Krea 2 strict-isolation profile

Krea 2 RAW/Turbo uses:

```text
krea2_activation_delta_v3_strict_isolation
```

The runtime maps LoRA A/B pairs onto eligible live Krea modules and applies forward-time activation deltas through region token masks. IMG-SD2 adds a stricter exclusion rule for attention key/value projections:

```text
blocks.<n>.attn.wk → excluded from regional LoRA execution
blocks.<n>.attn.wv → excluded from regional LoRA execution
```

Reason: Krea's main transformer is single-stream. Text and image tokens participate in the same attention blocks. Even if a K/V LoRA delta is written only to tokens inside Region A, queries from Region B can attend to those changed keys/values. That makes K/V a direct identity-broadcast path. Suppressing K/V writes trades some LoRA capacity for a cleaner isolation boundary.

Eligible spatially maskable targets can still include local query/output/MLP/image-input/final projection paths when module resolution proves a trustworthy token lane.

### Honest limitation

IMG-SD2 does **not** claim mathematically perfect character identity isolation. A transformer can propagate information between tokens in later layers even after direct K/V LoRA writes are suppressed. The contract therefore records:

```text
hard_identity_isolation_claimed = false
runtime_gpu_proven = false   # until a live run supplies proof
```

The goal is to reduce the strongest direct cross-region LoRA broadcast path while preserving a single native generation pass.

## Region overlap diagnostics

Region boxes are also evaluated for overlap. Execution proof reports the maximum intersection as a fraction of the smaller region and a risk label:

```text
none / low / medium / high
```

Overlap is not automatically blocked because some compositions need touching regions, but high overlap lowers isolation confidence and should be corrected before judging LoRA fidelity.

## Inpaint parity

IMG-SD1D's native-inpaint ownership remains current:

- `InpaintModelConditioning` remains the provider conditioning wrapper;
- optional regional prompt conditioning is combined upstream of that wrapper;
- LoRA-only isolation does not require regional prompt nodes;
- KSampler positive/negative/latent wrapper outputs remain provider-owned;
- Krea Turbo zero-negative validation traces through the wrapper.

## Execution proof

Provider proof schema is now:

```text
neo.image.scene_director.execution_proof.img_sd2.v4
```

Important fields include:

- `modern_scene_director_core.primary_purpose = regional_lora_isolation`
- `modern_scene_director_core.global_prompt_mutation = false` by default
- `regional_prompt_lane_count`
- `regional_lora_route_count`
- `regional_lora_isolation_goal`
- `regional_lora_isolation_profile`
- `regional_lora_overlap_diagnostics`
- `regional_lora_clip_delta_execution`
- `regional_lora_hard_isolation_claimed = false`
- `single_sampler_preserved`
- `contract_ok`

Compile-time graph routing is not treated as visual identity proof.

## Comfy runtime deployment

Copy the bundled `neo_scene_director` folder from the Neo Studio root into:

```text
<ComfyUI-root>/custom_nodes/neo_scene_director
```

Then fully restart ComfyUI and refresh/Test the selected ComfyUI profile in Neo. A Neo-only restart is not enough because the regional runtime executes inside ComfyUI.

## SDXL boundary

SDXL and experimental SD1.5 checkpoint routes remain on `classic_v054`. IMG-SD2 does not remove Character Lock, repair chains, advanced regional controls, relationship/trait authority, or other V054 functionality from those classic routes.


## IMG-SD2A submit-scope hotfix

IMG-SD2A fixes a frontend submit regression introduced by IMG-SD2 where `sceneDirectorPayloadPreview()` referenced `modernBasicOnly` after using the modern route predicate only as an inline expression. The payload builder now resolves `modernBasicOnly = sceneDirectorModernBasicOnly(route)` once at function scope and reuses that same authority for modern settings normalization and emitted payload fields. This is a frontend-only scope correction; the IMG-SD2 regional LoRA isolation runtime contract is unchanged.
