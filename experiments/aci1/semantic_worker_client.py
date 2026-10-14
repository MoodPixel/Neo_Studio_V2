from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


class SemanticWorkerError(RuntimeError):
    pass


def _resolve_python(executable: str) -> str:
    requested = str(executable or "").strip()
    if not requested:
        raise SemanticWorkerError("Semantic worker Python executable is required")
    candidate = Path(requested)
    if candidate.exists():
        return str(candidate.resolve())
    resolved = shutil.which(requested)
    if not resolved:
        raise SemanticWorkerError(f"Semantic worker Python executable does not exist: {requested}")
    return str(Path(resolved).resolve())


def run_semantic_worker_batch(
    *,
    python_executable: str,
    worker_path: Path,
    jobs: list[dict[str, Any]],
    output_dir: Path,
    embedding_model: str,
    reranker_model: str,
    device: str | None,
    candidate_k: int,
    top_k: int,
    timeout_seconds: int = 7200,
) -> dict[str, Any]:
    python_path = _resolve_python(python_executable)
    worker = Path(worker_path).resolve()
    if not worker.exists():
        raise SemanticWorkerError(f"Semantic worker script is missing: {worker}")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "semantic_worker"
    workspace.mkdir(parents=True, exist_ok=True)

    request_payload = {
        "schema_id": "neo.aci1.semantic_worker.request.v1",
        "embedding_model": str(embedding_model or "").strip(),
        "reranker_model": str(reranker_model or "").strip(),
        "device": str(device or "").strip(),
        "candidate_k": max(1, int(candidate_k)),
        "top_k": max(1, int(top_k)),
        "workspace": str((workspace / "indexes").resolve()),
        "jobs": jobs,
    }

    with tempfile.TemporaryDirectory(prefix="neo_aci1_semantic_") as temp_dir:
        temp = Path(temp_dir)
        request_path = temp / "request.json"
        result_path = temp / "result.json"
        request_path.write_text(json.dumps(request_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        command = [
            python_path,
            str(worker),
            "--request",
            str(request_path),
            "--output",
            str(result_path),
        ]
        stdout_path = temp / "semantic_worker.stdout.log"
        stderr_path = temp / "semantic_worker.stderr.log"
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        started = time.monotonic()
        last_heartbeat = started
        try:
            with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                process = subprocess.Popen(
                    command,
                    cwd=str(worker.parents[1]),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=env,
                )
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if elapsed > max(60, int(timeout_seconds)):
                        process.kill()
                        process.wait(timeout=10)
                        raise SemanticWorkerError(f"Semantic worker timed out after {timeout_seconds}s")
                    if time.monotonic() - last_heartbeat >= 30:
                        print(f"[ACI-1][SEMANTIC] worker active · {int(elapsed)}s elapsed", flush=True)
                        last_heartbeat = time.monotonic()
                    time.sleep(1.0)
                return_code = int(process.returncode or 0)
        except SemanticWorkerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SemanticWorkerError(f"Semantic worker could not start: {exc}") from exc

        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        stdout_tail = stdout_text[-5000:]
        stderr_tail = stderr_text[-5000:]
        if not result_path.exists():
            detail = stderr_tail or stdout_tail or f"exit code {return_code}"
            raise SemanticWorkerError(f"Semantic worker produced no result: {detail}")
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SemanticWorkerError(f"Semantic worker returned invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SemanticWorkerError("Semantic worker result must be a JSON object")
    payload.setdefault("worker", {})
    if isinstance(payload.get("worker"), dict):
        payload["worker"].update(
            {
                "python": python_path,
                "exit_code": return_code,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
        )
    return payload
