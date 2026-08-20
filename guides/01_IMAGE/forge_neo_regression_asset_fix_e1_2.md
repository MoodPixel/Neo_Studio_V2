---
guide_id: image.forge_neo_hires_controlnet_recovery
title: Forge Neo High-Res and ControlNet Recovery
surface: image
scope: built_in
applies_to:
  - image
  - forge_neo
  - high_res_lab
  - controlnet
tags:
  - forge
  - high-res
  - controlnet
  - troubleshooting
priority: 94
version: 2
updated: 2026-08-16
---

# Forge Neo High-Res and ControlNet Recovery

Use this guide when Forge generation unexpectedly enables High-Res processing, Forge High-Res fails during the second pass, or a generated ControlNet map is not reaching Forge correctly.

## High-Res Lab turns on unexpectedly

High-Res Lab should remain off unless you explicitly enable it for the current workflow. Changing Forge profiles should not silently enable the pass.

If an older saved draft behaves differently:

1. open **High-Res Lab**;
2. confirm the main enable switch is off;
3. save/replay the workflow again after selecting the intended Forge profile.

## Forge High-Res second-pass errors

Neo keeps Forge's safe "reuse current checkpoint/module choices" behavior for the Hires pass. If a Hires request still fails:

1. confirm the selected Forge profile is connected;
2. refresh Forge Admin after updating Forge or models;
3. verify the requested upscaler and target size are available;
4. if you are using selected-output native Hires, confirm the compatible Forge Bridge is active;
5. retry from a fresh output rather than an old draft that references removed models/modules.

## ControlNet generated map handoff

A ControlNet map generated inside Neo can be reused directly by the selected Forge workflow. You should not have to create a matching local file manually just to pass the preview map to Forge.

If the map preview exists but generation says the ControlNet image is missing:

1. regenerate or reselect the map in the current session;
2. confirm the ControlNet unit is enabled;
3. refresh the Forge profile if ControlNet was installed or updated recently;
4. check that the selected Forge route exposes the required ControlNet script/preprocessor;
5. stage the reference again and generate.

For general Forge readiness troubleshooting, see `forge_neo_validation_and_regression.md`.
