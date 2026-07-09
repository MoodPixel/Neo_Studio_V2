from __future__ import annotations

from copy import deepcopy
from typing import Any

VOICE_CAPABILITIES_SCHEMA = "neo.voice.capabilities.v12"
VOICE_CAPABILITY_CONTROLS_SCHEMA = "neo.voice.capability_controls.v12"

BASE_FEATURES: dict[str, Any] = {
    "tts": True,
    "preview": True,
    "render": True,
    "voice_clone": False,
    "reference_audio": False,
    "multilingual": False,
    "saved_voice_profiles": False,
    "batch": False,
    "dialogue": False,
    "emotion": False,
    "seed": True,
    "tags": False,
    "prosody": False,
    "pause_handling": False,
    "artifact_cleanup": False,
    "backend_native_extras": False,
    "finish_tools": True,
    "replay_memory": True,
    "project_handoff": False,
}

# Stable UI-facing aliases. These are intentionally duplicated from the backend-ish
# feature names so the frontend can make simple decisions without knowing provider jargon.
SUPPORT_ALIAS_MAP = {
    "supports_tts": "tts",
    "supports_preview": "preview",
    "supports_render": "render",
    "supports_cloning": "voice_clone",
    "supports_reference_audio": "reference_audio",
    "supports_multilingual": "multilingual",
    "supports_saved_voice_profiles": "saved_voice_profiles",
    "supports_batch": "batch",
    "supports_dialogue": "dialogue",
    "supports_speaker_mapping": "dialogue",
    "supports_emotion": "emotion",
    "supports_seed": "seed",
    "supports_tags": "tags",
    "supports_prosody": "prosody",
    "supports_pause_handling": "pause_handling",
    "supports_artifact_cleanup": "artifact_cleanup",
    "supports_backend_native_extras": "backend_native_extras",
    "supports_finish_tools": "finish_tools",
    "supports_normalize": "finish_tools",
    "supports_silence_trim": "finish_tools",
    "supports_audio_convert": "finish_tools",
    "supports_split_merge": "finish_tools",
    "supports_replay_metadata": "replay_memory",
    "supports_memory_export": "replay_memory",
    "supports_project_handoff": "project_handoff",
    "supports_project_asset_tray": "project_handoff",
}

CONTROL_CATALOG: dict[str, dict[str, Any]] = {
    "language": {"label": "Language", "zone": "default", "requires": "supports_tts"},
    "speaking_rate": {"label": "Speaking Rate", "zone": "default", "requires": "supports_tts"},
    "expression_strength": {"label": "Expression Strength", "zone": "advanced", "requires": "supports_emotion"},
    "reference_strength": {"label": "Reference Strength", "zone": "advanced", "requires": "supports_cloning"},
    "seed": {"label": "Seed / Variation", "zone": "advanced", "requires": "supports_seed"},
    "pause_handling": {"label": "Pause Handling", "zone": "advanced", "requires": "supports_pause_handling"},
    "artifact_cleanup": {"label": "Artifact Cleanup", "zone": "advanced", "requires": "supports_artifact_cleanup"},
    "tag_blocks": {"label": "Tag Blocks", "zone": "backend_native", "requires": "supports_tags"},
    "prosody": {"label": "Prosody", "zone": "backend_native", "requires": "supports_prosody"},
    "backend_native": {"label": "Backend-native Extras", "zone": "backend_native", "requires": "supports_backend_native_extras"},
    "speaker_blocks": {"label": "Speaker Blocks", "zone": "advanced", "requires": "supports_dialogue"},
    "speaker_mapping": {"label": "Speaker Mapping", "zone": "advanced", "requires": "supports_dialogue"},
    "script_import": {"label": "Script Import", "zone": "advanced", "requires": "supports_batch"},
    "batch_queue": {"label": "Batch Queue", "zone": "advanced", "requires": "supports_batch"},
    "output_naming": {"label": "Output Naming", "zone": "advanced", "requires": "supports_batch"},
    "normalize": {"label": "Normalize", "zone": "advanced", "requires": "supports_finish_tools"},
    "silence_trim": {"label": "Silence Trim", "zone": "advanced", "requires": "supports_finish_tools"},
    "noise_cleanup": {"label": "Noise Cleanup", "zone": "advanced", "requires": "supports_finish_tools"},
    "loudness_target": {"label": "Loudness Target", "zone": "advanced", "requires": "supports_finish_tools"},
    "convert_audio": {"label": "WAV / MP3 Convert", "zone": "advanced", "requires": "supports_audio_convert"},
    "split_chunks": {"label": "Split Chunks", "zone": "advanced", "requires": "supports_split_merge"},
    "merge_chunks": {"label": "Merge Chunks", "zone": "advanced", "requires": "supports_split_merge"},
    "replay_metadata": {"label": "Replay Metadata", "zone": "advanced", "requires": "supports_replay_metadata"},
    "memory_export": {"label": "Memory Export", "zone": "advanced", "requires": "supports_memory_export"},
    "project_handoff": {"label": "Send to Project", "zone": "advanced", "requires": "supports_project_handoff"},
    "project_asset_tray": {"label": "Project Asset Tray", "zone": "advanced", "requires": "supports_project_asset_tray"},
}

FAMILY_CAPABILITIES: dict[str, dict[str, Any]] = {
    "chatterbox_turbo": {
        **BASE_FEATURES,
        "voice_clone": True,
        "reference_audio": True,
        "saved_voice_profiles": True,
        "emotion": True,
        "multilingual": False,
        "dialogue": True,
        "batch": True,
        "recommended_tier": "low_mid_vram",
        "adapter_phase": "VO-V12",
        "batch_phase": "VO-V13",
        "finish_phase": "VO-V14",
        "memory_phase": "VO-V15",
        "project_handoff_phase": "VO-V16",
        "controls": ["language", "speaking_rate", "expression_strength", "reference_strength", "seed", "speaker_blocks", "speaker_mapping", "script_import", "batch_queue", "output_naming", "normalize", "silence_trim", "noise_cleanup", "loudness_target", "convert_audio", "split_chunks", "merge_chunks", "replay_metadata", "memory_export"],
        "notes": ["Primary adapter target for first Voice runtime pass.", "Quick preview, render, reference clone manifests, and saved voice profiles are contract-ready; final synthesis quality depends on the backend."],
    },
    "chatterbox_multilingual": {
        **BASE_FEATURES,
        "voice_clone": True,
        "reference_audio": True,
        "saved_voice_profiles": True,
        "emotion": True,
        "multilingual": True,
        "dialogue": True,
        "batch": True,
        "recommended_tier": "mid_vram",
        "adapter_phase": "VO-V12",
        "batch_phase": "VO-V13",
        "finish_phase": "VO-V14",
        "memory_phase": "VO-V15",
        "project_handoff_phase": "VO-V16",
        "controls": ["language", "speaking_rate", "expression_strength", "reference_strength", "seed", "speaker_blocks", "speaker_mapping", "script_import", "batch_queue", "output_naming", "normalize", "silence_trim", "noise_cleanup", "loudness_target", "convert_audio", "split_chunks", "merge_chunks", "replay_metadata", "memory_export"],
        "notes": ["Multilingual Chatterbox route is capability-mapped but guarded until the backend adapter exposes models/voices."],
    },
    "kokoro_preview": {
        **BASE_FEATURES,
        "voice_clone": False,
        "reference_audio": False,
        "saved_voice_profiles": True,
        "batch": True,
        "recommended_tier": "low_vram",
        "adapter_phase": "VO-V10",
        "batch_phase": "VO-V13",
        "finish_phase": "VO-V14",
        "memory_phase": "VO-V15",
        "project_handoff_phase": "VO-V16",
        "backend_profile_id": "voice.kokoro",
        "backend_label": "Kokoro Preview",
        "backend_badge": "Low-VRAM / Lightweight",
        "supports_remote_adapter": True,
        "clone_policy": "unsupported_hidden",
        "controls": ["language", "speaking_rate", "seed", "script_import", "batch_queue", "output_naming", "normalize", "silence_trim", "loudness_target", "convert_audio", "split_chunks", "merge_chunks", "replay_metadata", "memory_export"],
        "notes": ["VO-V10 low-end adapter lane. Kokoro is exposed as a lightweight TTS backend with preview/render support only.", "Clone/reference/dialogue controls stay hidden for Kokoro."],
    },
    "fish_hq": {
        **BASE_FEATURES,
        "voice_clone": True,
        "reference_audio": True,
        "multilingual": True,
        "saved_voice_profiles": True,
        "dialogue": True,
        "batch": True,
        "emotion": True,
        "seed": True,
        "tags": True,
        "prosody": True,
        "pause_handling": True,
        "artifact_cleanup": True,
        "backend_native_extras": True,
        "recommended_tier": "high_vram_hq",
        "adapter_phase": "VO-V12",
        "batch_phase": "VO-V13",
        "finish_phase": "VO-V14",
        "memory_phase": "VO-V15",
        "project_handoff_phase": "VO-V16",
        "backend_profile_id": "voice.fish_speech",
        "backend_label": "Fish Speech HQ",
        "backend_badge": "HQ / Advanced",
        "supports_remote_adapter": True,
        "clone_policy": "advanced_reference_clone",
        "install_expectation": "heavier_backend",
        "runtime_warning": "Fish Speech HQ is an advanced backend lane. Expect higher VRAM use, slower startup, and more setup complexity than Kokoro or Chatterbox.",
        "controls": ["language", "speaking_rate", "expression_strength", "reference_strength", "seed", "pause_handling", "artifact_cleanup", "tag_blocks", "prosody", "backend_native", "speaker_blocks", "speaker_mapping", "script_import", "batch_queue", "output_naming", "normalize", "silence_trim", "noise_cleanup", "loudness_target", "convert_audio", "split_chunks", "merge_chunks", "replay_metadata", "memory_export"],
        "notes": [
            "VO-V11 HQ adapter lane. Fish Speech is exposed as an advanced TTS/clone backend contract with richer prosody/tag controls.",
            "VO-V12 dialogue/multi-speaker lane is active through Neo speaker blocks, speaker-to-profile mapping, and alternating turn render manifests.",
            "Use guarded health and model/voice fallbacks until a local Fish HTTP server is configured.",
        ],
    },
    "custom_tts": {
        **BASE_FEATURES,
        "voice_clone": True,
        "reference_audio": True,
        "multilingual": True,
        "saved_voice_profiles": True,
        "dialogue": True,
        "batch": True,
        "emotion": True,
        "tags": True,
        "prosody": True,
        "pause_handling": True,
        "artifact_cleanup": True,
        "backend_native_extras": True,
        "recommended_tier": "manual",
        "controls": ["language", "speaking_rate", "expression_strength", "reference_strength", "seed", "pause_handling", "artifact_cleanup", "tag_blocks", "prosody", "backend_native", "speaker_blocks", "speaker_mapping", "script_import", "batch_queue", "output_naming", "normalize", "silence_trim", "noise_cleanup", "loudness_target", "convert_audio", "split_chunks", "merge_chunks", "replay_metadata", "memory_export"],
        "notes": ["Custom adapter slot for power users."],
    },
}

RUNTIME_ALIASES = {
    "chatterbox": ["chatterbox_turbo", "chatterbox_multilingual"],
    "kokoro": ["kokoro_preview"],
    "fish_speech": ["fish_hq"],
    "custom_tts": ["custom_tts"],
}


def normalize_family(family: str | None) -> str:
    value = str(family or "chatterbox_turbo").strip() or "chatterbox_turbo"
    return value if value in FAMILY_CAPABILITIES else "chatterbox_turbo"


def _support_flags(features: dict[str, Any]) -> dict[str, bool]:
    return {alias: bool(features.get(feature_key)) for alias, feature_key in SUPPORT_ALIAS_MAP.items()}


def _control_state(control_id: str, support_flags: dict[str, bool], *, enabled_by_family: bool) -> dict[str, Any]:
    meta = CONTROL_CATALOG.get(control_id, {"label": control_id.replace("_", " ").title(), "zone": "advanced", "requires": "supports_tts"})
    required_flag = str(meta.get("requires") or "supports_tts")
    supported = bool(support_flags.get(required_flag, False)) and enabled_by_family
    return {
        "id": control_id,
        "label": meta.get("label") or control_id,
        "zone": meta.get("zone") or "advanced",
        "requires": required_flag,
        "visible": supported,
        "enabled": supported,
        "status": "available" if supported else "hidden_unsupported",
    }


def capability_controls_payload(features: dict[str, Any]) -> dict[str, Any]:
    """Build the UI manifest that decides which Voice controls are visible.

    VO-V8 makes this payload the single source of truth for advanced controls. The
    frontend should not hardcode backend rules; it reads this manifest and renders
    controls by zone.
    """
    support_flags = _support_flags(features)
    family_controls = set(str(item) for item in (features.get("controls") or []))
    all_control_ids = list(dict.fromkeys(["language", "speaking_rate", *family_controls, *CONTROL_CATALOG.keys()]))
    controls = [_control_state(control_id, support_flags, enabled_by_family=(control_id in family_controls or control_id in {"language", "speaking_rate"})) for control_id in all_control_ids]
    visible = [control for control in controls if control["visible"]]
    zones = {
        "default": [control for control in visible if control.get("zone") == "default"],
        "advanced": [control for control in visible if control.get("zone") == "advanced"],
        "backend_native": [control for control in visible if control.get("zone") == "backend_native"],
    }
    source_options = [
        {"id": "built_in", "label": "Built-in Voice", "visible": True, "enabled": True},
        {"id": "saved_profile", "label": "Saved Profile", "visible": support_flags["supports_saved_voice_profiles"], "enabled": support_flags["supports_saved_voice_profiles"]},
        {"id": "reference_clone", "label": "Reference Clone", "visible": support_flags["supports_cloning"] and support_flags["supports_reference_audio"], "enabled": support_flags["supports_cloning"] and support_flags["supports_reference_audio"]},
    ]
    return {
        "schema_id": VOICE_CAPABILITY_CONTROLS_SCHEMA,
        "adapter_phase": str(features.get("adapter_phase") or "VO-V8"),
        "backend_badge": str(features.get("backend_badge") or features.get("recommended_tier") or ""),
        "clone_policy": str(features.get("clone_policy") or "capability_driven"),
        "support_flags": support_flags,
        "controls": controls,
        "visible_controls": [control["id"] for control in visible],
        "default_controls": [control["id"] for control in zones["default"]],
        "advanced_controls": [control["id"] for control in zones["advanced"]],
        "backend_native_controls": [control["id"] for control in zones["backend_native"]],
        "zones": zones,
        "source_options": source_options,
        "ui_rule": "Render only controls whose visible/enabled flags are true. Unsupported backend controls stay hidden, not disabled clutter.",
    }


def capability_payload(*, family: str | None = None, runtime: str | None = None, profile: dict[str, Any] | None = None, backend_health: dict[str, Any] | None = None) -> dict[str, Any]:
    family_id = normalize_family(family)
    caps = deepcopy(FAMILY_CAPABILITIES[family_id])
    profile_flags = profile.get("capability_flags") if isinstance(profile, dict) else None
    if isinstance(profile_flags, dict):
        for key, value in profile_flags.items():
            # Accept both backend-ish keys and VO-V8 stable support aliases.
            if key in caps and isinstance(caps[key], bool):
                caps[key] = bool(value)
            elif key in SUPPORT_ALIAS_MAP:
                caps[SUPPORT_ALIAS_MAP[key]] = bool(value)
    runtime_id = str(runtime or (profile.get("provider_id") if isinstance(profile, dict) else None) or "chatterbox").strip() or "chatterbox"
    compatible = family_id in RUNTIME_ALIASES.get(runtime_id, [family_id]) or runtime_id in {"custom_tts", "chatterbox"}
    status = "ready" if (backend_health or {}).get("reachable") else "adapter_contract_ready"
    if not compatible:
        status = "family_runtime_mismatch"
    control_manifest = capability_controls_payload(caps)
    return {
        "schema_id": VOICE_CAPABILITIES_SCHEMA,
        "surface": "voice",
        "family": family_id,
        "runtime": runtime_id,
        "profile_id": profile.get("profile_id") if isinstance(profile, dict) else "",
        "status": status,
        "compatible": compatible,
        "adapter_phase": str(caps.get("adapter_phase") or "VO-V8"),
        "backend_profile_id": str(caps.get("backend_profile_id") or ""),
        "backend_label": str(caps.get("backend_label") or family_id.replace("_", " ").title()),
        "backend_badge": str(caps.get("backend_badge") or caps.get("recommended_tier") or ""),
        "features": caps,
        "support_flags": control_manifest["support_flags"],
        "controls": caps.get("controls") or [],
        "control_manifest": control_manifest,
        "ui_manifest": control_manifest,
        "notes": caps.get("notes") or [],
        "backend": backend_health or {},
    }
