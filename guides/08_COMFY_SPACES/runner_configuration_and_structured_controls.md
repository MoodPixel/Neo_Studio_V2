# Comfy Spaces — runner configuration and structured controls

Updated 2026-10-10 following real Minimax H3 Long Space screenshots. This is an incremental runner UX correction on the existing NCS-15 + incremental NCS-16 baseline; **not** NCS-16 security signoff.

## The top of a published Space

The Space title, published-version label, and dependency readiness stay in the header. Below them, the **Configurations** disclosure contains both:

- **Space variants** — approved graph add-ons/stage settings and optional non-media values. Changes may affect graph/stage configuration only through the existing explicit Variant contract.
- **Input presets** — value-only snapshots. They never change the graph, enabled stages, or selected media.

Configurations is compact and initially collapsed to keep the controls and results near the top. Expand to select, save, or delete configurations. The choice and save fields use dark Neo-style controls instead of raw white browser widgets. Expansion is local interface state, not a saved Space setting.

## Why several controls have the same name

**Source node** grouping reflects separate nodes in the imported ComfyUI graph. A workflow can contain multiple nodes titled `LONG VIDEO PROMPT`, each feeding a different frame, segment, or branch. Even when the label is identical, the node IDs are different. Neo must **not** automatically merge their values or imply shared ownership.

The runner now visually separates the friendly node title from the class/type and original source node ID (for example, `PrimitiveStringMultiline · Node #184`). When names repeat, a note explains that the controls are independent.

For genuinely user-facing labels such as `Opening prompt`, `Motion continuation`, or `Final segment`, rename the corresponding section/control during **Builder → Controls / Presentation**, review Preview, save the draft, and publish a new immutable version. The runner must not guess intent from node order or duplicate titles.

## Structured prompt values

Some ComfyUI nodes provide an object/array where the published UI contract says `text_area`. Previously, JavaScript converted this to `[object Object]`. The shared renderer now renders the **original structured value as JSON** instead of coercing its type to a string. A special hint indicates that the binding may need author review. Valid JSON edits preserve the original object/array shape. Malformed JSON or attempts to replace the object with a primitive string are rejected locally, leaving the last valid value intact and blocking Validate/Generate until corrected.

**This is not automatic prompt extraction.** The field may represent a custom-node widget configuration or an unsuitable candidate binding. Neo cannot safely infer which nested property is the intended prompt text from a screenshot. If JSON is not the intended input, inspect the original workflow and node `object_info` and author the exact string binding in Builder; do not flatten arbitrary objects into text.

## Real-hardware acceptance steps

1. Restart Neo and hard-refresh the browser after applying the patch.
2. Open the existing published Minimax H3 Long Space, expand Configurations, confirm Variant/Preset dropdowns are legible and actionable.
3. Compare each repeated prompt section against the original ComfyUI workflow node ID; confirm all distinct controls remain available.
4. Verify that structured values display real JSON and **never** `[object Object]`.
5. Try malformed JSON. Neo should preserve the last valid value and block Validate/Generate until the field is corrected.
6. Confirm with the *trusted* workflow JSON and connected runtime `/object_info` whether each multiline control should truly be a string prompt. Correct any misclassified binding in the Builder.
7. Validate and generate a small run on the actual Comfy runtime, checking the queued graph receives expected input values. **This has not been verified by automated UI tests.**

This patch does not change backend bindings, graph topology, the published package, Comfy runtime execution, or security policies. The existing Space version remains immutable; visual label changes in the Builder require a new published version.
