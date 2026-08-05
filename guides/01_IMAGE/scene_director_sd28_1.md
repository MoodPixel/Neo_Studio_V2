---

> **SD-28.8 documentation note:** This is a phase-history document. For the current released Scene Director architecture, support matrix, proof semantics, and apply order, use `guides/01_IMAGE/scene_director_current.md`. Historical gates/statuses below describe this phase at the time it shipped and must not override the current guide.

guide_id: image.scene_director.sd28_1
title: Scene Director — SD-28.1 Execution Architecture
surface: image
scope: built_in
applies_to:
  - scene_director
  - sdxl
  - sd15
  - krea2
  - krea2_turbo
  - flux2_klein
  - z_image
  - z_image_turbo
  - execution_strategy
  - compatibility_boundary
tags:
  - scene director
  - sd-28.1
  - classic v054
  - lightweight regional
  - regional lora
  - route gating
priority: 127
version: 1
updated: 2026-07-27
---

# Scene Director — SD-28.1 Execution Architecture

SD-28.1 introduces the execution-strategy boundary for Scene Director without changing generation behavior.

## Public node contract

`NeoSceneDirectorV054` remains the only exported Scene Director Comfy node. No `NeoSceneDirectorModern`, Krea-only, Klein-only, or Z-Image-only Scene Director node is introduced.

The existing V054 public input/output contract and saved-workflow compatibility remain authoritative.

## Execution engines

| Family | Engine | SD-28.1 route state | Runtime mutation in SD-28.1 | Heavy SD repair policy |
|---|---|---|---|---|
| SDXL checkpoint | `classic_v054` | Available | Existing behavior only | Existing V054 policy preserved |
| SD1.5 checkpoint | `classic_v054` | Experimental available | Existing behavior only | Existing V054 policy preserved |
| Krea 2 RAW | `lightweight_regional` | Planned gated | No | Disabled by architecture |
| Krea 2 Turbo | `lightweight_regional` | Planned gated | No | Disabled by architecture |
| FLUX.2 Klein | `lightweight_regional` | Planned gated | No | Disabled by architecture |
| Z-Image | `lightweight_regional` | Planned gated | No | Disabled by architecture |
| Z-Image Turbo | `lightweight_regional` | Planned gated | No | Disabled by architecture |

Modern families are now **recognized**, not runtime-supported. A planned-gated modern route may preserve Scene Director configuration/replay intent, but it must not emit a Scene Director workflow patch until its family implementation phase is validated.

## Modern lightweight contract

The future lightweight route is locked to these architectural rules:

- one public Scene Director node contract;
- no fallback from a modern family into `classic_v054`;
- no automatic Character Lock rescue, midpoint repair, end refinement, background repaint, or masked LoRA finish pass;
- future regional prompts use masked conditioning rather than the SDXL V054 attention stack;
- future regional LoRA support requires a family-specific masked model-delta runtime proof;
- Krea 2 Turbo and Z-Image Turbo must preserve their native low-step profile;
- the future modern route requires one base sampler unless a later explicit user-owned feature says otherwise.

## Loader recognition

For the SD-28.1 lightweight boundary, modern families recognize Neo's `diffusion_model` and `gguf` loader routes. This does **not** mean their Scene Director runtime is active yet; both remain planned-gated until validated.

Checkpoint routes remain the classic V054 execution path for SDXL/SD1.5 only.

## Regional LoRA honesty rule

The UI binding `LoRA row → Scene Director region` is not enough to claim hard isolation. A modern family can only be marked regional-LoRA supported after runtime proof confirms:

- LoRA loaded;
- family compatibility matched;
- region mask bound;
- masked-delta hook active;
- delta evaluated and non-zero;
- no global model mutation;
- exactly one sampler for the lightweight run.

Until then, the modern regional-LoRA state is `blocked_until_native_masked_delta_runtime_proof`.

## Phase boundary

SD-28.1 does **not** implement masked conditioning or model-delta hooks. Those are intentionally deferred to the family/runtime phases. This phase only establishes routing, gating, contracts, and regression protection so later work cannot accidentally mutate SDXL behavior or fake modern support.
