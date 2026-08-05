---
guide_id: image.provider_action_release_integration
title: Provider-Aware Image Actions — Release and Integration Guide
surface: image
scope: built_in
applies_to:
  - image_preview
  - output_inspector
  - provider_routing
  - forge_neo
  - comfyui
  - release_validation
  - migration
  - rollback
tags:
  - image
  - provider
  - release
  - integration
  - forge
  - comfyui
  - bridge
  - migration
  - smoke-test
priority: 120
version: 1
updated: 2026-08-03
---

# Provider-Aware Image Actions — Release and Integration Guide

This guide is the release-facing contract for the provider-aware Preview and Output Inspector action system completed in Phases 1–13.

The core rule is unchanged:

> The selected Image profile owns every Source, Reference, and Finish action. Neo does not silently switch Forge work to ComfyUI or borrow another profile's capabilities.

## Release prerequisites

Before upgrading, keep copies of:

- the current Neo Studio source folder or release archive;
- the current Forge Bridge extension folder, when installed;
- local `neo_data/` runtime state using the user's normal backup method;
- any custom backend launch scripts or environment variables.

Do not copy `neo_data/` into a public source archive. It is runtime state, not release content.

## Upgrade order

Use this order so Neo never evaluates a new provider contract against an old Bridge installation.

1. Stop Neo Studio.
2. Stop Forge Neo and ComfyUI.
3. Replace or update the Neo Studio source files.
4. Update the Forge Bridge from the Neo repository root:

```text
python neo_integrations/forge_neo_bridge/install_bridge.py --forge-root <FORGE_ROOT> --replace
```

5. Restart Forge Neo with API access enabled:

```text
--api
```

6. Start ComfyUI when its routes are needed.
7. Start Neo Studio.
8. Open **Admin → Backends → Image**.
9. Select the intended Forge or ComfyUI profile and run its connection/refresh action.
10. Return to Image and confirm the provider/profile badge beside Preview actions.

## Required Forge Bridge contract

Forge generation can use standard SDAPI without the Bridge, depending on the profile's `bridge_mode`. Native selected-output High-Res Fix requires the updated Bridge contract.

Required release values:

```text
Bridge version: 1.2.1 or newer compatible version
native_post_hires: true
native_operations includes native_txt2img_upscale
native_post_hires_size_contract: true
```

All three capability values are required. A legacy Bridge or any incomplete native-Hires capability set fails closed.

After installing or replacing the Bridge:

1. Restart Forge Neo.
2. Run **Refresh Forge Admin**.
3. Confirm the selected profile reports the Bridge as selected and compatible.
4. Confirm **High-Res Fix** reports the native Forge route in Expert diagnostics.

## Complete action-routing reference

| Group | Action ID | Forge Neo route | ComfyUI route | Auto-run |
|---|---|---|---|---:|
| Source | `core.img2img` | Selected-profile Img2Img source staging | Selected-profile Img2Img source staging | No |
| Source | `core.inpaint` | Forge source staging + fresh mask editor | Comfy source staging + fresh mask editor | No |
| Source | `core.outpaint` | Forge source staging + Neo outpaint canvas | Comfy source staging + outpaint workspace | No |
| Reference | `extension.controlnet` | Forge Integrated ControlNet, first empty unit | Comfy ControlNet workflow, first empty unit | No |
| Reference | `extension.ip_adapter` | Forge Integrated ControlNet IP Adapter, shared unit pool | Comfy IP Adapter workflow | No |
| LayerDiffuse | `extension.layerdiffuse.source` | Unsupported and hidden | Named Source slot | No |
| LayerDiffuse | `extension.layerdiffuse.background` | Unsupported and hidden | Named Background slot | No |
| LayerDiffuse | `extension.layerdiffuse.foreground` | Unsupported and hidden | Named Foreground slot | No |
| LayerDiffuse | `extension.layerdiffuse.replace_target` | Unsupported and hidden | Named replace-target slot | No |
| Finish | `extension.high_res_lab` | Bridge `native_txt2img_upscale` diffusion pass | Comfy High-Res Lab workflow | Yes, after explicit click |
| Finish | `extension.adetailer` | Forge Img2Img + live ADetailer script | Comfy ADetailer workflow | Yes, after explicit click |
| Finish | `extension.identity_rescue` | Forge Img2Img + Integrated ControlNet FaceID | Comfy FaceID workflow | Yes, after explicit click |
| Finish | `extension.image_upscale` | Forge Extras `/sdapi/v1/extra-single-image` | Comfy model-upscale/SeedVR2 route | Yes, after explicit click |

Source and Reference actions only stage state. Finish actions execute only after the user explicitly clicks the corresponding action.

## Selected-profile diagnostics

The action toolbar shows the active provider and profile. Guided mode explains the route in plain language; Expert mode exposes dispatch, execution mode, requirements, and blocker checks.

Common blocker meanings:

| Diagnostic | Meaning | Corrective action |
|---|---|---|
| Profile missing/disabled | No eligible selected Image profile owns the action. | Select, enable, save, and test the intended profile. |
| Runtime offline | The selected backend cannot be reached. | Start the backend and verify host, port, auth, and API flags. |
| Bridge missing/incompatible | Native Forge Hires cannot use the selected Bridge. | Reinstall with `--replace`, restart Forge, and refresh Admin. |
| Route unavailable | The current family/loader/workflow is not mapped for this provider. | Select a supported route or use the backend's native UI. |
| Capability missing | Required script, model, preprocessor, detector, upscaler, or node is unavailable. | Install/configure it in the selected backend, then refresh. |
| Revalidation required | Replay restored canonical settings but disabled provider-specific execution. | Refresh the selected provider and review the extension before enabling it. |
| Explicit local profile required | A cloud output has no automatic local Finish owner. | Explicitly select a compatible local profile; Neo will not choose one. |

## Migration notes

### Forge Bridge 1.1 or older

Legacy Bridge installations remain usable only for operations they actually report. Native selected-output High-Res Fix stays disabled until Bridge 1.2.1 is installed, Forge is restarted, and Admin capabilities are refreshed.

### Older saved drafts and results

Replay sanitizes old temporary provider fields. It does not reactivate stale upload aliases, masks, Finish contracts, or Reference handoffs.

Provider-specific extension settings are restored as canonical settings but remain disabled pending selected-provider revalidation.

### Older LoRA rows

Legacy names, paths, file extensions, and prompt tags are normalized into provider-neutral LoRA identities. Forge compiles `<lora:name:strength>` at submission time; ComfyUI keeps workflow loader nodes.

### Older Embeddings/TI items

Legacy `embedding:` prefixes, weighted wrappers, extensions, and path variants are normalized into a plain canonical trigger. Forge uses plain trigger syntax; ComfyUI adds `embedding:` during workflow compilation.

### Legacy Comfy-only Finish bridge

The old automatic Comfy Finish profile switch is removed. Cloud and unsupported-provider outputs require an explicit visible local-profile decision.

## Physical Forge smoke-test checklist

Run this checklist on a real Forge installation after deterministic validation passes.

- [ ] Forge starts with `--api` and the selected profile reports Connected.
- [ ] Bridge reports version 1.2.1 and the complete native Hires capability/operation/size contract.
- [ ] Generate one SD 1.5 or SDXL output.
- [ ] Send it to Img2Img; verify Forge remains selected and generation does not start automatically.
- [ ] Send it to Inpaint; verify the previous mask is cleared.
- [ ] Send it to Outpaint; verify old canvas/padding state is cleared.
- [ ] Stage ControlNet into the first empty Forge unit.
- [ ] Stage IP Adapter without overwriting an occupied unit.
- [ ] Run native High-Res Fix and verify a derived output with parent/root lineage.
- [ ] Run ADetailer with a live detector and inspect the resulting detail repair.
- [ ] Run Identity Rescue with a compatible FaceID model/preprocessor and reference.
- [ ] Run Image Upscale through Forge Extras and verify the selected upscaler and dimensions.
- [ ] Cancel one running job and verify the UI returns to an idle state.
- [ ] Force one backend error and verify active-job/action state is cleared.
- [ ] Replay a derived result and verify the recorded profile binding and revalidation warnings.

Record GPU model, VRAM, Forge build, Bridge version, model names, route, duration, output dimensions, errors, and visual observations.

## Physical ComfyUI smoke-test checklist

- [ ] The selected ComfyUI profile reports Connected and object-info/workflow discovery is current.
- [ ] Source actions preserve the selected ComfyUI profile and do not auto-run.
- [ ] ControlNet and IP Adapter stage into valid workflow units/nodes.
- [ ] LayerDiffuse slot actions appear only where the workflow supports them.
- [ ] High-Res Lab executes through the Comfy-derived route.
- [ ] ADetailer and Identity Rescue execute through their Comfy workflows.
- [ ] Image Upscale preserves existing model-upscale and SeedVR2 behavior.
- [ ] Repeated Finish passes preserve immediate parent, root, ancestors, and exact depth.
- [ ] Cancellation, failure, replay, and provider changes clear temporary upload/action state.

## Deterministic release gates

Run from the repository root:

```text
python scripts/validate_forge_neo_phase6.py
python scripts/validate_provider_actions_phase13.py
python scripts/audit_provider_action_release_phase14.py
```

To save the Phase 14 machine-readable report:

```text
python scripts/audit_provider_action_release_phase14.py --json-out <OUTPUT_PATH>/provider_action_release_audit.json
```

The Phase 14 audit also builds and inspects a temporary clean public archive. It verifies package exclusions, release-facing documentation, Bridge integration, action inventory, no-fallback locks, obvious credential leaks, and portable path hygiene.

## Public repository and package hygiene

Public runtime archives must exclude:

- `neo_data/`;
- runtime databases, logs, generated outputs, and caches;
- `.env` and credentials;
- `.git`, bytecode, and test caches;
- local installed-extension workspaces;
- internal `neo_system_records/`, `scripts/`, and `tests/` from the public runtime export.

Use:

```text
python scripts/build_clean_release.py --output <OUTPUT_PATH>/Neo_Studio_V2_clean_release.zip
```

Absolute backend/model paths may exist only as clearly synthetic redaction fixtures in internal validation code. User-facing guides, defaults, browser payloads, release manifests, and runtime archives must use portable placeholders or redacted names.

## Known limitations

- Deterministic matrices do not prove GPU execution, image quality, VRAM stability, or third-party extension compatibility.
- Forge's standard API does not expose every native UI control.
- Forge LayerDiffuse Preview actions remain unsupported.
- Forge FaceID is limited to live-verified model/preprocessor pairs and supported SD 1.5/SDXL routes.
- Forge ADetailer requires a live script schema and detector coverage.
- Native Forge High-Res Fix requires the compatible Bridge; there is no Comfy fallback.
- Cloud outputs require an explicit local Finish profile decision.
- Replay cannot use a recorded profile that no longer exists until the user explicitly migrates the workflow.

## Rollback procedure

Rollback Neo and the Forge Bridge as one compatibility set.

1. Stop Neo Studio and Forge.
2. Restore the previous Neo source/release archive.
3. Restore or reinstall the Bridge version that belonged to that Neo release.
4. Restart Forge with `--api`.
5. Start Neo Studio and refresh the selected Forge Admin profile.
6. Keep native High-Res Fix disabled when the restored Bridge does not advertise the paired capability contract.
7. Run the validation scripts available in the restored release.

Do not delete `neo_data/` as a rollback shortcut. Preserve user state, and migrate or restore it using a backup when schema compatibility requires it.

## Related guides

- `forge_neo_complete_support.md`
- `forge_neo_optional_bridge.md`
- `provider_action_regression_matrix.md`
- `provider_aware_preview_diagnostics.md`
- `image_action_state_replay_lineage.md`
- `../07_ADMIN/forge_neo_admin.md`
- `../00_GLOBAL/public_path_hygiene.md`
