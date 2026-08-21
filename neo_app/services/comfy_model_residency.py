from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .comfy_gpu_lifecycle import normalize_base_url

SCHEMA_ID = "neo.runtime.comfy_model_residency.v1"
DEFAULT_STATE_RELATIVE = Path("neo_data") / "runtime" / "comfy_model_residency.json"


class ComfyModelResidencyGuard:
    """Track one-shot model-cache cleanup requirements per Comfy backend.

    Normal Comfy runs intentionally keep model objects resident. A cache flush is
    only requested when a prior LayerDiffuse prompt could have left a patched
    model object that must not be reused by the next non-LayerDiffuse workflow.

    The tiny state file lets that safety requirement survive a Neo restart while
    ComfyUI itself remains alive. It never stores model data or user prompts.
    """

    def __init__(self, state_path: Path | str | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.state_path = Path(state_path) if state_path is not None else root / DEFAULT_STATE_RELATIVE
        self._lock = threading.RLock()
        self._state: dict[str, Any] | None = None

    def _empty(self) -> dict[str, Any]:
        return {"schema_id": SCHEMA_ID, "backends": {}}

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if self._state is not None:
                return self._state
            data = self._empty()
            try:
                if self.state_path.is_file():
                    raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = raw
            except Exception:
                data = self._empty()
            if data.get("schema_id") != SCHEMA_ID or not isinstance(data.get("backends"), dict):
                data = self._empty()
            self._state = data
            return self._state

    def _persist(self) -> None:
        with self._lock:
            data = self._load()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            temp.replace(self.state_path)

    def _record(self, base_url: str | None) -> dict[str, Any]:
        data = self._load()
        key = normalize_base_url(base_url)
        backends = data.setdefault("backends", {})
        record = backends.get(key)
        if not isinstance(record, dict):
            record = {
                "base_url": key,
                "layerdiffuse_reset_required": False,
                "transition_count": 0,
                "free_success_count": 0,
                "free_failure_count": 0,
            }
            backends[key] = record
        return record

    def prequeue_decision(
        self,
        base_url: str | None,
        *,
        layerdiffuse_requested: bool,
        guard_enabled: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            record = dict(self._record(base_url))
        pending = bool(record.get("layerdiffuse_reset_required"))
        if not guard_enabled:
            reason = "guard_disabled"
            should_free = False
        elif layerdiffuse_requested:
            reason = "layerdiffuse_route_active"
            should_free = False
        elif pending:
            reason = "layerdiffuse_to_normal_transition"
            should_free = True
        else:
            reason = "keep_resident"
            should_free = False
        return {
            "schema_id": SCHEMA_ID,
            "base_url": normalize_base_url(base_url),
            "guard_enabled": bool(guard_enabled),
            "layerdiffuse_requested": bool(layerdiffuse_requested),
            "reset_required": pending,
            "should_free": should_free,
            "reason": reason,
            "resident_policy": "keep_models_loaded_until_transition_or_explicit_recovery",
        }

    def mark_layerdiffuse_queued(self, base_url: str | None, *, prompt_id: str = "", run_id: str = "") -> dict[str, Any]:
        now = time.time()
        with self._lock:
            record = self._record(base_url)
            record.update({
                "layerdiffuse_reset_required": True,
                "marked_at": now,
                "source_prompt_id": str(prompt_id or ""),
                "source_run_id": str(run_id or ""),
                "last_event": "layerdiffuse_prompt_queued",
            })
            record["transition_count"] = int(record.get("transition_count") or 0) + 1
            self._persist()
            return dict(record)

    def mark_free_succeeded(self, base_url: str | None, *, reason: str, prompt_id: str = "", run_id: str = "") -> dict[str, Any]:
        now = time.time()
        with self._lock:
            record = self._record(base_url)
            record.update({
                "layerdiffuse_reset_required": False,
                "last_free_at": now,
                "last_free_reason": str(reason or ""),
                "last_free_prompt_id": str(prompt_id or ""),
                "last_free_run_id": str(run_id or ""),
                "last_event": "cache_free_succeeded",
            })
            record["free_success_count"] = int(record.get("free_success_count") or 0) + 1
            self._persist()
            return dict(record)

    def mark_free_failed(self, base_url: str | None, *, reason: str, error: str, run_id: str = "") -> dict[str, Any]:
        now = time.time()
        with self._lock:
            record = self._record(base_url)
            # Keep reset_required unchanged so the next normal request retries.
            record.update({
                "last_free_failure_at": now,
                "last_free_reason": str(reason or ""),
                "last_free_error": str(error or ""),
                "last_free_run_id": str(run_id or ""),
                "last_event": "cache_free_failed",
            })
            record["free_failure_count"] = int(record.get("free_failure_count") or 0) + 1
            self._persist()
            return dict(record)

    def status(self, base_url: str | None) -> dict[str, Any]:
        with self._lock:
            record = dict(self._record(base_url))
        return {"schema_id": SCHEMA_ID, **record}

    def reset_for_tests(self) -> None:
        with self._lock:
            self._state = self._empty()
            try:
                if self.state_path.exists():
                    self.state_path.unlink()
            except OSError:
                pass


_RESIDENCY_GUARD: ComfyModelResidencyGuard | None = None
_RESIDENCY_LOCK = threading.Lock()


def get_comfy_model_residency_guard() -> ComfyModelResidencyGuard:
    global _RESIDENCY_GUARD
    if _RESIDENCY_GUARD is None:
        with _RESIDENCY_LOCK:
            if _RESIDENCY_GUARD is None:
                _RESIDENCY_GUARD = ComfyModelResidencyGuard()
    return _RESIDENCY_GUARD
