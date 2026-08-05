from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_does_not_globally_require_v054():
    manifest = json.loads((ROOT / 'extension_manifest.json').read_text(encoding='utf-8'))
    assert manifest['version'] == '1.2.20'
    assert manifest['required_nodes']['comfyui'] == []
    conditional = manifest['conditional_required_nodes']
    assert conditional['classic_v054'] == ['NeoSceneDirectorV054']
    assert conditional['lightweight_regional_lora'] == ['NeoRegionalLoRADelta']
    assert conditional['lightweight_regional_prompt'] == []


def test_editor_assets_are_extension_owned_and_loaded():
    manifest = json.loads((ROOT / 'extension_manifest.json').read_text(encoding='utf-8'))
    assert 'ui/editor_restore.js' in manifest['asset_bundle']['js']
    assert 'ui/editor_restore.css' in manifest['asset_bundle']['css']
    assert manifest['entrypoints']['editor_ui'] == 'ui/editor_restore.js'


def test_panel_contains_real_editor_not_hidden_inspector_only_placeholder():
    html = (ROOT / 'ui' / 'panel.html').read_text(encoding='utf-8')
    assert 'data-scene-director-editor-root' in html
    assert 'data-sd-add="character"' in html
    assert 'data-sd-regions' in html
    assert 'data-sd-canvas' in html
    assert 'data-scene-director-inspector-root hidden' not in html
    assert 'name="scene_director_state"' in html
    assert 'name="scene_graph_json"' in html


def test_inspector_targets_child_root_and_cannot_replace_editor():
    js = (ROOT / 'ui' / 'panel.js').read_text(encoding='utf-8')
    assert "const ROOT_SELECTOR = '[data-scene-director-inspector-root]'" in js
    assert 'data-extension-id="image.scene_director"' in js
    assert 'return container.querySelector(ROOT_SELECTOR)' in js


def test_editor_keeps_modern_prompting_independent_from_regional_lora_node():
    js = (ROOT / 'ui' / 'editor_restore.js').read_text(encoding='utf-8')
    assert "nodes.has('NeoRegionalLoRADelta')" in js
    assert "Regional prompting is available. NeoRegionalLoRADelta is missing" in js
    assert "nodes.has('NeoSceneDirectorV054')" in js
    assert 'never falls back to a global LoRA loader' in js


def test_editor_serializes_canonical_and_legacy_payloads():
    js = (ROOT / 'ui' / 'editor_restore.js').read_text(encoding='utf-8')
    for token in ('canonicalBlock', 'sceneGraph', 'scene_director_extension_json', 'neo:extension-state-changed', 'getPayload', 'getSceneGraph'):
        assert token in js or token in (ROOT / 'ui' / 'panel.html').read_text(encoding='utf-8')
