from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Final


COMFY_ARTIFACT_TYPES: Final[frozenset[str]] = frozenset({"input", "output", "temp"})
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_URI_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_ANNOTATED_REFERENCE = re.compile(r"^(?P<path>.*?)(?:\s+\[(?P<type>[^\[\]]+)\])?$")


class ComfyArtifactPathError(ValueError):
    """Raised when a browser/runtime value is not a safe Comfy-relative artifact path."""


@dataclass(frozen=True)
class ComfyArtifactReference:
    """Canonical provider-relative Comfy artifact coordinates."""

    filename: str
    subfolder: str
    artifact_type: str
    load_name: str


def _text(value: Any) -> str:
    text = str(value or "").strip()
    if "\x00" in text or any(ord(char) < 32 for char in text):
        raise ComfyArtifactPathError("Comfy artifact paths cannot contain control characters.")
    return text


def normalize_comfy_artifact_type(value: Any, *, default: str = "output") -> str:
    artifact_type = _text(value).lower() or str(default or "output").strip().lower()
    if artifact_type not in COMFY_ARTIFACT_TYPES:
        raise ComfyArtifactPathError("Comfy artifact type must be input, output, or temp.")
    return artifact_type


def normalize_comfy_relative_path(value: Any, *, allow_empty: bool = False) -> str:
    """Return one portable Comfy-relative path using forward slashes only.

    This is a provider-boundary path, not a host filesystem path. Absolute paths,
    UNC paths, drive-prefixed paths, and traversal components are rejected rather
    than rewritten into something that could point at the wrong Comfy install.
    """

    text = _text(value)
    if not text:
        if allow_empty:
            return ""
        raise ComfyArtifactPathError("Comfy artifact path is required.")

    if text.startswith(("/", "\\", "~")) or _DRIVE_PREFIX.match(text) or _URI_PREFIX.match(text):
        raise ComfyArtifactPathError("Comfy artifact paths must be provider-relative.")

    portable = text.replace("\\", "/")
    parts: list[str] = []
    for raw_part in portable.split("/"):
        if not raw_part:
            continue
        part = raw_part.strip()
        if not part:
            continue
        if part in {".", ".."}:
            raise ComfyArtifactPathError("Comfy artifact paths cannot contain traversal components.")
        parts.append(part)

    if not parts:
        if allow_empty:
            return ""
        raise ComfyArtifactPathError("Comfy artifact path is required.")
    return "/".join(parts)


def normalize_comfy_subfolder(value: Any) -> str:
    return normalize_comfy_relative_path(value, allow_empty=True)


def normalize_comfy_filename(value: Any) -> str:
    filename = normalize_comfy_relative_path(value)
    if "/" in filename:
        raise ComfyArtifactPathError("Comfy artifact filename must not include a subfolder.")
    if filename in {".", ".."}:
        raise ComfyArtifactPathError("Comfy artifact filename is invalid.")
    return filename


def build_comfy_artifact_name(
    *,
    filename: Any,
    subfolder: Any = "",
    artifact_type: Any = "output",
) -> str:
    clean_filename = normalize_comfy_filename(filename)
    clean_subfolder = normalize_comfy_subfolder(subfolder)
    clean_type = normalize_comfy_artifact_type(artifact_type)
    relative_name = f"{clean_subfolder}/{clean_filename}" if clean_subfolder else clean_filename
    return relative_name if clean_type == "input" else f"{relative_name} [{clean_type}]"


def parse_comfy_artifact_name(value: Any, *, default_type: str = "output") -> ComfyArtifactReference:
    """Parse and normalize a Comfy dropdown/load name, preserving its type annotation."""

    text = _text(value)
    if not text:
        raise ComfyArtifactPathError("Comfy artifact reference is required.")
    match = _ANNOTATED_REFERENCE.fullmatch(text)
    if not match:
        raise ComfyArtifactPathError("Comfy artifact reference is malformed.")

    relative_path = normalize_comfy_relative_path(match.group("path"))
    annotation = match.group("type")
    artifact_type = normalize_comfy_artifact_type(annotation, default=default_type)
    if "/" in relative_path:
        subfolder, filename = relative_path.rsplit("/", 1)
    else:
        subfolder, filename = "", relative_path
    filename = normalize_comfy_filename(filename)
    load_name = build_comfy_artifact_name(
        filename=filename,
        subfolder=subfolder,
        artifact_type=artifact_type,
    )
    return ComfyArtifactReference(
        filename=filename,
        subfolder=subfolder,
        artifact_type=artifact_type,
        load_name=load_name,
    )


def normalize_comfy_artifact_reference(
    *,
    filename: Any = "",
    subfolder: Any = "",
    artifact_type: Any = "output",
    load_name: Any = "",
) -> ComfyArtifactReference:
    """Canonicalize source fields or a legacy Comfy load name into one reference."""

    raw_filename = _text(filename)
    if raw_filename:
        clean_filename = normalize_comfy_filename(raw_filename)
        clean_subfolder = normalize_comfy_subfolder(subfolder)
        clean_type = normalize_comfy_artifact_type(artifact_type)
        return ComfyArtifactReference(
            filename=clean_filename,
            subfolder=clean_subfolder,
            artifact_type=clean_type,
            load_name=build_comfy_artifact_name(
                filename=clean_filename,
                subfolder=clean_subfolder,
                artifact_type=clean_type,
            ),
        )

    raw_load_name = _text(load_name)
    if raw_load_name:
        return parse_comfy_artifact_name(raw_load_name, default_type=str(artifact_type or "output"))

    raise ComfyArtifactPathError("Comfy artifact reference is missing its filename.")
