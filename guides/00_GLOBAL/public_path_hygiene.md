---
guide_id: global.public_path_hygiene
title: Public Path Hygiene
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
  - public repository
  - runtime data
priority: 92
version: 1
updated: 2026-07-14
---

# Public Path Hygiene

Neo Studio is local-first, so users must be able to select real folders on their own machines. Those values are runtime configuration, not source-code defaults.

## Ownership boundary

| Data | Owner | Public-repo rule |
|---|---|---|
| Backend, model, embedding, and reranker paths | `neo_data/` runtime settings | Never copy into tracked JSON or documentation |
| Image Node Manager paths and installed-node records | `neo_data/admin/image/node_manager/` | Local only and ignored by Git |
| Admin Engine profiles | `neo_data/admin/engine/` | Local only and ignored by Git |
| Detector/SAM discovery roots | Server runtime | Do not return absolute roots to the browser |
| README, guides, UI placeholders | Public source | Use role labels such as `<ComfyUI-root>` or selection instructions |
| Tracked Node Manager/model-profile JSON files | Public template | Keep path fields empty, Node Manager records empty, and related timestamps null |

The tracked Node Manager files and Engine model-profile files under `neo_app/admin/` are sanitized templates. Neo's active Node Manager and Engine modules resolve their writable files under `neo_data/`, so source updates do not replace an existing user's saved paths.

## Safe examples

Use portable roles:

```text
<ComfyUI-root>/models
<ComfyUI-root>/ComfyUI/custom_nodes
<backend-root>/KoboldCPP
```

Do not publish a contributor's drive letter, home directory, username, personal image name, private model folder, or captured installed-node inventory.

## Release check

Before publishing source:

1. Confirm `neo_data/` remains ignored.
2. Confirm tracked Admin template path fields are empty.
3. Confirm tracked node records are empty.
4. Scan public source/docs for absolute Windows drive paths and named macOS/Linux home folders.
5. Confirm API payloads expose portable role paths, model values, counts, and safe error codes only.
6. Package only intentional changed files; never include local runtime data.

Developer-only tests may use synthetic absolute paths when the path parser itself is under test. Those fixtures must be obviously fictional and must never be loaded as defaults or returned to users.
