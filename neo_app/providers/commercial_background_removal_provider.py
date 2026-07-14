from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib import error, request
from uuid import uuid4

from PIL import Image

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.providers.base import BaseProvider
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderFeatureCapabilities, ProviderRunResult, ProviderValidationResult


COMMERCIAL_PROVIDER_IDS = {"remove_bg", "clipdrop_remove_bg"}


class CommercialBackgroundRemovalError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _profile_connection(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("connection") if isinstance(profile.get("connection"), dict) else {}


def _profile_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("defaults") if isinstance(profile.get("defaults"), dict) else {}


def _join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").rstrip("/")
    suffix = "/" + str(path or "").lstrip("/")
    return base + suffix


def _multipart_body(*, fields: dict[str, Any], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----NeoStudioCommercialBoundary{uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        if value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode("utf-8")
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def _error_message(exc: error.HTTPError) -> str:
    payload = b""
    try:
        payload = exc.read()
    except Exception:
        payload = b""
    text = payload.decode("utf-8", errors="replace").strip()
    detail = ""
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                detail = str(parsed.get("error") or parsed.get("message") or parsed.get("detail") or "").strip()
                if isinstance(parsed.get("errors"), list) and parsed["errors"]:
                    row = parsed["errors"][0]
                    if isinstance(row, dict):
                        detail = str(row.get("title") or row.get("detail") or row.get("message") or detail).strip()
        except Exception:
            detail = text[:500]
    mapping = {
        400: "The commercial provider rejected the image or request settings.",
        401: "The commercial provider API key is missing or invalid.",
        402: "The commercial provider account has no remaining credits.",
        403: "The commercial provider API key is not permitted to use this endpoint.",
        406: "The requested output format is not accepted by the commercial provider.",
        413: "The source image is larger than the commercial provider allows.",
        429: "The commercial provider rate limit was reached.",
    }
    base = mapping.get(exc.code, f"Commercial provider request failed with HTTP {exc.code}.")
    return f"{base} {detail}".strip()


class CommercialBackgroundRemovalProvider(BaseProvider):
    """Server-side adapters for opt-in paid background-removal APIs.

    These profiles are utility bindings only. They are intentionally excluded from
    the Image generation backend selector and are invoked only from Image → Finish
    → Remove Background after per-run upload consent is supplied.
    """

    def __init__(self, manifest, *, profile: dict[str, Any] | None = None) -> None:
        super().__init__(manifest)
        self.profile = profile or {}

    def feature_capabilities(self) -> ProviderFeatureCapabilities:
        return ProviderFeatureCapabilities(
            progress=False,
            live_preview=False,
            cancel=False,
            pause=False,
            resume=False,
            output_handoff="neo_owned_file",
            progress_source="synchronous_http",
            live_preview_source="none",
        )

    def validate_job(self, job: NeoJob) -> ProviderValidationResult:
        base = super().validate_job(job)
        errors = list(base.errors)
        warnings = list(base.warnings)
        if job.mode != "background_removal":
            errors.append("Commercial background-removal profiles only support background_removal mode.")
        if not bool((job.params or {}).get("commercial_upload_consent")):
            errors.append("Commercial provider upload consent is required for this run.")
        return ProviderValidationResult(ok=not errors, provider_id=self.manifest.provider_id, errors=errors, warnings=warnings)

    def compile_job(self, job: NeoJob) -> CompiledJob:
        validation = self.validate_job(job)
        return CompiledJob(
            provider_id=self.manifest.provider_id,
            compile_status="compiled" if validation.ok else "mock_compiled",
            backend_payload={
                "validation": model_to_dict(validation),
                "provider_id": self.manifest.provider_id,
                "mode": job.mode,
                "params": {k: v for k, v in (job.params or {}).items() if k not in {"api_key", "api_key_value"}},
            },
        )

    def run_job(self, job: NeoJob) -> ProviderRunResult:
        return ProviderRunResult(
            job_id=job.job_id or f"commercial-bg-{uuid4().hex[:10]}",
            provider_id=self.manifest.provider_id,
            status="failed",
            message="Commercial background removal is executed through the Image Finish extension route.",
        )

    def _api_key(self) -> str:
        # Lazy import avoids the provider-registry/profile-store import cycle.
        from neo_app.providers.profiles import resolve_backend_profile_api_key

        status = resolve_backend_profile_api_key(self.profile)
        value = str(status.get("api_key_value") or "").strip()
        if not value:
            env = str((_profile_connection(self.profile).get("api_key_env") or "API key")).strip()
            raise CommercialBackgroundRemovalError(f"Configure {env} or save a manual key in Admin → Backends.")
        return value

    def _request_config(self, settings: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
        connection = _profile_connection(self.profile)
        defaults = _profile_defaults(self.profile)
        base_url = str(connection.get("base_url") or "").strip()
        path = str(defaults.get("remove_path") or connection.get("remove_path") or "").strip()
        if not base_url or not path:
            raise CommercialBackgroundRemovalError("Commercial provider profile is missing its base URL or removal path.")
        key = self._api_key()
        if self.manifest.provider_id == "remove_bg":
            requested_size = str(settings.get("commercial_output_size") or defaults.get("size") or "auto")
            requested_format = str(settings.get("commercial_remove_bg_format") or "png").strip().lower() or "png"
            fields = {
                "size": "50MP" if requested_size.lower() == "50mp" else requested_size,
                "format": requested_format,
                "type": str(settings.get("commercial_subject_type") or defaults.get("type") or "auto"),
                "semitransparency": "true" if settings.get("commercial_preserve_semitransparency", True) else "false",
            }
            headers = {"X-Api-Key": key, "Accept": "image/webp" if requested_format == "webp" else "image/png"}
        elif self.manifest.provider_id == "clipdrop_remove_bg":
            fields = {
                "transparency_handling": str(
                    settings.get("commercial_transparency_handling")
                    or defaults.get("transparency_handling")
                    or "return_input_if_non_opaque"
                )
            }
            headers = {"x-api-key": key, "Accept": "image/png"}
        else:
            raise CommercialBackgroundRemovalError(f"Unsupported commercial background-removal provider: {self.manifest.provider_id}")
        return _join_url(base_url, path), headers, fields

    def run_background_removal(self, source_path: Path, *, output_root: Path, settings: dict[str, Any]) -> dict[str, Any]:
        if not bool(settings.get("commercial_upload_consent")):
            raise CommercialBackgroundRemovalError(
                "This run was blocked because commercial-provider upload consent was not enabled."
            )
        if not source_path.exists() or not source_path.is_file():
            raise CommercialBackgroundRemovalError("The selected source image could not be found.")
        try:
            with Image.open(source_path) as source_image:
                width, height = source_image.size
        except Exception as exc:
            raise CommercialBackgroundRemovalError(f"The selected source image could not be decoded: {exc}") from exc
        megapixels = (width * height) / 1_000_000.0
        size_bytes = source_path.stat().st_size
        request_settings = dict(settings or {})
        if self.manifest.provider_id == "clipdrop_remove_bg":
            if megapixels > 25.0:
                raise CommercialBackgroundRemovalError(f"Clipdrop accepts images up to 25 megapixels; this source is {megapixels:.1f} MP.")
            if size_bytes > 30 * 1024 * 1024:
                raise CommercialBackgroundRemovalError("Clipdrop accepts source files up to 30 MB.")
        elif self.manifest.provider_id == "remove_bg":
            if megapixels > 50.0:
                raise CommercialBackgroundRemovalError(f"remove.bg accepts images up to 50 megapixels; this source is {megapixels:.1f} MP.")
            # remove.bg PNG responses are limited below its WebP ceiling. Request
            # transparent WebP for larger sources, then normalize it to Neo PNG.
            request_settings["commercial_remove_bg_format"] = "webp" if megapixels > 10.0 else "png"
        url, headers, fields = self._request_config(request_settings)
        body, boundary = _multipart_body(fields=fields, file_field="image_file", file_path=source_path)
        headers = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
        timeout = max(10.0, float(_profile_connection(self.profile).get("timeout_seconds") or 120))
        req = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - explicit opt-in commercial profile
                content = response.read()
                content_type = str(response.headers.get("Content-Type") or "image/png").split(";", 1)[0].strip().lower()
                response_headers = {str(k): str(v) for k, v in response.headers.items()}
        except error.HTTPError as exc:
            retry_after = None
            try:
                retry_after = int(exc.headers.get("Retry-After") or 0) or None
            except Exception:
                retry_after = None
            raise CommercialBackgroundRemovalError(_error_message(exc), status_code=exc.code, retry_after=retry_after) from exc
        except Exception as exc:
            raise CommercialBackgroundRemovalError(f"Could not reach the commercial background-removal provider: {exc}") from exc

        if not content or not content_type.startswith("image/"):
            text = content.decode("utf-8", errors="replace")[:500] if content else "empty response"
            raise CommercialBackgroundRemovalError(f"Commercial provider returned no image output: {text}")

        output_root.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex[:12]
        foreground_path = output_root / f"NeoStudioBackgroundRemovedCommercial_{token}.png"
        mask_path = output_root / f"NeoStudioBackgroundMaskCommercial_{token}.png"
        try:
            from io import BytesIO

            with Image.open(BytesIO(content)) as image:
                rgba = image.convert("RGBA")
                rgba.save(foreground_path, format="PNG")
                alpha = rgba.getchannel("A")
                if settings.get("save_mask", True):
                    alpha.save(mask_path, format="PNG")
        except Exception as exc:
            raise CommercialBackgroundRemovalError(f"Commercial provider returned an unreadable image: {exc}") from exc

        credits = {
            "consumed": response_headers.get("x-credits-consumed") or response_headers.get("X-Credits-Consumed") or response_headers.get("X-Credits-Charged") or "",
            "remaining": response_headers.get("x-remaining-credits") or response_headers.get("X-Remaining-Credits") or response_headers.get("X-RateLimit-Remaining") or "",
            "retry_after": response_headers.get("Retry-After") or "",
        }
        outputs = [
            {
                "kind": "image",
                "filename": foreground_path.name,
                "path": str(foreground_path),
                "role": "foreground",
                "metadata": {
                    "background_removal_role": "foreground",
                    "engine": "commercial_api",
                    "commercial_provider_id": self.manifest.provider_id,
                    "commercial_profile_id": self.profile.get("profile_id") or "",
                    "credits": credits,
                },
            }
        ]
        if settings.get("save_mask", True):
            outputs.append(
                {
                    "kind": "image",
                    "filename": mask_path.name,
                    "path": str(mask_path),
                    "role": "mask",
                    "metadata": {
                        "background_removal_role": "mask",
                        "engine": "commercial_api",
                        "commercial_provider_id": self.manifest.provider_id,
                        "commercial_profile_id": self.profile.get("profile_id") or "",
                    },
                }
            )
        return {
            "outputs": outputs,
            "engine": "commercial_api",
            "provider_id": self.manifest.provider_id,
            "profile_id": self.profile.get("profile_id") or "",
            "credits": credits,
            "runtime": {
                "provider_id": self.manifest.provider_id,
                "profile_id": self.profile.get("profile_id") or "",
                "content_type": content_type,
                "source_width": width,
                "source_height": height,
                "source_megapixels": round(megapixels, 4),
                "source_bytes": size_bytes,
                "credits": credits,
                "privacy": {
                    "external_upload": True,
                    "consent_recorded": True,
                    "source_retention_policy": "provider_terms_apply",
                },
            },
            "notes": [
                f"Commercial background removal completed through {self.manifest.display_name}.",
                "The source image was uploaded only after explicit per-run consent.",
            ],
        }
