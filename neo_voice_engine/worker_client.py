from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib import parse, request
from urllib.error import HTTPError

from .errors import VoiceEngineError


@dataclass(frozen=True)
class WorkerAudio:
    data: bytes
    media_type: str
    filename: str = "output.wav"


class VoiceWorkerClient(Protocol):
    def health(self) -> dict[str, Any]: ...
    def capabilities(self) -> dict[str, Any]: ...
    def models(self) -> dict[str, Any]: ...
    def voices(self) -> dict[str, Any]: ...
    def controls(self, model_id: str, mode: str) -> dict[str, Any]: ...
    def submit(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def poll(self, provider_job_id: str) -> dict[str, Any] | WorkerAudio: ...
    def cancel(self, provider_job_id: str) -> dict[str, Any]: ...
    def output(self, provider_job_id: str, output_url: str | None = None) -> WorkerAudio: ...
    def model_lifecycle(self, model_id: str) -> dict[str, Any]: ...
    def load_model(self, model_id: str, *, device: str = "", device_index: int | None = None) -> dict[str, Any]: ...
    def unload_model(self, model_id: str) -> dict[str, Any]: ...


class HttpVoiceWorkerClient:
    """HTTP adapter for Voice workers using the existing Neo Voice adapter grammar.

    This deliberately supports the physical Chatterbox adapter's current behavior,
    including direct audio returned by GET /api/voice/jobs/{id}. VO-E5 can therefore
    place Chatterbox behind the gateway without rewriting its synthesis implementation.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0, health_path: str = "/api/voice/health") -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.timeout = float(timeout)
        self.health_path = str(health_path or "/api/voice/health").strip() or "/api/voice/health"
        if not self.health_path.startswith("/"):
            self.health_path = "/" + self.health_path
        if not self.base_url:
            raise ValueError("Worker base URL is required")

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _decode(self, response: Any) -> dict[str, Any] | WorkerAudio:
        raw = response.read()
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        disposition = str(response.headers.get("Content-Disposition") or "")
        if content_type.startswith("audio/") or content_type == "application/octet-stream":
            filename = "output.wav"
            if "filename=" in disposition:
                filename = disposition.split("filename=", 1)[1].strip().strip('"') or filename
            return WorkerAudio(data=raw, media_type=content_type or "audio/wav", filename=filename)
        try:
            decoded = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            raise VoiceEngineError(
                "worker_unavailable",
                "Voice worker returned an unreadable response.",
                retryable=True,
                details={"base_url": self.base_url, "content_type": content_type},
                http_status=502,
            ) from exc
        return decoded if isinstance(decoded, dict) else {"items": decoded}

    def _get(self, path: str, *, optional: bool = False) -> dict[str, Any] | WorkerAudio:
        req = request.Request(self._url(path), headers={"Accept": "application/json,audio/*,application/octet-stream"}, method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310 - trusted worker URL from supervisor config.
                return self._decode(response)
        except VoiceEngineError:
            raise
        except HTTPError as exc:
            if optional and exc.code in {404, 405, 501}:
                raise VoiceEngineError(
                    "unsupported_operation",
                    "Voice worker does not expose this optional operation.",
                    retryable=False,
                    details={"base_url": self.base_url, "path": path, "status": exc.code},
                    http_status=501,
                ) from exc
            raise VoiceEngineError(
                "worker_unavailable",
                f"Voice worker HTTP request failed with status {exc.code}.",
                retryable=True,
                details={"base_url": self.base_url, "path": path, "status": exc.code},
                http_status=503,
            ) from exc
        except Exception as exc:
            raise VoiceEngineError(
                "worker_unavailable",
                f"Voice worker did not respond: {exc}",
                retryable=True,
                details={"base_url": self.base_url, "path": path},
                http_status=503,
            ) from exc

    def _post(self, path: str, payload: dict[str, Any] | None = None, *, optional: bool = False) -> dict[str, Any] | WorkerAudio:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self._url(path),
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json,audio/*,application/octet-stream"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310 - trusted worker URL from supervisor config.
                return self._decode(response)
        except VoiceEngineError:
            raise
        except HTTPError as exc:
            if optional and exc.code in {404, 405, 501}:
                raise VoiceEngineError(
                    "unsupported_operation",
                    "Voice worker does not expose this optional operation.",
                    retryable=False,
                    details={"base_url": self.base_url, "path": path, "status": exc.code},
                    http_status=501,
                ) from exc
            raise VoiceEngineError(
                "worker_unavailable",
                f"Voice worker HTTP request failed with status {exc.code}.",
                retryable=True,
                details={"base_url": self.base_url, "path": path, "status": exc.code},
                http_status=503,
            ) from exc
        except Exception as exc:
            raise VoiceEngineError(
                "worker_unavailable",
                f"Voice worker request failed: {exc}",
                retryable=True,
                details={"base_url": self.base_url, "path": path},
                http_status=503,
            ) from exc

    def health(self) -> dict[str, Any]:
        payload = self._get(self.health_path)
        return payload if isinstance(payload, dict) else {}

    def capabilities(self) -> dict[str, Any]:
        payload = self._get("/api/voice/capabilities")
        return payload if isinstance(payload, dict) else {}

    def models(self) -> dict[str, Any]:
        payload = self._get("/api/voice/models")
        return payload if isinstance(payload, dict) else {}

    def voices(self) -> dict[str, Any]:
        payload = self._get("/api/voice/voices")
        return payload if isinstance(payload, dict) else {}

    def controls(self, model_id: str, mode: str) -> dict[str, Any]:
        query = parse.urlencode({"model_id": model_id, "mode": mode})
        try:
            payload = self._get(f"/api/voice/controls?{query}")
        except VoiceEngineError:
            return {"schema_id": "neo.voice_engine.worker_controls.v1", "controls": [], "authoritative": False}
        return payload if isinstance(payload, dict) else {"controls": []}

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._post("/api/voice/render", payload)
        if isinstance(response, WorkerAudio):
            raise VoiceEngineError("internal_error", "Voice worker returned audio directly from an asynchronous submit endpoint.", http_status=502)
        return response

    def poll(self, provider_job_id: str) -> dict[str, Any] | WorkerAudio:
        return self._get(f"/api/voice/jobs/{parse.quote(provider_job_id, safe='')}")

    def cancel(self, provider_job_id: str) -> dict[str, Any]:
        response = self._post(f"/api/voice/jobs/{parse.quote(provider_job_id, safe='')}/cancel", {})
        return response if isinstance(response, dict) else {"status": "cancelled"}

    def output(self, provider_job_id: str, output_url: str | None = None) -> WorkerAudio:
        path = output_url or f"/api/voice/jobs/{parse.quote(provider_job_id, safe='')}/output"
        response = self._get(path)
        if isinstance(response, WorkerAudio):
            return response
        raise VoiceEngineError(
            "output_missing",
            "Voice worker reported completion without retrievable audio.",
            retryable=True,
            details={"provider_job_id": provider_job_id},
            http_status=502,
        )

    def model_lifecycle(self, model_id: str) -> dict[str, Any]:
        encoded = parse.quote(str(model_id), safe="")
        payload = self._get(f"/api/voice/models/{encoded}/lifecycle", optional=True)
        return payload if isinstance(payload, dict) else {}

    def load_model(self, model_id: str, *, device: str = "", device_index: int | None = None) -> dict[str, Any]:
        encoded = parse.quote(str(model_id), safe="")
        body: dict[str, Any] = {"model_id": str(model_id)}
        if device:
            body["device"] = str(device)
        if device_index is not None:
            body["device_index"] = int(device_index)
        payload = self._post(f"/api/voice/models/{encoded}/load", body, optional=True)
        return payload if isinstance(payload, dict) else {}

    def unload_model(self, model_id: str) -> dict[str, Any]:
        encoded = parse.quote(str(model_id), safe="")
        payload = self._post(f"/api/voice/models/{encoded}/unload", {"model_id": str(model_id)}, optional=True)
        return payload if isinstance(payload, dict) else {}
