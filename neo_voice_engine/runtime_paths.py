from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


def _resolved_path(raw: str | os.PathLike[str], *, base: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


@dataclass(frozen=True)
class VoiceRuntimePaths:
    """Resolved external runtime layout for Neo Voice Engine.

    Source stays under the Neo Studio repository. Mutable environments, model
    assets, caches, logs and gateway handoff data live under the external Voice
    runtime root so new model families do not create root-level venv clutter.
    """

    project_root: Path
    neo_runtime_root: Path
    voice_runtime_root: Path

    @property
    def envs_root(self) -> Path:
        return self.voice_runtime_root / "envs"

    @property
    def gateway_env_root(self) -> Path:
        return self.envs_root / "gateway"

    @property
    def models_root(self) -> Path:
        return self.voice_runtime_root / "models"

    @property
    def cache_root(self) -> Path:
        return self.voice_runtime_root / "cache"

    @property
    def temp_root(self) -> Path:
        return self.voice_runtime_root / "temp"

    @property
    def logs_root(self) -> Path:
        return self.voice_runtime_root / "logs"

    @property
    def state_root(self) -> Path:
        return self.voice_runtime_root / "state"

    @property
    def outputs_root(self) -> Path:
        # Gateway-owned provider handoff remains a dedicated directory. It is
        # temporary/runtime data, not Neo's durable Voice Results store.
        return self.voice_runtime_root / "outputs"

    @property
    def legacy_backups_root(self) -> Path:
        return self.voice_runtime_root / "legacy_backups"

    def engine_env_root(self, engine_id: str) -> Path:
        safe = str(engine_id or "").strip().lower()
        if not safe or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in safe):
            raise ValueError(f"Invalid Voice engine environment id: {engine_id!r}")
        return self.envs_root / safe

    def ensure_dirs(self) -> None:
        for path in (
            self.voice_runtime_root,
            self.envs_root,
            self.models_root,
            self.cache_root,
            self.temp_root,
            self.logs_root,
            self.state_root,
            self.outputs_root,
            self.legacy_backups_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def public_payload(self) -> dict[str, str]:
        return {
            "neo_runtime_root": str(self.neo_runtime_root),
            "voice_runtime_root": str(self.voice_runtime_root),
            "envs_root": str(self.envs_root),
            "models_root": str(self.models_root),
            "cache_root": str(self.cache_root),
            "temp_root": str(self.temp_root),
            "logs_root": str(self.logs_root),
            "state_root": str(self.state_root),
            "outputs_root": str(self.outputs_root),
        }


def resolve_voice_runtime_paths(
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> VoiceRuntimePaths:
    env = os.environ if environ is None else environ
    project = Path(project_root).expanduser().resolve()
    parent = project.parent

    explicit_voice = str(env.get("NEO_VOICE_RUNTIME_ROOT") or "").strip()
    legacy_voice_data = str(env.get("NEO_VOICE_ENGINE_DATA") or "").strip()
    explicit_neo = str(env.get("NEO_RUNTIME_ROOT") or "").strip()

    if explicit_neo:
        neo_root = _resolved_path(explicit_neo, base=project)
    else:
        neo_root = (parent / "Neo_Runtime").resolve()

    if explicit_voice:
        voice_root = _resolved_path(explicit_voice, base=project)
    elif legacy_voice_data:
        # Backward-compatible explicit override from VO-E2..E5. New installs
        # should use NEO_VOICE_RUNTIME_ROOT instead.
        voice_root = _resolved_path(legacy_voice_data, base=project)
    else:
        voice_root = (neo_root / "voice").resolve()

    if not explicit_neo and explicit_voice:
        # Keep the global root useful for diagnostics when only a Voice-specific
        # override is supplied. The Voice root itself remains authoritative.
        neo_root = voice_root.parent.resolve()

    return VoiceRuntimePaths(
        project_root=project,
        neo_runtime_root=neo_root,
        voice_runtime_root=voice_root,
    )
