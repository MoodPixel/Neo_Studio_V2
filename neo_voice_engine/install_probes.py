from __future__ import annotations

from pathlib import Path
from typing import Any

from .qwen3_tts_model_registry import probe_model_install
from .qwen3_tts_runtime_resolver import probe_qwen3_tts_runtime_model
from .chatterbox_runtime_resolver import probe_chatterbox_runtime_model


def _qwen_runtime_probe(runtime_root: Path) -> dict[str, Any]:
    env_root = runtime_root / "envs" / "qwen3_tts"
    python_path = env_root / "Scripts" / "python.exe"
    ready_marker = env_root / ".neo_qwen3_tts_ready"
    existing = [str(path) for path in (python_path, ready_marker) if path.exists()]
    missing = [str(path) for path in (python_path, ready_marker) if not path.exists()]
    if not missing:
        state = "installed"
        message = "Qwen3-TTS isolated runtime passed setup verification."
    elif existing:
        state = "partial"
        message = "Qwen3-TTS isolated runtime exists but setup verification is incomplete."
    else:
        state = "not_installed"
        message = "Qwen3-TTS isolated runtime is not installed."
    return {
        "probe_id": "qwen3_tts_runtime_env",
        "state": state,
        "required_paths": [str(python_path), str(ready_marker)],
        "missing_paths": missing,
        "message": message,
    }


def _chatterbox_runtime_probe(runtime_root: Path) -> dict[str, Any]:
    env_root = runtime_root / "envs" / "chatterbox"
    python_path = env_root / "Scripts" / "python.exe"
    ready_marker = env_root / ".neo_chatterbox_ready"
    existing = [str(path) for path in (python_path, ready_marker) if path.exists()]
    missing = [str(path) for path in (python_path, ready_marker) if not path.exists()]
    if not missing:
        state = "installed"
        message = "Chatterbox isolated runtime passed setup verification."
    elif existing:
        state = "partial"
        message = "Chatterbox isolated runtime exists but setup verification is incomplete."
    else:
        state = "not_installed"
        message = "Chatterbox isolated runtime is not installed."
    return {
        "probe_id": "chatterbox_runtime_env",
        "state": state,
        "required_paths": [str(python_path), str(ready_marker)],
        "missing_paths": missing,
        "message": message,
    }


def run_install_probe(
    probe_id: str,
    *,
    runtime_root: Path,
    engine_id: str,
    model_id: str = "",
    project_root: Path | None = None,
) -> dict[str, Any]:
    probe = str(probe_id or "").strip().lower()
    if not probe:
        return {"probe_id": "", "state": "installed", "missing_paths": [], "message": "No extra probe configured."}
    if probe == "qwen3_tts_runtime_env":
        return _qwen_runtime_probe(Path(runtime_root).resolve())
    if probe == "chatterbox_runtime_env":
        return _chatterbox_runtime_probe(Path(runtime_root).resolve())
    if probe == "chatterbox_model_snapshot":
        if project_root is None:
            return {
                "probe_id": probe,
                "state": "not_installed",
                "missing_paths": [],
                "message": "Chatterbox model probe requires the Neo project root.",
            }
        return probe_chatterbox_runtime_model(
            project_root=Path(project_root).resolve(),
            model_id=model_id,
        )
    if probe == "qwen3_tts_model_snapshot":
        if project_root is not None:
            return probe_qwen3_tts_runtime_model(
                project_root=Path(project_root).resolve(),
                voice_runtime_root=Path(runtime_root).resolve(),
                model_id=model_id,
            )
        return probe_model_install(Path(runtime_root).resolve(), model_id)
    return {
        "probe_id": probe,
        "state": "not_installed",
        "missing_paths": [],
        "message": f"Unknown install probe '{probe}' for engine '{engine_id}'.",
    }
