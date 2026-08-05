from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request
from urllib.parse import urlencode, urlparse


class ForgeNeoClientError(RuntimeError):
    """Normalized Forge Neo HTTP client failure."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "request_failed",
        status_code: int | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ForgeNeoClientConfig:
    base_url: str
    timeout_seconds: float = 30.0
    authorization_header: str = ""
    bridge_token: str = ""

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "ForgeNeoClientConfig":
        profile = profile if isinstance(profile, dict) else {}
        connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
        runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
        base_url = str(
            connection.get("base_url")
            or runtime.get("base_url")
            or "http://127.0.0.1:7860"
        ).strip().rstrip("/")
        timeout = float(connection.get("timeout_seconds") or 30.0)
        authorization = _resolve_basic_authorization(connection)
        bridge_token_env = str(connection.get("bridge_token_env") or "NEO_FORGE_BRIDGE_TOKEN").strip()
        bridge_token = str(os.getenv(bridge_token_env) or "").strip() if bridge_token_env else ""
        validate_forge_base_url(base_url)
        return cls(
            base_url=base_url,
            timeout_seconds=max(1.0, min(timeout, 600.0)),
            authorization_header=authorization,
            bridge_token=bridge_token,
        )


def validate_forge_base_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ForgeNeoClientError("Forge base URL must be a valid http(s) URL.", kind="invalid_config")
    if parsed.username or parsed.password:
        raise ForgeNeoClientError(
            "Credentials must not be embedded in the Forge base URL.",
            kind="invalid_config",
        )
    return str(base_url).strip().rstrip("/")


def _resolve_basic_authorization(connection: dict[str, Any]) -> str:
    """Resolve optional Basic auth without writing credentials into public profiles.

    Supported runtime-only forms:
    - ``api_auth_env``: environment variable containing ``username:password``
    - ``api_auth_username`` + ``api_auth_password_env``

    The client intentionally does not accept a plaintext password from committed
    profile JSON. Phase 2 can display authentication-required diagnostics while
    local deployments opt into environment-backed credentials.
    """

    auth_env = str(connection.get("api_auth_env") or "").strip()
    combined = str(os.getenv(auth_env) or "").strip() if auth_env else ""
    if not combined:
        username = str(connection.get("api_auth_username") or "").strip()
        password_env = str(connection.get("api_auth_password_env") or "").strip()
        password = str(os.getenv(password_env) or "") if password_env else ""
        if username and password:
            combined = f"{username}:{password}"
    if not combined or ":" not in combined:
        return ""
    encoded = base64.b64encode(combined.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


class ForgeNeoClient:
    """Provider-facing client for Forge Neo's A1111-compatible API.

    The client owns transport, authentication, generation submission, progress,
    preview, and interruption calls. Durable state and queue policy remain owned
    by Neo's Forge job manager.
    """

    def __init__(self, config: ForgeNeoClientConfig) -> None:
        self.config = config

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "ForgeNeoClient":
        return cls(ForgeNeoClientConfig.from_profile(profile))

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Any = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.config.base_url}/{str(path or '').lstrip('/')}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.config.authorization_header:
            request_headers["Authorization"] = self.config.authorization_header
        req = request.Request(url, data=body, headers=request_headers, method=str(method or "GET").upper())
        try:
            with request.urlopen(req, timeout=timeout or self.config.timeout_seconds) as response:  # noqa: S310 - user-configured backend URL
                raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            kind = "authentication_required" if int(exc.code) in {401, 403} else "http_error"
            raise ForgeNeoClientError(
                f"Forge endpoint returned HTTP {exc.code}.",
                kind=kind,
                status_code=int(exc.code),
                detail=detail,
            ) from exc
        except error.URLError as exc:
            raise ForgeNeoClientError(
                f"Could not reach Forge: {exc.reason}",
                kind="offline",
                detail=str(exc.reason),
            ) from exc
        except json.JSONDecodeError as exc:
            raise ForgeNeoClientError(
                "Forge endpoint returned non-JSON content.",
                kind="invalid_json",
                detail=str(exc),
            ) from exc
        except TimeoutError as exc:
            raise ForgeNeoClientError("Forge request timed out.", kind="timeout", detail=str(exc)) from exc

    def get_options(self) -> dict[str, Any]:
        payload = self.request_json("GET", "/sdapi/v1/options")
        return payload if isinstance(payload, dict) else {}

    def list_models(self) -> list[dict[str, Any]]:
        payload = self.request_json("GET", "/sdapi/v1/sd-models")
        return payload if isinstance(payload, list) else []

    def list_modules(self) -> list[dict[str, Any]]:
        payload = self.request_json("GET", "/sdapi/v1/sd-modules")
        return payload if isinstance(payload, list) else []

    def list_samplers(self) -> list[dict[str, Any]]:
        payload = self.request_json("GET", "/sdapi/v1/samplers")
        return payload if isinstance(payload, list) else []

    def list_schedulers(self) -> list[dict[str, Any]]:
        payload = self.request_json("GET", "/sdapi/v1/schedulers")
        return payload if isinstance(payload, list) else []

    def submit(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Submit one synchronous Forge generation request.

        Forge owns the backend queue lock; Neo's lifecycle manager calls this
        primitive from a single durable worker and uses a longer generation
        timeout than the short Admin/connection probes.
        """

        response = self.request_json("POST", endpoint, payload=payload, timeout=timeout)
        return response if isinstance(response, dict) else {}

    def get_progress(self, *, skip_current_image: bool = True, timeout: float | None = None) -> dict[str, Any]:
        skip = "true" if skip_current_image else "false"
        payload = self.request_json("GET", f"/sdapi/v1/progress?skip_current_image={skip}", timeout=timeout)
        return payload if isinstance(payload, dict) else {}

    def interrupt(self, *, timeout: float | None = None) -> dict[str, Any]:
        payload = self.request_json("POST", "/sdapi/v1/interrupt", payload={}, timeout=timeout)
        return payload if isinstance(payload, dict) else {}

    def skip(self, *, timeout: float | None = None) -> dict[str, Any]:
        payload = self.request_json("POST", "/sdapi/v1/skip", payload={}, timeout=timeout)
        return payload if isinstance(payload, dict) else {}

    def _bridge_headers(self) -> dict[str, str]:
        return {"X-Neo-Bridge-Token": self.config.bridge_token} if self.config.bridge_token else {}

    def bridge_handshake(self, *, timeout: float | None = None) -> dict[str, Any]:
        payload = self.request_json("GET", "/neo-api/v1/handshake", timeout=timeout, headers=self._bridge_headers())
        return payload if isinstance(payload, dict) else {}

    def bridge_capabilities(self, *, timeout: float | None = None) -> dict[str, Any]:
        payload = self.request_json("GET", "/neo-api/v1/capabilities", timeout=timeout, headers=self._bridge_headers())
        return payload if isinstance(payload, dict) else {}

    def bridge_settings_schema(self, *, timeout: float | None = None) -> dict[str, Any]:
        payload = self.request_json("GET", "/neo-api/v1/settings-schema", timeout=timeout, headers=self._bridge_headers())
        return payload if isinstance(payload, dict) else {}

    def bridge_submit(
        self,
        *,
        job_id: str,
        endpoint: str = "",
        operation: str = "",
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        endpoint = str(endpoint or "").strip()
        operation = str(operation or "").strip()
        if bool(endpoint) == bool(operation):
            raise ForgeNeoClientError(
                "Forge Bridge submission requires exactly one endpoint or native operation.",
                kind="invalid_request",
            )
        request_payload = {"job_id": job_id, "payload": payload}
        if operation:
            request_payload["operation"] = operation
        else:
            request_payload["endpoint"] = endpoint
        response = self.request_json(
            "POST",
            "/neo-api/v1/jobs",
            payload=request_payload,
            timeout=timeout,
            headers=self._bridge_headers(),
        )
        return response if isinstance(response, dict) else {}

    def bridge_get_job(
        self,
        job_id: str,
        *,
        include_images: bool = False,
        include_preview: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        query = urlencode({"include_images": str(bool(include_images)).lower(), "include_preview": str(bool(include_preview)).lower()})
        response = self.request_json(
            "GET",
            f"/neo-api/v1/jobs/{job_id}?{query}",
            timeout=timeout,
            headers=self._bridge_headers(),
        )
        return response if isinstance(response, dict) else {}

    def bridge_cancel(self, job_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        response = self.request_json(
            "POST",
            f"/neo-api/v1/jobs/{job_id}/cancel",
            payload={},
            timeout=timeout,
            headers=self._bridge_headers(),
        )
        return response if isinstance(response, dict) else {}

    def bridge_history(self, *, limit: int = 50, timeout: float | None = None) -> dict[str, Any]:
        response = self.request_json(
            "GET",
            f"/neo-api/v1/history?{urlencode({'limit': max(1, min(int(limit), 500))})}",
            timeout=timeout,
            headers=self._bridge_headers(),
        )
        return response if isinstance(response, dict) else {}

