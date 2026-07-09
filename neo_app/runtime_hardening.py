from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.runtime_data import bootstrap_neo_runtime_data

ROOT_DIR = Path(__file__).resolve().parents[1]
NEO_DATA_DIR = ROOT_DIR / "neo_data"
LOGS_DIR = NEO_DATA_DIR / "logs"
INPUTS_DIR = NEO_DATA_DIR / "inputs"
OUTPUTS_DIR = NEO_DATA_DIR / "outputs"
PROJECTS_DIR = NEO_DATA_DIR / "projects"
PACKAGES_DIR = PROJECTS_DIR / "packages"
RUNTIME_DIR = NEO_DATA_DIR / "runtime"
RUNTIME_MANIFEST_PATH = RUNTIME_DIR / "runtime_hardening_manifest.json"
STATIC_DIR = ROOT_DIR / "neo_app" / "static"
MAIN_JS = STATIC_DIR / "js" / "neo.js"
MAIN_CSS = STATIC_DIR / "css" / "neo.css"

DEPENDENCY_GROUPS: list[dict[str, Any]] = [
    {
        "id": "core_web",
        "label": "Core web runtime",
        "required": True,
        "modules": ["fastapi", "uvicorn", "pydantic", "websockets", "multipart"],
    },
    {
        "id": "image_core",
        "label": "Image and upload runtime",
        "required": True,
        "modules": ["PIL", "numpy", "cv2", "yaml"],
    },
    {
        "id": "image_detection_preview",
        "label": "ADetailer detector preview",
        "required": False,
        "modules": ["ultralytics"],
    },
    {
        "id": "memory_semantic",
        "label": "Semantic memory runtime",
        "required": True,
        "modules": ["chromadb", "sentence_transformers", "transformers", "accelerate", "safetensors", "torch"],
    },
]
FIRST_RUN_DIRS = [
    NEO_DATA_DIR,
    LOGS_DIR,
    INPUTS_DIR / "image",
    INPUTS_DIR / "image_masks",
    OUTPUTS_DIR,
    PROJECTS_DIR,
    PACKAGES_DIR,
    RUNTIME_DIR,
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR.resolve())).replace("\\", "/")
    except Exception:
        return str(path)


def _safe_write_probe(directory: Path) -> tuple[bool, str]:
    """Return a conservative writable signal without creating missing folders.

    Runtime diagnostics should be read-only. The explicit setup endpoint is the
    path that creates ``neo_data`` folders.
    """

    try:
        if not directory.exists():
            return False, "missing; run first-run setup"
        if not directory.is_dir():
            directory = directory.parent
        if not directory.exists():
            return False, "parent missing; run first-run setup"
        return (os.access(directory, os.W_OK), "writable" if os.access(directory, os.W_OK) else "not writable")
    except Exception as exc:
        return False, f"not writable: {exc}"


def _module_check(name: str, required: bool = True, *, group_id: str = "python", group_label: str = "Python packages") -> dict[str, Any]:
    spec = importlib.util.find_spec(name)
    status = "ready" if spec else ("missing" if required else "optional missing")
    detail = "available" if spec else ("required Python package is not importable" if required else "optional package is not installed")
    return {
        "id": f"python_module_{name.lower().replace('-', '_')}",
        "label": name,
        "status": status,
        "required": required,
        "detail": detail,
        "group_id": group_id,
        "group_label": group_label,
    }


def _path_check(path: Path, *, label: str, kind: str = "directory", writable: bool = False, required: bool = True) -> dict[str, Any]:
    exists = path.exists()
    status = "ready" if exists else ("missing" if required else "optional missing")
    detail = _rel(path)
    if exists and writable:
        ok, write_detail = _safe_write_probe(path if path.is_dir() else path.parent)
        status = "ready" if ok else "blocked"
        detail = f"{detail} · {write_detail}"
    return {"id": f"path_{path.name.lower().replace('.', '_') or 'root'}", "label": label, "status": status, "kind": kind, "path": _rel(path), "detail": detail}


def _read_manifest() -> dict[str, Any]:
    try:
        if not RUNTIME_MANIFEST_PATH.exists():
            return {}
        data = json.loads(RUNTIME_MANIFEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_manifest(payload: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RUNTIME_MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RUNTIME_MANIFEST_PATH)


def runtime_hardening_payload() -> dict[str, Any]:
    """Return read-only packaging/runtime diagnostics.

    This is intentionally local and conservative: it reports startup/portable-path
    readiness without launching providers, installing packages, or mutating data.
    """
    path_checks = [
        _path_check(ROOT_DIR, label="Repo root", writable=False),
        _path_check(NEO_DATA_DIR, label="Neo data root", writable=True),
        _path_check(LOGS_DIR, label="Runtime logs", writable=True),
        _path_check(INPUTS_DIR, label="Input workspace", writable=True),
        _path_check(OUTPUTS_DIR, label="Output workspace", writable=True),
        _path_check(PROJECTS_DIR, label="Project workspace data", writable=True),
        _path_check(PACKAGES_DIR, label="Project package output", writable=True),
        _path_check(STATIC_DIR, label="Static UI directory", writable=False),
        _path_check(MAIN_JS, label="Main UI script", kind="file", writable=False),
        _path_check(MAIN_CSS, label="Main UI stylesheet", kind="file", writable=False),
    ]
    dependency_checks: list[dict[str, Any]] = []
    for group in DEPENDENCY_GROUPS:
        required = bool(group.get("required"))
        group_id = str(group.get("id") or "python")
        group_label = str(group.get("label") or group_id)
        for name in group.get("modules") or []:
            dependency_checks.append(_module_check(str(name), required, group_id=group_id, group_label=group_label))
    python_ok = sys.version_info >= (3, 10)
    python_check = {
        "id": "python_version",
        "label": "Python version",
        "status": "ready" if python_ok else "blocked",
        "detail": platform.python_version(),
        "required": True,
    }
    env = {
        "NEO_HOST": os.environ.get("NEO_HOST", "127.0.0.1"),
        "NEO_PORT": os.environ.get("NEO_PORT", "7860"),
        "NEO_BACKEND_BASE_URL": os.environ.get("NEO_BACKEND_BASE_URL", "http://localhost:5001"),
    }
    launcher = {
        "recommended_command": "python -m neo_app.main --host 127.0.0.1 --port 7860",
        "quiet_command": "python -m neo_app.main --quiet",
        "dev_command": "python -m neo_app.main --dev",
        "portable_policy": "Keep runtime writes inside neo_data; avoid absolute user-machine paths in records when a Neo-relative path can be stored.",
    }
    first_run_missing = [_rel(path) for path in FIRST_RUN_DIRS if not path.exists()]
    checks = [python_check, *path_checks, *dependency_checks]
    failed_statuses = {"missing", "blocked"}
    required_failures = [item for item in checks if item.get("required", True) and str(item.get("status") or "").lower() in failed_statuses]
    warnings = [item for item in checks if str(item.get("status") or "").lower() in {"optional missing", "warning"}]
    manifest = _read_manifest()
    status = "ready" if not required_failures else "needs setup"
    if any(str(item.get("status") or "").lower() == "blocked" for item in required_failures):
        status = "blocked"
    return {
        "schema_id": "neo.runtime.hardening.v1",
        "release_stage": "runtime_readiness",
        "status": status,
        "generated_at": _now(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "environment": env,
        "launcher": launcher,
        "portable_paths": {
            "root": _rel(ROOT_DIR),
            "neo_data": _rel(NEO_DATA_DIR),
            "logs": _rel(LOGS_DIR),
            "outputs": _rel(OUTPUTS_DIR),
            "packages": _rel(PACKAGES_DIR),
            "manifest": _rel(RUNTIME_MANIFEST_PATH),
        },
        "first_run": {
            "ready": not first_run_missing,
            "missing_dirs": first_run_missing,
            "setup_endpoint": "/api/runtime/hardening/setup",
        },
        "checks": checks,
        "required_failure_count": len(required_failures),
        "warning_count": len(warnings),
        "attention_items": required_failures[:12],
        "manifest": manifest,
        "safe_actions": [
            {"id": "setup_runtime_dirs", "label": "Create missing runtime directories", "endpoint": "/api/runtime/hardening/setup", "safe": True},
            {"id": "refresh_runtime_checks", "label": "Refresh runtime diagnostics", "endpoint": "/api/runtime/hardening", "safe": True},
        ],
    }


def runtime_hardening_setup_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run Neo's full first-run bootstrap and write a local hardening manifest."""

    before_missing = {_rel(path) for path in FIRST_RUN_DIRS if not path.exists()}
    bootstrap = bootstrap_neo_runtime_data()
    created = sorted(path for path in before_missing if (ROOT_DIR / path).exists())
    already_ready = sorted(_rel(path) for path in FIRST_RUN_DIRS if path.exists() and _rel(path) not in set(created))
    manifest = {
        "schema_id": "neo.runtime.hardening.manifest.v1",
        "release_stage": "runtime_readiness",
        "updated_at": _now(),
        "created_paths": created,
        "already_ready_paths": already_ready,
        "notes": str((payload or {}).get("notes") or "Safe first-run setup completed locally."),
        "bootstrap": {
            "schema_id": bootstrap.get("schema_id"),
            "status": bootstrap.get("status"),
            "neo_data_root": bootstrap.get("neo_data_root"),
            "runtime_only": True,
        },
    }
    _write_manifest(manifest)
    refreshed = runtime_hardening_payload()
    return {
        "ok": True,
        "schema_id": "neo.runtime.hardening.setup.v1",
        "created_paths": created,
        "already_ready_paths": already_ready,
        "manifest": manifest,
        "bootstrap": bootstrap,
        "runtime_hardening": refreshed,
    }
