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
    admission_mode: str = "cold_load"
    observed_free_vram_mb: int = 0
    effective_free_vram_mb: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "job_id": self.job_id,
            "engine_id": self.engine_id,
            "model_id": self.model_id,
            "device": self.device,
            "device_index": self.device_index,
            "reserved_vram_mb": self.reserved_vram_mb,
            "admission_mode": self.admission_mode,
            "observed_free_vram_mb": self.observed_free_vram_mb,
            "effective_free_vram_mb": self.effective_free_vram_mb,
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
        self._last_admission: dict[str, Any] = {
            "mode": "unprobed",
            "admitted": False,
            "message": "No scheduler admission has been attempted yet.",
            "checked_at": "",
        }

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

        # Legacy manifests express cold admission with min_vram_mb only.
        # Phase 4.4 adds an optional split contract so a model can require a
        # validated GPU capacity class independently from the amount of VRAM
        # that must be free immediately before a cold load.
        legacy_minimum = _safe_int(hardware.get("min_vram_mb"), 0)
        cold_load_free = _safe_int(hardware.get("cold_load_free_vram_mb"), 0) or legacy_minimum
        recommended = _safe_int(hardware.get("recommended_vram_mb"), cold_load_free)
        min_total = _safe_int(hardware.get("min_total_vram_mb"), 0)
        recommended_total = _safe_int(hardware.get("recommended_total_vram_mb"), min_total)
        return {
            "cuda": cuda,
            "cpu": cpu,
            "min_vram_mb": legacy_minimum,
            "cold_load_free_vram_mb": cold_load_free,
            "recommended_vram_mb": max(cold_load_free, recommended),
            "min_total_vram_mb": min_total,
            "recommended_total_vram_mb": max(min_total, recommended_total),
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

    @staticmethod
    def _device_payload(hardware: dict[str, Any], device_index: int) -> dict[str, Any] | None:
        for raw in hardware.get("devices") or []:
            if not isinstance(raw, dict):
                continue
            if _safe_int(raw.get("index"), -1) == int(device_index):
                return raw
        return None

    def _record_admission(self, **payload: Any) -> None:
        record = {
            "mode": str(payload.pop("mode", "unknown") or "unknown"),
            "admitted": bool(payload.pop("admitted", False)),
            "message": str(payload.pop("message", "") or ""),
            "checked_at": _now(),
            **payload,
        }
        with self._condition:
            self._last_admission = record
            self._last_admission_error = "" if record["admitted"] else record["message"]

    def _confirmed_resident_cuda_device(self, model: dict[str, Any]) -> int | None:
        """Return the CUDA device only when the worker confirms the same model is resident.

        Scheduler bookkeeping alone is not enough: a worker can unload or switch a
        model outside the scheduler's last record.  Resident-reuse admission is
        therefore granted only after a live, read-only lifecycle check on an
        already-running worker.  This check never starts a worker.
        """
        model_id = str(model["id"])
        engine_id = str(model["engine_id"])
        with self._condition:
            record = self._residency.get(model_id)
            if record is None:
                return None
            if str(record.get("state") or "") not in {"resident", "implicit", "idle"}:
                return None
            if str(record.get("device") or "") != "cuda":
                return None
            device_index = record.get("device_index")
            if device_index is None:
                return None

        try:
            worker = self.supervisor.public_worker(engine_id)
        except Exception:
            return None
        if bool(worker.get("managed")) and str(worker.get("state") or "").lower() not in {"ready", "connected", "ok", "healthy"}:
            return None
        try:
            lifecycle = self.supervisor.model_lifecycle(engine_id, model_id)
        except Exception:
            return None
        if not bool(lifecycle.get("supported")):
            return None
        lifecycle_state = str(lifecycle.get("state") or "").lower()
        loaded_model_id = str(lifecycle.get("loaded_model_id") or "").strip()
        if lifecycle_state == "resident" and loaded_model_id == model_id:
            with self._condition:
                live = self._residency.get(model_id)
                if live is not None:
                    live["state"] = "resident"
                    live["message"] = "Worker lifecycle confirmed the model is already resident."
            return int(device_index)

        if lifecycle_state in {"unloaded", "missing", "stopped"}:
            with self._condition:
                live = self._residency.get(model_id)
                if live is not None and int(live.get("active_jobs") or 0) == 0:
                    live["state"] = "unloaded"
                    live["device"] = ""
                    live["device_index"] = None
                    live["message"] = "Worker lifecycle reported that the model is no longer resident."
        return None

    def _choose_cuda_device(
        self,
        hardware: dict[str, Any],
        required_free_mb: int,
        *,
        min_total_vram_mb: int = 0,
    ) -> tuple[int, int] | None:
        candidates: list[tuple[int, int]] = []
        reserve = max(0, int(self.config.gpu_vram_reserve_mb))
        for raw in hardware.get("devices") or []:
            if not isinstance(raw, dict):
                continue
            index = _safe_int(raw.get("index"), 0)
            total = _safe_int(raw.get("total_vram_mb"), 0)
            if min_total_vram_mb > 0 and total < min_total_vram_mb:
                continue
            free = _safe_int(raw.get("free_vram_mb"), 0)
            effective = max(0, free - reserve - self._reserved_on_device(index))
            if effective >= required_free_mb:
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
        record["last_admission_mode"] = lease.admission_mode
        record["last_observed_free_vram_mb"] = lease.observed_free_vram_mb
        record["last_effective_free_vram_mb"] = lease.effective_free_vram_mb

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
                            admission_mode="cpu",
                        )
                        self._leases[lease.lease_id] = lease
                        self._mark_lease(lease, model)
                        self._waiters.pop(0)
                        self._condition.notify_all()
                        return lease

                    if requirements["gpu_exclusive"] and self._active_gpu_leases() >= self.config.gpu_max_concurrent_jobs:
                        self._condition.wait(timeout=0.05)
                        continue

                resident_device_index = self._confirmed_resident_cuda_device(model)
                hardware = self._probe()
                reserve = max(0, int(self.config.gpu_vram_reserve_mb))
                resident_headroom_failed = False
                if resident_device_index is not None and hardware.get("available"):
                    raw_device = self._device_payload(hardware, resident_device_index)
                    if raw_device is not None:
                        observed_free = _safe_int(raw_device.get("free_vram_mb"), 0)
                        other_reserved = self._reserved_on_device(resident_device_index)
                        effective_free = max(0, observed_free - other_reserved - reserve)
                        if observed_free - other_reserved >= reserve:
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
                                    device_index=resident_device_index,
                                    reserved_vram_mb=0,
                                    acquired_at=_now(),
                                    admission_mode="resident_reuse",
                                    observed_free_vram_mb=observed_free,
                                    effective_free_vram_mb=effective_free,
                                )
                                self._leases[lease.lease_id] = lease
                                self._mark_lease(lease, model)
                                self._waiters.pop(0)
                                self._record_admission(
                                    mode="resident_reuse",
                                    admitted=True,
                                    message=f"Reusing resident voice model '{model_id}' on CUDA:{resident_device_index} without charging its cold-load VRAM twice.",
                                    model_id=model_id,
                                    engine_id=engine_id,
                                    device_index=resident_device_index,
                                    observed_free_vram_mb=observed_free,
                                    effective_free_vram_mb=effective_free,
                                    resident_headroom_mb=reserve,
                                    cold_min_vram_mb=requirements["cold_load_free_vram_mb"],
                                    min_total_vram_mb=requirements["min_total_vram_mb"],
                                )
                                self._condition.notify_all()
                                return lease
                        else:
                            resident_headroom_failed = True

                required = requirements["cold_load_free_vram_mb"]
                min_total = requirements["min_total_vram_mb"]
                choice = (
                    self._choose_cuda_device(hardware, required, min_total_vram_mb=min_total)
                    if hardware.get("available") and resident_device_index is None
                    else None
                )
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
                        raw_device = self._device_payload(hardware, device_index) or {}
                        observed_free = _safe_int(raw_device.get("free_vram_mb"), 0)
                        lease = ResourceLease(
                            lease_id=f"lease_{uuid4().hex[:16]}",
                            job_id=job_id,
                            engine_id=engine_id,
                            model_id=model_id,
                            device="cuda",
                            device_index=device_index,
                            reserved_vram_mb=max(0, reservation),
                            acquired_at=_now(),
                            admission_mode="cold_load",
                            observed_free_vram_mb=observed_free,
                            effective_free_vram_mb=effective_free,
                        )
                        self._leases[lease.lease_id] = lease
                        self._mark_lease(lease, model)
                        self._waiters.pop(0)
                        self._record_admission(
                            mode="cold_load",
                            admitted=True,
                            message=f"Cold-load admission granted for voice model '{model_id}' on CUDA:{device_index}.",
                            model_id=model_id,
                            engine_id=engine_id,
                            device_index=device_index,
                            observed_free_vram_mb=observed_free,
                            effective_free_vram_mb=effective_free,
                            required_vram_mb=required,
                            cold_load_free_vram_mb=required,
                            min_total_vram_mb=min_total,
                            recommended_total_vram_mb=requirements["recommended_total_vram_mb"],
                            reserved_vram_mb=max(0, reservation),
                            reserve_vram_mb=reserve,
                        )
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
                            admission_mode="cpu_fallback",
                        )
                        self._leases[lease.lease_id] = lease
                        self._mark_lease(lease, model)
                        self._waiters.pop(0)
                        self._condition.notify_all()
                        return lease

                admission_reason = "unknown"
                observed_free_vram_mb = 0
                effective_free_vram_mb = 0
                if resident_headroom_failed:
                    admission_reason = "resident_safety_reserve_unavailable"
                    raw_device = self._device_payload(hardware, resident_device_index) if resident_device_index is not None else None
                    observed_free_vram_mb = _safe_int((raw_device or {}).get("free_vram_mb"), 0)
                    other_reserved = self._reserved_on_device(resident_device_index) if resident_device_index is not None else 0
                    effective_free_vram_mb = max(0, observed_free_vram_mb - other_reserved - reserve)
                    message = (
                        f"Voice model '{model_id}' is already resident, but Neo's {reserve} MB CUDA safety reserve "
                        f"is not currently available (GPU free: {observed_free_vram_mb} MB). "
                        "Another GPU workload may still be holding VRAM; finish or unload that workload and retry."
                    )
                    admission_mode = "resident_reuse"
                else:
                    capacity_ok = True
                    devices = [raw for raw in (hardware.get("devices") or []) if isinstance(raw, dict)]
                    capacity_devices = devices
                    if hardware.get("available") and min_total > 0:
                        capacity_devices = [raw for raw in devices if _safe_int(raw.get("total_vram_mb"), 0) >= min_total]
                        capacity_ok = bool(capacity_devices)
                    if hardware.get("available") and not capacity_ok:
                        admission_reason = "insufficient_total_vram"
                        message = (
                            f"Voice model '{model_id}' requires a CUDA GPU with at least {min_total} MB total VRAM; "
                            "no detected device meets that validated capacity class."
                        )
                    elif hardware.get("available"):
                        admission_reason = "insufficient_free_vram"
                        best_device = max(capacity_devices or devices, key=lambda raw: _safe_int(raw.get("free_vram_mb"), 0), default={})
                        best_index = _safe_int(best_device.get("index"), 0)
                        observed_free_vram_mb = _safe_int(best_device.get("free_vram_mb"), 0)
                        other_reserved = self._reserved_on_device(best_index)
                        effective_free_vram_mb = max(0, observed_free_vram_mb - other_reserved - reserve)
                        message = (
                            f"Voice model '{model_id}' needs at least {required} MB safely free for a cold load plus "
                            f"Neo's {reserve} MB CUDA reserve, but the best matching GPU currently has "
                            f"{observed_free_vram_mb} MB free ({effective_free_vram_mb} MB safely available). "
                            "Another GPU workload may be using VRAM (for example Video/Comfy). Finish or unload it and retry."
                        )
                    else:
                        admission_reason = "cuda_unavailable"
                        message = f"Voice model '{model_id}' requires CUDA and no CUDA device is available to the gateway."
                    admission_mode = "cold_load"
                self._record_admission(
                    mode=admission_mode,
                    admitted=False,
                    message=message,
                    model_id=model_id,
                    engine_id=engine_id,
                    required_vram_mb=0 if resident_headroom_failed else required,
                    cold_min_vram_mb=required,
                    cold_load_free_vram_mb=required,
                    min_total_vram_mb=min_total,
                    recommended_total_vram_mb=requirements["recommended_total_vram_mb"],
                    recommended_vram_mb=requirements["recommended_vram_mb"],
                    reserve_vram_mb=self.config.gpu_vram_reserve_mb,
                    resident_reuse_confirmed=resident_device_index is not None,
                    admission_reason=admission_reason,
                    observed_free_vram_mb=observed_free_vram_mb,
                    effective_free_vram_mb=effective_free_vram_mb,
                    hardware=hardware,
                )
                code = "gpu_oom" if hardware.get("available") else "hardware_unavailable"
                raise VoiceEngineError(
                    code,
                    message,
                    retryable=True,
                    details={
                        "model_id": model_id,
                        "engine_id": engine_id,
                        "admission_mode": admission_mode,
                        "resident_reuse_confirmed": resident_device_index is not None,
                        "required_vram_mb": 0 if resident_headroom_failed else required,
                        "cold_min_vram_mb": required,
                        "cold_load_free_vram_mb": required,
                        "min_total_vram_mb": min_total,
                        "recommended_total_vram_mb": requirements["recommended_total_vram_mb"],
                        "recommended_vram_mb": requirements["recommended_vram_mb"],
                        "reserve_vram_mb": self.config.gpu_vram_reserve_mb,
                        "admission_reason": admission_reason,
                        "observed_free_vram_mb": observed_free_vram_mb,
                        "effective_free_vram_mb": effective_free_vram_mb,
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
        install = model.get("install") if isinstance(model.get("install"), dict) else {}
        runtime_state = str(install.get("runtime_state") or model.get("install_state") or "external")
        model_state = str(install.get("model_state") or model.get("install_state") or "external")
        runtime_install = install.get("runtime") if isinstance(install.get("runtime"), dict) else {}
        runtime_has_authoritative_probe = bool(runtime_install.get("probes"))
        if runtime_state not in {"installed", "external"} and runtime_has_authoritative_probe:
            raise VoiceEngineError(
                "dependency_missing",
                f"Voice runtime for '{model_id}' is not fully installed; worker launch was blocked before contact.",
                details={
                    "engine_id": engine_id,
                    "model_id": model_id,
                    "runtime_install_state": runtime_state,
                    "model_install_state": model_state,
                    "install": install,
                },
                http_status=409,
            )
        if model_state not in {"installed", "external"}:
            raise VoiceEngineError(
                "model_not_installed",
                f"Voice model '{model_id}' is not fully installed; worker launch was blocked before contact.",
                details={
                    "engine_id": engine_id,
                    "model_id": model_id,
                    "runtime_install_state": runtime_state,
                    "model_install_state": model_state,
                    "install": install,
                },
                http_status=409,
            )
        self.supervisor.ensure_ready(engine_id)
        if lease.admission_mode == "resident_reuse":
            lifecycle = self.supervisor.model_lifecycle(engine_id, model_id)
            lifecycle_state = str(lifecycle.get("state") or "").lower()
            loaded_model_id = str(lifecycle.get("loaded_model_id") or "").strip()
            if not bool(lifecycle.get("supported")) or lifecycle_state != "resident" or loaded_model_id != model_id:
                raise VoiceEngineError(
                    "gpu_oom",
                    f"Resident-reuse admission for '{model_id}' became stale before dispatch; retry the job for a fresh admission decision.",
                    retryable=True,
                    details={
                        "model_id": model_id,
                        "engine_id": engine_id,
                        "admission_mode": "resident_reuse",
                        "lifecycle": lifecycle,
                    },
                    http_status=503,
                )
            response = {**lifecycle, "supported": True, "state": "resident", "reused": True}
        else:
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
            "admission_mode": lease.admission_mode,
            "observed_free_vram_mb": lease.observed_free_vram_mb,
            "effective_free_vram_mb": lease.effective_free_vram_mb,
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
                "last_admission": dict(self._last_admission),
                "last_admission_error": self._last_admission_error,
            }
