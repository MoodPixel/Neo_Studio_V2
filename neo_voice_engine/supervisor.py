from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable

from .config import GatewayConfig
from .errors import VoiceEngineError
from .worker_client import HttpVoiceWorkerClient, VoiceWorkerClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkerSpec:
    engine_id: str
    label: str
    base_url: str
    command: tuple[str, ...] = ()
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    managed: bool = False
    auto_start: bool = False
    startup_policy: str = "manual"
    runtime_install_state: str = "external"
    runtime_install_message: str = ""
    runtime_missing_paths: tuple[str, ...] = ()
    health_path: str = "/api/voice/health"
    startup_timeout_seconds: float | None = None
    source: str = "manual"
    manifest_id: str = ""
    environment_kind: str = "external"
    environment_root: str = ""
    python_path: str = ""


@dataclass
class WorkerRuntime:
    spec: WorkerSpec
    client: VoiceWorkerClient
    process: subprocess.Popen[Any] | None = None
    state: str = "stopped"
    message: str = "Worker registered but not probed."
    last_error: str = ""
    last_checked: str = ""
    pid: int | None = None
    log_path: str = ""
    restart_attempts: list[float] = field(default_factory=list)
    recovery_count: int = 0
    crash_count: int = 0
    last_recovery: str = ""
    last_exit_code: int | None = None


class WorkerSupervisor:
    """Owns worker process lifecycle without importing worker ML dependencies."""

    @staticmethod
    def _managed_on_demand(spec: WorkerSpec) -> bool:
        # ``auto_start`` is the legacy VO-E5 spelling. Manifest v1 now also
        # exposes an explicit startup policy so registration/discovery never
        # implies process launch. Existing Chatterbox manifests remain compatible.
        return bool(spec.managed and (spec.startup_policy == "on_demand" or spec.auto_start))

    @staticmethod
    def _runtime_install_ready(spec: WorkerSpec) -> bool:
        return str(spec.runtime_install_state or "external").lower() in {"installed", "external"}

    def __init__(
        self,
        config: GatewayConfig,
        *,
        client_factory: Callable[[WorkerSpec], VoiceWorkerClient] | None = None,
        process_factory: Callable[..., subprocess.Popen[Any]] | None = None,
    ) -> None:
        self.config = config
        self.config.ensure_runtime_dirs()
        self._client_factory = client_factory or (
            lambda spec: HttpVoiceWorkerClient(
                spec.base_url,
                timeout=self.config.worker_http_timeout_seconds,
                health_path=spec.health_path,
            )
        )
        self._process_factory = process_factory or subprocess.Popen
        self._workers: dict[str, WorkerRuntime] = {}
        self._lock = threading.RLock()

    def register(self, spec: WorkerSpec, *, client: VoiceWorkerClient | None = None, replace: bool = False) -> None:
        engine_id = str(spec.engine_id or "").strip()
        if not engine_id:
            raise ValueError("Worker engine_id is required")
        with self._lock:
            if engine_id in self._workers and not replace:
                raise ValueError(f"Worker '{engine_id}' is already registered")
            existing = self._workers.get(engine_id)
            if existing and existing.process is not None and existing.process.poll() is None:
                raise ValueError(f"Worker '{engine_id}' is running and cannot be replaced without stopping it first")
            runtime = WorkerRuntime(spec=spec, client=client or self._client_factory(spec))
            if spec.managed and not self._runtime_install_ready(spec):
                runtime.state = str(spec.runtime_install_state or "not_installed").lower()
                runtime.message = spec.runtime_install_message or "Managed worker runtime is not fully installed."
            elif self._managed_on_demand(spec):
                runtime.message = "Managed worker is registered and will start on demand."
            self._workers[engine_id] = runtime

    def unregister(self, engine_id: str, *, force: bool = False) -> bool:
        with self._lock:
            runtime = self._workers.get(engine_id)
        if runtime is None:
            return False
        if runtime.process is not None and runtime.process.poll() is None:
            if not force:
                raise ValueError(f"Worker '{engine_id}' is running")
            self.stop(engine_id, force=True)
        with self._lock:
            self._workers.pop(engine_id, None)
        return True

    def sync_manifest_workers(self, specs: list[WorkerSpec]) -> dict[str, Any]:
        """Synchronize only manifest-owned workers; manual/test registrations are preserved."""
        desired = {spec.engine_id: spec for spec in specs}
        added: list[str] = []
        updated: list[str] = []
        removed: list[str] = []
        conflicts: list[dict[str, Any]] = []

        with self._lock:
            existing_ids = list(self._workers)
        for engine_id in existing_ids:
            with self._lock:
                runtime = self._workers.get(engine_id)
            if runtime is None or runtime.spec.source != "manifest" or engine_id in desired:
                continue
            try:
                self.unregister(engine_id, force=True)
                removed.append(engine_id)
            except Exception as exc:  # noqa: BLE001
                conflicts.append({"engine_id": engine_id, "code": "manifest_worker_remove_failed", "message": str(exc)})

        for engine_id, spec in desired.items():
            with self._lock:
                runtime = self._workers.get(engine_id)
            if runtime is None:
                self.register(spec)
                added.append(engine_id)
                continue
            if runtime.spec.source != "manifest":
                conflicts.append(
                    {
                        "engine_id": engine_id,
                        "code": "manual_worker_conflict",
                        "message": "Manifest worker could not replace an existing non-manifest worker registration.",
                    }
                )
                continue
            if runtime.spec == spec:
                continue
            try:
                if runtime.process is not None and runtime.process.poll() is None:
                    self.stop(engine_id, force=True)
                self.register(spec, replace=True)
                updated.append(engine_id)
            except Exception as exc:  # noqa: BLE001
                conflicts.append({"engine_id": engine_id, "code": "manifest_worker_update_failed", "message": str(exc)})

        return {"added": added, "updated": updated, "removed": removed, "conflicts": conflicts}

    def engine_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._workers)

    def client(self, engine_id: str) -> VoiceWorkerClient:
        with self._lock:
            runtime = self._workers.get(engine_id)
        if runtime is None:
            raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
        return runtime.client

    def spec(self, engine_id: str) -> WorkerSpec:
        with self._lock:
            runtime = self._workers.get(engine_id)
        if runtime is None:
            raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
        return runtime.spec

    def probe(self, engine_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._workers.get(engine_id)
        if runtime is None:
            raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
        if runtime.spec.managed and not self._runtime_install_ready(runtime.spec):
            install_state = str(runtime.spec.runtime_install_state or "not_installed").lower()
            with self._lock:
                runtime.state = install_state
                runtime.message = runtime.spec.runtime_install_message or "Managed worker runtime is not fully installed."
                runtime.last_error = ""
                runtime.last_checked = _now()
                runtime.pid = None
            return {
                "status": install_state,
                "message": runtime.message,
                "startup_policy": runtime.spec.startup_policy,
                "installation": {
                    "state": install_state,
                    "missing_paths": list(runtime.spec.runtime_missing_paths),
                },
            }
        if runtime.spec.managed and runtime.process is not None:
            exit_code = runtime.process.poll()
            if exit_code is not None:
                with self._lock:
                    if runtime.last_exit_code != int(exit_code):
                        runtime.crash_count += 1
                    runtime.last_exit_code = int(exit_code)
                    runtime.state = "failed"
                    runtime.message = f"Managed worker process exited with code {exit_code}."
                    runtime.last_error = runtime.message
                    runtime.last_checked = _now()
                    runtime.pid = None
                return {"status": "unavailable", "message": runtime.message, "exit_code": exit_code}
        try:
            health = runtime.client.health()
            remote_status = str(health.get("status") or health.get("state") or "ready").strip().lower()
            ready = remote_status in {"ready", "connected", "ok", "healthy"}
            with self._lock:
                runtime.state = "ready" if ready else ("degraded" if remote_status not in {"failed", "unavailable", "dependency_missing"} else "failed")
                runtime.message = str(health.get("message") or f"Worker reported {remote_status}.")
                runtime.last_error = "" if ready else runtime.message
                runtime.last_checked = _now()
                runtime.pid = runtime.process.pid if runtime.process and runtime.process.poll() is None else runtime.pid
            return health
        except Exception as exc:  # noqa: BLE001 - supervisor normalizes process/service failures.
            # A managed auto-start worker that has never been launched is not a
            # failed dependency merely because its loopback port is currently
            # closed. VO-E5 keeps Chatterbox stopped until first executable
            # work, so health polling must preserve that idle/stopped state.
            with self._lock:
                if (
                    self._managed_on_demand(runtime.spec)
                    and runtime.process is None
                    and runtime.state == "stopped"
                    and runtime.last_exit_code is None
                ):
                    runtime.message = "Managed worker is stopped and will start on demand."
                    runtime.last_error = ""
                    runtime.last_checked = _now()
                    return {
                        "status": "stopped",
                        "message": runtime.message,
                        "auto_start": runtime.spec.auto_start,
                        "startup_policy": runtime.spec.startup_policy,
                    }
                runtime.state = "failed"
                runtime.message = f"Worker probe failed: {exc}"
                runtime.last_error = str(exc)
                runtime.last_checked = _now()
            return {"status": "unavailable", "message": str(exc)}

    def _open_log(self, engine_id: str):
        path = self.config.logs_root / f"worker_{engine_id}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path, path.open("ab")

    def start(self, engine_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._workers.get(engine_id)
        if runtime is None:
            raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
        if not runtime.spec.managed:
            health = self.probe(engine_id)
            if str(health.get("status") or "").lower() in {"ready", "connected", "ok", "healthy"}:
                return self.public_worker(engine_id)
            raise VoiceEngineError(
                "worker_unavailable",
                f"External worker '{engine_id}' is not reachable and cannot be started by this gateway.",
                retryable=True,
                http_status=503,
            )
        if not self._runtime_install_ready(runtime.spec):
            raise VoiceEngineError(
                "dependency_missing",
                f"Managed worker '{engine_id}' runtime is not fully installed and cannot be started.",
                details={
                    "engine_id": engine_id,
                    "runtime_install_state": runtime.spec.runtime_install_state,
                    "missing_paths": list(runtime.spec.runtime_missing_paths),
                    "message": runtime.spec.runtime_install_message,
                },
                http_status=409,
            )
        if not runtime.spec.command:
            raise VoiceEngineError("dependency_missing", f"Managed worker '{engine_id}' has no configured launch command.", http_status=503)
        if runtime.process is not None and runtime.process.poll() is None:
            self.probe(engine_id)
            return self.public_worker(engine_id)

        log_path, log_handle = self._open_log(engine_id)
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in runtime.spec.env.items()})
        cwd = str((runtime.spec.cwd or self.config.project_root).resolve())
        try:
            process = self._process_factory(
                list(runtime.spec.command),
                cwd=cwd,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            log_handle.close()
            with self._lock:
                runtime.state = "failed"
                runtime.last_error = str(exc)
                runtime.message = f"Worker process failed to start: {exc}"
            raise VoiceEngineError("worker_unavailable", runtime.message, retryable=True, http_status=503) from exc

        with self._lock:
            runtime.process = process
            runtime.pid = getattr(process, "pid", None)
            runtime.state = "starting"
            runtime.message = "Worker process started; waiting for health readiness."
            runtime.log_path = str(log_path)
        try:
            log_handle.close()
        except Exception:
            pass

        deadline = time.monotonic() + float(runtime.spec.startup_timeout_seconds or self.config.worker_start_timeout_seconds)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                with self._lock:
                    runtime.state = "failed"
                    runtime.message = f"Worker process exited during startup with code {process.returncode}."
                    runtime.last_error = runtime.message
                raise VoiceEngineError("worker_crashed", runtime.message, retryable=True, details={"log_path": str(log_path)}, http_status=503)
            health = self.probe(engine_id)
            if str(health.get("status") or "").lower() in {"ready", "connected", "ok", "healthy"}:
                return self.public_worker(engine_id)
            time.sleep(0.2)

        with self._lock:
            runtime.state = "failed"
            runtime.message = "Worker startup timed out before health became ready."
            runtime.last_error = runtime.message
        raise VoiceEngineError("worker_unavailable", runtime.message, retryable=True, details={"log_path": str(log_path)}, http_status=503)

    def _prune_restart_attempts(self, runtime: WorkerRuntime) -> None:
        cutoff = time.monotonic() - float(self.config.worker_restart_window_seconds)
        runtime.restart_attempts = [stamp for stamp in runtime.restart_attempts if stamp >= cutoff]

    def recover(self, engine_id: str, *, reason: str = "worker_failure") -> dict[str, Any]:
        with self._lock:
            runtime = self._workers.get(engine_id)
            if runtime is None:
                raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
            if not self._managed_on_demand(runtime.spec):
                raise VoiceEngineError(
                    "worker_unavailable",
                    f"Voice worker '{engine_id}' is not gateway-managed for automatic recovery.",
                    retryable=True,
                    details={
                        "managed": runtime.spec.managed,
                        "auto_start": runtime.spec.auto_start,
                        "startup_policy": runtime.spec.startup_policy,
                    },
                    http_status=503,
                )
            self._prune_restart_attempts(runtime)
            if len(runtime.restart_attempts) >= int(self.config.worker_max_restarts):
                runtime.state = "recovery_exhausted"
                runtime.message = "Automatic worker recovery budget is exhausted."
                runtime.last_error = runtime.message
                raise VoiceEngineError(
                    "worker_recovery_exhausted",
                    runtime.message,
                    retryable=True,
                    details={
                        "engine_id": engine_id,
                        "attempts": len(runtime.restart_attempts),
                        "window_seconds": self.config.worker_restart_window_seconds,
                    },
                    http_status=503,
                )
            runtime.restart_attempts.append(time.monotonic())
            runtime.recovery_count += 1
            attempt = len(runtime.restart_attempts)
            runtime.state = "recovering"
            runtime.message = f"Recovering managed worker after {reason} (attempt {attempt})."
            runtime.last_recovery = _now()

        backoff = float(self.config.worker_restart_backoff_seconds) * max(0, attempt - 1)
        if backoff > 0:
            time.sleep(backoff)
        try:
            self.stop(engine_id, force=True)
            worker = self.start(engine_id)
            worker["recovered"] = True
            worker["recovery_reason"] = reason
            return worker
        except VoiceEngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                runtime = self._workers.get(engine_id)
                if runtime is not None:
                    runtime.state = "failed"
                    runtime.message = f"Worker recovery failed: {exc}"
                    runtime.last_error = str(exc)
            raise VoiceEngineError("worker_unavailable", f"Worker recovery failed: {exc}", retryable=True, http_status=503) from exc

    def recover_after_failure(self, engine_id: str, *, code: str, message: str = "") -> dict[str, Any]:
        if code not in {"worker_crashed", "worker_unavailable"}:
            return {"attempted": False, "reason": "failure_not_recoverable", "code": code}
        with self._lock:
            runtime = self._workers.get(engine_id)
            if runtime is None or not self._managed_on_demand(runtime.spec):
                return {"attempted": False, "reason": "worker_not_managed", "code": code}
            runtime.last_error = message or runtime.last_error
        try:
            worker = self.recover(engine_id, reason=code)
            return {"attempted": True, "recovered": True, "worker": worker}
        except VoiceEngineError as exc:
            return {"attempted": True, "recovered": False, "error": exc.payload()["error"]}

    def ensure_ready(self, engine_id: str) -> None:
        with self._lock:
            runtime = self._workers.get(engine_id)
        if runtime is None:
            raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
        if runtime.spec.managed and not self._runtime_install_ready(runtime.spec):
            raise VoiceEngineError(
                "dependency_missing",
                f"Voice worker '{engine_id}' runtime is not fully installed.",
                details={
                    "engine_id": engine_id,
                    "runtime_install_state": runtime.spec.runtime_install_state,
                    "missing_paths": list(runtime.spec.runtime_missing_paths),
                    "message": runtime.spec.runtime_install_message,
                },
                http_status=409,
            )
        if self._managed_on_demand(runtime.spec) and runtime.process is None and runtime.state != "recovery_exhausted":
            # Discovery/health never starts workers. Executable work reaches this
            # method only after model/install admission, so this is the single
            # automatic first-start boundary.
            health = self.probe(engine_id)
            if str(health.get("status") or "").lower() in {"ready", "connected", "ok", "healthy"}:
                return
            self.start(engine_id)
            return
        health = self.probe(engine_id)
        if str(health.get("status") or "").lower() in {"ready", "connected", "ok", "healthy"}:
            return
        if self._managed_on_demand(runtime.spec):
            self.recover(engine_id, reason="health_probe_failed")
            return
        raise VoiceEngineError(
            "worker_unavailable",
            f"Voice worker '{engine_id}' is unavailable.",
            retryable=True,
            details={"worker": self.public_worker(engine_id)},
            http_status=503,
        )

    def model_lifecycle(self, engine_id: str, model_id: str) -> dict[str, Any]:
        client = self.client(engine_id)
        method = getattr(client, "model_lifecycle", None)
        if not callable(method):
            return {"supported": False, "state": "implicit", "model_id": model_id}
        try:
            payload = method(model_id)
        except VoiceEngineError as exc:
            if exc.code == "unsupported_operation":
                return {"supported": False, "state": "implicit", "model_id": model_id}
            raise
        result = dict(payload or {})
        result.setdefault("supported", True)
        result.setdefault("model_id", model_id)
        return result

    def load_model(self, engine_id: str, model_id: str, *, device: str = "", device_index: int | None = None) -> dict[str, Any]:
        client = self.client(engine_id)
        method = getattr(client, "load_model", None)
        if not callable(method):
            return {"supported": False, "state": "implicit", "model_id": model_id}
        try:
            payload = method(model_id, device=device, device_index=device_index)
        except VoiceEngineError as exc:
            if exc.code == "unsupported_operation":
                return {"supported": False, "state": "implicit", "model_id": model_id}
            raise
        result = dict(payload or {})
        result.setdefault("supported", True)
        result.setdefault("model_id", model_id)
        return result

    def unload_model(self, engine_id: str, model_id: str) -> dict[str, Any]:
        client = self.client(engine_id)
        method = getattr(client, "unload_model", None)
        if not callable(method):
            return {"supported": False, "state": "implicit", "model_id": model_id}
        try:
            payload = method(model_id)
        except VoiceEngineError as exc:
            if exc.code == "unsupported_operation":
                return {"supported": False, "state": "implicit", "model_id": model_id}
            raise
        result = dict(payload or {})
        result.setdefault("supported", True)
        result.setdefault("model_id", model_id)
        return result

    def stop(self, engine_id: str, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            runtime = self._workers.get(engine_id)
        if runtime is None:
            raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
        process = runtime.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                if force:
                    try:
                        process.kill()
                    except Exception:
                        pass
        with self._lock:
            runtime.process = None
            runtime.pid = None
            runtime.state = "stopped"
            runtime.message = "Worker stopped by supervisor."
            runtime.last_checked = _now()
        return self.public_worker(engine_id)

    def restart(self, engine_id: str) -> dict[str, Any]:
        self.stop(engine_id, force=True)
        return self.start(engine_id)

    def public_worker(self, engine_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._workers.get(engine_id)
            if runtime is None:
                raise VoiceEngineError("worker_unavailable", f"Voice worker '{engine_id}' is not registered.", http_status=503)
            return {
                "engine_id": runtime.spec.engine_id,
                "label": runtime.spec.label,
                "base_url": runtime.spec.base_url,
                "managed": runtime.spec.managed,
                "auto_start": runtime.spec.auto_start,
                "startup_policy": runtime.spec.startup_policy,
                "source": runtime.spec.source,
                "manifest_id": runtime.spec.manifest_id,
                "environment": {
                    "kind": runtime.spec.environment_kind,
                    "root_configured": bool(runtime.spec.environment_root),
                    "python_configured": bool(runtime.spec.python_path),
                },
                "installation": {
                    "state": runtime.spec.runtime_install_state,
                    "message": runtime.spec.runtime_install_message,
                    "missing_paths": list(runtime.spec.runtime_missing_paths),
                },
                "state": runtime.state,
                "message": runtime.message,
                "pid": runtime.pid,
                "last_error": runtime.last_error,
                "last_checked": runtime.last_checked,
                "log_path": runtime.log_path,
                "recovery": {
                    "recovery_count": runtime.recovery_count,
                    "crash_count": runtime.crash_count,
                    "last_recovery": runtime.last_recovery,
                    "last_exit_code": runtime.last_exit_code,
                    "attempts_in_window": len([stamp for stamp in runtime.restart_attempts if stamp >= time.monotonic() - float(self.config.worker_restart_window_seconds)]),
                    "max_attempts": self.config.worker_max_restarts,
                    "window_seconds": self.config.worker_restart_window_seconds,
                },
            }

    def probe_all(self) -> dict[str, Any]:
        for engine_id in self.engine_ids():
            self.probe(engine_id)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        ids = self.engine_ids()
        items = [self.public_worker(engine_id) for engine_id in ids]
        return {
            "registered": len(items),
            "ready": sum(1 for item in items if item["state"] == "ready"),
            "failed": sum(1 for item in items if item["state"] in {"failed", "recovery_exhausted"}),
            "starting": sum(1 for item in items if item["state"] in {"starting", "recovering"}),
            "items": items,
        }

    def shutdown(self) -> None:
        for engine_id in self.engine_ids():
            try:
                runtime = self._workers.get(engine_id)
                if runtime and runtime.spec.managed:
                    self.stop(engine_id, force=True)
            except Exception:
                continue
