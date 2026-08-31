# Video LoRA Stack — Phase 9 Legacy Compatibility Hardening

**Date:** 2026-09-01  
**Branch:** `phase-9-legacy-lora-compat-hardening`  
**Base:** Phase-8 CI-green head `74bea6406a1cd49dabd0649467fb623d30978475`

## Objective

Phase 9 hardens the compatibility boundary for saved/request state created before the universal Video LoRA Stack became graph authority.

This phase does **not** widen model-family or route capability. It makes legacy H3 Turbo and WAN Normal/LightX2V state deterministic, read-compatible, deprecated for writeback, and incapable of owning workflow graph mutation.

## Locked authority

```text
Compiler
  owns safe model patch location

Universal video.lora_stack runtime
  owns LoRA graph nodes and rewiring

Legacy H3/WAN fields
  provide read-compatible intent only
```

Every LoRA node on hardened H3/WAN workflows must be represented in the universal runtime's `applied` metadata. An undeclared `LoraLoaderModelOnly` or generic `LoraLoader` node fails closed.

## H3 compatibility

Readable legacy fields:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

If a matching universal row already exists, its uid and strength remain authoritative. Legacy Turbo intent may promote it to `role=speed` and normalize the target to `all`; duplicate runtime nodes are not created.

## WAN compatibility

Readable legacy fields:

```text
enable_video_lora
video_lora_mode
video_lora_model
video_lora_strength
video_lora_target
enable_lightx2v
high_noise_lora
low_noise_lora
high_noise_lora_strength
low_noise_lora_strength
```

Universal state has precedence for uid/model strength/CLIP strength on already-covered branches. Legacy rows fill uncovered branches only.

### Branch-exact promotion fix

Phase 8 could promote an entire matching universal `target=all` row to speed when only one legacy branch requested speed. That could widen speed semantics to the other branch.

Phase 9 fixes this by splitting the universal row when required:

```text
universal: standard/all
legacy:    speed/high

result:
  speed/high
  standard/low
```

The same rule applies symmetrically to low-only promotion. Universal strengths remain intact. Derived split UIDs are deterministic and collision-safe. If splitting would exceed the 12-row maximum, migration fails closed.

## Writeback deprecation contract

Compiled H3/WAN metadata now states:

```text
legacy_field_writeback = false
universal_stack_writeback = true
next_save_action = persist_video.lora_stack_only
status = compatibility_only_deprecated
```

Legacy fields remain readable in Phase 9 so old saved state is not broken. New persisted state must use the universal Video LoRA stack.

## Legacy WAN diagnostic sanitization

`video_lora_adapter.py` remains present during the compatibility period, but its diagnostic snapshot loses graph ownership data before exposure.

Removed fields:

```text
node_id
source_model_link
output_model_link
```

The sanitized snapshot is marked:

```text
deprecated = true
compatibility_only = true
graph_mutation_authority = none
```

Historical fixed WAN IDs such as `129:101`, `129:102`, `9001`, and `9002` cannot function as a second graph mutation path.

## Implementation

New Phase-9 modules:

```text
neo_app/video/video_lora_legacy_compat.py
neo_app/video/video_lora_legacy_compat_regression.py
tests/test_video_lora_legacy_compat_phase9.py
.github/workflows/phase9_legacy_lora_compat_regression.yml
```

The compatibility hardening installer runs after existing H3/LTX/WAN LoRA integrations from `neo_app/video/__init__.py`.

It upgrades the H3 legacy merge and WAN legacy merge at the compatibility boundary and wraps the built H3/WAN dual workflow outputs with graph-authority validation and deprecation metadata. Compiler patch anchors are unchanged.

## Regression gate

Phase 9 adds 21 compatibility cases:

- WAN all-row + high-only speed promotion;
- WAN all-row + low-only speed promotion;
- WAN all-target speed promotion;
- partial branch fill;
- deterministic mixed-state merge;
- split UID collision handling;
- maximum stack fail-closed behavior;
- H3 universal uid/strength precedence;
- disabled H3 bridge canonical-state invariance;
- H3/WAN active and inactive compatibility metadata;
- WAN legacy snapshot sanitization;
- accepted declared H3/WAN universal graph nodes;
- rejected undeclared model-only LoRA node;
- rejected historical WAN hardcoded node;
- rejected undeclared generic LoRA loader;
- declared dual-branch node-map recognition.

Promotion requires all previous gates to remain green:

```text
H3       43 / 43
LTX      17 / 17
WAN      30 / 30
Phase 9  21 / 21
-----------------
Total   111 / 111
```

## No capability expansion

No support-matrix route is promoted in this phase. Existing fail-closed boundaries remain in force for H3 GGUF, LTX speed/GGUF/extended modes, WAN UNET speed/high-low targets, WAN Rapid AIO, and imported native workflows.

## Removal boundary

Phase 9 does not remove the old fields or adapter.

Removal is deferred until:

1. save/writeback paths persist universal `video.lora_stack` state;
2. migration diagnostics show no unresolved legacy-only state;
3. at least one compatibility release boundary has retained read support before final removal.

That boundary prevents a cleanup release from breaking older saved workflows before their state has been migrated.
