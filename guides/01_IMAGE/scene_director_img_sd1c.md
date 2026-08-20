# IMG-SD1C — Modern Scene Director Subject Authority + Prompt Conflict Guard

**Status:** Superseded by IMG-SD1D for global subject-authority encoding — 2026-08-15  
**Surface:** Image → Generations / Reference → Scene Director  
**Engine:** `lightweight_regional` only  
**Families:** Krea 2 RAW/Turbo, FLUX.2 Klein, Z-Image Base/Turbo  
**Classic SDXL/SD1.5:** unchanged (`classic_v054`)


> **IMG-SD1D correction:** the SD1C subject-count bridge is no longer encoded as a separate unmasked conditioning lane. Current modern routes merge the structural bridge into the provider global text before encoding. See `scene_director_img_sd1d.md`.

IMG-SD1C closes the prompt-authority gap exposed after IMG-SD1B made modern regional LoRA isolation executable. Physical Krea 2 Turbo tests proved that masked regional prompts and regional LoRAs were present in the final graph, but removing the global cast phrase allowed the model to invent the wrong subject class or an additional person. Raising regional mask strength did not solve scene cardinality.

## Root cause

The lightweight compiler previously emitted only:

```text
provider global conditioning
+ masked regional prompt lane 1
+ masked regional prompt lane 2
+ regional MODEL-side LoRA wrapper
```

That is spatially valid, but the global model still owns full-canvas scene formation. A masked regional prompt can strengthen a subject inside a box without establishing that the declared character regions are the complete cast for the whole image.

The canonical Scene Director payload already carried the structural contracts used by classic V054:

```text
count_contract
subject_contract
negative_contract
```

but `lightweight_regional.py` ignored those contracts. IMG-SD1C makes the modern engine consume the existing contract instead of inventing an SDXL-style repair pipeline.

## Modern subject authority contract

For active **Character** regions, the compiler derives a compact subject-authority plan:

```text
Global user prompt
  + unmasked subject-count bridge
  + masked regional subject prompt(s)
  + regional MODEL-side LoRA isolation
```

Default count bridge:

```text
exactly {count} visible subjects,
one complete subject per character region,
every assigned character region occupied,
no additional visible subjects
```

The user's global prompt remains untouched. The bridge is encoded as its own conditioning lane and combined with the provider positive conditioning.

Each Character-region prompt also receives the existing local structural contract:

```text
exactly one complete visible subject inside this assigned region,
separate from neighboring subjects
```

Identity, body, clothing, trigger words, and other appearance details remain regional. They are never copied into the global bridge.

## Conservative subject-class inference

IMG-SD1C may add a short cast-class clause only when **every** Character region explicitly states a safe class/gender term in its own prompt.

Examples:

```text
2 prompts explicitly describe adult men
→ all declared subjects are adult men

1 prompt says man and 1 says woman
→ declared cast: 1 male subject and 1 female subject
```

If any Character prompt does not explicitly declare a class/gender term, Neo does not guess from LoRA filenames, trigger words, labels, identity names, or prior generations. Count authority still applies.

## Negative contract

Base/non-turbo families that already support regional negative conditioning may receive the existing count-aware negative contract as an additional global negative lane.

Turbo/zero-negative families preserve their provider zero-negative policy. Krea 2 Turbo therefore uses the positive subject bridge only; IMG-SD1C does not create a hidden non-zero negative lane.

## Prompt-vs-mask direction conflict guard

The region box owns spatial position. Neo detects explicit position phrases such as:

```text
standing on the left
standing on the right
positioned in the center
```

and compares them with the region box center.

Example:

```text
mask = left side
prompt = "standing on the right"
```

produces a warning:

```text
prompt_direction_vs_mask_position
```

Policy:

```text
warning only
no silent prompt rewrite
mask remains spatial authority
```

The core Image Scene Director UI surfaces these warnings in the prompt-authority diagnostics. The extension-owned editor also shows the warning inside the affected modern Basic region card.

## Execution proof

`_neo_scene_director_execution_proof` is now schema:

```text
neo.image.scene_director.execution_proof.img_sd1c.v2
```

and includes:

```text
subject_authority_applied
subject_authority_bridge_ref
subject_authority_node_ids
subject_authority
prompt_conflict_count
prompt_conflicts
```

The lightweight runtime proof also records the subject-authority plan, conservative class inference, region mask positions, conflict list, and sanitizer policy.

## Fail-closed / compatibility policy

- No extra sampler is added.
- Existing provider sampler/latent/profile remains authoritative.
- Existing regional LoRA ownership remains unchanged.
- No global LoRA fallback is introduced.
- No Character Lock or classic V054 repair pass is introduced.
- If subject contracts are explicitly disabled in the payload, the previous lightweight prompt behavior is preserved.
- SDXL/SD1.5 classic behavior is untouched.

## Recommended authoring pattern

For modern Scene Director:

**Global prompt:** scene, environment, camera, light, composition.  
**Character regions:** identity, gender/class when important, body, clothing, pose, expression, trigger words.  
**Region box:** spatial placement authority.

Avoid duplicating `left/right` in the regional prompt unless it agrees with the box.
