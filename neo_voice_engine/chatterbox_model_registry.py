from __future__ import annotations

from pathlib import Path
from typing import Any

MODEL_SPECS: dict[str, dict[str, Any]] = {
    "chatterbox_turbo": {
        "label": "Chatterbox Turbo",
        "repo_id": "ResembleAI/chatterbox-turbo",
        "loader": "turbo",
        "required_files": [
            "ve.safetensors",
            "t3_turbo_v1.safetensors",
            "s3gen_meanflow.safetensors",
            "conds.pt",
            "added_tokens.json",
            "merges.txt",
            "special_tokens_map.json",
            "tokenizer_config.json",
            "vocab.json",
        ],
    },
    "chatterbox_multilingual": {
        "label": "Chatterbox Multilingual V3",
        "repo_id": "ResembleAI/chatterbox",
        "loader": "multilingual_v3",
        "required_files": [
            "ve.pt",
            "t3_mtl23ls_v3.safetensors",
            "s3gen.pt",
            "grapheme_mtl_merged_expanded_v1.json",
            "conds.pt",
            "Cangjie5_TC.json",
        ],
    },
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def probe_model_snapshot_directory(snapshot_path: Path, model_id: str, *, canonical_path: Path | None = None) -> dict[str, Any]:
    model_id = _clean(model_id)
    spec = MODEL_SPECS.get(model_id)
    path = Path(snapshot_path).expanduser().resolve()
    canonical = Path(canonical_path or path).expanduser().resolve()
    if spec is None:
        return {
            "probe_id": "chatterbox_model_snapshot",
            "state": "not_installed",
            "model_id": model_id,
            "resolved_path": str(path),
            "canonical_path": str(canonical),
            "missing_paths": [],
            "errors": ["unknown_chatterbox_model_id"],
            "message": "Unknown Chatterbox model ID.",
        }
    required = [path / rel for rel in spec["required_files"]]
    existing = [item for item in required if item.exists() and item.is_file()]
    missing = [str(item) for item in required if not item.exists() or not item.is_file()]
    if not path.exists() or not path.is_dir():
        state = "not_installed"
        message = "Chatterbox snapshot directory is not installed."
    elif missing and existing:
        state = "partial"
        message = "Chatterbox snapshot exists but required model files are incomplete."
    elif missing:
        state = "not_installed"
        message = "Chatterbox snapshot is missing required model files."
    else:
        state = "installed"
        message = "Chatterbox snapshot contains all model files required by Neo's local loader."
    return {
        "probe_id": "chatterbox_model_snapshot",
        "state": state,
        "model_id": model_id,
        "repo_id": spec["repo_id"],
        "loader": spec["loader"],
        "resolved_path": str(path),
        "canonical_path": str(canonical),
        "required_paths": [str(item) for item in required],
        "missing_paths": missing,
        "existing_count": len(existing),
        "required_count": len(required),
        "errors": [],
        "message": message,
    }
