from __future__ import annotations

from typing import Any


class VoiceEngineError(RuntimeError):
    """Structured gateway error safe to expose through the VO-E1 API contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "internal_error")
        self.message = str(message or "Neo Voice Engine error")
        self.retryable = bool(retryable)
        self.details = dict(details or {})
        self.http_status = int(http_status)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_id": "neo.voice_engine.error.v1",
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            },
        }
