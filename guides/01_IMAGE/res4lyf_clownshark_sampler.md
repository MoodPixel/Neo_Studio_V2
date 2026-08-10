---
title: RES4LYF ClownsharKSampler
surface: image
section: parameters
status: experimental
updated: 2026-08-07
---

# RES4LYF ClownsharKSampler

Neo can use RES4LYF's **ClownsharKSampler** as an alternative sampling engine for graph-compatible local ComfyUI / ComfyUI Portable Image routes.

This is a sampler-engine choice, not a model family and not an extension card. It lives in **Image → Parameters → Sampler Engine** so the normal user-owned Steps, CFG, Sampler, Scheduler, Denoise, and Seed remain the primary controls.

## Required custom node

Install the official RES4LYF pack:

```text
https://github.com/ClownsharkBatwing/RES4LYF
```

Typical venv install from `ComfyUI/custom_nodes`:

```bash
git clone https://github.com/ClownsharkBatwing/RES4LYF/
cd RES4LYF
pip install -r requirements.txt
```

For ComfyUI Portable, use the portable Python/embedded pip instead of the system `pip`. Restart ComfyUI afterward and use Neo's **Connect/Test** so `/object_info` is refreshed.

`rgthree-comfy` is optional for RES4LYF's nested ComfyUI sampler menus. Neo does not require rgthree to execute ClownsharKSampler.

## Live node contract

Phase 5 discovers the actual backend through `/object_info` and currently recognizes:

- `ClownsharKSampler_Beta` — current RES4LYF class id; display name `ClownsharKSampler`;
- `ClownsharKSampler` — compatibility candidate for older installs.

Neo verifies that the live node declares the controls it needs before allowing execution. The current contract uses:

- `eta`;
- `sampler_name`;
- `scheduler`;
- `steps`;
- `steps_to_run`;
- `denoise`;
- `cfg`;
- `seed`;
- `sampler_mode`;
- `bongmath`;
- `model`;
- `positive`;
- `negative`;
- `latent_image`.

If RES4LYF is missing or its node signature changes incompatibly, Neo blocks the selected run instead of silently falling back to core `KSampler`.

## Phase 5 execution scope

Phase 5 deliberately uses ClownsharKSampler's **standard** sampling mode:

```text
sampler_mode = standard
steps_to_run = -1
```

Neo does not synthesize RES4LYF unsampling/resampling, external SIGMAS, guides, options groups, regional/temporal guides, or `ClownsharkChainsampler` in this phase. Those are separate workflow systems and must not be confused with Neo's independent Multi-KSampler refinement stages.

The additional simple ClownsharKSampler controls exposed by Neo are:

- **RES4LYF Eta**;
- **BongMath**.

These Phase 5 values are shared by all ClownsharKSampler stages in a run. Normal Stage-specific Steps/CFG/Sampler/Scheduler/Denoise/Seed controls remain independent.

## Multi-KSampler integration

When Multi-KSampler is enabled, each stage can independently choose:

```text
Use Stage 1
Standard KSampler
ClownsharKSampler · RES4LYF
```

This supports mixed graphs such as:

```text
KSampler → ClownsharKSampler → KSampler
```

or:

```text
ClownsharKSampler → ClownsharKSampler
```

Neo first builds the normal Stage 1/2/3 latent chain, then replaces only the stages explicitly assigned to the RES4LYF backend. Both core KSampler and ClownsharKSampler expose the generated LATENT as output index 0, so the existing downstream graph references remain stable.

## Compatibility policy

Neo does **not** silently whitelist or rewrite settings by model family. The execution rule is:

1. the selected Image route must compile to a Neo-owned core `KSampler` stage;
2. the live RES4LYF node must exist and expose the required signature;
3. the selected Sampler and Scheduler must exist in the live ClownsharKSampler catalog when the catalog is published;
4. otherwise the request fails closed.

RES4LYF upstream documents broad support/use cases including SD1.5, SDXL, Flux, HiDream, SD3.5, AuraFlow, Chroma and WAN. Other Neo families such as Krea 2, Qwen, or Z-Image may still be graph-compatible when they compile through core KSampler, but Neo treats those as experimental rather than claiming upstream validation.

LanPaint and Ideogram 4 route-native custom sampler graphs remain outside this Phase 5 replacement path.

## Parameter Truth and Integrity

Phase 1 Parameter Truth still applies. Selecting ClownsharKSampler must not replace explicit user Steps, CFG, Sampler, Scheduler, Denoise, or Seed values with recommendations.

Phase 2 Parameter Integrity now understands `sampler_backend`. The final Comfy graph reports `res4lyf_clownshark` when the primary sampler node is ClownsharKSampler, so a backend substitution can be detected before `/prompt` submission.

## Quality claim

Neo makes no guarantee that RES4LYF or a particular RES sampler will improve an image. Sampling behavior depends on model, scheduler, denoise, stage structure, and user settings. The feature is exposed for controlled experimentation.

## Upstream references

- RES4LYF: `https://github.com/ClownsharkBatwing/RES4LYF`
- Current node mapping: `beta/__init__.py`
- Current ClownsharKSampler schema: `beta/samplers.py`

## Phase 6 pipeline verification

RES4LYF replacement still happens after Neo builds the Multi-KSampler stage chain. Phase 6 then verifies the mixed physical graph, including any `LatentUpscaleBy` transition immediately before a ClownsharKSampler stage. This ensures backend replacement cannot silently change a stage's Steps, CFG, sampler, scheduler, denoise, seed, or latent input.

## Phase 7 — exact route compatibility

The Phase 7 family compatibility matrix is now the frontend and provider preflight authority for ClownsharKSampler. A route must both expose a compiler-owned core `KSampler` shape and pass live RES4LYF signature discovery.

LanPaint sampler graphs and Ideogram 4 custom-advanced graphs remain explicitly gated. Missing/incompatible `ClownsharKSampler_Beta` is reported as a dependency state rather than being replaced with Standard KSampler.
