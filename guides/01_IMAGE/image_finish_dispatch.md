---
guide_id: image.finish_dispatch
title: Provider-Owned Finish Dispatch
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - image_results
  - preview
  - output_inspector
  - high_res_lab
  - adetailer
  - identity_rescue
  - image_upscale
tags:
  - image
  - finish
  - provider routing
  - derived output
  - output lineage
  - forge
  - comfyui
priority: 113
version: 1
updated: 2026-08-02
---

# Provider-Owned Finish Dispatch

Preview and Output Inspector use one Finish dispatcher for:

- High-Res Lab;
- ADetailer;
- Identity Rescue / FaceID;
- Image Upscale.

The selected Image backend profile owns a local Finish action. Neo does not temporarily switch Forge to Comfy or search another local profile to make an action run.

## Derived-action contract

Every executable Finish action stages:

```text
neo.image.derived_action.v2
```

The contract records:

- canonical action ID and label;
- selected finishing provider and profile;
- provider-owned dispatch and execution mode;
- source output/job/file lineage;
- parent output and parent job;
- whether the operation is cross-provider;
- the `append_derived` save/output lane;
- `automatic_provider_fallback: false`.

The backend revalidates the contract before provider compilation. Provider, profile, dispatch, runtime mode, source, or cross-provider mismatches fail closed with HTTP `409`.

## Dispatch types

| Dispatch type | Ownership | Current use |
|---|---|---|
| `run_comfy_derived` | Selected Comfy profile | Existing Comfy High-Res Lab, ADetailer, and Identity Rescue derived passes where their route is ready. |
| `run_provider_img2img_derived` | Selected provider | Reserved provider-owned diffusion repair path, including later Forge ADetailer/FaceID support. |
| `run_forge_native_hires` | Selected Forge profile | Native selected-image Forge Hires. Declared now; executable in Phase 6 after the versioned Bridge capability exists. |
| `run_provider_upscale` | Selected provider's standalone upscale boundary | Current Image Upscale queue path. |
| `run_provider_extras` | Selected Forge profile | Active Forge Extras image upscale through `/sdapi/v1/extra-single-image`; selected-profile only. |
| `explicit_cross_provider_bridge` | User-selected finishing profile | Cloud or other-provider output sent to a different local provider only after an explicit profile choice. |

Capability discovery and dispatch readiness are separate. A Forge capability can be physically present while its action remains disabled until Neo's matching provider executor is implemented.

## Preview and Output Inspector parity

Both surfaces call the same `previewActionDispatchFinish(...)` browser boundary. They use the same canonical action registry, selected-profile evaluation, source normalization, contract builder, execution branch, and output-lineage policy.

No separate Output Inspector implementation may reintroduce provider switching or a Comfy-only shortcut.

## Image Upscale boundary

Image Upscale remains a standalone utility rather than normal image generation. Its queue accepts the same derived-action contract, validates it at the upscale API boundary, and preserves it in job settings and output metadata.

The normal image generation endpoint rejects upscale dispatch types. The standalone upscale endpoint rejects ordinary generation-backed Finish types. This prevents a Finish action from reaching the wrong backend route.

## Cross-provider finishing

Cross-provider finishing is allowed only when the user explicitly chooses the finishing profile. The profile-list endpoint is:

```text
GET /api/image/finish-bridge/profiles
```

It reports compatible candidates but sets:

```text
automatic_selection: false
selection_policy: explicit_user_selection_only
```

The old Comfy-named endpoint remains only as a deprecated compatibility alias. New code and metadata must not use `neo.image.post_output_comfy_bridge.v1`.

## Output lineage and replay

A derived output carries:

```text
parent_output_id
parent_job_id
source_output_id
source_job_id
action_id
provider_id
profile_id
dispatch_type
save_lane: append_derived
```

Readers prefer `_neo_derived_action`. `_neo_preview_action` is retained temporarily as a compatibility copy for older output/replay readers. `_post_output_bridge` is removed during normalization.

## Provider-owned Finish implementation milestones

Phase 5 established dispatch ownership, validation, and lineage. The Forge executors are now implemented in their provider-owned phases:

- native Forge post-Hires: Phase 6;
- Forge ADetailer and Identity Rescue: Phase 7;
- Forge Extras Image Upscale: Phase 10.

Each action remains capability-gated against the selected profile. Missing runtime capabilities disable that action with a specific reason; they must not fall back to Comfy.

## Phase 6 native Forge Hires activation

`run_forge_native_hires` is now executable. The browser materializes the selected output into Neo-owned storage, creates `neo.image.derived_action.v2`, forces only the **txt2img runtime boundary** (`generate`), and queues normal Image generation without changing the selected profile.

The Forge compiler emits:

```text
operation: native_txt2img_upscale
endpoint: <empty>
```

Only `ForgeNeoBridgeJobManager` accepts that operation. Standard SDAPI managers reject it. The Bridge job runner decodes the selected image, creates `StableDiffusionProcessingTxt2Img`, sets `firstpass_image`, `enable_hr`, and `txt2img_upscale`, then runs Forge's native Hires diffusion pass through the durable Bridge lifecycle.

High-Res Fix and Image Upscale remain separate dispatches. Forge Extras is not a fallback for native Hires.

## Phase 7 Forge derived Img2Img activation

`run_provider_img2img_derived` is now executable for Forge ADetailer and Identity Rescue.

The Forge compiler validates the derived contract, restores recorded source dimensions, forces `batch_size=1` and `n_iter=1`, applies conservative outer denoise limits, and requires the action-specific extension compiler to report a real enabled pass:

```text
ADetailer        → forge_adetailer_finish → ADetailer always-on script
Identity Rescue  → forge_faceid_finish    → ControlNet FaceID unit
```

The generic Finish dispatcher cleans temporary derived-action state after queueing and verifies that the selected backend profile did not change. Extension settings may remain for intentional reuse, but stale provider-dispatch contracts do not leak into later generations.



## Phase 10 Forge Extras dispatcher completion

`run_provider_extras` now dispatches the selected output to the existing Image Upscale extension and queues one Forge Extras job per source.

The boundary validates:

- selected provider/profile ownership before and after queue submission;
- `neo.image.derived_action.v2` provider/profile match;
- selected-profile upscaler membership;
- optional secondary upscaler membership;
- face-restorer availability reported by the selected Forge profile;
- separation from native Forge Hires.

There is no profile search, automatic provider selection, or Comfy fallback.

## Phase 11 lifecycle and lineage

All Finish routes now share terminal action-state cleanup. Success, failure, cancellation, and recovery detachment clear the derived-action contract, provider upload aliases, and staged Finish source. The completed output keeps durable lineage instead:

- selected output = immediate parent;
- first output in the chain = root;
- every previous output = ordered ancestor;
- provider/profile and dispatch type remain recorded.

Preview and Output Inspector use the same dispatcher and lineage contract.

## Phase 12 Finish-route clarity

The shared Preview/Output Inspector toolbar distinguishes Finish actions by execution class:

- High-Res Fix — diffusion second pass;
- ADetailer — automatic repair pass;
- Identity Rescue — identity-guided repair pass;
- Image Upscale — pixel/post-processing pass.

Guided mode shows the class. Expert mode shows the exact selected-provider route, including Forge Bridge, Forge always-on script, Forge Integrated ControlNet FaceID, Forge Extras, or the equivalent Comfy workflow route.

A disabled action displays the selected-profile reason. Neo does not recommend or silently choose another provider from this diagnostic surface.

## Hotfix 07 — Native Forge Hires size contract

The Forge native Hires dispatcher now carries `neo.image.native_hires_size.v1` from the selected output into the compiler. The compiler keeps scale mode free of stale `hr_resize_x` / `hr_resize_y` values and records the expected target. Bridge 1.2.1 resolves the authoritative source dimensions from the decoded image, forces the exact target into `StableDiffusionProcessingTxt2Img`, and verifies the returned primary image size before completing the durable job.

A same-resolution result is treated as a failed native Hires job rather than being appended as a successful derived output.
