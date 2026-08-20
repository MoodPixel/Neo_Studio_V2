---
guide_id: image.provider_action_release_integration
title: Upgrading Provider-Aware Image Integrations
surface: image
scope: built_in
applies_to:
  - image_preview
  - output_inspector
  - provider_routing
  - forge_neo
  - comfyui
  - migration
  - rollback
tags:
  - image
  - provider
  - upgrade
  - integration
  - forge
  - comfyui
  - bridge
  - migration
priority: 120
version: 2
updated: 2026-08-16
---

# Upgrading Provider-Aware Image Integrations

Provider-aware Preview and Output Inspector actions always use the **currently selected Image profile**. When Neo, Forge, ComfyUI, or a provider-side extension changes, refresh the affected profile before relying on old action availability.

## Before an upgrade

Keep a backup of the local state you care about, especially:

- your current Neo Studio installation/source snapshot;
- `neo_data/` if you want to preserve Neo settings, project/runtime metadata, and local history;
- custom backend launch files or environment variables;
- the current Forge Bridge extension folder when you use the Bridge.

## Recommended upgrade order

1. Stop Neo Studio.
2. Stop Forge Neo and ComfyUI.
3. Update Neo Studio.
4. If you use the bundled Forge Bridge, reinstall/update it from the Neo repository root:

```text
python neo_integrations/forge_neo_bridge/install_bridge.py --forge-root <FORGE_ROOT> --replace
```

5. Restart Forge with API access enabled (`--api`).
6. Start ComfyUI when you need ComfyUI routes.
7. Start Neo Studio.
8. Open **Admin → Backends → Image**.
9. Refresh/Test the Forge and/or ComfyUI profiles you actually use.
10. Return to Image and review any route or action that Neo marks unavailable or in need of revalidation.

## Forge Bridge notes

Standard Forge generation can work without the Bridge depending on the profile mode. Some native Forge operations, including selected-output native post-Hires support, require a compatible Bridge.

After updating the Bridge:

1. restart Forge fully;
2. refresh the Forge profile in Neo Admin;
3. confirm the profile reports the Bridge as connected/selected when expected;
4. check that the action is enabled in Preview or Output Inspector.

Do not judge compatibility from a copied Bridge version string alone; Neo uses the capabilities actually reported by the running Forge profile.

## ComfyUI updates

After updating ComfyUI or custom nodes:

1. restart ComfyUI;
2. refresh/Test the selected ComfyUI profile;
3. allow Neo to rebuild its live node/capability view;
4. revisit the Image workflow and reselect any route that became unavailable.

A missing custom node should disable only the dependent workflow rather than forcing Neo to invent a fallback.

## If an action worked before the upgrade but is now unavailable

Check:

- selected provider/profile;
- backend connection state;
- model/module availability;
- Forge Bridge status for Bridge-owned operations;
- required Forge scripts or ComfyUI custom nodes;
- Output Inspector/tooltip disabled reason;
- whether the original result references a profile or model that was renamed or removed.

## Rollback

When rolling back a Neo release that also changed the Forge Bridge, restore Neo and the Bridge as a compatible pair:

1. stop Neo and Forge;
2. restore the earlier Neo version;
3. restore or reinstall the Bridge version bundled with that version when applicable;
4. restart Forge with `--api`;
5. start Neo and refresh the Forge profile;
6. leave Bridge-owned actions disabled if the restored profile does not report the required capability.

Do not delete `neo_data/` as a rollback shortcut. Restore or migrate local state deliberately.

## Related user guides

- `forge_neo_complete_support.md`
- `forge_neo_optional_bridge.md`
- `provider_action_regression_matrix.md`
- `provider_aware_preview_diagnostics.md`
- `image_action_state_replay_lineage.md`
- `../07_ADMIN/forge_neo_admin.md`
- `../00_GLOBAL/public_path_hygiene.md`
