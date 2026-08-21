from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MODEL_17B_CUSTOM = "qwen3_tts_17b_custom_voice"
MODEL_06B_CUSTOM = "qwen3_tts_06b_custom_voice"
MODEL_17B_BASE = "qwen3_tts_17b_base"
MODEL_06B_BASE = "qwen3_tts_06b_base"
MODEL_17B_DESIGN = "qwen3_tts_17b_voice_design"

MODEL_SPECS: dict[str, dict[str, Any]] = {
    MODEL_17B_CUSTOM: {
        "label": "Qwen3-TTS 1.7B CustomVoice",
        "upstream_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "role": "custom_voice",
        "size_class": "1.7B",
        "tasks": ["tts"],
        "instruction_control": True,
        "reference_audio": False,
        "built_in_speakers": True,
    },
    MODEL_06B_CUSTOM: {
        "label": "Qwen3-TTS 0.6B CustomVoice",
        "upstream_model_id": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "role": "custom_voice",
        "size_class": "0.6B",
        "tasks": ["tts"],
        "instruction_control": False,
        "reference_audio": False,
        "built_in_speakers": True,
    },
    MODEL_17B_BASE: {
        "label": "Qwen3-TTS 1.7B Base (Voice Clone)",
        "upstream_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "role": "base_clone",
        "size_class": "1.7B",
        "tasks": ["voice_clone"],
        "instruction_control": False,
        "reference_audio": True,
        "built_in_speakers": False,
    },
    MODEL_06B_BASE: {
        "label": "Qwen3-TTS 0.6B Base (Voice Clone)",
        "upstream_model_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "role": "base_clone",
        "size_class": "0.6B",
        "tasks": ["voice_clone"],
        "instruction_control": False,
        "reference_audio": True,
        "built_in_speakers": False,
    },
    MODEL_17B_DESIGN: {
        "label": "Qwen3-TTS 1.7B VoiceDesign",
        "upstream_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "role": "voice_design",
        "size_class": "1.7B",
        "tasks": ["voice_design"],
        "instruction_control": True,
        "reference_audio": False,
        "built_in_speakers": False,
    },
}

SUPPORTED_MODELS = tuple(MODEL_SPECS)

REQUIRED_ROOT_FILES = (
    "config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "preprocessor_config.json",
)

REQUIRED_SPEECH_TOKENIZER_FILES = (
    "config.json",
    "preprocessor_config.json",
)


def model_root(voice_runtime_root: Path) -> Path:
    return Path(voice_runtime_root).expanduser().resolve() / "models" / "qwen3_tts"


def canonical_model_dir(voice_runtime_root: Path, model_id: str) -> Path:
    if model_id not in MODEL_SPECS:
        raise KeyError(model_id)
    return model_root(voice_runtime_root) / model_id


def candidate_model_dirs(
    voice_runtime_root: Path,
    model_id: str,
    *,
    model_root_override: Path | None = None,
) -> list[Path]:
    if model_id not in MODEL_SPECS:
        raise KeyError(model_id)
    upstream_slug = str(MODEL_SPECS[model_id]["upstream_model_id"]).rsplit("/", 1)[-1]
    root = Path(model_root_override).expanduser().resolve() if model_root_override is not None else model_root(voice_runtime_root)
    return [root / model_id, root / upstream_slug]


def _weight_index_status(directory: Path) -> tuple[bool, list[str], str]:
    index_candidates = [directory / "model.safetensors.index.json", directory / "pytorch_model.bin.index.json"]
    for index_path in index_candidates:
        if not index_path.exists():
            continue
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            weight_map = payload.get("weight_map") if isinstance(payload, dict) else None
            files = sorted({str(value) for value in (weight_map or {}).values() if str(value).strip()})
        except Exception:
            return False, [index_path.name], "invalid_weight_index"
        if not files:
            return False, [index_path.name], "empty_weight_index"
        missing = [name for name in files if not (directory / name).exists()]
        return not missing, missing, "indexed_weights"

    weight_files = [
        *directory.glob("*.safetensors"),
        *directory.glob("pytorch_model*.bin"),
        *directory.glob("*.pt"),
    ]
    return bool(weight_files), ([] if weight_files else ["model weights"]), "direct_weights"


def probe_model_snapshot_directory(
    directory: Path,
    model_id: str,
    *,
    canonical_path: Path | None = None,
) -> dict[str, Any]:
    """Validate one already-resolved Qwen3-TTS snapshot directory.

    Phase 4.5.5 reuses this content validator for Hugging Face cache snapshots.
    It does not resolve cache refs and it never downloads or mutates files.
    """

    if model_id not in MODEL_SPECS:
        return {
            "probe_id": "qwen3_tts_model_snapshot",
            "state": "not_installed",
            "model_id": model_id,
            "message": "Unknown Qwen3-TTS model ID.",
            "missing_paths": [],
        }

    directory = Path(directory).expanduser()
    canonical = Path(canonical_path).expanduser() if canonical_path is not None else directory
    if not directory.exists() or not directory.is_dir():
        return {
            "probe_id": "qwen3_tts_model_snapshot",
            "state": "not_installed",
            "model_id": model_id,
            "repo_id": MODEL_SPECS[model_id]["upstream_model_id"],
            "canonical_path": str(canonical),
            "resolved_path": "",
            "missing_paths": [
                str(canonical / "config.json"),
                str(canonical / "<model weights>"),
                str(canonical / "tokenizer_config.json"),
                str(canonical / "vocab.json"),
                str(canonical / "merges.txt"),
                str(canonical / "preprocessor_config.json"),
                str(canonical / "speech_tokenizer" / "config.json"),
                str(canonical / "speech_tokenizer" / "<model weights>"),
                str(canonical / "speech_tokenizer" / "preprocessor_config.json"),
            ],
            "message": "Local Qwen3-TTS snapshot is not installed.",
        }

    missing: list[str] = []
    for rel in REQUIRED_ROOT_FILES:
        if not (directory / rel).is_file():
            missing.append(str(directory / rel))

    weights_ok, missing_weights, weight_mode = _weight_index_status(directory)
    missing.extend(str(directory / item) for item in missing_weights)

    speech_tokenizer_dir = directory / "speech_tokenizer"
    for rel in REQUIRED_SPEECH_TOKENIZER_FILES:
        if not (speech_tokenizer_dir / rel).is_file():
            missing.append(str(speech_tokenizer_dir / rel))
    speech_weights_ok, missing_speech_weights, speech_weight_mode = _weight_index_status(speech_tokenizer_dir)
    missing.extend(str(speech_tokenizer_dir / item) for item in missing_speech_weights)

    try:
        has_content = any(directory.iterdir())
    except OSError:
        has_content = False

    if not missing and weights_ok and speech_weights_ok:
        state = "installed"
        message = "Local Qwen3-TTS snapshot is ready, including its bundled speech tokenizer."
    elif has_content:
        state = "partial"
        message = "Local Qwen3-TTS snapshot exists but is incomplete."
    else:
        state = "not_installed"
        message = "Local Qwen3-TTS snapshot directory is empty."

    processor_markers = [
        name for name in ("tokenizer_config.json", "preprocessor_config.json", "processor_config.json", "generation_config.json", "vocab.json", "merges.txt")
        if (directory / name).exists()
    ]
    return {
        "probe_id": "qwen3_tts_model_snapshot",
        "state": state,
        "model_id": model_id,
        "repo_id": MODEL_SPECS[model_id]["upstream_model_id"],
        "canonical_path": str(canonical),
        "resolved_path": str(directory),
        "missing_paths": missing,
        "weight_mode": weight_mode,
        "speech_tokenizer_weight_mode": speech_weight_mode,
        "processor_markers": processor_markers,
        "message": message,
    }


def probe_model_install(
    voice_runtime_root: Path,
    model_id: str,
    *,
    model_root_override: Path | None = None,
) -> dict[str, Any]:
    if model_id not in MODEL_SPECS:
        return {
            "probe_id": "qwen3_tts_model_snapshot",
            "state": "not_installed",
            "model_id": model_id,
            "message": "Unknown Qwen3-TTS model ID.",
            "missing_paths": [],
        }

    candidates = candidate_model_dirs(voice_runtime_root, model_id, model_root_override=model_root_override)
    directory = next((item for item in candidates if item.exists() and item.is_dir()), None)
    if directory is None:
        return probe_model_snapshot_directory(candidates[0], model_id, canonical_path=candidates[0])
    return probe_model_snapshot_directory(directory, model_id, canonical_path=candidates[0])


def resolve_local_model_dir(
    voice_runtime_root: Path,
    model_id: str,
    *,
    model_root_override: Path | None = None,
) -> Path | None:
    probe = probe_model_install(voice_runtime_root, model_id, model_root_override=model_root_override)
    if probe.get("state") != "installed":
        return None
    raw = str(probe.get("resolved_path") or "").strip()
    return Path(raw).resolve() if raw else None


def registry_snapshot(voice_runtime_root: Path) -> dict[str, Any]:
    rows = []
    for model_id, spec in MODEL_SPECS.items():
        probe = probe_model_install(voice_runtime_root, model_id)
        rows.append({
            "id": model_id,
            "label": spec["label"],
            "repo_id": spec["upstream_model_id"],
            "role": spec["role"],
            "size_class": spec["size_class"],
            "install": probe,
        })
    return {
        "schema_id": "neo.voice_engine.qwen3_tts.model_registry.v1",
        "model_root": str(model_root(voice_runtime_root)),
        "models": rows,
        "installed": sum(1 for row in rows if row["install"]["state"] == "installed"),
        "partial": sum(1 for row in rows if row["install"]["state"] == "partial"),
        "not_installed": sum(1 for row in rows if row["install"]["state"] == "not_installed"),
        "download_policy": "explicit_only",
    }
