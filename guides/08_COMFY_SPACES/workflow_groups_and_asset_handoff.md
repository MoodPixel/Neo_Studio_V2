# Comfy Groups + Asset Handoff — Integration Correction (2026-10-13)

## What the user means by node grouping

A saved ComfyUI workflow often has colored **canvas groups** such as `Models`, `Conditioning`, `preview`, and `Sampling and Decoding`. These are **functional containers** holding several independently editable nodes, not one group per node and not executable stage boundaries.

On a **new import from normal ComfyUI Save JSON**, Neo now reads those group titles and node positions. It assigns source nodes to their innermost containing group. Reusable top-level subgraph instances inherit their canvas group. The Builder creates one collapsible Space section per occupied group, and the shared renderer identifies different node IDs inside each group. Groups follow deterministic graph-dependency ordering rather than canvas X/Y order. Parallel branches have no unique physical execution sequence.

If workflow JSON contains no usable canvas groups (for example an API-format export), Neo uses **functional role** sections rather than hundreds of one-node cards. `Source node`, `Source subgraph`, `Functional`, `Comfy groups`, and `Main` remain author-selectable in the Builder. Neither section ordering nor collapsing changes any ComfyUI node, link, bypass state, or execution plan. Use NCS-10C Stages / NCS-10B Graph Add-ons for execution behavior.

Group overlap is ambiguous; Neo uses the smallest containing group. Ungrouped nodes use functional role fallback. A workflow's **original ComfyUI group positions/titles must be present in the imported Save JSON** to get the same names; a published canonical API graph cannot recover lost canvas groups.

## Media handling correction (NCS-6 / NCS-7)

The publication's old image filename is **not** an uploaded Neo asset. At runner opening, media inputs are empty regardless of saved workflow defaults; the user must select the media needed for this run. The runner uploads each actual selected browser File through `POST /api/comfy-spaces/assets`, receives a typed asset reference, and sends that reference to preflight. A missing required selection is reported as `missing_required_asset` with an actionable instruction rather than being mislabeled as a supplied raw path.

**Do not accept local filenames or filesystem paths as runtime asset references.** A caller that explicitly supplies a filename/path remains rejected as `invalid_asset_reference`. Upload/transport and queue-time validation remain separate: preflight is still authoritative and can refuse unavailable runtimes, unknown/missing models, invalid bindings, or unsafe asset references.

If `POST /api/comfy-spaces/assets` still returns HTTP 500, this patch does **not** prove it has fixed that separate server exception. Check the backend traceback and the POST response. GET returning 200 and an empty asset list only establishes the listing route works.

## Upgrade and verification

1. Apply patch preserving repo-relative paths. Restart Neo; hard refresh browser.
2. To get new Comfy-style groups, create a **new Builder draft from the normal saved ComfyUI workflow**, or explicitly apply `Comfy groups` grouping to a draft whose frozen analysis includes group metadata. Previously published versions remain immutable. Workflow replacement with a reviewed new version is recommended for existing public Spaces.
3. Validate a Space with no image selected: expect `missing_required_asset` rather than invalid filename.
4. Choose a PNG/JPG, click Validate, and confirm POST upload returns a typed asset reference before preflight runs. Then Generate using a trusted workflow and record real GPU results.
5. Verify repeated prompt nodes remain independently editable even when shown inside the same group.

The included automated tests validate grouping, independent values, parser hygiene and asset rejection behavior. Physical GPU generation, the user's exact Minimax H3 workflow, and a real browser multipart upload were **not** executed in this environment.
