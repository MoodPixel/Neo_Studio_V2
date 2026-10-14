---
guide_id: comfy_spaces.versioning_workflow_replacement
title: Comfy Spaces Versioning and Workflow Replacement
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - versions
  - workflow-replacement
  - migration
  - rollback
  - variants
priority: 79
version: 1
updated: 2026-10-08
---

# Comfy Spaces Versioning and Workflow Replacement

Published Comfy Spaces keep immutable version history. Updating the underlying ComfyUI workflow creates a **new version**; Neo does not overwrite an older published package.

## Replace a workflow safely

In **Admin → Comfy Space Builder**, use **Replace a published workflow** when the Comfy graph itself changed.

Choose:

- the published Space/version you are replacing;
- the new workflow JSON;
- a new semantic version such as `1.1.0`;
- an optional Comfy runtime for stronger live node-schema evidence.

Neo compares the old and new workflows using canonical graph structure and saved source lineage. It can migrate controls, outputs, confirmed Graph Anchors, stages, and dynamic-control references when there is one uniquely compatible target.

## Ambiguous mappings are never guessed

If two new nodes/inputs look equally compatible, Neo leaves the item **ambiguous/unmapped** instead of silently choosing one.

Review those items in Builder, make any manual authoring changes you want, save the draft, then use **Confirm migration review**. If you edit the draft after confirming, the confirmation becomes stale and must be performed again before publication.

## Immutable publication + migration audit

Publishing the replacement creates a new version folder and a signed migration audit. The audit records the source version, target version, graph diff summary, and reviewed migration summary. It does not execute the workflow and has no binding/execution authority.

Older published versions remain intact.

## Active version and rollback

A Space has one **active version** for normal library opening. Publishing a new version activates it. Admin can explicitly activate an older version as a rollback.

Rollback changes only Neo's active-version pointer:

```text
v1.0.0  immutable
v1.1.0  immutable
v1.2.0  immutable

active_version → v1.1.0
```

No published version is copied, rewritten, or deleted.

## Variants remain pinned

Existing Space Variants stay pinned to the exact version they were created for. Workflow replacement never silently retargets them.

The replacement review reports Variant compatibility. A compatible Variant can be **explicitly migrated**, which creates a new Variant pinned to the target version. The original Variant remains unchanged.

## Dependency Health after replacement

Dependency Health is version-specific. After publishing or activating another workflow version, refresh the relevant runtime health evidence. Generate still performs the normal fresh queue-time runtime validation.
