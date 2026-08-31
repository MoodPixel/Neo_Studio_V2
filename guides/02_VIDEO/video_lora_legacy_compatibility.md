---
guide_id: video.lora_legacy_compatibility
title: Video LoRA Legacy Compatibility Boundary
surface: video
scope: built_in
applies_to:
  - minimax_h3
  - wan22
  - video_lora_stack
tags:
  - video
  - lora
  - migration
  - compatibility
  - deprecation
priority: 86
version: 1
updated: 2026-09-01
---

# Video LoRA Legacy Compatibility Boundary

Phase 9 defines how old MiniMax H3 Turbo and WAN Video LoRA state remains loadable while `video.lora_stack` becomes the only persistent writeback and graph-mutation authority.

## Boundary

```text
legacy saved/request fields
        ↓ read only
compatibility bridge
        ↓ normalize + merge
universal video.lora_stack
        ↓
compiler-owned patch profile
        ↓
validated LoRA graph nodes
```

Legacy controls may contribute **intent**. They may not contribute workflow node IDs or graph links.

## Writeback contract

On hardened H3/WAN routes compiled metadata reports:

```text
legacy_field_writeback = false
universal_stack_writeback = true
next_save_action = persist_video.lora_stack_only
```

A caller loading legacy state should migrate it into the universal stack and save the universal representation on the next write. Phase 9 deliberately keeps legacy fields readable so older saved states are not broken.

## H3 legacy Turbo

These fields remain read-compatible:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

When the same file already exists in `video.lora_stack`, the universal row owns its uid and strength. Legacy Turbo intent may promote that row to `role=speed` and normalize its target to `all`, but it does not create a duplicate graph path.

## WAN legacy controls

These fields remain read-compatible:

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

### Mixed universal + legacy precedence

For a WAN branch already represented by the universal stack:

1. universal uid wins;
2. universal `strength_model` wins;
3. universal `strength_clip` wins;
4. legacy intent may promote the matching branch from `standard` to `speed`;
5. legacy intent fills only branches that are not already represented.

This prevents reopening saved universal choices merely because older legacy fields are still present in a request.

### Branch-exact promotion

A universal WAN row with `target=all` represents both high and low branches. If legacy speed intent applies to only one branch, Phase 9 splits the universal row before promotion.

Example:

```text
Universal:
  same_file, standard, all, strength=0.72

Legacy:
  same_file, speed, high, strength=1.0

Migrated:
  same_file, speed,    high, strength=0.72
  same_file, standard, low,  strength=0.72
```

The low branch is not accidentally promoted to speed. The original universal uid is retained on the deterministic high split; derived branch uids are collision-safe.

If a required branch split would exceed the 12-row Video LoRA limit, migration fails closed instead of silently discarding state.

## Graph authority hardening

Phase 9 scans the compiled H3/WAN workflow after universal LoRA application.

Every `LoraLoaderModelOnly` or generic `LoraLoader` node must be declared by `video_lora_stack.applied`. An undeclared LoRA loader causes compile failure.

This turns the architectural rule into an executable invariant:

```text
legacy fields → intent only
universal runtime → LoRA graph nodes
compiler profile → patch location
```

Historical WAN node IDs such as `129:101`, `129:102`, `9001`, and `9002` are not accepted as a second mutation path.

## Legacy WAN diagnostics

`video_lora_adapter.py` is not deleted in Phase 9 because its legacy request semantics are still useful during migration. Its diagnostic snapshot is sanitized before exposure:

- `node_id` is removed;
- `source_model_link` is removed;
- `output_model_link` is removed;
- snapshot is marked `deprecated: true`;
- snapshot is marked `compatibility_only: true`;
- `graph_mutation_authority` is `none`.

This prevents old diagnostic data from being mistaken for an active graph contract.

## Removal boundary

Phase 9 explicitly does **not** remove legacy fields or `video_lora_adapter.py`.

Removal requires all of the following:

1. saved-state writeback persists the universal `video.lora_stack` representation;
2. migration diagnostics show no unresolved legacy-only states;
3. at least one compatibility boundary retains read support before final field removal.

Until those conditions are satisfied, legacy **read** compatibility stays; legacy **write** compatibility does not.

## Regression gate

```bash
python -m neo_app.video.video_lora_legacy_compat_regression
```

Phase 9 adds 21 deterministic compatibility cases covering:

- WAN high-only/low-only branch-exact speed promotion;
- all-target promotion;
- partial branch fill;
- universal uid/strength precedence;
- deterministic merge ordering;
- split uid collision handling;
- max-stack fail-closed behavior;
- H3 duplicate Turbo precedence;
- H3/WAN deprecation/writeback metadata;
- sanitized WAN legacy diagnostics;
- accepted compiler-declared universal nodes;
- rejected undeclared ModelOnly/generic LoRA nodes;
- rejected historical WAN hardcoded nodes.

The promotion gate also reruns every earlier Video LoRA regression:

```text
MiniMax H3  43 / 43
LTX 2.3     17 / 17
WAN 2.2     30 / 30
Phase 9     21 / 21
--------------------
Total      111 / 111
```

## Route capability remains unchanged

Phase 9 is a compatibility/deprecation phase, not a support expansion. It does not enable:

- H3 GGUF LoRA;
- LTX speed/Turbo;
- LTX GGUF or extended-mode LoRA;
- WAN UNET speed LoRA;
- WAN UNET high/low targeting;
- WAN Rapid AIO LoRA;
- imported native-workflow LoRA patching.

Those boundaries remain governed by the exact-route support matrix.
