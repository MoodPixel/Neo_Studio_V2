from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
NEO_DATA_DIR = ROOT / "neo_data"
BATCH_SAFETY_SCHEMA = "neo.prompt_captioning.batch_safety.v1"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "confirmed"}
    return bool(value)


def normalize_transfer_mode(value: Any) -> str:
    text = str(value or "copy").strip().lower()
    return "move" if text == "move" else "copy"


def _resolved(path_text: str) -> Path | None:
    text = str(path_text or "").strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve(strict=False)
    except Exception:
        return Path(text).expanduser().absolute()


def is_inside_neo_data(path_text: str) -> bool:
    path = _resolved(path_text)
    if path is None:
        return False
    try:
        path.relative_to(NEO_DATA_DIR.resolve(strict=False))
        return True
    except Exception:
        return False


def batch_dataset_safety(dataset: dict[str, Any] | None) -> dict[str, Any]:
    data = dataset if isinstance(dataset, dict) else {}
    output_folder = str(data.get("output_folder") or "").strip()
    transfer_mode = normalize_transfer_mode(data.get("transfer_mode") or "copy")
    confirm_move = _as_bool(data.get("confirm_move"))
    external_confirmed = _as_bool(data.get("external_output_confirmed"))
    resolved_output = str(_resolved(output_folder) or "") if output_folder else ""
    external_output = bool(output_folder) and not is_inside_neo_data(output_folder)
    errors: list[str] = []
    warnings: list[str] = []
    if transfer_mode == "move" and not confirm_move:
        errors.append("Move mode requires explicit confirmation because source images will be moved out of the input folder.")
    if external_output and not external_confirmed:
        errors.append("External output folder requires explicit confirmation because files will be written outside Neo_Data.")
    if transfer_mode == "move":
        warnings.append("Move mode changes the source folder. Copy mode is safer and remains the default.")
    if external_output:
        warnings.append("Output folder is outside Neo_Data. Confirm this external write before running batch.")
    return {
        "schema": BATCH_SAFETY_SCHEMA,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "transfer_mode": transfer_mode,
        "move_confirmed": confirm_move,
        "external_output": external_output,
        "external_output_confirmed": external_confirmed,
        "output_folder": output_folder,
        "resolved_output_folder": resolved_output,
        "neo_data_root": str(NEO_DATA_DIR),
    }
