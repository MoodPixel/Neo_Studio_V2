from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import subprocess
import threading
import time
from typing import Any, Callable, Protocol
from uuid import uuid4

from .config import GatewayConfig
from .errors import VoiceEngineError
from .supervisor import WorkerSupervisor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return max(0, int(default))


class HardwareProbe(Protocol):
    def snapshot(self) -> dict[str, Any]: ...


class NvidiaSmiHardwareProbe:
    """Dependency-free NVIDIA VRAM probe.

    The gateway intentionally does not import torch/pynvml. If nvidia-smi is not
    available, the scheduler reports CUDA telemetry as unavailable and can only use
    a model's manifest-declared CPU fallback.
    """

    def __init__(self, timeout_seconds: float = 2.0) -> None:
        self.timeout_seconds = max(0.2, float(timeout_seconds))

    def snapshot(self) -> dict[str, Any]:
        checked_at = _now()
        command = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.free,memory.used",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - fixed local diagnostic command.
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return {
                "schema_id": "neo.voice_engine.hardware.v1",
                "provider": "nvidia_smi",
                "available": False,
                "devices": [],
                "message": "nvidia-smi is not available; CUDA VRAM telemetry is unavailable.",
                "checked_at": checked_at,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "schema_id": "neo.voice_engine.hardware.v1",
                "provider": "nvidia_smi",
                "available": False,
                "devices": [],
                "message": f"CUDA telemetry probe failed: {exc}",
                "checked_at": checked_at,
            }

        if completed.returncode != 0:
            message = str(completed.stderr or completed.stdout or "nvidia-smi returned an error").strip()
            return {
                "schema_id": "neo.voice_engine.hardware.v1",
                "provider": "nvidia_smi",
                "available": False,
                "devices": [],
                "message": message,
                "checked_at": checked_at,
            }

        devices: list[dict[str, Any]] = []
        for line in str(completed.stdout or "").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                index = int(parts[0])
                total = int(float(parts[2]))
                free = int(float(parts[3]))
                used = int(float(parts[4]))
            except Exception:
                continue
            devices.append(
                {
                    "index": index,
                    "name": parts[1],
                    "total_vram_mb": max(0, total),
                    "free_vram_mb": max(0, free),
                    "used_vram_mb": max(0, used),
                }
            )
        return {
            "schema_id": "neo.voice_engine.hardware.v1",
            "provider": "nvidia_smi",
            "available": bool(devices),
            "devices": devices,
            "message": "CUDA telemetry ready." if devices else "nvidia-smi returned no GPU devices.",
            "checked_at": checked_at,
        }


@dataclass(frozen=True)
class ResourceLease:
    lease_id: str
    job_id: str
    engine_id: str
    model_id: str
    device: str
    device_index: int | None
    reserved_vram_mb: int
    acquired_at: str

    def public(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "job_id": self.job_id,
            "engine_id": self.engine_id,
            "model_id": self.model_id,
            "device": self.device,
            "device_index": self.device_index,
            "reserved_vram_mb": self.reserved_vram_mb,
            "acquired_at": self.acquired_at,
        }


class VoiceResourceScheduler:
    """GPU admission + model residency coordinator for isolated voice workers."""

    def __init__(
        self,
        config: GatewayConfig,
        supervisor: WorkerSupervisor,
        *,
        hardware_probe: HardwareProbe | None = None,
    ) -> None:
        self.config = config
        self.supervisor = supervisor
        self.hardware_probe = hardware_probe or NvidiaSmiHardwareProbe(config.gpu_probe_timeout_seconds)
        self._condition = threading.Condition(threading.RLock())
        self._waiters: list[str] = []
        self._leases: dict[str, ResourceLease] = {}
        self._residency: dict[str, dict[str, Any]] = {}
        self._last_hardware: dict[str, Any] = {
            "schema_id": "neo.voice_engine.hardware.v1",
            "provider": "unprobed",
            "available": False,
            "devices": [],
            "message": "Hardware telemetry has not been probed yet.",
            "checked_at": "",
        }
        self._last_admission_error = ""

    @staticmethod
    def _hardware(model: dict[str, Any]) -> dict[str, Any]:
        return model.get("hardware") if isinstance(model.get("hardware"), dict) else {}

    @staticmethod
    def _lifecycle(model: dict[str, Any]) -> dict[str, Any]:
        return model.get("lifecycle") if isinstance(model.get("lifecycle"), dict) else {}

    def _probe(self) -> dict[str, Any]:
        snapshot = self.hardware_probe.snapshot()
        if not isinstance(snapshot, dict):
            snapshot = {"available": False, "devices": [], "message": "Hardware probe returned an invalid payload."}
        snapshot.setdefault("schema_id", "neo.voice_engine.hardware.v1")
        snapshot.setdefault("provider", "custom")
        snapshot.setdefault("available", bool(snapshot.get("devices")))
        snapshot.setdefault("devices", [])
        snapshot.setdefault("message", "")
        snapshot.setdefault("checked_at", _now())
        with self._condition:
            self._last_hardware = dict(snapshot)
        return snapshot

    def _requirements(self, model: dict[str, Any]) -> dict[str, Any]:
        hardware = self._hardware(model)
        cuda = bool(hardware.get("cuda", False))
        cpu = bool(hardware.get("cpu", False))
        minimum = _safe_int(hardware.get("min_vram_mb"), 0)
        recommended = _safe_int(hardware.get("recommended_vram_mb"), minimum)
        return {
            "cuda": cuda,
            "cpu": cpu,
            "min_vram_mb": minimum,
            "recommended_vram_mb": max(minimum, recommended),
            "gpu_exclusive": bool(hardware.get("gpu_exclusive", cuda)),
            "allow_cpu_fallback": bool(hardware.get("allow_cpu_fallback", cpu)),
        }

    def _reserved_on_device(self, device_index: int) -> int:
        with self._condition:
            return sum(
                lease.reserved_vram_mb
                for lease in self._leases.values()
                if lease.device == "cuda" and lease.device_index == device_index
            )

    def _active_gpu_leases(self) -> int:
        return sum(1 for lease in self._leases.values() if lease.device == "cuda")

    def _choose_cuda_device(self, hardware: dict[str, Any], required_mb: int) -> tuple[int, int] | None:
        candidates: list[tuple[int, int]] = []
        reserve = max(0, int(self.config.gpu_vram_reserve_mb))
        for raw in hardware.get("devices") or []:
            if not isinstance(raw, dict):
                continue
            index = _safe_int(raw.get("index"), 0)
            free = _safe_int(raw.get("free_vram_mb"), 0)
            effective = max(0, free - reserve - self._reserved_on_device(index))
            if effective >= required_mb:
                candidates.append((index, effective))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[1], reverse=True)
        return candidates[0]

    def _residency_record(self, model: dict[str, Any]) -> dict[str, Any]:
        model_id = str(model["id"])
        record = self._residency.get(model_id)
        if record is None:
            lifecycle = self._lifecycle(model)
            idle_seconds = lifecycle.get("idle_unload_seconds")
            if idle_seconds in (None, ""):
                idle_seconds = self.config.model_idle_unload_seconds
            record = {
                "model_id": model_id,
                "engine_id": str(model["engine_id"]),
                "state": "unloaded",
                "device": "",
                "device_index": None,
                "estimated_vram_mb": self._requirements(model)["recommended_vram_mb"],
                "active_jobs": 0,
                "evictable": bool(lifecycle.get("evictable", True)),
                "idle_unload_seconds": max(0, int(idle_seconds)),
                "unload_strategy": str(lifecycle.get("unload_strategy") or "auto"),
                "load_supported": None,
                "unload_supported": None,
                "last_used_monotonic": 0.0,
                "last_used": "",
                "message": "Model has not been prepared by the gateway yet.",
            }
            self._residency[model_id] = record
        return record

    def _mark_lease(self, lease: ResourceLease, model: dict[str, Any]) -> None:
        record = self._residency_record(model)
        record["active_jobs"] = int(record.get("active_jobs") or 0) + 1
        record["device"] = lease.device
        record["device_index"] = lease.device_index
        record["estimated_vram_mb"] = max(record.get("estimated_vram_mb") or 0, lease.reserved_vram_mb)
        record["last_used_monotonic"] = time.monotonic()
        record["last_used"] = _now()

    def _evict_candidates(self, *, exclude_model_id: str = "") -> list[str]:
        now = time.monotonic()
        candidates: list[tuple[float, str]] = []
        with self._condition:
            for model_id, record in self._residency.items():
                if model_id == exclude_model_id:
                    continue
                if int(record.get("active_jobs") or 0) > 0 or not bool(record.get("evictable", True)):
                    continue
                if str(record.get("state") or "") not in {"resident", "implicit", "idle"}:
                    continue
                last = float(record.get("last_used_monotonic") or 0.0)
                candidates.append((last if last > 0 else now, model_id))
        candidates.sort(key=lambda item: item[0])
        return [model_id for _, model_id in candidates]

    def _mark_engine_unloaded(self, engine_id: str, message: str) -> None:
        with self._condition:
            for record in self._residency.values():
                if str(record.get("engine_id") or "") == engine_id and int(record.get("active_jobs") or 0) == 0:
                    record["state"] = "unloaded"
                    record["device"] = ""
                    record["device_index"] = None
                    record["message"] = message

    def unload_model(self, model_id: str, *, reason: str = "manual", force: bool = False) -> dict[str, Any]:
        with self._condition:
            record = self._residency.get(str(model_id))
            if record is None:
                return {"model_id": str(model_id), "state": "unloaded", "changed": False, "message": "Model is not resident."}
            if int(record.get("active_jobs") or 0) > 0 and not force:
                raise VoiceEngineError(
                    "model_busy",
                    f"Voice model '{model_id}' cannot be unloaded while jobs are active.",
                    retryable=True,
                    details={"model_id": model_id, "active_jobs": record.get("active_jobs")},
                    http_status=409,
                )
            engine_id = str(record.get("engine_id") or "")
            strategy = str(record.get("unload_strategy") or "auto")

        response: dict[str, Any] = {"supported": False}
        if strategy in {"auto", "worker_api"}:
            response = self.supervisor.unload_model(engine_id, str(model_id))
        if bool(response.get("supported")):
            with self._condition:
                record = self._residency.get(str(model_id))
                if record is not None:
                    record["state"] = "unloaded"
                    record["device"] = ""
                    record["device_index"] = None
                    record["unload_supported"] = True
                    record["message"] = f"Model unloaded by worker API ({reason})."
            return {"model_id": str(model_id), "state": "unloaded", "changed": True, "strategy": "worker_api", "worker": response}

        with self._condition:
            record = self._residency.get(str(model_id))
            if record is not None:
                record["unload_supported"] = False
        worker = self.supervisor.public_worker(engine_id)
        if strategy in {"auto", "stop_worker"} and bool(worker.get("managed")):
            active_engine_jobs = 0
            with self._condition:
                active_engine_jobs = sum(
                    1 for lease in self._leases.values() if lease.engine_id == engine_id and lease.model_id != str(model_id)
                )
            if active_engine_jobs == 0:
                self.supervisor.stop(engine_id, force=True)
                self._mark_engine_unloaded(engine_id, f"Managed worker stopped to unload model ({reason}).")
                return {"model_id": str(model_id), "state": "unloaded", "changed": True, "strategy": "stop_worker"}

        with self._condition:
            final_state = str((self._residency.get(str(model_id)) or {}).get("state") or "unknown")
        return {
            "model_id": str(model_id),
            "state": final_state,
            "changed": False,
            "strategy": "none",
            "message": "Worker does not expose unload and the gateway will not stop an external/busy worker.",
        }

    def _evict_for_pressure(self, exclude_model_id: str) -> bool:
        changed = False
        for model_id in self._evict_candidates(exclude_model_id=exclude_model_id):
            try:
                result = self.unload_model(model_id, reason="vram_pressure")
            except VoiceEngineError:
                continue
            if result.get("changed"):
                changed = True
        return changed

    def _maintenance(self) -> None:
        now = time.monotonic()
        candidates: list[str] = []
        with self._condition:
            for model_id, record in self._residency.items():
                if int(record.get("active_jobs") or 0) > 0 or not bool(record.get("evictable", True)):
                    continue
                threshold = max(0, int(record.get("idle_unload_seconds") or 0))
                if threshold <= 0:
                    continue
                last = float(record.get("last_used_monotonic") or 0.0)
                if last > 0 and now - last >= threshold and str(record.get("state") or "") in {"resident", "implicit", "idle"}:
                    candidates.append(model_id)
        for model_id in candidates:
            try:
                self.unload_model(model_id, reason="idle_timeout")
            except Exception:
                continue

    def acquire(
        self,
        job_id: str,
        model: dict[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ResourceLease:
        model_id = str(model["id"])
        engine_id = str(model["engine_id"])
        requirements = self._requirements(model)
        waiter_id = f"wait_{uuid4().hex[:16]}"
        deadline = time.monotonic() + float(self.config.scheduler_wait_timeout_seconds)
        self._maintenance()

        with self._condition:
            self._waiters.append(waiter_id)
        try:
            pressure_attempted = False
            while True:
                if cancel_check and cancel_check():
                    raise VoiceEngineError("cancelled", "Voice job was cancelled while waiting for scheduler admission.", retryable=False, http_status=409)
                if time.monotonic() >= deadline:
                    raise VoiceEngineError(
                        "scheduler_timeout",
                        f"Voice job timed out waiting for resources for model '{model_id}'.",
                        retryable=True,
                        details={"model_id": model_id, "engine_id": engine_id},
                        http_status=503,
                    )

                with self._condition:
                    first = bool(self._waiters and self._waiters[0] == waiter_id)
                    if not first:
                        self._condition.wait(timeout=0.05)
                        continue

                    if not requirements["cuda"]:
                        lease = ResourceLease(
                            lease_id=f"lease_{uuid4().hex[:16]}",
                            job_id=job_id,
                            engine_id=engine_id,
                            model_id=model_id,
                            device="cpu",
                            device_index=None,
                            reserved_vram_mb=0,
                            acquired_at=_now(),
                        )
                        self._leases[lease.lease_id] = lease
                        self._mark_lease(lease, model)
                        self._waiters.pop(0)
                        self._condition.notify_all()
                        return lease

                    if requirements["gpu_exclusive"] and self._active_gpu_leases() >= self.config.gpu_max_concurrent_jobs:
                        self._condition.wait(timeout=0.05)
                        continue

                hardware = self._probe()
                required = requirements["min_vram_mb"]
                choice = self._choose_cuda_device(hardware, required) if hardware.get("available") else None
                if choice is not None:
                    device_index, effective_free = choice
                    reservation = requirements["recommended_vram_mb"] or required
                    if reservation <= 0:
                        reservation = min(effective_free, required)
                    reservation = min(max(required, reservation), effective_free) if effective_free > 0 else required
                    with self._condition:
                        if not self._waiters or self._waiters[0] != waiter_id:
                            continue
                        if requirements["gpu_exclusive"] and self._active_gpu_leases() >= self.config.gpu_max_concurrent_jobs:
                            self._condition.wait(timeout=0.05)
                            continue
                        lease = ResourceLease(
                            lease_id=f"lease_{uuid4().hex[:16]}",
                            job_id=job_id,
                            engine_id=engine_id,
                            model_id=model_id,
                            device="cuda",
                            device_index=device_index,
                            reserved_vram_mb=max(0, reservation),
                            acquired_at=_now(),
                        )
                        self._leases[lease.lease_id] = lease
                        self._mark_lease(lease, model)
                        self._waiters.pop(0)
                        self._condition.notify_all()
                        return lease

                if not pressure_attempted:
                    pressure_attempted = True
                    if self._evict_for_pressure(model_id):
                        continue

                if requirements["cpu"] and requirements["allow_cpu_fallback"]:
                    with self._condition:
                        if not self._waiters or self._waiters[0] != waiter_id:
                            continue
                        lease = ResourceLease(
                            lease_id=f"lease_{uuid4().hex[:16]}",
                            job_id=job_id,
                            engine_id=engine_id,
                            model_id=model_id,
                            device="cpu",
                            device_index=None,
                            reserved_vram_mb=0,
                            acquired_at=_now(),
                        )
                        self._leases[lease.lease_id] = lease
                        self._mark_lease(lease, model)
                        self._waiters.pop(0)
                        self._condition.notify_all()
                        return lease

                message = (
                    f"Voice model '{model_id}' requires CUDA VRAM but no safe admission is currently available."
                    if hardware.get("available")
                    else f"Voice model '{model_id}' requires CUDA and no CUDA device is available to the gateway."
                )
                with self._condition:
                    self._last_admission_error = message
                code = "gpu_oom" if hardware.get("available") else "hardware_unavailable"
                raise VoiceEngineError(
                    code,
                    message,
                    retryable=True,
                    details={
                        "model_id": model_id,
                        "engine_id": engine_id,
                        "required_vram_mb": required,
                        "recommended_vram_mb": requirements["recommended_vram_mb"],
                        "reserve_vram_mb": self.config.gpu_vram_reserve_mb,
                        "hardware": hardware,
                    },
                    http_status=503,
                )
        finally:
            with self._condition:
                if waiter_id in self._waiters:
                    self._waiters.remove(waiter_id)
                self._condition.notify_all()

    def prepare_model(self, lease: ResourceLease, model: dict[str, Any]) -> dict[str, Any]:
        model_id = str(model["id"])
        engine_id = str(model["engine_id"])
        self.supervisor.ensure_ready(engine_id)
        response = self.supervisor.load_model(
            engine_id,
            model_id,
            device=lease.device,
            device_index=lease.device_index,
        )
        with self._condition:
            record = self._residency_record(model)
            if bool(response.get("supported")):
                record["state"] = "resident"
                record["load_supported"] = True
                record["message"] = "Worker confirmed model load/readiness."
            else:
                record["state"] = "implicit"
                record["load_supported"] = False
                record["message"] = "Worker does not expose model lifecycle APIs; residency is worker-managed."
            record["device"] = lease.device
            record["device_index"] = lease.device_index
            record["last_used_monotonic"] = time.monotonic()
            record["last_used"] = _now()
        return response

    def release(self, lease: ResourceLease | None) -> None:
        if lease is None:
            return
        unload_now = False
        with self._condition:
            stored = self._leases.pop(lease.lease_id, None)
            record = self._residency.get(lease.model_id)
            if record is not None:
                record["active_jobs"] = max(0, int(record.get("active_jobs") or 0) - 1)
                record["last_used_monotonic"] = time.monotonic()
                record["last_used"] = _now()
                if record["active_jobs"] == 0 and str(record.get("state") or "") in {"resident", "implicit"}:
                    record["state"] = "idle"
                    unload_now = int(record.get("idle_unload_seconds") or 0) == 0 and bool(record.get("evictable", True))
            if stored is not None:
                self._condition.notify_all()
        if unload_now:
            try:
                self.unload_model(lease.model_id, reason="job_complete")
            except Exception:
                pass

    def execution_hint(self, lease: ResourceLease) -> dict[str, Any]:
        return {
            "schema_id": "neo.voice_engine.execution_hint.v1",
            "device": lease.device,
            "device_index": lease.device_index,
            "reserved_vram_mb": lease.reserved_vram_mb,
        }

    def snapshot(self, *, refresh_hardware: bool = False) -> dict[str, Any]:
        # Diagnostics stay side-effect free. Idle lifecycle maintenance runs when
        # scheduler admission occurs, not merely because an observer reads state.
        if refresh_hardware:
            self._probe()
        with self._condition:
            residency: list[dict[str, Any]] = []
            for model_id, record in sorted(self._residency.items()):
                public = {key: value for key, value in record.items() if key != "last_used_monotonic"}
                public["model_id"] = model_id
                residency.append(public)
            return {
                "schema_id": "neo.voice_engine.scheduler.v1",
                "mode": "gpu_aware",
                "gpu_max_concurrent_jobs": self.config.gpu_max_concurrent_jobs,
                "vram_reserve_mb": self.config.gpu_vram_reserve_mb,
                "waiting_jobs": len(self._waiters),
                "active_leases": [lease.public() for lease in self._leases.values()],
                "active_gpu_leases": self._active_gpu_leases(),
                "hardware": dict(self._last_hardware),
                "residency": residency,
                "last_admission_error": self._last_admission_error,
            }
