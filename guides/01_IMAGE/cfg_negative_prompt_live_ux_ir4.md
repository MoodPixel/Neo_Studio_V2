# IR-4 — CFG + Negative Prompt Live UX Repair

## Scope

IR-4 reconnects the IP-1/IP-2 guidance and negative-prompt contracts to the actual Image renderer used by Neo Studio V2. It does not change backend execution semantics.

## Live control contract

- SD 1.5 / SDXL / Krea 2 RAW / Z-Image Base: editable **CFG**. Negative prompt is inactive at CFG <= 1, weak for 1 < CFG < 1.5, and active at CFG >= 1.5.
- Qwen Image / Qwen Image Edit 2509: the single existing CFG control is relabeled **True CFG**. The UI writes the value to both `true_cfg` (semantic authority) and `cfg` (current compiler compatibility). Negative eligibility reads `true_cfg` first.
- FLUX.1 / FLUX.2 Klein: classic CFG is not presented as an editable SD-style control. Guidance belongs to the model/Flux Guidance route and the negative prompt is disabled for the current True-CFG contract.
- Krea 2 Turbo / Z-Image Turbo: CFG is visibly fixed at 1 and disabled. Negative prompt is retained in authoring state but greyed/disabled because the family uses disabled/zeroed negative conditioning.
- Qwen Rapid AIO remains `PROFILE_CONTROLLED`; IR-4 does not invent a family-wide True-CFG promise for provider-controlled behavior.
- Qwen True CFG manual edits also clear Clean Slate / provider-managed unset state for the semantic `true_cfg` field, so values typed into the single CFG box stay visible instead of being blanked on rerender.
- The sampling-preset helper treats Qwen `cfg` and `true_cfg` as aliases of one physical input. It clears that DOM control only once, and a dirty/manual sampling edit survives workspace remounts instead of re-applying the previous built-in preset over the user's value.

## Retention rule

Greyness is execution state, not destructive editing. When a route disables the negative prompt, Neo keeps the user's text in `state.imageDraft.negative_prompt`. Switching back to an eligible route restores the same text and re-enables the field. Backend IP-2 continues to keep `negative_prompt_input` while clearing only `effective_negative_prompt` when execution is suppressed.

## Live renderer IDs

The IP-2 browser mirror now recognizes the source-of-truth renderer IDs: `imageWorkspaceFamily`, `imageFamily`, `imageWorkspaceLoader`, `imageLoader`, `imageWorkflowMode`, `imageCfg`, and `imageNegativePrompt`.

## Ownership

- IP-1: family/route guidance and negative capability semantics.
- IP-2: authoritative negative eligibility + provider-effective negative.
- IR-4: live browser rendering, greying, True-CFG labeling, and DOM refresh.
- IP-8: release-lock/Inspector validation remains authoritative before provider compile.

## Next phase

IR-5 repairs Scene Director route authority in the real Image renderer.
