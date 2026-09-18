# Phase 11 guide-index update

When applying this pack, add the following authority row to `guides/02_VIDEO/README.md`:

```md
| Video LoRA current-tree completion baseline | `python -m neo_app.video.video_lora_completion_baseline` |
```

Add this guide entry after `video_lora_stack_ui.md`:

```md
[`video_lora_completion_baseline.md`](video_lora_completion_baseline.md) — Phase-11 aggregate 144-case current-tree gate, exact-route support/manifest parity, fail-closed boundaries, and physical-GPU validation boundary.
```

Then increment the README frontmatter version and set `updated: 2026-09-11`.

This separate index instruction avoids replacing the complete README when the pack is applied onto a developer tree that may have gained unrelated guide entries after the audited tree.

