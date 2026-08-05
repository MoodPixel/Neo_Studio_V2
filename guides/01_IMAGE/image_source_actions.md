---
guide_id: image.source_actions
title: Preview Source Actions
surface: image
scope: built_in
applies_to:
  - image_preview
  - output_inspector
  - img2img
  - inpaint
  - outpaint
tags:
  - image
  - preview actions
  - source handoff
  - img2img
  - inpaint
  - outpaint
  - provider routing
priority: 114
version: 2
updated: 2026-08-05
---

# Preview Source Actions

The Preview and Output Inspector **Source** group contains **Img2Img**, **Inpaint**, and **Outpaint**. These buttons stage the selected output as the next Image source. They do not start generation.

## Provider ownership

A Source action is evaluated against the currently selected Image backend profile. The same selected profile remains active through staging, optional recipe replay, mode switching, and the next explicit Generate action.

Neo does not silently switch Forge to Comfy or choose another local profile. A stale action evaluation or provider/profile mismatch is rejected.

## Source handoff contract

Source actions use:

```text
schema: neo.image.preview_source_handoff.v1
provider_policy: selected_profile_only
automatic_provider_fallback: false
auto_run: false
```

The contract records the canonical action, target mode, selected profile/provider, output lineage, source filename/path/URL, dimensions, and optional replay source.

URL-only live previews are copied through Neo's validated source upload endpoint before staging. The draft therefore keeps a Neo-owned source reference rather than depending on a temporary browser or backend URL.

## Action behavior

| Action | Staging result |
|---|---|
| **Img2Img** | Stages the selected image, clears stale mask/provider upload state, switches to Img2Img, and waits for Generate. |
| **Inpaint** | Stages the selected image, clears the previous mask, switches to Inpaint, and opens the mask editor for a fresh mask. |
| **Outpaint** | Stages the selected image, clears previous mask/canvas/padding state, switches to Outpaint, and opens the outpaint editor. |

Prompt and reference settings are preserved unless the user explicitly selected a saved-result replay source. Replay may restore recipe data, but the selected backend profile is restored immediately afterward.


## Manual source upload and drag/drop lifecycle

Img2Img, Inpaint, Outpaint, Qwen reference lanes, and Qwen Stitch use the shared `data-neo-image-dropzone` contract. Drop handling is delegated once at the document boundary before Image panels render. This keeps newly replaced dropzones interactive while backend capability overlays, model catalogs, or mode changes rebuild the Image DOM.

Do not move image drops back to one-time listeners attached to individual preview nodes. A panel render can replace those nodes and silently remove their listeners. File pickers and dropped files still enter the same validated source/reference upload functions; the change is lifecycle ownership, not upload or provider semantics.

## Backend validation

Before provider compilation, Neo revalidates the handoff against the actual request provider and backend profile. The canonical staged source overrides stale source fields, and temporary Comfy/Forge upload aliases are removed. A provider, profile, target-mode, or missing-source mismatch fails before submission.

## Safety rules

- Source staging never calls generation automatically.
- A newly staged source clears stale mask and backend-upload ownership.
- Preview and Output Inspector use the same canonical action registry and provider evaluation.
- Manual source upload or source clearing removes old preview-action ownership.
- Cross-provider finishing is a separate explicit workflow and is not part of Source actions.

## Phase 11 cleanup and replay ownership

A Source action may explicitly preserve the currently selected Image profile while loading prompt/recipe context from a saved result. This is the only replay override used by Source staging. After the staged generation reaches a terminal state, the Source handoff and provider upload aliases are removed while the canonical Neo-owned source image remains available.
