"""Optional ONNX runtime discovery and explicit provider selection.

Importing this module never imports ONNX Runtime or downloads weights.
"""
from __future__ import annotations

import importlib
from pathlib import Path


def runtime_status() -> dict:
    try:
        ort = importlib.import_module("onnxruntime")
        return {"available": True, "version": ort.__version__,
                "providers": list(ort.get_available_providers()), "reason": ""}
    except Exception as exc:
        return {"available": False, "version": "", "providers": [],
                "reason": f"Optional ONNX Runtime is unavailable: {exc}"}


def open_session(path: Path, provider: str):
    status = runtime_status()
    if not status["available"]:
        raise RuntimeError(status["reason"])
    if provider not in status["providers"]:
        raise RuntimeError(f"Requested ONNX provider {provider} is unavailable. Select an installed provider explicitly.")
    ort = importlib.import_module("onnxruntime")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    if provider != "CPUExecutionProvider":
        # Reject unsupported operators instead of silently computing them on CPU.
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    if provider == "DmlExecutionProvider":
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(str(path), sess_options=options, providers=[provider])
    session.disable_fallback()
    if session.get_providers()[0] != provider:
        raise RuntimeError(f"ONNX did not load the requested provider {provider}; no fallback is permitted.")
    return session, status
