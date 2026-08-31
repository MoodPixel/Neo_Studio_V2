# Video LoRA Stack — Phases 1–5 System Record

**Recorded:** 2026-08-31  
**Surface:** Video  
**Extension:** `video.lora_stack`  
**Status:** Phase 5 MiniMax H3 UNET integration active; LTX/WAN runtime onboarding remains later work.

> Repository note: the current GitHub branch and `main` did not contain a tracked `neo_system_records` directory when Phase 5 began, although `guides/02_VIDEO/README.md` references `neo_system_records/06_SURFACES/video/` as the historical-record location. This file starts a tracked Video LoRA record using only verified repository work from Phases 1–5; it does not overwrite or reconstruct any unseen local/gitignored records.

## Architecture lock

The Video LoRA system follows these invariants:

1. **Compiler owns patch location.** The LoRA extension must not invent graph node ids.
2. **Extension owns requested LoRAs.** LoRA selection/strength/role/target are portable payload data.
3. **Compatibility is exact-route and fail-closed.** Family-level matching never grants support to an unvalidated loader or generation type.
4. **Standard and speed/Turbo LoRAs use one engine.** Turbo is a role/metadata distinction, not a separate graph subsystem.
5. **Manual selection is not classifier-gated.** Filename heuristics may recommend speed LoRAs but cannot reject an explicitly selected normal LoRA.
6. **Model-only and model+CLIP loaders are distinct contracts.** MiniMax H3 currently requires `LoraLoaderModelOnly`; generic `LoraLoader` fallback is forbidden.
7. **WAN branch targeting exists only where the compiler exposes branches.** `all/high/low` is valid for the verified WAN dual-noise route, not globally.
8. **Empty/disabled stack is a no-op.** No LoRA rows means no LoRA graph mutation.

## Phase 1 — Extension identity and mount

Established:

- built-in extension id: `video.lora_stack`;
- Video Assets mount: `video.assets.lora_stack`;
- initial extension manifest under `neo_extensions/built_in/video.lora_stack/`;
- no runtime patching, catalog discovery, or UI behavior.

Primary files:

- `neo_extensions/built_in/video.lora_stack/extension_manifest.json`
- `neo_app/surfaces/surface_manifest.json`

## Phase 2 — Universal payload

Added a portable payload contract with up to 12 rows.

Canonical row:

```json
{
  "uid": "video_lora_1",
  "enabled": true,
  "name": "example.safetensors",
  "strength_model": 1.0,
  "role": "standard",
  "target": "all"
}
```

Supported normalized roles:

- `standard`
- `speed`

Turbo/LightX2V/Lightning/distilled aliases normalize to `speed`.

Supported normalized targets:

- `all`
- `high`
- `low`

`both` normalizes to `all`; `high_noise`/`low_noise` normalize to `high`/`low`.

Primary file:

- `neo_extensions/built_in/video.lora_stack/backend/payload_schema.py`

## Phase 3 — Exact-route support matrix

Added a fail-closed Video LoRA support authority.

### MiniMax H3

Supported for standard + speed LoRAs:

- `minimax_h3.unet.txt2vid`
- `minimax_h3.unet.img2vid`
- `minimax_h3.unet.first_last_frame`
- `minimax_h3.unet.reference_to_video`
- `minimax_h3.unet.vid2vid`

H3 GGUF LoRA routes remain provisional/fail-closed until exact GGUF LoRA-loader compatibility is validated.

### WAN 2.2

Verified:

- `wan22.gguf.img2vid_14b_dual_noise`
- model-only multi-branch LoRA topology
- targets `all/high/low`

WAN UNET remains provisional for the universal stack; Rapid AIO/native-workflow LoRA injection remains blocked until explicit compiler support exists.

### LTX 2.3

Verified matrix entries:

- `ltx23.unet.txt2vid` — standard LoRA
- `ltx23.unet.img2vid` — standard LoRA

Runtime onboarding is not yet active; other LTX modes/GGUF remain provisional.

Primary files:

- `neo_extensions/built_in/video.lora_stack/backend/support_matrix.py`
- `neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json`

## Phase 4 — Compiler-owned patch profiles

Added schema `neo.video.lora_patch_profile.v1`.

A compiler profile declares:

- route id;
- compiler owner;
- loader compatibility class;
- exact upstream model ref;
- exact consumer node/input pairs;
- optional high/low branch mapping;
- whether the topology is validated.

The extension-side validator rejects:

- missing/stale model refs;
- consumer refs that no longer match the compiler declaration;
- route/loader mismatches;
- generic `LoraLoader` fallback where model-only is required;
- invalid branch targets.

Primary files:

- `neo_app/video/lora_patch_profiles.py`
- `neo_extensions/built_in/video.lora_stack/backend/patch_profile.py`

## Phase 5 — MiniMax H3 reference implementation

MiniMax H3 UNET is the first runtime-onboarded family.

### Unified H3 graph order

The compiler-owned anchor is the model input immediately before `MiniMaxH3SigmaShift`.

```text
H3 model loader
  -> standard Video LoRA(s)
  -> speed/Turbo Video LoRA(s)
  -> MiniMaxH3SigmaShift
  -> optional Sage / Spectrum / T8 BlockCache
  -> H3 scheduler / guider / sampler
```

Speed rows are applied after standard rows while preserving order within each role.

### Legacy Turbo bridge

Existing request fields remain accepted:

- `h3_turbo_enabled`
- `h3_turbo_lora`
- `h3_turbo_strength`

They are converted into one synthetic `role: speed`, `target: all` row before graph mutation. If the same LoRA already exists in the universal stack, the legacy row is suppressed so the model is not double-patched.

### Turbo/Speed discovery fix

The old discovery rule required a filename containing literal `h3`. Phase 5 replaces that with family aliases plus speed tokens.

Recognized H3-family aliases include:

- `h3`
- `minimax`
- `minimax_h3` / `minimax-h3`
- `hailuo`

Recognized speed tokens include:

- `turbo`
- `lightx2v`
- `lightning`
- `4step` / `4steps`
- `8step` / `8steps`
- `distilled`

This allows candidates such as:

```text
MiniMax-LightX2V-4steps.safetensors
```

Classification is recommendation-only. Explicitly selected LoRAs are not rejected because their filename lacks a speed token.

### Loader safety

H3 LoRA discovery and runtime application now require:

```text
LoraLoaderModelOnly
```

A backend exposing only generic `LoraLoader` is not treated as compatible.

### H3 GGUF boundary

H3 GGUF generation may remain available as an experimental base route, but Video LoRA/Turbo application stays fail-closed until its exact LoRA loader/model topology is physically validated.

### Phase-5 implementation files

- `neo_app/video/video_lora_runtime.py`
- `neo_app/video/minimax_h3_lora_integration.py`
- `neo_app/video/__init__.py`
- `neo_extensions/built_in/video.lora_stack/extension_manifest.json`

The Phase-5 integration adapter is installed idempotently from the Video package. It keeps the existing H3 compiler as graph authority, replaces H3 LoRA discovery with model-only-safe discovery, publishes the compiler-owned patch profile, bridges legacy Turbo state, and applies the universal stack before the sigma-shift consumer.

## Validation completed through Phase 5

Smoke validation covers:

- `MiniMax-LightX2V-4steps.safetensors` classification;
- normal LoRA followed by speed LoRA ordering;
- legacy Turbo duplicate suppression;
- exact compiler consumer rewiring;
- H3 GGUF fail-closed behavior;
- missing `LoraLoaderModelOnly` fail-closed behavior;
- compiler/model-discovery integration adapter behavior.

Full five-mode workflow regression is intentionally reserved for Phase 6.

## Next phase

**Phase 6 — MiniMax H3 LoRA validation / regression gate**

Required gate:

- no LoRA graph equivalence;
- standard LoRA;
- multiple standard LoRAs;
- speed/Turbo only;
- standard + speed ordering;
- legacy Turbo bridge and duplicate suppression;
- all five H3 generation modes;
- missing LoRA / missing loader failures;
- H3 GGUF refusal;
- compiled workflow JSON/profile validation;
- Img2Vid Turbo regression specifically.

Do not move to LTX runtime onboarding until this gate is clean.
