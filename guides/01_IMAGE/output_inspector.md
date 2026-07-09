---
guide_id: image.output_inspector
title: Image Output Inspector and Metadata
surface: image
scope: built_in
applies_to:
  - image_workspace
  - output_inspector
  - metadata_sidecars
  - delete_output
  - latent_capture
tags:
  - image
  - output inspector
  - metadata
  - sidecar
  - cleanup
priority: 85
version: 1
updated: 2026-07-09
---

# Image Output Inspector and Metadata

The Image Output Inspector should use Neo-owned output files and metadata sidecars under `neo_data`. Metadata can include prompt, negative prompt, selected route, model, dimensions, steps, seed, source assets, latent capture, run timing, and cleanup reports.

Delete actions should use a safe preview/cascade contract. Output-only delete removes the saved output and sidecars. Full linked-asset delete should only delete unique Neo-owned linked assets after reference scanning, and skip shared or unsafe paths.
