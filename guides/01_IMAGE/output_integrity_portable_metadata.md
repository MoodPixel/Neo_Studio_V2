# Image Output Integrity & Portable Metadata Recovery

## Purpose
Neo image filenames are human-facing storage names, not durable record identities. New Image outputs therefore receive a per-generation `neo_result_uid` and a per-image `neo_output_id`. The per-image ID is embedded into PNG, JPEG, and WebP output bytes without pixel re-encoding.

## Save contract
For each newly persisted image Neo records:
- `neo_result_uid` on the sidecar result.
- `neo_output_id` on each output file record.
- a compact portable recovery payload embedded in the image.
- size, mtime, and SHA-256 integrity data in the sidecar.
- an identity index under `neo_data/cache/image_output_identity_index.json` for fast lookup.

The existing readable result ID remains compatible. If a sidecar with the same result ID already exists, Neo appends a short unique suffix rather than overwriting it.

## Embedded payload
The portable payload contains the Neo output/result IDs, file ID, created time, save category, filename, prompts, model summary, and core sampling values. It intentionally excludes API keys, authorization data, raw backend payloads, and other secret-bearing runtime state.

Embedding methods:
- PNG: iTXt chunk.
- JPEG: comment segment inserted into the JPEG stream.
- WebP: Neo RIFF metadata chunk.

These methods do not decode/re-encode image pixels.

## Metadata integrity scan
Results includes **Scan Metadata Integrity**. The scan reports:
- sidecars whose recorded image files no longer exist;
- multiple sidecars that claim the same image path;
- collisions that can be resolved by the live image's embedded `neo_output_id`;
- unresolved legacy collisions where Neo cannot safely decide which record is authoritative.

Neo never hard-deletes sidecars from this action.

## Quarantine
**Quarantine Safe Cleanup** moves orphan sidecars and stale sides of resolved filename collisions to:

`neo_data/outputs/image_metadata_quarantine/`

The identity index is updated to the quarantined path. This keeps moved images recoverable later. Permanent deletion is intentionally not part of the automatic integrity action.

## Inspector recovery
Use **Load Image Metadata** in Output Inspector to choose a PNG/JPEG/WebP from any location.

Neo reads the embedded `neo_output_id` and:
1. checks the identity index;
2. searches active metadata if necessary;
3. searches quarantined metadata if necessary;
4. shows the full sidecar when found;
5. otherwise shows the compact embedded metadata as a fallback.

This means renaming or moving an image does not break metadata recovery as long as its embedded Neo metadata has not been stripped by another application.

## Legacy files
Older Neo images that predate portable metadata remain readable through normal Results sidecars. They cannot gain portable recovery automatically unless explicitly migrated later. Filename collisions between legacy records are reported but are not auto-repaired unless a newer embedded ID provides an unambiguous authority signal.

## Assistant attachment recovery

When an image is attached directly to Neo Assistant, the Assistant now uses the same Image identity/recovery authority before answering questions about how that image was generated.

Resolution is conservative:

1. If the file contains a Neo portable `neo_output_id`, Neo resolves it through the Image identity index and active/quarantined sidecars.
2. If portable identity is absent, Neo may use an **exact recorded SHA-256 match** to recover one unique native sidecar.
3. If the file is not a proven Neo output, Neo can inspect supported external embedded metadata such as ComfyUI workflow/prompt data, A1111/Forge-style parameters, Fooocus-like fields, and generic PNG/JPEG EXIF metadata.
4. If no recoverable generation metadata exists, the Assistant may still inspect visible pixels when vision is available, but hidden generation settings such as seed, checkpoint, sampler, workflow, LoRAs, or prompt are **not established** from pixels alone.

External metadata is always identified as external; it is never promoted to a Neo generation record. If multiple native records claim the same exact file hash, Neo reports an unresolved conflict instead of silently choosing one.

Secret-bearing embedded fields such as API keys, authorization values, tokens, passwords, and cookies are redacted before metadata is placed in Assistant context. Precise EXIF GPS/location data is not forwarded into Assistant metadata context.
