---
guide_id: global.neo_guides
title: Neo Studio User Guides
surface: global
scope: built_in
applies_to:
  - global
  - image
  - video
  - voice
  - prompt_captioning
  - roleplay
  - assistant
  - admin
tags:
  - guides
  - help
  - setup
  - troubleshooting
priority: 100
version: 2
updated: 2026-08-21
---

# Neo Studio User Guides

The root `README.md` is Neo Studio's **public quick-start and product overview**. The `guides/` folder is the deeper **user-help knowledge surface** for feature setup, controls, compatibility, recovery, and user-visible status messages.

Both surfaces are written for users. Internal milestone history, implementation notes, engineering evidence, and developer-only tooling belong under `neo_system_records/`, `scripts/`, or `tests/` instead.

## What belongs in a Guide

A Guide may include:

- feature setup and installation steps a normal user needs;
- where the feature appears in Neo Studio;
- supported families, loaders, workflows, and backend requirements;
- recommended starting settings;
- user-facing warnings and limitations;
- **Connect/Test**, **Refresh**, restart, and recovery steps available to the user;
- Output Inspector or Admin status explanations;
- safe upgrade and rollback instructions when users must perform them.

## What does not belong in a Guide

Developer validation, regression suites, internal audit scripts, release-build commands, test fixtures, implementation-phase evidence, and private development paths belong in Neo's internal developer records/tooling rather than user help.

If a feature is unavailable, the Guide should tell the user what they can check in Neo or the connected backend—not ask them to run an internal developer script.

## Main sections

```text
00_GLOBAL             General setup and backend help
01_IMAGE              Image generation and Image extensions
02_VIDEO              Video workflows
03_ROLEPLAY           Roleplay workspace
04_PROMPT_CAPTIONING  Prompt + Captioning
05_VOICE              Voice workspace and local voice backends
06_ASSISTANT          Assistant, Scopes, memory, and Project Brain
07_ADMIN              Admin setup and backend/model management
```
