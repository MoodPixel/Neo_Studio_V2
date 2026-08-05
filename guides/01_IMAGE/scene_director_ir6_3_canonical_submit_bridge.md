# Scene Director IR-6.3 Canonical Submit Bridge

After installing the patch, restart Neo and hard-refresh the browser once. Enable Scene Director, assign each LoRA row to its intended region, and generate normally.

Expected output metadata:

- `params._neo_extension_state.extensions.image.scene_director.enabled = true`
- `extensions.payloads.image.scene_director.enabled = true`
- LoRA Stack keeps regional rows out of its global patch
- Scene Director adds the regional workflow patch

This phase changes submission ownership only. Extension Routing remains in its current UI location until the separate routing information-architecture phase.
