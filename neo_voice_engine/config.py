from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path

from .runtime_paths import resolve_voice_runtime_paths


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, value)


def is_loopback_host(host: str) -> bool:
    value = str(host or "").strip().lower()
    if value in {"localhost", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _manifest_roots_from_env(project_root: Path) -> tuple[Path, ...]:
    roots: list[Path] = [(project_root / "neo_voice_engine" / "manifests").resolve()]
    raw = str(os.getenv("NEO_VOICE_ENGINE_MANIFEST_DIRS") or "").strip()
    if raw:
        for item in raw.split(os.pathsep):
            value = str(item or "").strip()
            if not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = project_root / path
            resolved = path.resolve()
            if resolved not in roots:
                roots.append(resolved)
    return tuple(roots)


@dataclass(frozen=True)
class GatewayConfig:
    project_root: Path
    runtime_root: Path
    neo_reference_root: Path
    neo_runtime_root: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8790
    worker_poll_seconds: float = 0.25
    worker_start_timeout_seconds: float = 20.0
    worker_http_timeout_seconds: float = 30.0
    max_concurrent_jobs: int = 1
    gpu_max_concurrent_jobs: int = 1
    gpu_vram_reserve_mb: int = 512
    gpu_probe_timeout_seconds: float = 2.0
    scheduler_wait_timeout_seconds: float = 120.0
    model_idle_unload_seconds: int = 300
    worker_max_restarts: int = 2
    worker_restart_window_seconds: float = 120.0
    worker_restart_backoff_seconds: float = 0.25
    allow_local_reference_paths: bool = True
    manifest_roots: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def outputs_root(self) -> Path:
        return self.runtime_root / "outputs"

    @property
    def envs_root(self) -> Path:
        return self.runtime_root / "envs"

    @property
    def models_root(self) -> Path:
        return self.runtime_root / "models"

    @property
    def cache_root(self) -> Path:
        return self.runtime_root / "cache"

    @property
    def temp_root(self) -> Path:
        return self.runtime_root / "temp"

    @property
    def logs_root(self) -> Path:
        return self.runtime_root / "logs"

    @property
    def state_root(self) -> Path:
        return self.runtime_root / "state"

    @property
    def legacy_backups_root(self) -> Path:
        return self.runtime_root / "legacy_backups"

    @property
    def effective_manifest_roots(self) -> tuple[Path, ...]:
        if self.manifest_roots:
            return tuple(Path(path).expanduser().resolve() for path in self.manifest_roots)
        return ((self.project_root / "neo_voice_engine" / "manifests").resolve(),)

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "GatewayConfig":
        root = (project_root or Path(os.getenv("NEO_VOICE_ENGINE_PROJECT_ROOT") or Path(__file__).resolve().parents[1])).resolve()
        runtime_paths = resolve_voice_runtime_paths(root)
        runtime_root = runtime_paths.voice_runtime_root
        reference_root = Path(
            os.getenv("NEO_VOICE_ENGINE_REFERENCE_ROOT")
            or (root / "neo_data" / "outputs" / "voice" / "reference")
        ).expanduser().resolve()
        host = str(os.getenv("NEO_VOICE_ENGINE_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        allow_local = _bool_env("NEO_VOICE_ENGINE_ALLOW_LOCAL_REFERENCE_PATHS", is_loopback_host(host))
        return cls(
            project_root=root,
            runtime_root=runtime_root,
            neo_runtime_root=runtime_paths.neo_runtime_root,
            neo_reference_root=reference_root,
            host=host,
            port=_int_env("NEO_VOICE_ENGINE_PORT", 8790),
            worker_poll_seconds=max(0.05, float(os.getenv("NEO_VOICE_ENGINE_WORKER_POLL_SECONDS") or 0.25)),
            worker_start_timeout_seconds=max(1.0, float(os.getenv("NEO_VOICE_ENGINE_WORKER_START_TIMEOUT_SECONDS") or 20.0)),
            worker_http_timeout_seconds=max(1.0, float(os.getenv("NEO_VOICE_ENGINE_WORKER_HTTP_TIMEOUT_SECONDS") or 30.0)),
            max_concurrent_jobs=_int_env("NEO_VOICE_ENGINE_MAX_CONCURRENT_JOBS", 1),
            gpu_max_concurrent_jobs=_int_env("NEO_VOICE_ENGINE_GPU_MAX_CONCURRENT_JOBS", 1),
            gpu_vram_reserve_mb=_int_env("NEO_VOICE_ENGINE_GPU_VRAM_RESERVE_MB", 512, minimum=0),
            gpu_probe_timeout_seconds=max(0.2, float(os.getenv("NEO_VOICE_ENGINE_GPU_PROBE_TIMEOUT_SECONDS") or 2.0)),
            scheduler_wait_timeout_seconds=max(1.0, float(os.getenv("NEO_VOICE_ENGINE_SCHEDULER_WAIT_TIMEOUT_SECONDS") or 120.0)),
            model_idle_unload_seconds=_int_env("NEO_VOICE_ENGINE_MODEL_IDLE_UNLOAD_SECONDS", 300, minimum=0),
            worker_max_restarts=_int_env("NEO_VOICE_ENGINE_WORKER_MAX_RESTARTS", 2, minimum=0),
            worker_restart_window_seconds=max(1.0, float(os.getenv("NEO_VOICE_ENGINE_WORKER_RESTART_WINDOW_SECONDS") or 120.0)),
            worker_restart_backoff_seconds=max(0.0, float(os.getenv("NEO_VOICE_ENGINE_WORKER_RESTART_BACKOFF_SECONDS") or 0.25)),
            allow_local_reference_paths=allow_local,
            manifest_roots=_manifest_roots_from_env(root),
        )

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.runtime_root,
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

    def runtime_paths_payload(self) -> dict[str, str]:
        neo_root = (self.neo_runtime_root or self.runtime_root.parent).resolve()
        return {
            "neo_runtime_root": str(neo_root),
            "voice_runtime_root": str(self.runtime_root.resolve()),
            "envs_root": str(self.envs_root.resolve()),
            "models_root": str(self.models_root.resolve()),
            "cache_root": str(self.cache_root.resolve()),
            "temp_root": str(self.temp_root.resolve()),
            "logs_root": str(self.logs_root.resolve()),
            "state_root": str(self.state_root.resolve()),
            "outputs_root": str(self.outputs_root.resolve()),
        }
