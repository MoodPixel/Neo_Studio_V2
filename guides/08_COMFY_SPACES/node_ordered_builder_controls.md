> **Updated 2026-10-13:** New imports now prefer native ComfyUI canvas groups (Models / Conditioning / preview / etc.) where Save JSON provides them; otherwise Neo defaults to functional-role sections. The earlier one-section-per-node default below describes the 2026-10-10 state. See [workflow_groups_and_asset_handoff.md](workflow_groups_and_asset_handoff.md).

# Node-ordered Space Builder controls

## New Space drafts

New Builder drafts now default to **Source node** sections, one section per detected source node with exposed controls, in frozen workflow dependency order (topological order). Node labels and class names identify the settings; each section can be collapsed. The Builder's Controls view uses the same section layout that the published runner receives.

This order reflects dependencies among nodes. Independent parallel branches do not have a uniquely defined execution order; Neo uses a stable topological ordering and does not promise a serial runtime schedule. Node sections are **visual only**: collapsing, editing or rearranging them does not bypass or rewire graph nodes. For workflows with no authoring map, the previous Main fallback remains.

Other grouping options (Semantic role, Source subgraph and Manual) remain accessible in Controls. Existing saved Space drafts are **not silently regrouped**. If a draft already exists, choose **Source node** manually, review and save. This replaces the old layout; do not do it without reviewing custom sections.

## Expose vs recommended

**Recommended** is an advisory default, not an allowlist. You can expose a non-recommended candidate. The Builder still validates the actual field type, writable graph binding, UI contract, defaults and runtime dependencies, and can block an incompatible choice. Enabling all candidates is not a guarantee that all graph fields are publishable.

A controlled regression test confirms that an example workflow with non-recommended input candidates saves and compiles with those candidates exposed. It does **not** reproduce the user-reported failure for an unknown real workflow. To triage that case, keep the exact workflow JSON, chosen ComfyUI runtime/profile, control/node/field, whether **Save Draft**, **Preview** or **Publish** fails, and the exact error message from the Builder. Do not loosen publish or queue safety checks based solely on recommendation status.

## Workflow replacement

A replacement draft preserves previous sections for successfully migrated controls and retains sections for newly discovered controls. This avoids referencing deleted sections and does not mutate an already published Space.

## Runtime readability update (2026-10-10)

The public renderer now displays friendly node titles separately from class and source node IDs, and calls out repeated node titles without merging their independently bound inputs. The shared renderer also detects when a nominal text field's actual value is structured, showing JSON with type protection rather than `[object Object]`. For a human-friendly source-specific prompt label, edit the Builder control/section text and publish a new version; automatic merging or guessing nested prompt fields is not permitted. See `runner_configuration_and_structured_controls.md`.
