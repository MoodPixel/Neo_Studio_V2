"""Node readiness helpers for Image Upscale Phase D.

This module does not connect to Comfy yet. It normalizes a caller-provided node
list into the support-matrix shape that later API/UI phases can consume.
"""
from __future__ import annotations

from typing import Any

from .support_matrix import node_gate_status, support_with_nodes


def readiness_from_nodes(route: dict[str, Any] | None = None, available_nodes: list[str] | set[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    return support_with_nodes(route or {"backend": "comfyui", "workspace_app": "finish"}, available_nodes=available_nodes or [])
