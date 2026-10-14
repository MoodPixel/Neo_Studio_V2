from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .contracts import ACI1_ARM_DOCLING, normalized_extraction

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_WORKER = ROOT_DIR / "scripts" / "aci1_docling_worker.py"


class DoclingAdapterError(RuntimeError):
    pass


def prepare_with_docling(
    path: Path,
    *,
    python_executable: str,
    worker_path: Path = DEFAULT_WORKER,
    timeout_seconds: int = 900,
    save_raw_dir: Path | None = None,
) -> dict[str, Any]:
    """Run Docling in an external Python process and normalize its output.

    ``python_executable`` is deliberately explicit. The recommended ACI-1 setup
    points this at a dedicated Docling virtual environment, keeping Docling and
    its optional OCR/PDF dependencies out of Neo's production environment.
    """
    source = Path(path).resolve()
    worker = Path(worker_path).resolve()
    if not worker.exists():
        raise DoclingAdapterError(f"ACI-1 Docling worker is missing: {worker}")
    if not python_executable:
        raise DoclingAdapterError("A Docling Python executable is required for the isolated ACI-1 arm")

    raw_dir = Path(save_raw_dir).resolve() if save_raw_dir else None
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="neo_aci1_docling_") as temp_dir:
        result_path = Path(temp_dir) / "result.json"
        command = [
            str(python_executable),
            str(worker),
            "--input",
            str(source),
            "--output",
            str(result_path),
        ]
        if raw_dir:
            command.extend(["--raw-dir", str(raw_dir)])
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        completed = subprocess.run(
            command,
            cwd=str(ROOT_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, int(timeout_seconds)),
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "Docling worker failed").strip()
            raise DoclingAdapterError(message[-4000:])
        if not result_path.exists():
            raise DoclingAdapterError("Docling worker returned success without a result file")
        payload = json.loads(result_path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise DoclingAdapterError("Docling worker result must be an object")
    if str(payload.get("status") or "") not in {"extracted", "partial"}:
        reason = str(payload.get("error") or payload.get("status") or "conversion_failed")
        return normalized_extraction(
            arm=ACI1_ARM_DOCLING,
            source_path=source,
            parser="docling_external_worker",
            status="stored_only",
            blocks=[],
            metadata={"worker": payload, "stderr": completed.stderr[-2000:]},
        )
    return normalized_extraction(
        arm=ACI1_ARM_DOCLING,
        source_path=source,
        parser=str(payload.get("parser") or "docling_document_v2"),
        status="extracted",
        blocks=list(payload.get("blocks") or []),
        metadata={
            "docling": {k: v for k, v in payload.items() if k not in {"blocks"}},
            "worker_stderr": completed.stderr[-2000:],
        },
        raw_artifact=str(payload.get("raw_artifact") or ""),
    )
