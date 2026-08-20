---
guide_id: image.forge_neo_optional_bridge
title: Forge Neo Optional Bridge
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - runtime
  - admin
tags:
  - forge
  - forge-neo
  - bridge
  - jobs
  - recovery
priority: 96
version: 4
updated: 2026-08-03
---

# Forge Neo Optional Bridge

For the canonical current Forge setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`.

## Purpose

The Forge Neo Bridge is an optional Forge-side extension that gives Neo Studio a job-specific lifecycle API. It does not replace Forge Neo, the standard `/sdapi/v1/*` API, or Neo's provider compiler.

Without the Bridge, Neo continues to use its standard durable single-worker wrapper around Forge's synchronous API. With the Bridge, Forge owns a persistent backend job record that Neo can reattach to after a Neo restart.

## Install

From the Neo Studio repository root:

```bash
python neo_integrations/forge_neo_bridge/install_bridge.py --forge-root <FORGE_ROOT>
```

For an existing installation, add `--replace`. Restart Forge Neo with `--api`, then open:

```text
Admin → Backends → Image → Forge / Forge Neo → Refresh Forge Admin
```

Neo should report the Bridge as `connected` and `selected` when the profile uses `bridge_mode=auto`.

## Profile modes

| Mode | Behaviour |
|---|---|
| `auto` | Prefer the Bridge when a compatible handshake succeeds. Fall back to standard SDAPI when absent. |
| `standard` | Never use the Bridge, even when installed. |
| `required` | Block Forge execution unless a compatible Bridge handshake succeeds. |

The seeded Forge profile defaults to `auto`.

## Bridge endpoints

```text
GET  /neo-api/v1/handshake
GET  /neo-api/v1/capabilities
GET  /neo-api/v1/settings-schema
POST /neo-api/v1/jobs
GET  /neo-api/v1/jobs/{job_id}
POST /neo-api/v1/jobs/{job_id}/cancel
GET  /neo-api/v1/history
```

The Bridge supports the same verified Forge generation endpoints currently owned by Neo's compiler:

```text
/sdapi/v1/txt2img
/sdapi/v1/img2img
```

It does not unlock new model families, routes, or Image extensions.

## Live preview

Bridge jobs expose the latest Forge `current_image` frame through the same Neo provider-neutral preview endpoint used by standard SDAPI jobs. The browser polls that endpoint while the job is queued or running. Forge live previews must be enabled and `show_progress_every_n_steps` must be greater than zero; a missing frame is non-fatal.

## Recovery behaviour

When Neo restarts while Forge remains running:

- queued or running Bridge jobs can be reattached;
- completed Bridge results can be fetched and imported into Neo;
- Bridge history remains available from Neo Admin;
- result images are transferred to Neo only when requested and then persisted through Neo's normal output pipeline.

When Forge itself restarts during a running Bridge job, the Bridge marks that job failed and recoverable because the underlying synchronous generation call no longer exists.

## Security

When `NEO_FORGE_BRIDGE_TOKEN` is not configured, Bridge routes accept loopback clients only.

For remote or shared-network use, set the same environment variable before starting both applications:

```text
NEO_FORGE_BRIDGE_TOKEN=<random-secret>
```

Neo reads this through `connection.bridge_token_env`. The token value is never committed to public profile JSON or written into capability snapshots.

## Runtime data

Bridge state is stored under Forge's runtime data path:

```text
<data_path>/neo_forge_bridge/
```

An alternative runtime-only location may be provided through `NEO_FORGE_BRIDGE_DATA_DIR`.

## Fallback contract

Bridge failure in `auto` mode must never disable a healthy standard Forge SDAPI connection. Bridge failure in `required` mode must fail closed and block generation with an explicit diagnostic.

## Native post-Hires capability

Bridge 1.2.1 adds the native operation:

```text
native_txt2img_upscale
```

The capability response must advertise all three values:

```json
{
  "native_post_hires": true,
  "native_operations": ["native_txt2img_upscale"],
  "native_post_hires_size_contract": true
}
```

A Bridge job submission must provide exactly one of `endpoint` or `operation`. Native post-Hires requires `operation`; it cannot be submitted to standard SDAPI or auto-routed to another provider. Reinstall with `--replace`, restart Forge, and refresh the Forge Admin profile after upgrading the bundled Bridge.


## After upgrading the Bridge

1. Fully restart Forge.
2. Open **Admin → Backends → Image** in Neo.
3. Refresh/Test the Forge profile.
4. Confirm the Bridge is shown as connected/selected when the profile expects it.
5. Return to Image and verify that Bridge-owned actions are enabled only when their required capability is reported.

If an operation disappears after an upgrade, do not force it through standard SDAPI or another provider. Restore Neo and the Bridge as a compatible pair when rolling back. See `provider_action_release_integration.md` for the user upgrade/rollback flow.

