from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

from neo_app.video import wan_lora_integration as integration

_ROOT_WAN_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("neo_video_wan_phase8_root_payload", default=None)
_INSTALLED = False


def _wrap_generate(module: Any, name: str) -> None:
    current: Callable[..., dict[str, Any]] = getattr(module, name)
    if getattr(current, "_neo_phase8_root_payload_wrapper", False):
        return

    def wrapped(payload: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _ROOT_WAN_PAYLOAD.set(dict(payload or {}))
        try:
            return current(payload, *args, **kwargs)
        finally:
            _ROOT_WAN_PAYLOAD.reset(token)

    wrapped._neo_phase8_root_payload_wrapper = True  # type: ignore[attr-defined]
    setattr(module, name, wrapped)


def _wrap_build(module: Any, name: str) -> None:
    current: Callable[..., dict[str, Any]] = getattr(module, name)
    if getattr(current, "_neo_phase8_root_build_guard", False):
        return

    def wrapped(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        root_payload = _ROOT_WAN_PAYLOAD.get()
        if root_payload is None:
            return current(req, object_info=object_info)
        token = integration._PHASE8_PAYLOAD.set(dict(root_payload))
        try:
            return current(req, object_info=object_info)
        finally:
            integration._PHASE8_PAYLOAD.reset(token)

    wrapped._neo_phase8_root_build_guard = True  # type: ignore[attr-defined]
    setattr(module, name, wrapped)


def install_wan_lora_payload_context_guard() -> None:
    """Keep the original extension payload authoritative through Generate -> Compile nesting.

    WAN compiler request dataclasses intentionally ignore extension blocks. Generate functions
    reconstruct request payloads before calling Compile, so this guard preserves the outer user
    payload until the compiler-owned LoRA build hook consumes it.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from neo_app.video import wan_gguf_i2v14_compiler as dual
    from neo_app.video import wan_txt2vid_compiler as single

    _wrap_build(single, "build_wan22_txt2vid_workflow")
    _wrap_build(dual, "build_wan22_gguf_i2v14_workflow")
    for name in ("video_wan22_txt2vid_generate_payload", "video_wan22_img2vid_generate_payload"):
        _wrap_generate(single, name)
    _wrap_generate(dual, "video_wan22_gguf_i2v14_generate_payload")
    _INSTALLED = True
