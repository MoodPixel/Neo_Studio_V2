---
guide_id: comfy_spaces.dependency_health
title: Comfy Spaces Dependency Health
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - dependencies
  - health
  - models
  - custom-nodes
  - runtime
priority: 78
version: 1
updated: 2026-10-07
---

# Comfy Spaces Dependency Health

Published Comfy Spaces can keep a persistent compatibility snapshot for each configured ComfyUI runtime. This lets Neo show likely readiness **before** you click Generate while still performing a fresh live validation when a run actually starts.

## Health is evidence, not execution authority

A green health badge is not permission to skip runtime validation.

```text
Saved health snapshot
      ↓
Early UI readiness / troubleshooting

Generate
      ↓
Fresh selected-runtime /object_info check
      ↓
Actual per-run graph validation
      ↓
Queue only if live preflight succeeds
```

A runtime may change after a health check, so Neo always rechecks the selected runtime at queue time.

## Health states

A Space/runtime pair may show:

- **Ready** — the published base workflow matched the runtime when health was last refreshed;
- **Blocked** — one or more required nodes/models were known missing;
- **Degraded / stale** — the saved evidence is older than the current health window or incomplete;
- **Unreachable** — Neo could not read the selected ComfyUI runtime's `/object_info`;
- **Not checked** — no saved health snapshot exists yet.

A stale or missing snapshot does not stop you from opening the Space. Use **Refresh Health** or Validate/Generate to obtain current evidence.

## Runtime selector health

When more than one Comfy runtime is configured, the runtime selector can show the saved dependency status for each profile, such as:

```text
Local Comfy          · dependencies ready
Video Comfy          · dependencies blocked
Remote Comfy         · ready · stale
Experimental Comfy   · health unchecked
```

Neo still executes against exactly one runtime selected for that run. There is no silent runtime fallback.

## Dependency Health panel

The runner's **Dependency Health** panel can show:

- base-workflow readiness;
- missing node-class count;
- missing model count;
- last checked time;
- a warning when the runtime node/model inventory changed since the previous health check;
- missing node names;
- missing model details;
- package/module hints when Neo has them;
- per-stage readiness;
- reviewed Graph Add-on readiness.

Click **Refresh Health** to explicitly re-read the selected runtime and replace the saved snapshot.

## Missing custom nodes

Neo may show a package/module hint for a missing or required custom node. Treat this as troubleshooting evidence, not an automatic installer command. Depending on the node pack, Comfy's Python-module name may not be identical to the repository or package-manager name.

Neo never installs custom nodes, Python packages, or model weights automatically from a Space health report.

## Missing models

Model requirements are checked against the selected runtime's live loader catalogs. Neo may reconcile safe portable path differences when there is one unambiguous match, but ambiguous names remain blocked rather than being guessed.

If a model is reported missing, install/copy it into the appropriate ComfyUI model location and refresh/restart Comfy as required, then click **Refresh Health** again.

## Stage readiness

For a staged Space, Neo checks each stage against the part of the workflow needed to reach that stage's declared outputs. This can help identify that, for example, Base Generation is runnable while an optional Video stage needs additional nodes/models.

Stage health is diagnostic only. **Run Stage / Continue / Run All** still use the normal live execution preflight.

## Graph Add-on readiness

Reviewed Graph Add-ons are checked separately from the immutable base workflow. For example, ControlNet readiness depends on:

- confirmed conditioning Graph Anchors in the published Space;
- compatible ControlNet loader/apply/image-loader nodes on the selected runtime;
- at least one ControlNet model in the live runtime catalog.

An unavailable optional Graph Add-on does not by itself make the base Space invalid.

## Stale or changed runtime

Health snapshots age into stale evidence after the health window. Neo also fingerprints dependency-relevant runtime state so it can warn when node schemas or model catalogs materially changed between refreshes.

This is useful after:

- installing/updating Comfy custom nodes;
- adding/removing models;
- switching Comfy environments;
- updating a node pack that changes socket definitions.

## Corrupt local health data

Health snapshots live in Neo runtime data, not inside the immutable published Space package. If a local health cache entry becomes invalid, Neo treats it as **not checked / stale** and lets the published Space continue to open. Refreshing health rebuilds the evidence.

## What health does not do

Dependency Health does not:

- install anything;
- mutate the published workflow;
- guarantee a future run will succeed;
- replace queue-time validation;
- silently switch to another runtime;
- migrate a Space to a newer workflow version.

It is a persistent troubleshooting/readiness layer over the existing fail-closed execution contract.


## Health after a workflow version change

Dependency Health is scoped to an exact Space version and runtime. After publishing or activating another version, refresh health for that version as needed. Old health evidence does not migrate or authorize the replacement workflow; Generate still performs the normal live queue-time preflight.
