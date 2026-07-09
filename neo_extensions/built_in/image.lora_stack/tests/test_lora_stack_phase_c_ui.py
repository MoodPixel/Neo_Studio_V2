from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EXTENSION_DIR = ROOT / "neo_extensions" / "built_in" / "image.lora_stack"
JS = ROOT / "neo_app" / "static" / "js" / "neo.js"
CSS = ROOT / "neo_app" / "static" / "css" / "neo.css"


def _manifest() -> dict:
    return json.loads((EXTENSION_DIR / "extension_manifest.json").read_text(encoding="utf-8"))


def _js() -> str:
    return JS.read_text(encoding="utf-8")


def test_lora_stack_phase_c_manifest_declares_route_aware_assets_only_ui():
    data = _manifest()
    assert data["id"] == "lora_stack"
    assert data["workspace_apps"] == ["assets"]
    assert data["mount_slots"] == ["image.assets.lora_stack"]
    assert data["ui_schema"]["phase"] in {"C", "D", "L1", "L2", "L3", "L4", "L5", "L6"}
    assert data["ui_schema"]["route_aware"] is True
    assert data["ui_schema"]["display_modes"] == ["compact", "guided", "expert"]
    assert "no workflow graph patching in Phase C" in data["ui_schema"]["phase_c_features"]
    assert "server-side payload normalization" in data["ui_schema"].get("phase_d_features", [])
    assert "Assets-only editable LoRA Stack mount" in data["ui_schema"].get("phase_l1_features", [])
    assert "backend-prefixed route state keys" in data["ui_schema"].get("phase_l2_features", [])


def test_lora_stack_phase_c_ui_assets_are_no_longer_phase_b_placeholders():
    stack_html = (EXTENSION_DIR / "ui" / "stack_panel.html").read_text(encoding="utf-8")
    library_html = (EXTENSION_DIR / "ui" / "library_panel.html").read_text(encoding="utf-8")
    assert 'data-phase="C"' in stack_html
    assert 'data-phase="C"' in library_html
    assert "Phase B scaffold" not in stack_html
    assert "Phase B scaffold" not in library_html
    assert "CivitAI" in library_html
    assert "preview" in library_html.lower()


def test_lora_stack_phase_c_main_ui_renders_specialized_assets_panel():
    js = _js()
    assert "const LORA_STACK_EXTENSION_ID = 'lora_stack';" in js
    assert "function loraStackPanel(record)" in js
    assert "function loraLibraryPanel(record)" in js
    assert "function loraStackPanels(record)" in js
    assert ("if (extensionId(record) === LORA_STACK_EXTENSION_ID) return loraStackPanels(record);" in js or "if (id === LORA_STACK_EXTENSION_ID) return loraStackPanels(record);" in js)
    assert "bindLoraStackControls();" in js


def test_lora_stack_phase_c_controls_cover_v1_stack_and_library_surface():
    js = _js()
    for token in [
        "loraStackEnabled",
        "loraStackAddRow",
        "loraStackClearEmpty",
        "data-lora-row-field",
        "data-lora-row-action",
        "profileModelOptions('loras')",
        "LoraLoader.lora_name",
        "loraLibraryRecordSelect",
        "loraLibraryAddToStack",
        "loraCivitaiUrl",
        "loraCivitaiMergeMode",
        "loraCivitaiPull",
        "loraPromptAppend",
        "loraPromptReplace",
        "data-lora-token",
    ]:
        assert token in js


def test_lora_stack_phase_c_payload_preview_uses_clean_extension_contract():
    js = _js()
    assert "function loraStackPayloadPreview(record)" in js
    assert "function loraStackValidationPreview(record)" in js
    assert "params: active ? { loras: cleanRows } : {}" in js
    assert "assets: active ? { loras: assets } : {}" in js
    assert "source: 'image.assets.lora_stack'" in js
    assert "payloads[extId] = preview;" in js
    assert "validation.push(...loraStackValidationPreview(record));" in js


def test_lora_stack_phase_c_route_gating_and_expert_mode_diagnostics():
    js = _js()
    assert "function loraStackRouteVisible(route)" in js
    assert "return workspace === 'assets';" in js
    assert "function loraStackRouteControlsEnabled(route)" in js
    assert "return route.route_state === 'available' || route.route_state === 'experimental_available';" in js
    assert "extensionRouteStateRank(item) > extensionRouteStateRank(worst)" in js
    assert "workspace_state" in js
    assert "data-route-state" in js
    assert "data-display-mode" in js


def test_lora_stack_phase_c_css_is_registered_in_main_stylesheet():
    css = CSS.read_text(encoding="utf-8")
    assert ".neo-lora-stack-panel" in css
    assert ".neo-lora-library-panel" in css
    assert ".neo-lora-preview" in css
    assert ".neo-lora-token-chip" in css


def test_lora_stack_phase_c1_layout_wraps_rows_instead_of_clipping():
    js = _js()
    css = CSS.read_text(encoding="utf-8")
    assert "neo-lora-row-main" in js
    assert "neo-lora-row-routing" in js
    assert "neo-lora-library-tool-row" in js
    assert "neo-lora-record-summary" in js
    assert "neo-lora-record-details" in js
    assert ".neo-lora-stack-panel *" in css
    assert "min-width: 0;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "grid-column: 1 / -1;" in css


def test_lora_stack_phase_c2_uses_comfy_lora_loader_catalog_not_manual_folder_scan():
    js = _js()
    assert "profileModelOptions('loras')" in js
    assert "LoraLoader.lora_name" in js
    assert "loraCivitaiUrl" in js
    assert "loraLibraryPath" not in js
    assert "loraLibraryScan" not in js
    assert "loraLibraryRefresh" not in js


def test_phase_c2_extension_containment_guard_is_shared_not_lora_only():
    css = CSS.read_text(encoding="utf-8")
    assert "Shared extension containment guard" in css
    assert ".neo-extension-card," in css
    assert ".neo-panel[data-panel-id=\"built-in-extension-direct\"]" in css
    assert ".neo-extension-card *" in css


def test_phase_c3_extension_status_chips_use_shared_horizontal_rule():
    js = _js()
    css = CSS.read_text(encoding="utf-8")
    assert "neo-extension-status-line neo-cfg-fix-status-line" in js
    assert ".neo-extension-status-line," in css
    assert "flex-direction: row;" in css
    assert ".neo-lora-panel-header > div:first-child" in css


def test_phase_c3_lora_stack_rows_are_selectable_and_drive_library_focus():
    js = _js()
    css = CSS.read_text(encoding="utf-8")
    assert "selected_row_index" in js
    assert "function selectLoraStackRow(index)" in js
    assert "data-lora-row-select" in js
    assert "Show this LoRA in the library details" in js
    assert "loraRecordIdForName" in js
    assert "loraRecordMatchesName" in js
    assert ".neo-lora-stack-row.selected" in css


def test_phase_c3_lora_library_preview_nav_uses_emoji_buttons():
    js = _js()
    assert "aria-label=\"Previous LoRA preview\"" in js
    assert "aria-label=\"Next LoRA preview\"" in js
    assert "⬅️" in js
    assert "➡️" in js


def test_lora_stack_phase_l3_browser_clean_rows_preserve_apply_to_targets():
    js = _js()
    manifest = _manifest()
    assert manifest["ui_schema"]["phase"] in {"L3", "L4", "L5", "L6"}
    assert "phase_l3_features" in manifest["ui_schema"]
    assert "apply_to: loraStackNormalizeApplyTo(row.apply_to)" in js
    assert "`${clean.name}|${clean.strength}|${clean.target}|${clean.apply_to}`" in js
    assert "apply_to: loraStackNormalizeApplyTo(source.apply_to)" in js
    assert "function loraStackTargetSummary(rows = [])" in js
    assert "regional_target_preservation" in js
    assert "params.loras[].apply_to" in js


def test_lora_stack_phase_l3_submit_preserves_lora_payload_on_gated_routes():
    js = _js()
    assert "const preserveLoraPayload = extId === LORA_STACK_EXTENSION_ID" in js
    assert "if (!preserveLoraPayload) return;" in js
    assert "source: 'scene_director_extension_routes'" in js
    assert "policy: 'preserve_payload_intent_without_unvalidated_graph_patch'" in js
