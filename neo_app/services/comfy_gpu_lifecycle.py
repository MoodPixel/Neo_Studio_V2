from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib import error, parse, request
from uuid import uuid4

from .comfy_runtime_recovery import classify_comfy_runtime_error


DEFAULT_WAIT_TIMEOUT_SECONDS = 900.0
DEFAULT_WATCH_TIMEOUT_SECONDS = 21600.0
DEFAULT_WATCH_INTERVAL_SECONDS = 1.0
DEFAULT_HISTORY_MISSING_CONFIRMATIONS = 3
DEFAULT_UNBOUND_STALE_GRACE_SECONDS = 30.0
DEFAULT_RECOVERY_SLOW_INTERVAL_SECONDS = 5.0
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


class ComfyGpuBusyError(RuntimeError):
    def __init__(self, message: str, *, status: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status or {}


@dataclass
class _Lease:
    token: str
    resource_group: str
    base_url: str
    owner_kind: str
    owner_label: str
    profile_id: str = ""
    requested_at: float = field(default_factory=time.time)
    acquired_at: float = field(default_factory=time.time)
    prompt_id: str = ""
    cleanup_after: bool = False
    phase: str = "acquired"
    metadata: dict[str, Any] = field(default_factory=dict)
    watcher_started: bool = False
    external_guard: bool = False
    last_observed_at: float = field(default_factory=time.time)
    last_history_seen_at: float = 0.0
    consecutive_history_misses: int = 0
    consecutive_watch_errors: int = 0
    recovery_state: str = "normal"
    last_error: str = ""
    completion_started: bool = False

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "schema_id": "neo.runtime.comfy_gpu_lifecycle.v2",
            "token": self.token,
            "resource_group": self.resource_group,
            "base_url": self.base_url,
            "owner_kind": self.owner_kind,
            "owner_label": self.owner_label,
            "profile_id": self.profile_id,
            "prompt_id": self.prompt_id,
            "phase": self.phase,
            "cleanup_after": self.cleanup_after,
            "requested_at": self.requested_at,
            "acquired_at": self.acquired_at,
            "wait_seconds": round(max(0.0, self.acquired_at - self.requested_at), 3),
            "held_seconds": round(max(0.0, now - self.acquired_at), 3),
            "metadata": dict(self.metadata or {}),
            "external_guard": self.external_guard,
            "last_observed_at": self.last_observed_at,
            "last_history_seen_at": self.last_history_seen_at,
            "consecutive_history_misses": self.consecutive_history_misses,
            "consecutive_watch_errors": self.consecutive_watch_errors,
            "recovery_state": self.recovery_state,
            "last_error": self.last_error,
            "completion_started": self.completion_started,
        }


def normalize_base_url(base_url: str | None) -> str:
    return str(base_url or "http://127.0.0.1:8188").strip().rstrip("/") or "http://127.0.0.1:8188"


def comfy_gpu_resource_group(base_url: str | None) -> str:
    """Map local Comfy processes to one GPU group; remote servers stay isolated."""
    clean = normalize_base_url(base_url)
    parsed = parse.urlparse(clean if "://" in clean else f"http://{clean}")
    host = str(parsed.hostname or "").strip().lower()
    if host in LOCAL_HOSTS or not host:
        return "local_gpu:0"
    scheme = str(parsed.scheme or "http").lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"comfy:{scheme}://{host}{port}"


def _http_json(base_url: str, path: str, *, method: str = "GET", payload: Any = None, timeout: float = 15.0) -> Any:
    url = f"{normalize_base_url(base_url)}{path if path.startswith('/') else '/' + path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=max(0.5, float(timeout))) as response:
            raw = response.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        raise RuntimeError(f"ComfyUI {method} {path} failed with HTTP {exc.code}: {body or exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": raw.decode("utf-8", errors="replace")}


def _queue_prompt_ids(payload: Any) -> list[str]:
    """Extract prompt IDs from the standard /queue response without model-name false positives."""
    data = payload if isinstance(payload, Mapping) else {}
    found: list[str] = []
    for key in ("queue_running", "queue_pending", "running", "pending"):
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            prompt_id = ""
            if isinstance(row, Mapping):
                prompt_id = str(row.get("prompt_id") or row.get("id") or "").strip()
            elif isinstance(row, (list, tuple)) and len(row) > 1:
                prompt_id = str(row[1] or "").strip()
            if prompt_id and prompt_id not in found:
                found.append(prompt_id)
    return found


class ComfyGpuLifecycleManager:
    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._active: dict[str, _Lease] = {}
        self._waiters: dict[str, list[str]] = {}
        self._token_to_group: dict[str, str] = {}
        self._prompt_to_token: dict[tuple[str, str], str] = {}
        self._last_release: dict[str, dict[str, Any]] = {}
        self._cleanup_debt: dict[str, dict[str, Any]] = {}
        self._last_reconcile: dict[str, dict[str, Any]] = {}

    def _cleanup_debt_retry(self, base_url: str) -> dict[str, Any]:
        group = comfy_gpu_resource_group(base_url)
        with self._condition:
            debt = dict(self._cleanup_debt.get(group) or {})
        if not debt:
            return {"attempted": False, "ok": True, "state": "no_cleanup_debt"}
        result: dict[str, Any] = {"attempted": True, "ok": False, "state": "cleanup_retry_failed"}
        try:
            response = _http_json(
                base_url,
                "/free",
                method="POST",
                payload={"unload_models": True, "free_memory": True},
                timeout=15.0,
            )
            result.update({"ok": True, "state": "cleanup_debt_cleared", "response": response})
            with self._condition:
                self._cleanup_debt.pop(group, None)
        except Exception as exc:  # noqa: BLE001
            failure = classify_comfy_runtime_error(exc, phase="cleanup")
            result.update({"error": str(exc), "failure": failure})
            with self._condition:
                current = dict(self._cleanup_debt.get(group) or debt)
                current["retry_count"] = int(current.get("retry_count") or 0) + 1
                current["last_retry_at"] = time.time()
                current["last_error"] = str(exc)
                current["failure"] = failure
                self._cleanup_debt[group] = current
        return result

    def reconcile_backend(self, base_url: str, *, adopt_external: bool = True) -> dict[str, Any]:
        """Reconcile process-local leases with Comfy after restart/disconnect.

        If Neo restarted while Comfy still has queued/running work, this creates a
        temporary external guard so the first new heavy Neo request cannot collide
        with that pre-existing queue. A failed /free from a previous run is retried
        here as a non-deadlocking cleanup debt.
        """
        clean_url = normalize_base_url(base_url)
        group = comfy_gpu_resource_group(clean_url)
        with self._condition:
            if group in self._active:
                result = {"ok": True, "state": "already_guarded", "resource_group": group, "cleanup_retry": {"attempted": False, "ok": True, "state": "deferred_while_busy"}}
                self._last_reconcile[group] = result
                return result
        try:
            queue_payload = _http_json(clean_url, "/queue", timeout=8.0)
            prompt_ids = _queue_prompt_ids(queue_payload)
        except Exception as exc:  # noqa: BLE001
            failure = classify_comfy_runtime_error(exc, phase="reconcile")
            result = {
                "ok": False,
                "state": "backend_unreachable",
                "resource_group": group,
                "cleanup_retry": {"attempted": False, "ok": False, "state": "deferred_backend_unreachable"},
                "failure": failure,
            }
            with self._condition:
                self._last_reconcile[group] = result
            return result

        # Never call /free while Comfy already has work in its queue. Cleanup
        # debt is retried only after the backend is visibly idle.
        cleanup_retry = self._cleanup_debt_retry(clean_url) if not prompt_ids else {"attempted": False, "ok": True, "state": "deferred_external_queue_busy"}
        result = {
            "ok": True,
            "state": "queue_clear" if not prompt_ids else "external_queue_detected",
            "resource_group": group,
            "prompt_ids": prompt_ids,
            "cleanup_retry": cleanup_retry,
        }
        should_start = False
        token = ""
        if prompt_ids and adopt_external:
            with self._condition:
                if group not in self._active:
                    now = time.time()
                    token = f"comfy-gpu-external-{uuid4().hex}"
                    lease = _Lease(
                        token=token,
                        resource_group=group,
                        base_url=clean_url,
                        owner_kind="external_comfy_queue",
                        owner_label="Existing ComfyUI queue detected after Neo startup/reconnect",
                        requested_at=now,
                        acquired_at=now,
                        prompt_id=prompt_ids[0] if len(prompt_ids) == 1 else "",
                        phase="recovered_external_queue",
                        external_guard=True,
                        metadata={"external_prompt_ids": prompt_ids, "reconciled_after_restart": True},
                        watcher_started=True,
                        recovery_state="external_queue_guard",
                    )
                    self._active[group] = lease
                    self._token_to_group[token] = group
                    should_start = True
                    result["adopted_guard_token"] = token
                else:
                    result["state"] = "external_queue_already_guarded"
        with self._condition:
            self._last_reconcile[group] = dict(result)
        if should_start and token:
            threading.Thread(
                target=self._watch_external_queue,
                name=f"neo-comfy-gpu-external-{group.replace(':', '-')}",
                daemon=True,
                kwargs={"token": token},
            ).start()
        return result

    def acquire(
        self,
        *,
        base_url: str,
        owner_kind: str,
        owner_label: str,
        profile_id: str = "",
        wait_timeout_seconds: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_url = normalize_base_url(base_url)
        group = comfy_gpu_resource_group(clean_url)
        # Important after Neo restart: inspect Comfy's existing queue before a new
        # process-local lease can be granted.
        self.reconcile_backend(clean_url, adopt_external=True)
        timeout = DEFAULT_WAIT_TIMEOUT_SECONDS if wait_timeout_seconds is None else max(0.0, float(wait_timeout_seconds))
        requested_at = time.time()
        token = f"comfy-gpu-{uuid4().hex}"
        ticket = f"wait-{uuid4().hex}"
        deadline = requested_at + timeout if timeout > 0 else requested_at
        with self._condition:
            queue = self._waiters.setdefault(group, [])
            queue.append(ticket)
            while True:
                is_head = bool(queue and queue[0] == ticket)
                if is_head and group not in self._active:
                    queue.pop(0)
                    if not queue:
                        self._waiters.pop(group, None)
                    acquired_at = time.time()
                    lease = _Lease(
                        token=token,
                        resource_group=group,
                        base_url=clean_url,
                        owner_kind=str(owner_kind or "comfy_job"),
                        owner_label=str(owner_label or owner_kind or "Comfy job"),
                        profile_id=str(profile_id or ""),
                        requested_at=requested_at,
                        acquired_at=acquired_at,
                        metadata=dict(metadata or {}),
                    )
                    self._active[group] = lease
                    self._token_to_group[token] = group
                    return lease.snapshot()

                remaining = deadline - time.time()
                if timeout <= 0 or remaining <= 0:
                    if ticket in queue:
                        queue.remove(ticket)
                    if not queue:
                        self._waiters.pop(group, None)
                    active = self._active.get(group)
                    status = self.status(group=group)
                    owner = active.owner_label if active else "another Comfy job"
                    raise ComfyGpuBusyError(
                        f"Shared Comfy GPU is busy with {owner}. Wait for that job to finish, then retry.",
                        status=status,
                    )
                self._condition.wait(timeout=min(1.0, remaining))

    def update_lease(
        self,
        token: str,
        *,
        phase: str | None = None,
        prompt_id: str | None = None,
        cleanup_after: bool | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._condition:
            lease = self._lease_for_token(token)
            if lease is None:
                return {"ok": False, "state": "lease_missing", "token": token}
            if phase is not None:
                lease.phase = str(phase or lease.phase)
            if prompt_id is not None:
                lease.prompt_id = str(prompt_id or "")
            if cleanup_after is not None:
                lease.cleanup_after = bool(cleanup_after)
            if metadata:
                lease.metadata.update(dict(metadata))
            lease.last_observed_at = time.time()
            return {"ok": True, **lease.snapshot()}

    def bind_prompt(
        self,
        token: str,
        *,
        prompt_id: str,
        cleanup_after: bool = False,
        watch: bool = True,
        watch_timeout_seconds: float | None = None,
        watch_interval_seconds: float | None = None,
    ) -> dict[str, Any]:
        prompt_id = str(prompt_id or "").strip()
        if not prompt_id:
            raise ValueError("prompt_id is required to bind a Comfy GPU lease")
        with self._condition:
            lease = self._lease_for_token(token)
            if lease is None:
                return {"ok": False, "state": "lease_missing", "token": token, "prompt_id": prompt_id}
            lease.prompt_id = prompt_id
            lease.cleanup_after = bool(cleanup_after)
            lease.phase = "running"
            lease.last_observed_at = time.time()
            lease.recovery_state = "watching_history"
            self._prompt_to_token[(lease.resource_group, prompt_id)] = token
            should_start = bool(watch and not lease.watcher_started)
            if should_start:
                lease.watcher_started = True
            snapshot = lease.snapshot()
        if should_start:
            thread = threading.Thread(
                target=self._watch_prompt,
                name=f"neo-comfy-gpu-{prompt_id[:18]}",
                daemon=True,
                kwargs={
                    "token": token,
                    "watch_timeout_seconds": DEFAULT_WATCH_TIMEOUT_SECONDS if watch_timeout_seconds is None else max(30.0, float(watch_timeout_seconds)),
                    "watch_interval_seconds": DEFAULT_WATCH_INTERVAL_SECONDS if watch_interval_seconds is None else max(0.25, float(watch_interval_seconds)),
                },
            )
            thread.start()
        return {"ok": True, **snapshot}

    def guard_unbound_after_queue_uncertainty(
        self,
        token: str,
        *,
        cleanup_after: bool = False,
        reason: str = "queue_response_uncertain",
    ) -> dict[str, Any]:
        """Keep a lease when POST /prompt may have succeeded but no prompt_id arrived."""
        with self._condition:
            lease = self._lease_for_token(token)
            if lease is None:
                return {"ok": False, "state": "lease_missing", "token": token}
            lease.cleanup_after = bool(cleanup_after)
            lease.phase = "queue_uncertain_guard"
            lease.recovery_state = "queue_uncertain_guard"
            lease.last_error = str(reason or "queue_response_uncertain")
            should_start = not lease.watcher_started
            if should_start:
                lease.watcher_started = True
            snapshot = lease.snapshot()
        if should_start:
            threading.Thread(
                target=self._watch_unbound_queue,
                name=f"neo-comfy-gpu-unbound-{token[-12:]}",
                daemon=True,
                kwargs={"token": token},
            ).start()
        return {"ok": True, **snapshot}

    def complete_prompt(self, *, base_url: str, prompt_id: str, state: str = "completed", cleanup_after: bool | None = None) -> dict[str, Any]:
        group = comfy_gpu_resource_group(base_url)
        clean_prompt_id = str(prompt_id or "").strip()
        with self._condition:
            token = self._prompt_to_token.get((group, clean_prompt_id))
            last_release = dict(self._last_release.get(group) or {})
        if not token:
            if clean_prompt_id and str(last_release.get("prompt_id") or "") == clean_prompt_id:
                return {"ok": True, "idempotent": True, **last_release}
            return {"ok": False, "state": "prompt_lease_missing", "prompt_id": prompt_id, "resource_group": group}
        return self.complete(token, state=state, cleanup_after=cleanup_after)

    def complete(self, token: str, *, state: str = "completed", cleanup_after: bool | None = None) -> dict[str, Any]:
        with self._condition:
            lease = self._lease_for_token(token)
            if lease is None:
                return {"ok": False, "state": "already_released", "token": token}
            if lease.completion_started:
                return {"ok": True, "state": "completion_in_progress", **lease.snapshot()}
            lease.completion_started = True
            if cleanup_after is not None:
                lease.cleanup_after = bool(cleanup_after)
            do_cleanup = bool(lease.cleanup_after)
            lease.phase = "cleanup" if do_cleanup else str(state or "completed")
            before = lease.snapshot()

        cleanup: dict[str, Any] = {"attempted": False, "ok": True, "response": {}}
        if do_cleanup:
            cleanup["attempted"] = True
            try:
                cleanup["response"] = _http_json(
                    lease.base_url,
                    "/free",
                    method="POST",
                    payload={"unload_models": True, "free_memory": True},
                    timeout=30.0,
                )
            except Exception as exc:  # noqa: BLE001
                cleanup["ok"] = False
                cleanup["error"] = str(exc)
                cleanup["failure"] = classify_comfy_runtime_error(exc, phase="cleanup", prompt_id=lease.prompt_id)

        released_at = time.time()
        with self._condition:
            current = self._lease_for_token(token)
            if current is None:
                return {"ok": False, "state": "already_released", "token": token, "cleanup": cleanup}
            group = current.resource_group
            prompt_id = current.prompt_id
            current.phase = str(state or "completed")
            current.recovery_state = "released"
            snapshot = current.snapshot()
            snapshot.update({
                "state": str(state or "completed"),
                "released_at": released_at,
                "cleanup": cleanup,
            })
            self._active.pop(group, None)
            self._token_to_group.pop(token, None)
            if prompt_id:
                self._prompt_to_token.pop((group, prompt_id), None)
            if cleanup.get("attempted") and cleanup.get("ok") is False:
                self._cleanup_debt[group] = {
                    "schema_id": "neo.runtime.comfy_gpu_cleanup_debt.v1",
                    "base_url": current.base_url,
                    "resource_group": group,
                    "created_at": released_at,
                    "retry_count": 0,
                    "last_error": cleanup.get("error") or "",
                    "failure": cleanup.get("failure") or {},
                    "source_state": state,
                    "prompt_id": prompt_id,
                }
            elif cleanup.get("attempted") and cleanup.get("ok"):
                self._cleanup_debt.pop(group, None)
            self._last_release[group] = snapshot
            self._condition.notify_all()
        return {"ok": True, **snapshot, "before_release": before}

    def release(self, token: str, *, state: str = "released") -> dict[str, Any]:
        return self.complete(token, state=state, cleanup_after=False)

    def recover(self, *, base_url: str | None = None, force_if_queue_clear: bool = False) -> dict[str, Any]:
        """Attempt safe stale-lease recovery without blindly interrupting Comfy."""
        targets: list[tuple[str, _Lease]] = []
        with self._condition:
            if base_url:
                group = comfy_gpu_resource_group(base_url)
                lease = self._active.get(group)
                if lease:
                    targets.append((group, lease))
            else:
                targets = [(group, lease) for group, lease in self._active.items()]
        results: list[dict[str, Any]] = []
        if base_url:
            self._cleanup_debt_retry(base_url)
        for group, lease in targets:
            result = self._probe_and_recover_lease(lease.token, force_if_queue_clear=force_if_queue_clear)
            results.append(result)
        return {
            "schema_id": "neo.runtime.comfy_gpu_lifecycle.recovery.v1",
            "ok": all(item.get("ok", True) for item in results) if results else True,
            "results": results,
            "status": self.status(group=comfy_gpu_resource_group(base_url) if base_url else None),
        }

    def status(self, *, group: str | None = None) -> dict[str, Any]:
        with self._condition:
            groups = [group] if group else sorted(set(self._active) | set(self._waiters) | set(self._last_release) | set(self._cleanup_debt) | set(self._last_reconcile))
            records: list[dict[str, Any]] = []
            for resource_group in groups:
                active = self._active.get(resource_group)
                records.append({
                    "resource_group": resource_group,
                    "busy": active is not None,
                    "active": active.snapshot() if active else None,
                    "waiting_count": len(self._waiters.get(resource_group) or []),
                    "last_release": dict(self._last_release.get(resource_group) or {}),
                    "cleanup_debt": dict(self._cleanup_debt.get(resource_group) or {}),
                    "last_reconcile": dict(self._last_reconcile.get(resource_group) or {}),
                })
            return {
                "schema_id": "neo.runtime.comfy_gpu_lifecycle.status.v2",
                "ok": True,
                "busy": any(record.get("busy") for record in records),
                "recovery_attention": any(bool(record.get("cleanup_debt")) or bool((record.get("active") or {}).get("recovery_state") in {"backend_unreachable", "history_missing", "recovery_hold"}) for record in records),
                "groups": records,
            }

    def _lease_for_token(self, token: str) -> _Lease | None:
        group = self._token_to_group.get(str(token or ""))
        if not group:
            return None
        lease = self._active.get(group)
        if lease and lease.token == token:
            return lease
        return None

    def _record_watch_state(self, token: str, *, phase: str | None = None, recovery_state: str | None = None, error_text: str = "", history_seen: bool = False, history_miss: bool = False, reset_errors: bool = False) -> _Lease | None:
        with self._condition:
            lease = self._lease_for_token(token)
            if lease is None:
                return None
            now = time.time()
            lease.last_observed_at = now
            if phase:
                lease.phase = phase
            if recovery_state:
                lease.recovery_state = recovery_state
            if history_seen:
                lease.last_history_seen_at = now
                lease.consecutive_history_misses = 0
            elif history_miss:
                lease.consecutive_history_misses += 1
            if error_text:
                lease.last_error = str(error_text)
                lease.consecutive_watch_errors += 1
            elif reset_errors:
                lease.last_error = ""
                lease.consecutive_watch_errors = 0
            return lease

    def _probe_and_recover_lease(self, token: str, *, force_if_queue_clear: bool = False) -> dict[str, Any]:
        with self._condition:
            lease = self._lease_for_token(token)
            if lease is None:
                return {"ok": True, "state": "already_released", "token": token}
            base_url = lease.base_url
            prompt_id = lease.prompt_id
            external_guard = lease.external_guard
            held_seconds = max(0.0, time.time() - lease.acquired_at)

        try:
            queue_payload = _http_json(base_url, "/queue", timeout=10.0)
            queue_ids = _queue_prompt_ids(queue_payload)
        except Exception as exc:  # noqa: BLE001
            failure = classify_comfy_runtime_error(exc, phase="recovery_probe", prompt_id=prompt_id)
            self._record_watch_state(token, phase="recovery_hold", recovery_state="backend_unreachable", error_text=str(exc))
            return {"ok": False, "state": "backend_unreachable", "token": token, "failure": failure}

        if external_guard:
            if not queue_ids:
                return self.complete(token, state="external_queue_cleared", cleanup_after=False)
            self.update_lease(token, phase="recovered_external_queue", metadata={"external_prompt_ids": queue_ids})
            return {"ok": True, "state": "external_queue_still_active", "token": token, "prompt_ids": queue_ids}

        if prompt_id:
            try:
                history = _http_json(base_url, f"/history/{parse.quote(prompt_id)}", timeout=10.0)
            except Exception:
                history = {}
            if isinstance(history, Mapping) and prompt_id in history:
                item = history.get(prompt_id) if isinstance(history.get(prompt_id), Mapping) else {}
                status = item.get("status") if isinstance(item, Mapping) and isinstance(item.get("status"), Mapping) else {}
                messages = status.get("messages") if isinstance(status.get("messages"), list) else []
                state = "failed" if str(status.get("status_str") or "").lower() in {"error", "failed"} else "completed"
                if any(isinstance(msg, (list, tuple)) and msg and str(msg[0]).lower() in {"execution_interrupted", "execution_cancelled"} for msg in messages):
                    state = "cancelled"
                return self.complete(token, state=state)
            if prompt_id in queue_ids:
                self._record_watch_state(token, phase="running", recovery_state="watching_history", reset_errors=True)
                return {"ok": True, "state": "prompt_still_queued", "token": token, "prompt_id": prompt_id}
            lease = self._record_watch_state(token, phase="history_missing", recovery_state="history_missing", history_miss=True, reset_errors=True)
            misses = int(lease.consecutive_history_misses if lease else 0)
            if force_if_queue_clear or misses >= DEFAULT_HISTORY_MISSING_CONFIRMATIONS:
                recovery = classify_comfy_runtime_error("history_lost: prompt disappeared from queue and history", phase="recovery", prompt_id=prompt_id)
                result = self.complete(token, state="history_lost_recovered")
                result["recovery"] = recovery
                return result
            return {"ok": True, "state": "history_missing_confirmation", "token": token, "confirmations": misses, "required": DEFAULT_HISTORY_MISSING_CONFIRMATIONS}

        if not queue_ids and held_seconds >= DEFAULT_UNBOUND_STALE_GRACE_SECONDS:
            return self.complete(token, state="unbound_stale_recovered", cleanup_after=lease.cleanup_after)
        return {"ok": True, "state": "unbound_guard_retained", "token": token, "queue_ids": queue_ids, "held_seconds": held_seconds}

    def _watch_prompt(self, *, token: str, watch_timeout_seconds: float, watch_interval_seconds: float) -> None:
        started = time.monotonic()
        timeout_marked = False
        while True:
            with self._condition:
                lease = self._lease_for_token(token)
                if lease is None:
                    return
                prompt_id = lease.prompt_id
                base_url = lease.base_url
            if not prompt_id:
                time.sleep(watch_interval_seconds)
                continue
            try:
                history = _http_json(base_url, f"/history/{parse.quote(prompt_id)}", timeout=20.0)
                if isinstance(history, Mapping) and prompt_id in history:
                    self._record_watch_state(token, phase="terminal_history_seen", recovery_state="terminal", history_seen=True, reset_errors=True)
                    item = history.get(prompt_id)
                    state = "completed"
                    if isinstance(item, Mapping):
                        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
                        status_str = str(status.get("status_str") or "").strip().lower()
                        if status_str in {"error", "failed"}:
                            state = "failed"
                        messages = status.get("messages") if isinstance(status.get("messages"), list) else []
                        if any(isinstance(message, (list, tuple)) and message and str(message[0]).lower() in {"execution_interrupted", "execution_cancelled"} for message in messages):
                            state = "cancelled"
                    self.complete(token, state=state)
                    return

                # History absence alone is ambiguous. Prove whether the prompt is
                # still active through /queue before considering stale recovery.
                queue_payload = _http_json(base_url, "/queue", timeout=10.0)
                queue_ids = _queue_prompt_ids(queue_payload)
                if prompt_id in queue_ids:
                    self._record_watch_state(token, phase="running", recovery_state="watching_history", reset_errors=True)
                else:
                    observed = self._record_watch_state(token, phase="history_missing", recovery_state="history_missing", history_miss=True, reset_errors=True)
                    if observed and observed.consecutive_history_misses >= DEFAULT_HISTORY_MISSING_CONFIRMATIONS:
                        result = self.complete(token, state="history_lost_recovered")
                        if isinstance(result, dict):
                            result["recovery"] = classify_comfy_runtime_error("history_lost: prompt disappeared from queue and history", phase="recovery", prompt_id=prompt_id)
                        return
            except Exception as exc:  # noqa: BLE001
                self._record_watch_state(token, phase="recovery_hold", recovery_state="backend_unreachable", error_text=str(exc))

            elapsed = time.monotonic() - started
            if elapsed >= watch_timeout_seconds and not timeout_marked:
                timeout_marked = True
                self._record_watch_state(
                    token,
                    phase="recovery_hold",
                    recovery_state="recovery_hold",
                    error_text="Surface/watch timeout exceeded; waiting for backend reconciliation instead of releasing the GPU unsafely.",
                )
            # Do not fail-open at watch timeout. Keep probing at a slower cadence;
            # once Comfy returns, queue/history reconciliation releases safely.
            sleep_for = max(watch_interval_seconds, DEFAULT_RECOVERY_SLOW_INTERVAL_SECONDS) if timeout_marked else watch_interval_seconds
            time.sleep(sleep_for)

    def _watch_unbound_queue(self, *, token: str) -> None:
        empty_confirmations = 0
        started = time.monotonic()
        while True:
            with self._condition:
                lease = self._lease_for_token(token)
                if lease is None:
                    return
                base_url = lease.base_url
            try:
                queue_payload = _http_json(base_url, "/queue", timeout=10.0)
                prompt_ids = _queue_prompt_ids(queue_payload)
                if prompt_ids:
                    empty_confirmations = 0
                    self._record_watch_state(token, phase="queue_uncertain_active", recovery_state="queue_uncertain_guard", reset_errors=True)
                    self.update_lease(token, metadata={"uncertain_queue_prompt_ids": prompt_ids})
                else:
                    empty_confirmations += 1
                    self._record_watch_state(token, phase="queue_uncertain_verifying_clear", recovery_state="queue_uncertain_verifying_clear", history_miss=True, reset_errors=True)
                    # A lost POST response can race with queue insertion. Require a
                    # short grace period plus multiple empty queue confirmations.
                    if (time.monotonic() - started) >= DEFAULT_UNBOUND_STALE_GRACE_SECONDS and empty_confirmations >= DEFAULT_HISTORY_MISSING_CONFIRMATIONS:
                        self.complete(token, state="queue_uncertainty_recovered")
                        return
            except Exception as exc:  # noqa: BLE001
                empty_confirmations = 0
                self._record_watch_state(token, phase="queue_uncertain_recovery_hold", recovery_state="backend_unreachable", error_text=str(exc))
            time.sleep(DEFAULT_RECOVERY_SLOW_INTERVAL_SECONDS)

    def _watch_external_queue(self, *, token: str) -> None:
        while True:
            with self._condition:
                lease = self._lease_for_token(token)
                if lease is None:
                    return
                base_url = lease.base_url
            try:
                queue_payload = _http_json(base_url, "/queue", timeout=10.0)
                prompt_ids = _queue_prompt_ids(queue_payload)
                if not prompt_ids:
                    self.complete(token, state="external_queue_cleared", cleanup_after=False)
                    return
                self._record_watch_state(token, phase="recovered_external_queue", recovery_state="external_queue_guard", reset_errors=True)
                self.update_lease(token, metadata={"external_prompt_ids": prompt_ids})
            except Exception as exc:  # noqa: BLE001
                self._record_watch_state(token, phase="external_queue_recovery_hold", recovery_state="backend_unreachable", error_text=str(exc))
            time.sleep(DEFAULT_RECOVERY_SLOW_INTERVAL_SECONDS)


_MANAGER = ComfyGpuLifecycleManager()


def get_comfy_gpu_lifecycle_manager() -> ComfyGpuLifecycleManager:
    return _MANAGER


def comfy_gpu_lifecycle_status() -> dict[str, Any]:
    return _MANAGER.status()


def comfy_gpu_lifecycle_recover(base_url: str | None = None, *, force_if_queue_clear: bool = False) -> dict[str, Any]:
    return _MANAGER.recover(base_url=base_url, force_if_queue_clear=force_if_queue_clear)


__all__ = [
    "ComfyGpuBusyError",
    "ComfyGpuLifecycleManager",
    "comfy_gpu_lifecycle_recover",
    "comfy_gpu_lifecycle_status",
    "comfy_gpu_resource_group",
    "get_comfy_gpu_lifecycle_manager",
    "normalize_base_url",
]
