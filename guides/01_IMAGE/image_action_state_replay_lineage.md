# Image action state, replay, and lineage

## Purpose

Source, Reference, and Finish actions use short-lived contracts to move a selected output into another Image workflow. Those contracts are execution state, not reusable recipe state. Neo therefore separates:

- **canonical draft state** — prompts, model choices, extension settings, and Neo-owned source assets;
- **provider upload state** — temporary Comfy filenames and Forge base64 aliases;
- **action handoff state** — Preview/Output Inspector Source, Reference, and Finish contracts;
- **output lineage** — durable parent, root, and ancestor metadata saved with completed outputs.

## Lifecycle cleanup

`imageFinalizeActionLifecycle()` is the browser authority for terminal cleanup. It runs after success, failure, cancellation, recovery detachment, and provider-profile ownership changes.

It removes action-only fields such as:

```text
_neo_derived_action
_neo_preview_action
_preview_action_source
_preview_action_finish_pass
_preview_action_force_workflow_mode
_post_output_bridge
```

It also removes provider upload aliases. Canonical Neo-owned source images remain available unless the caller explicitly requests a full source clear.

Extension handoffs such as `preview_reference_handoff`, `preview_action_source`, and `staged_preview_source` are removed after their action lifecycle. Provider changes and replay restore disable affected extensions until the selected provider revalidates them.

## Provider ownership changes

Switching the selected Image profile clears temporary aliases owned by the previous provider/profile. Neo does not reuse:

- Comfy upload filenames under Forge;
- Forge base64 payloads under Comfy;
- Reference/Finish handoffs created for a different profile.

The backend repeats this check with `neo.image.action_state_provider_boundary.v1` before provider compilation.

## Replay binding

Saved replay payloads include `neo.image.provider_binding.v1`.

Default replay behavior:

1. Select the recorded backend profile when it still exists.
2. Block execution when the recorded profile is missing.
3. Never silently select another provider.
4. Allow an explicit current-provider override only for workflows that intentionally request it, such as a selected-profile Source action.

Extension payloads are restored provider-neutrally and disabled pending live revalidation. Temporary upload fields and action handoffs are never restored.

## Output lineage

Completed outputs contain `neo.image.output_lineage.v1`:

```text
current_output_id
source_output_id
parent_output_id
root_output_id
depth
ancestor_output_ids
action_id
dispatch_type
provider_id
profile_id
```

For repeated Finish passes, the selected output is always the **immediate parent**. Its earlier parent remains source provenance, while the root and ancestor list preserve the complete chain.

Example:

```text
Base output A
  └─ Native Hires output B
       └─ ADetailer output C
            └─ Forge Extras output D
```

For output D:

```text
parent_output_id = C
root_output_id = A
ancestor_output_ids = [A, B, C]
depth = 3
```

Preview and Output Inspector use the same source resolver and Finish dispatcher, so both surfaces create identical lineage semantics.

## Phase 12 replay and lineage visibility

Replay provider binding is now visible in both the shared Preview action toolbar and Output Inspector. The UI states whether the recorded profile is active or an explicit Source-action profile override is in use.

Restored provider-specific extensions show **Revalidation required** and remain disabled until the selected provider revalidates their route and assets.

Output Inspector renders the durable lineage contract as a parent-to-current chain. Guided mode summarizes parent/root/depth. Expert mode additionally exposes source, action, dispatch, provider/profile, and job identifiers.

## Phase 13 regression hardening

The Phase 13 matrix found and fixed a lineage-depth double increment. `build_derived_action_contract()` already stores the depth of the derived output being created, so `build_output_lineage_metadata()` must not add another level when that contract depth is present.

The exact chain is now locked:

```text
Base A      depth 0
Hires B     depth 1, parent A
ADetailer C depth 2, parent B, root A
Upscale D   depth 3, parent C, root A
```

The same matrix also verifies that a failed polling terminal state closes live preview, clears the watchdog, clears `activeImageJob`, finalizes transient action state, and restores generation controls.
## Phase 9 ADetailer recipe lock

ADetailer replay now carries `neo.image.adetailer.execution_recipe.v1` and `neo.image.adetailer.replay_contract.v1`. The recipe freezes the route and exact effective sampling values that reached the repair graph. Automatic family-preset results are restored as manual values so later preset revisions cannot alter the saved execution.

Replay restoration is deliberately disabled and pending live revalidation. It never restores provider uploads, never auto-enables ADetailer, and never treats a historical successful run as permission for the current backend. Neo verifies the SHA-256 recipe fingerprint, backend/family/loader/mode binding, normalized parameter equality, nodes, assets, LoRA catalog bindings, identity policy, sampling lineage, and output ownership.

Saved warning codes remain visible and require review/reconfirmation. A tampered fingerprint, route mismatch, or parameter drift fails closed before graph submission.

