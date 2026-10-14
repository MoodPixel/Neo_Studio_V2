---
guide_id: comfy_spaces.conditional_dynamic_controls
title: Comfy Spaces Conditional and Dynamic Controls
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - controls
  - conditions
  - repeaters
  - variants
  - stages
priority: 72
version: 1
updated: 2026-10-07
---

# Comfy Spaces Conditional and Dynamic Controls

Published Comfy Spaces can use **declarative dynamic controls** to keep complex workflow interfaces focused. The Space author configures them in **Admin → Comfy Spaces → Dynamics**; users do not write JavaScript.

## Conditional behavior

A behavior rule can make an input or whole presentation section **visible** or **enabled** only when its condition matches. Conditions can read:

- another published Space input value;
- whether an author-defined execution stage is enabled;
- whether a selected per-run Graph Add-on module is present;
- whether the runner currently has a Space Variant applied.

Rules can combine up to 16 predicates with **All** or **Any**, and may negate the final condition. Supported comparisons include equals/not-equals, membership, truthy/falsy, exists, and numeric greater/less comparisons.

Dynamic visibility and enabled state are presentation/value-availability behavior only. They do **not** bypass/mute Comfy nodes, rewire the graph, alter Graph Anchors, or change execution-stage dependencies.

## Repeaters and reference lists

An author can first mark a supported input as **Multiple values**, then give it a collection UI:

- **Repeater** — repeatable structured/JSON values with author-defined minimum/maximum rows and optional reordering;
- **Reference list** — multiple image/mask/video/audio/file references using Neo's existing asset transport.

Neo only permits collection UI when the published manifest explicitly says the input has `multiplicity=multiple`. It does not assume that an arbitrary Comfy scalar socket accepts an array.

A repeated list is still an ordinary validated Space value by the time the existing binding engine receives it. Dynamic controls do not create a second binding system.

## Stacks and topology

Repeaters are the safe UI/value primitive for stack-like configuration, but they do not automatically create graph topology. For example, Neo does not interpret a repeated ControlNet list as permission to chain several ControlNet modules onto one Graph Anchor. NCS-10B intentionally rejects ambiguous multiple claims on one anchor.

A topology-producing stack therefore requires an explicit reviewed Graph Add-on contract that defines ordering and composition. NCS-11 does not guess that contract from presentation state.

## Publication and trust

Dynamic metadata is published as `dynamic_controls.json`, separately from `space_ui.json`. The publication receipt hashes the sidecar, and the public loader verifies it before the metadata can affect the runner.

The contract explicitly has:

```text
declarative_only = true
execution_authority = false
binding_authority = false
generated_neo_javascript = false
```

Older published Spaces without dynamic metadata continue to render normally.

## Builder Preview and runner parity

Builder Preview and the top-level Comfy Spaces runner both use Neo's same shared renderer. That renderer evaluates the same declarative condition model as the backend. There is no per-Space `eval`, generated function, or custom browser script.

Stage toggles, selected Graph Add-ons, and applied Variants are passed into the renderer only as dynamic-condition context. Their real execution/topology authority remains in the existing NCS-10 contracts.
