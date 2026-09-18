---
guide_id: video.native_workflow_lora_contract
title: Native-workflow Video LoRA Contract
surface: video
scope: built_in
applies_to: [video_lora_stack, wan22, ltx23, native_workflow]
tags: [video, lora, native workflow, import, security, contract]
priority: 89
version: 1
updated: 2026-09-11
---

# Native-workflow Video LoRA Contract

Phase 17 defines how an imported ComfyUI workflow may request universal Video LoRA patching without giving the extension permission to guess graph topology.

## Contract ownership

The importer/compiler must emit `neo.video.native_workflow_lora_contract.v1` containing:

- exact Neo route ID;
- canonical SHA-256 digest of the imported prompt graph;
- `owner=compiler`;
- model-only loader requirement;
- exact role and target policy;
- one semantic `all` model anchor;
- the source model reference and every consumer input to rewire.

Node identifiers are captured from the imported graph by the importer. They are not embedded as fixed IDs in the Video LoRA extension.

## Route policy

| Native route | Standard | Speed | Targets |
|---|---:|---:|---|
| WAN Txt2Vid / Img2Vid | Contract permits | Contract permits | `all` |
| LTX Txt2Vid / Img2Vid | Contract permits | Blocked | `all` |

This table describes the contract ceiling. It does not make the registry routes runnable.

## Fail-closed validation

The contract is rejected when the graph digest changed, a model reference or consumer no longer matches, the route declares wider capabilities, the graph already contains a LoRA loader, or live `LoraLoaderModelOnly` socket/catalog proof fails. Selected files missing from the live catalog are also rejected.

## Current product state

WAN and LTX native routes remain blocked because the current tree contains planned route entries but no native-workflow importer/compiler. A later phase must build that importer, make it emit this contract, connect canonical payload persistence, and complete real GPU queue tests before the route state becomes available.
