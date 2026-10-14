---
guide_id: comfy_spaces.live_node_registry_sync
title: Refresh Installed ComfyUI Nodes
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - custom-nodes
  - runtime
  - object-info
  - node-registry
priority: 75
version: 1
updated: 2026-10-14
---

# Refresh Installed ComfyUI Nodes

Go to **Admin → Comfy Spaces → Import**. Select the **ComfyUI runtime** containing your custom nodes and select your ComfyUI workflow JSON. Click **Refresh Installed Nodes** to compare the workflow's node classes with the classes currently loaded by that ComfyUI instance. You can also enter one node class name (for example `SeedVR2`) to check it explicitly.

This action is **read-only**. It queries that exact backend profile's live `/object_info` endpoint, counts loaded node classes, reports types missing from the selected workflow, and displays newly added/removed classes since the last local snapshot. It does not create or modify a workflow, load models, execute ComfyUI, install custom nodes, or attempt to run missing code.

## What to do when a node is missing

1. Confirm that Neo's selected runtime points to the ComfyUI installation where the workflow runs. Portable and Local are often separate installations.
2. In that ComfyUI installation, install/enable the custom-node pack that supplies the missing class, using the trusted source instructions.
3. **Restart ComfyUI**. Installation on disk is not proof that the node successfully loaded; dependency or startup errors can prevent registration.
4. Click **Refresh Installed Nodes** again. When the node class is present, retry **Import & create draft**.

The exact node class string must match. A display label, custom folder name, or package name is not interchangeable with the registered ComfyUI class type.

## Do we maintain handmade node schemas?

**No.** ComfyUI generates live `/object_info` definitions from its registered node classes. These provide input names, required/optional/hidden fields, types, widget defaults/ranges/choices, and output definitions. Neo uses these schemas to map older positional editor widget values into named inputs when the workflow itself does not carry named values.

A locally synchronized snapshot stores **only node class names, count, sync time, and a metadata fingerprint**, per runtime profile. It does not copy model-choice arrays, executable code, or full schemas. Cached data is diagnostic-only and is **not** a substitute for live schema authority. Import, preflight, graph validation, and Generate continue to query the actual chosen runtime. If a node type is missing in live `/object_info`, the snapshot cannot make it available.

Some ComfyUI front-end-only extension widgets or exotic custom nodes may need targeted compatibility work even when their class appears in `/object_info`; this is a mapping issue to report with the workflow and specific node, not a reason to guess input positions.

## Troubleshooting

- **Missing node class:** Wrong runtime, custom pack not loaded, dependencies failed, or node class was renamed. Check ComfyUI startup console and the selected runtime.
- **Can't reach ComfyUI:** Start that ComfyUI instance and check its backend profile URL. Neo will report a connection error and keep the earlier cached snapshot untouched.
- **Node exists in ComfyUI but absent in Neo:** Check `http://127.0.0.1:<your-ComfyUI-port>/object_info/CLASS_NAME` or the equivalent actual ComfyUI URL, then verify Neo's runtime choice. Some ComfyUI versions expose only `/object_info` for this check.
- **Refresh says present but import still fails:** Capture the *new* import error; presence alone doesn't guarantee widget-mapping compatibility or graph validity.

For local diagnostics the Admin endpoints are:
- `GET /api/admin/comfy-spaces/runtime/node-registry?runtime_profile_id=<profile>` — last cached diagnostic summary, explicitly **not live**.
- `POST /api/admin/comfy-spaces/runtime/node-registry/refresh` with `{"runtime_profile_id":"comfyui_local","required_classes":["SeedVR2"]}` — live discovery and comparison.

Do not expose Neo's Admin API over an untrusted network. Node discovery is not a security review of installed custom-node code.
