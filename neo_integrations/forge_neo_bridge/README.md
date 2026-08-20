# Neo Studio Forge Bridge

This optional Forge Neo extension adds job-specific durable lifecycle endpoints and provider-owned native operations for Neo Studio. Standard Forge SDAPI remains supported for compatible routes unless a Neo backend profile sets `bridge_mode` to `required`.

## Current compatibility

Provider-aware Image actions in this release require Bridge **1.2.1** for native selected-output High-Res Fix.

The refreshed capability response must advertise all three values:

```text
native_post_hires: true
native_operations includes native_txt2img_upscale
native_post_hires_size_contract: true
```

A legacy Bridge or any incomplete capability set does not enable native Hires.

## Install

Run from the Neo Studio repository root:

```bash
python neo_integrations/forge_neo_bridge/install_bridge.py --forge-root <FORGE_ROOT>
```

To upgrade or replace an existing installation:

```bash
python neo_integrations/forge_neo_bridge/install_bridge.py --forge-root <FORGE_ROOT> --replace
```

Then:

1. Restart Forge Neo with `--api`.
2. Start Neo Studio.
3. Open **Admin → Backends → Image**.
4. Select the intended Forge profile.
5. Run **Refresh Forge Admin**.
6. Confirm Bridge version 1.2.1 and the paired native Hires capability/operation.

Installing files without restarting Forge leaves the old in-memory extension active.

## Bridge modes

- `auto` — prefer a compatible Bridge and use standard SDAPI for compatible routes when the Bridge is absent.
- `standard` — disable Bridge use for this profile.
- `required` — block Bridge-owned execution when the selected Bridge is absent or incompatible.

Native selected-output Hires has no ordinary Img2Img, Extras, or Comfy fallback.

## Security

Without a token, Bridge routes accept loopback clients only. For remote or shared-network use, define the same environment variable before starting both Neo Studio and Forge:

```text
NEO_FORGE_BRIDGE_TOKEN=<RANDOM_SECRET>
```

The Neo Forge profile reads this variable through `connection.bridge_token_env`; the secret is never stored in the public profile JSON.

## Runtime data

Bridge job records and generated result spools are written under Forge's runtime data directory:

```text
<FORGE_DATA_PATH>/neo_forge_bridge/
```

Override this runtime-only location with `NEO_FORGE_BRIDGE_DATA_DIR` when necessary. Do not place Bridge runtime data inside the Neo source repository.

## Native post-generation Hires

Bridge 1.2.1 supports Neo Studio's Forge-owned Preview/Output Inspector High-Res action through `native_txt2img_upscale`.

The operation:

- uses the selected image as Forge's `firstpass_image`;
- forces `enable_hr` and `txt2img_upscale`;
- resolves the final target from the decoded source and selected scale or explicit target;
- forces the resolved target dimensions into Forge and verifies the returned PNG size;
- preserves compatible Hires settings;
- runs Forge's native Hires diffusion pass without regenerating the first pass;
- returns results through the durable Bridge job lifecycle.

It does not use ordinary Img2Img, Forge Extras, Gradio function indexes, or ComfyUI.

## After upgrading the Bridge

After replacing the Bridge:

1. Restart Forge completely.
2. Open **Admin → Backends → Image** in Neo Studio.
3. Refresh/Test the selected Forge profile.
4. Confirm the Bridge is selected by that profile when Bridge mode expects it.
5. Confirm native High-Res Fix becomes available only when the running Bridge reports `native_post_hires`, `native_txt2img_upscale`, and `native_post_hires_size_contract`.

If the action remains disabled, use Neo's profile/action diagnostics to identify the missing capability. Developer regression and release audits are intentionally kept out of the user installation guide.

## Rollback

Rollback Neo Studio and the Bridge as one compatibility set:

1. Stop Neo Studio and Forge.
2. Restore the earlier Neo source/release.
3. Restore or reinstall the Bridge version shipped with that release.
4. Restart Forge with `--api`.
5. Refresh the Forge Admin profile.
6. Keep native Hires disabled when the restored Bridge does not advertise the paired contract.

Do not delete Neo or Forge runtime data as a rollback shortcut.

## Troubleshooting

### Native High-Res Fix remains disabled

- Confirm `--replace` was used when upgrading an existing Bridge.
- Restart Forge after replacement.
- Refresh the exact selected Forge profile.
- Check both capability values; version text alone is insufficient.
- Confirm the profile is not set to `standard` Bridge mode.

### Bridge is connected but another action is unavailable

Bridge connectivity does not grant ADetailer, FaceID, ControlNet, or Extras support. Those actions depend on their own selected-profile scripts, models, preprocessors, detectors, upscalers, and route compatibility.

For the full operator procedure, see:

```text
guides/01_IMAGE/provider_action_release_integration.md
```
