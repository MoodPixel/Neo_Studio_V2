from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import platform
import shutil
import subprocess
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
NEO_DATA_DIR = ROOT_DIR / "neo_data"
NODE_MANAGER_DIR = NEO_DATA_DIR / "admin" / "image" / "node_manager"
SETTINGS_PATH = NODE_MANAGER_DIR / "settings.json"
NODE_RECORDS_PATH = NODE_MANAGER_DIR / "node_records.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    NODE_MANAGER_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_path(value: str | None) -> Path | None:
    if not value or not str(value).strip():
        return None
    return Path(str(value).strip()).expanduser()


def _custom_nodes_from_root(root: Path | None) -> Path | None:
    return root / "custom_nodes" if root else None


def _python_candidates_from_root(root: Path | None) -> list[Path]:
    if not root:
        return []
    if platform.system().lower() == "windows":
        return [
            root / "python_embeded" / "python.exe",
            root / "python_embedded" / "python.exe",
            root / "venv" / "Scripts" / "python.exe",
            root / ".venv" / "Scripts" / "python.exe",
        ]
    return [
        root / "python_embeded" / "python",
        root / "python_embedded" / "python",
        root / "python_embeded" / "bin" / "python",
        root / "python_embedded" / "bin" / "python",
        root / "venv" / "bin" / "python",
        root / ".venv" / "bin" / "python",
    ]


def detect_comfy_python_path(comfy_root_path: str | None = None) -> dict:
    settings = load_node_manager_settings() if SETTINGS_PATH.exists() else {}
    root_value = comfy_root_path or settings.get("comfy_root_path") or ""
    root = _safe_path(root_value)
    candidates = _python_candidates_from_root(root)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            validation = validate_comfy_python_path(str(candidate))
            return {
                "ok": validation.get("ok", False),
                "python_path": str(candidate),
                "source": "auto_detect",
                "candidates": [str(item) for item in candidates],
                "validation": validation,
            }
    fallback = shutil.which("python") or shutil.which("python3") or ""
    return {
        "ok": bool(fallback),
        "python_path": fallback,
        "source": "system_path" if fallback else "not_found",
        "candidates": [str(item) for item in candidates],
        "validation": validate_comfy_python_path(fallback) if fallback else {"ok": False, "errors": ["No ComfyUI Python executable found."]},
    }


def validate_comfy_python_path(python_path: str | None = None) -> dict:
    path_value = str(python_path or "").strip()
    if not path_value:
        return {"ok": False, "errors": ["Python path is required."]}
    path = _safe_path(path_value)
    if not path or not path.exists() or not path.is_file():
        return {"ok": False, "errors": [f"Python executable was not found: {path_value}"], "python_path": path_value}
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)], "python_path": str(path)}
    output = (completed.stdout or completed.stderr or "").strip()
    return {
        "ok": completed.returncode == 0,
        "python_path": str(path),
        "version": output,
        "errors": [] if completed.returncode == 0 else [output or "Python validation failed."],
    }


def load_node_manager_settings() -> dict:
    _ensure()
    payload = _read_json(SETTINGS_PATH, {})
    comfy_root = str(payload.get("comfy_root_path") or "")
    custom_nodes = str(payload.get("custom_nodes_path") or "")
    python_path = str(payload.get("python_path") or "")
    if comfy_root and not custom_nodes:
        custom_nodes = str(_custom_nodes_from_root(Path(comfy_root)))
    return {
        "schema_version": "neo.admin.image.node_manager.settings.v1",
        "comfy_root_path": comfy_root,
        "custom_nodes_path": custom_nodes,
        "python_path": python_path,
        "last_updated": payload.get("last_updated"),
    }


def save_node_manager_settings(payload: dict) -> dict:
    current = load_node_manager_settings()
    comfy_root = str(payload.get("comfy_root_path", current.get("comfy_root_path", "")) or "").strip()
    custom_nodes = str(payload.get("custom_nodes_path", current.get("custom_nodes_path", "")) or "").strip()
    python_path = str(payload.get("python_path", current.get("python_path", "")) or "").strip()
    if comfy_root and not custom_nodes:
        custom_nodes = str(_custom_nodes_from_root(Path(comfy_root)))
    saved = {
        "schema_version": "neo.admin.image.node_manager.settings.v1",
        "comfy_root_path": comfy_root,
        "custom_nodes_path": custom_nodes,
        "python_path": python_path,
        "last_updated": _now(),
    }
    _write_json(SETTINGS_PATH, saved)
    return get_node_manager_state(scan=False)


def load_node_records() -> dict:
    _ensure()
    payload = _read_json(NODE_RECORDS_PATH, {})
    records = payload.get("records") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        records = []
    return {
        "schema_version": "neo.admin.image.node_records.v1",
        "records": records,
        "last_updated": payload.get("last_updated") if isinstance(payload, dict) else None,
    }


def _git_remote(path: Path) -> str:
    config = path / ".git" / "config"
    if not config.exists():
        return ""
    text = config.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("url ="):
            return line.split("=", 1)[1].strip()
    return ""


def _git_branch(path: Path) -> str:
    head = path / ".git" / "HEAD"
    if not head.exists():
        return ""
    text = head.read_text(encoding="utf-8", errors="ignore").strip()
    if text.startswith("ref:"):
        return text.rsplit("/", 1)[-1]
    return text[:12]


def _scan_disk_nodes(custom_nodes_path: Path | None, tracked_names: set[str]) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    if not custom_nodes_path:
        return [], ["Set ComfyUI root path or custom_nodes path before scanning."]
    if not custom_nodes_path.exists():
        return [], [f"custom_nodes folder does not exist: {custom_nodes_path}"]
    if not custom_nodes_path.is_dir():
        return [], [f"custom_nodes path is not a folder: {custom_nodes_path}"]

    nodes: list[dict] = []
    for child in sorted(custom_nodes_path.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "__pycache__":
            continue
        has_git = (child / ".git").exists()
        node = {
            "name": child.name,
            "path": str(child),
            "installed_on_disk": True,
            "tracked_by_neo": child.name in tracked_names,
            "status": "tracked" if child.name in tracked_names else "untracked",
            "git": {
                "is_repo": has_git,
                "remote": _git_remote(child) if has_git else "",
                "branch": _git_branch(child) if has_git else "",
            },
            "requirements_txt": (child / "requirements.txt").exists(),
            "pyproject_toml": (child / "pyproject.toml").exists(),
            "install_hint": "pip requirements available" if (child / "requirements.txt").exists() else "",
        }
        nodes.append(node)
    return nodes, warnings


def node_manager_feedback_contract() -> dict:
    return {
        "schema_version": "neo.admin.image.node_manager.feedback.v1",
        "operations": ["install_github", "update", "install_requirements"],
        "states": ["idle", "installing", "installed", "failed", "restart_required"],
        "ui": {
            "disable_duplicate_actions": True,
            "show_spinner": True,
            "show_status_line": True,
            "show_recent_log": True,
        },
    }


def scan_node_manager_disk() -> dict:
    settings = load_node_manager_settings()
    records_payload = load_node_records()
    tracked_names = {str(record.get("folder_name") or record.get("name") or "") for record in records_payload.get("records", [])}
    custom_nodes_path = _safe_path(settings.get("custom_nodes_path"))
    nodes, warnings = _scan_disk_nodes(custom_nodes_path, tracked_names)
    disk_names = {node["name"] for node in nodes}
    missing = []
    for record in records_payload.get("records", []):
        name = str(record.get("folder_name") or record.get("name") or "")
        if name and name not in disk_names:
            missing.append({**record, "status": "missing_from_disk", "installed_on_disk": False, "tracked_by_neo": True})
    python_validation = validate_comfy_python_path(settings.get("python_path")) if settings.get("python_path") else {"ok": False, "errors": ["Comfy Python path is not configured."]}
    payload = {
        "schema_version": "neo.admin.image.node_manager.scan.v1",
        "settings": settings,
        "python": python_validation,
        "records": records_payload.get("records", []),
        "nodes": nodes + missing,
        "summary": {
            "disk": len(nodes),
            "tracked": len([node for node in nodes if node.get("tracked_by_neo")]),
            "untracked": len([node for node in nodes if not node.get("tracked_by_neo")]),
            "missing": len(missing),
            "warnings": len(warnings),
        },
        "warnings": warnings,
        "install_feedback": node_manager_feedback_contract(),
        "scanned_at": _now(),
    }
    return payload


def get_node_manager_state(scan: bool = False) -> dict:
    if scan:
        return scan_node_manager_disk()
    settings = load_node_manager_settings()
    return {
        "schema_version": "neo.admin.image.node_manager.state.v1",
        "settings": settings,
        "python": validate_comfy_python_path(settings.get("python_path")) if settings.get("python_path") else {"ok": False, "errors": ["Comfy Python path is not configured."]},
        "records": load_node_records().get("records", []),
        "nodes": [],
        "summary": {"disk": 0, "tracked": 0, "untracked": 0, "missing": 0, "warnings": 0},
        "warnings": [],
        "install_feedback": node_manager_feedback_contract(),
        "actions": ["save_settings", "detect_python", "validate_python", "scan_disk", "install_github", "update", "install_requirements", "open_folder"],
    }


def _slug_from_repo(repo_url: str) -> str:
    cleaned = repo_url.rstrip("/").split("/")[-1]
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-_ .").strip().replace(" ", "_")


def install_node_from_github(repo_url: str, branch: str | None = None, folder_name: str | None = None) -> dict:
    repo_url = str(repo_url or "").strip()
    if not repo_url:
        return {"ok": False, "errors": ["GitHub repository URL is required."]}
    settings = load_node_manager_settings()
    custom_nodes = _safe_path(settings.get("custom_nodes_path"))
    if not custom_nodes:
        return {"ok": False, "errors": ["Set custom_nodes path before installing nodes."]}
    custom_nodes.mkdir(parents=True, exist_ok=True)
    folder = (folder_name or _slug_from_repo(repo_url)).strip()
    if not folder:
        return {"ok": False, "errors": ["Could not determine node folder name."]}
    target = custom_nodes / folder
    if target.exists():
        return {"ok": False, "errors": [f"Node folder already exists: {target}"]}
    cmd = ["git", "clone"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([repo_url, str(target)])
    try:
        completed = subprocess.run(cmd, cwd=str(custom_nodes), capture_output=True, text=True, timeout=180, check=False)
    except FileNotFoundError:
        return {"ok": False, "errors": ["git executable was not found on PATH."]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": ["git clone timed out."]}
    if completed.returncode != 0:
        return {"ok": False, "errors": [completed.stderr.strip() or completed.stdout.strip() or "git clone failed"]}
    records = load_node_records()
    existing = [record for record in records.get("records", []) if record.get("folder_name") != folder]
    existing.append({"name": folder, "folder_name": folder, "source": "github", "repo_url": repo_url, "branch": branch or "", "installed_at": _now()})
    _write_json(NODE_RECORDS_PATH, {"schema_version": "neo.admin.image.node_records.v1", "records": existing, "last_updated": _now()})
    return {"ok": True, "operation": "install_github", "state": "restart_required", "installed": folder, "path": str(target), "scan": scan_node_manager_disk()}


def update_node(folder_name: str) -> dict:
    settings = load_node_manager_settings()
    custom_nodes = _safe_path(settings.get("custom_nodes_path"))
    folder_name = str(folder_name or "").strip()
    if not custom_nodes or not folder_name:
        return {"ok": False, "errors": ["custom_nodes path and folder_name are required."]}
    target = custom_nodes / folder_name
    if not (target / ".git").exists():
        return {"ok": False, "errors": [f"Node folder is not a git repo: {target}"]}
    completed = subprocess.run(["git", "pull"], cwd=str(target), capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode != 0:
        return {"ok": False, "errors": [completed.stderr.strip() or completed.stdout.strip() or "git pull failed"]}
    return {"ok": True, "operation": "update", "state": "restart_required", "folder_name": folder_name, "output": completed.stdout.strip(), "scan": scan_node_manager_disk()}


def install_node_requirements(folder_name: str) -> dict:
    settings = load_node_manager_settings()
    custom_nodes = _safe_path(settings.get("custom_nodes_path"))
    python_path = settings.get("python_path")
    folder_name = str(folder_name or "").strip()
    if not custom_nodes or not folder_name:
        return {"ok": False, "errors": ["custom_nodes path and folder_name are required."]}
    validation = validate_comfy_python_path(python_path)
    if not validation.get("ok"):
        return {"ok": False, "errors": validation.get("errors") or ["Comfy Python path is invalid."]}
    target = custom_nodes / folder_name
    requirements = target / "requirements.txt"
    if not requirements.exists():
        return {"ok": False, "errors": [f"requirements.txt was not found for node: {folder_name}"]}
    completed = subprocess.run(
        [str(validation["python_path"]), "-m", "pip", "install", "-r", str(requirements)],
        cwd=str(target),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        return {"ok": False, "errors": [completed.stderr.strip() or completed.stdout.strip() or "pip install failed"]}
    return {"ok": True, "operation": "install_requirements", "state": "restart_required", "folder_name": folder_name, "output": completed.stdout.strip(), "python": validation}


def open_custom_nodes_folder() -> dict:
    settings = load_node_manager_settings()
    custom_nodes = _safe_path(settings.get("custom_nodes_path"))
    if not custom_nodes:
        return {"ok": False, "errors": ["custom_nodes path is not configured."]}
    custom_nodes.mkdir(parents=True, exist_ok=True)
    system = platform.system().lower()
    try:
        if system == "windows":
            os.startfile(str(custom_nodes))  # type: ignore[attr-defined]
        elif system == "darwin":
            subprocess.Popen(["open", str(custom_nodes)])
        else:
            subprocess.Popen(["xdg-open", str(custom_nodes)])
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)], "path": str(custom_nodes)}
    return {"ok": True, "path": str(custom_nodes)}
