---
guide_id: global.public_path_hygiene
title: Local Paths, Privacy, and Portable Setup
surface: global
scope: built_in
applies_to:
  - admin
  - image
  - model_guide
  - node_manager
  - memory_engine
tags:
  - privacy
  - paths
  - local setup
  - runtime data
priority: 92
version: 4
updated: 2026-08-16
---

# Local Paths, Privacy, and Portable Setup

Neo Studio is local-first. Backend folders, model locations, runtime settings, generated media, logs, and personal project data belong to the local Neo runtime, not to the public application source.

## What you can configure locally

Depending on the feature, Neo may ask you to choose or configure locations for:

- ComfyUI or ComfyUI Portable;
- Forge / Forge Neo;
- model folders and extra model paths;
- local voice or language-model runtimes;
- extension or custom-node folders;
- local output and project data.

Use the relevant **Admin** page or feature setup screen rather than editing tracked source files with machine-specific paths.

## Runtime data

Neo keeps user/runtime state under `neo_data/`. This can include saved backend profiles, generated-output metadata, Assistant/Project Brain data, logs, indexes, local settings, and other machine-specific state.

When updating Neo Studio, preserve `neo_data/` unless an upgrade note explicitly says that a migration is required. Do not delete it as a normal troubleshooting step.

## Portable path examples

Documentation uses role-based paths such as:

```text
<ComfyUI-root>/models
<ComfyUI-root>/custom_nodes
<Forge-root>
<backend-root>
```

Replace those placeholders with the real locations on your own machine.

## Privacy notes

Avoid sharing screenshots, logs, configuration exports, or bug reports that expose:

- usernames or home-folder names;
- personal filenames or project names;
- API keys, tokens, cookies, or authorization headers;
- private backend URLs;
- full local model-library paths when they are not needed.

When asking for help, the most useful details are usually the backend type, Neo feature, selected model family/loader, visible error message, and a redacted screenshot of the relevant Admin or workspace panel.

## Moving Neo to another folder or computer

1. Close Neo Studio and connected local backends.
2. Move or reinstall the Neo source/application files.
3. Restore your intended `neo_data/` backup if you want to keep local Neo state.
4. Re-check backend paths in **Admin** because drive letters and installation folders may differ.
5. Start each backend and use Neo's normal connection/refresh controls.
6. Re-select any model or extension whose path changed.

A backend showing **Disconnected**, **Refresh required**, or a missing-model warning after a move usually means its local path or live capability snapshot needs to be updated rather than the project itself being damaged.
