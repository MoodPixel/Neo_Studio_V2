---
guide_id: comfy_spaces.neospace_import_export
title: Portable .neospace Import and Export
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
tags:
  - comfyui
  - spaces
  - portability
priority: 45
version: 1
updated: 2026-10-08
---

# `.neospace` Portability (NCS-15)

Export a published immutable version from the Comfy Spaces library using **Export .neospace**. To import, choose **Import .neospace** and select the saved file. Neo validates the transport, checks the internal publication against existing Neo schemas/integrity contracts, and requests confirmation before adding a new immutable version. Exact-version collisions are rejected, never overwritten.

## Transport contract

`.neospace` is a bounded ZIP-compatible archive containing `neospace.json` with a versioned format, Space ID/version, SHA-256 digest and byte length for every file; required package files are `space_manifest.json`, `workflow.json`, `space_ui.json`, `publication.json`. Optional signed publication sidecars are retained, and safe image presentation assets can be retained. No model weights, extension source, executable Python/JavaScript, local media uploads, user Presets, or runtime health caches are bundled. The transport limits 32 MiB compressed, 64 MiB uncompressed, and 32 package files. Package paths cannot traverse outside the Space directory.

Export and import share the existing immutable publication validators. Import does not auto-activate a runtime, install missing dependencies, or execute the workflow. Check dependency health at the destination and run live validation before Generate.

**Security boundary:** SHA-256 is evidence of integrity, not an authentic publisher identity. A checksum manifest that a malicious author controls is not a digital signature. Treat imported Spaces as untrusted; NCS-16 expands risk analysis, provenance/authenticity policy and isolation. Always verify Comfy custom nodes/models independently.

## HTTP endpoints

- `GET /api/comfy-spaces/portable/capabilities`
- `GET /api/comfy-spaces/portable/export/{space_id}/{version}`
- `POST /api/comfy-spaces/portable/import?dry_run=true|false` (multipart field `file`)

A dry run validates the archive but never installs it. Import returns `dependency_status: not_checked` and `requires_runtime_preflight: true`.
