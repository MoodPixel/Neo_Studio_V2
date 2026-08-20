---
guide_id: global.neo_overview
title: Neo Studio Overview
surface: global
scope: built_in
applies_to:
  - assistant
  - image
  - video
  - roleplay
  - prompt_captioning
  - voice
tags:
  - neo
  - overview
  - surfaces
  - assistant
priority: 80
version: 2
updated: 2026-08-14
---

# Neo Studio Overview

Neo Studio is a local creative workspace made of surface tabs. The Assistant is the user-facing brain that can combine stable guide knowledge, live surface snapshots, project knowledge, metadata sidecars, and uploaded project files.

Use the active Assistant scope to decide what knowledge to retrieve. General Assistant may search across all surfaces. A surface workspace, such as Image Workspace, should prefer global guides plus guides and runtime records for that specific surface.

Assistant answers should not claim a Neo surface is outside scope when the selected scope or guide metadata says that surface is relevant. If a live value is missing, say what is missing and use the stable guides for general explanation.


## Current public workspace state — 2026-08-14

Active public workspaces are Image, Video, Voice, Prompt & Captioning, Roleplay, Assistant, and Admin. Music and Board remain planned.

Public-facing UI copy should describe user capabilities and status rather than internal implementation phases, milestone IDs, schema names, or migration history. Those details remain in engineering records and Expert diagnostics only when genuinely needed for troubleshooting.

README screenshots are hosted externally through GitHub user attachments so screenshot binaries are not part of normal repository clones.
