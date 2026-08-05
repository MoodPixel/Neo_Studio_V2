---
guide_id: image.provider_aware_preview_diagnostics
title: Provider-Aware Preview Actions and Diagnostics
surface: image
scope: built_in
applies_to:
  - image_preview
  - output_inspector
  - preview_actions
  - provider_routing
  - replay
  - output_lineage
  - extension_revalidation
tags:
  - image
  - preview
  - output inspector
  - provider
  - diagnostics
  - replay
  - lineage
priority: 118
version: 2
updated: 2026-08-03
---

# Provider-Aware Preview Actions and Diagnostics

The live Image Preview and saved Output Inspector use the same provider evaluation and compact action toolbar. The image overlay contains emoji buttons only; route and disabled details are available through tooltips. Detailed diagnostics are rendered outside the image in Output Inspector.

## Selected provider and profile

Availability is evaluated against the currently selected Image profile only. The compact overlay does not print provider/profile text over the image. Each action tooltip identifies the owning provider/profile and explains the action route or disabled reason.

Neo does not make a button available because another connected profile could run it. Cross-provider finishing requires an explicit profile selection. Output Inspector exposes the full provider/profile diagnostic panel outside the media area.

## Guided and Expert display modes

### Guided mode

Guided mode uses plain route names and short action badges:

| Action | Guided route |
|---|---|
| Img2Img / Inpaint / Outpaint | Source |
| ControlNet / IP Adapter | Reference |
| High-Res Fix | Diffusion |
| ADetailer | Repair |
| Identity Rescue | Identity |
| Image Upscale | Pixel |

Unavailable actions remain visible as dimmed emoji buttons when the selected provider supports the action class but is missing a model, extension, runtime connection, Bridge capability, detector, preprocessor, or upscaler. Hover/focus tooltips show the concise reason without expanding the overlay.

Provider-unsupported actions remain hidden in Guided mode to avoid presenting controls that cannot work on the selected backend.

### Expert mode

Expert mode adds the real execution route and blocked requirement names to tooltips and the collapsed Output Inspector diagnostic panel. Examples include:

- `Forge native txt2img_upscale via Neo Forge Bridge`;
- `Forge Img2Img plus ADetailer always-on script`;
- `Forge Img2Img plus Integrated ControlNet FaceID`;
- `Forge Extras /sdapi/v1/extra-single-image`;
- Comfy workflow/node routes for the same canonical actions.

The provider evaluation publishes `neo.image.preview_action_diagnostics.v1` with route labels, disabled-reason codes, and requirement checks.

## Clear Finish distinctions

The four Finish actions are intentionally separate:

- **High-Res Fix** performs a diffusion second pass.
- **ADetailer** performs automatic face/detail repair.
- **Identity Rescue** performs identity-guided repair using FaceID-capable routing.
- **Image Upscale** performs pixel/post-processing upscaling.

Image Upscale is not High-Res Fix, and Neo must not substitute one for the other.

## Replay binding notice

When a replay draft is loaded, Output Inspector shows that replay is bound to the recorded provider/profile. Action tooltips continue to identify the currently owning provider/profile. Neo does not silently replace a missing recorded profile.

An explicit Source-action provider override is shown as an explicit override rather than a recorded-profile replay.

## Restored extension revalidation

Replay can restore provider-neutral extension settings, but provider-specific extensions remain disabled until the selected provider revalidates their route, models, nodes/scripts, and source assets.

Output Inspector shows a **Revalidation required** notice with the affected extensions. The compact image overlay never expands to show this notice. It does not imply that the extension will auto-enable.

## Output lineage

Output Inspector shows `neo.image.output_lineage.v1` as a visible chain:

- immediate parent;
- root output;
- source output;
- derived depth;
- action and dispatch route;
- ordered ancestors.

Guided mode explains the chain in plain language. Expert mode shows IDs, jobs, provider/profile, action, and dispatch details.

## Compact overlay rule

The media overlay must contain only compact emoji action buttons. Do not render action names, route badges, provider headers, replay notices, revalidation notices, diagnostic cards, or debug text on top of an image. Keep those details in hover/focus tooltips or in the collapsed Output Inspector diagnostic panel.

## Shared-surface rule

Do not create separate Preview-only and Output-Inspector-only action evaluation. Both surfaces must call `renderPreviewActionToolbar(...)` and use the same selected-profile evaluation. Output Inspector may additionally call `renderOutputPreviewActionDiagnostics(...)` outside the media overlay.

## Hotfix 01 — ComfyUI Portable High-Res Fix icon

The compact Preview toolbar resolves the High-Res Fix action from the canonical `extension.high_res_lab` definition and keeps the `✨` icon provider-independent. `image.high_res_lab` must declare both `comfyui` and `comfyui_portable` as supported backends; otherwise a portable Comfy profile is evaluated as `provider_unsupported` and the action is hidden before the icon renderer runs.


## Hotfix 02 — Finish actions stay on one row

The compact overlay reserves one dedicated row for the four canonical Finish actions: High-Res Fix, ADetailer, Identity Rescue, and Image Upscale. The row uses four fixed compact icon tracks and does not wrap. Source, Reference, and LayerDiffuse icons may continue to wrap normally above it. This is a presentation-only rule; action order, visibility, provider evaluation, tooltips, and dispatch behavior are unchanged.
