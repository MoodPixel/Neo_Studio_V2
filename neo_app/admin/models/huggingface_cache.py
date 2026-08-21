from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import os
import re

HF_CACHE_SCHEMA_ID = "neo.admin.models.huggingface.cache.v1"
HF_PROVIDER = "huggingface"
LEGACY_HUB_CACHE_ENV = "HUGGINGFACE_HUB_CACHE"


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _expand_percent_vars(value: str, env: Mapping[str, str]) -> str:
    """Expand Windows-style %NAME% variables even when tests run off Windows."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(env.get(key, match.group(0)))

    return re.sub(r"%([^%]+)%", replace, value)


def _expand_path(value: str, *, env: Mapping[str, str], home_dir: str) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = _expand_percent_vars(text, env)
    # Expand $NAME/${NAME} against the supplied environment instead of relying
    # on process-global os.environ. This keeps the resolver deterministic in
    # tests and future subprocess planning.
    variable_pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")

    def replace_dollar(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        return str(env.get(key, match.group(0)))

    text = variable_pattern.sub(replace_dollar, text)
    if text == "~":
        text = home_dir
    elif text.startswith(("~/", "~\\")):
        text = str(Path(home_dir) / text[2:])
    return os.path.normpath(text)


def _default_home(env: Mapping[str, str], home_dir: str) -> tuple[str, str]:
    xdg = _clean(env.get("XDG_CACHE_HOME"))
    if xdg:
        expanded = _expand_path(xdg, env=env, home_dir=home_dir)
        return os.path.join(expanded, "huggingface"), "XDG_CACHE_HOME"
    return os.path.join(home_dir, ".cache", "huggingface"), "platform_default"


def _library_snapshot() -> dict[str, Any]:
    """Return optional huggingface_hub diagnostics without making it required."""

    try:
        import huggingface_hub  # type: ignore
        from huggingface_hub import constants  # type: ignore
    except Exception as exc:
        return {
            "available": False,
            "version": "",
            "hf_home": "",
            "hub_cache": "",
            "error": type(exc).__name__,
        }
    return {
        "available": True,
        "version": str(getattr(huggingface_hub, "__version__", "") or ""),
        "hf_home": str(getattr(constants, "HF_HOME", "") or ""),
        "hub_cache": str(getattr(constants, "HF_HUB_CACHE", "") or ""),
        "legacy_hub_cache": str(getattr(constants, "HUGGINGFACE_HUB_CACHE", "") or ""),
        "error": "",
    }


def resolve_huggingface_cache(
    *,
    env: Mapping[str, str] | None = None,
    home_dir: str | os.PathLike[str] | None = None,
    include_library_snapshot: bool = True,
) -> dict[str, Any]:
    """Resolve the effective Hugging Face Hub cache without creating it.

    Resolution mirrors huggingface_hub's current public environment contract:

    1. HF_HUB_CACHE
    2. HUGGINGFACE_HUB_CACHE (legacy compatibility)
    3. HF_HOME + /hub
    4. XDG_CACHE_HOME + /huggingface/hub
    5. ~/.cache/huggingface/hub

    The function is intentionally read-only. It reports local machine state but
    never writes the resolved path into Neo's public model manifest or local
    model-path settings file.
    """

    runtime_env: Mapping[str, str] = os.environ if env is None else env
    resolved_home_dir = _clean(home_dir) if home_dir is not None else str(Path.home())
    if not resolved_home_dir:
        resolved_home_dir = str(Path.home())
    resolved_home_dir = _expand_path(resolved_home_dir, env=runtime_env, home_dir=str(Path.home()))

    explicit_hub = _clean(runtime_env.get("HF_HUB_CACHE"))
    legacy_hub = _clean(runtime_env.get(LEGACY_HUB_CACHE_ENV))
    explicit_home = _clean(runtime_env.get("HF_HOME"))

    if explicit_home:
        hf_home = _expand_path(explicit_home, env=runtime_env, home_dir=resolved_home_dir)
        hf_home_source = "HF_HOME"
    else:
        hf_home, hf_home_source = _default_home(runtime_env, resolved_home_dir)
        hf_home = _expand_path(hf_home, env=runtime_env, home_dir=resolved_home_dir)

    if explicit_hub:
        hub_cache = _expand_path(explicit_hub, env=runtime_env, home_dir=resolved_home_dir)
        source = "HF_HUB_CACHE"
        source_kind = "explicit"
    elif legacy_hub:
        hub_cache = _expand_path(legacy_hub, env=runtime_env, home_dir=resolved_home_dir)
        source = LEGACY_HUB_CACHE_ENV
        source_kind = "legacy_explicit"
    else:
        hub_cache = os.path.normpath(os.path.join(hf_home, "hub"))
        source = hf_home_source
        source_kind = "derived"

    hub_path = Path(hub_cache)
    hf_home_path = Path(hf_home)
    library = _library_snapshot() if include_library_snapshot else {
        "available": False,
        "version": "",
        "hf_home": "",
        "hub_cache": "",
        "legacy_hub_cache": "",
        "error": "not_requested",
    }

    library_hub = os.path.normcase(os.path.normpath(_clean(library.get("hub_cache")))) if _clean(library.get("hub_cache")) else ""
    resolved_hub = os.path.normcase(os.path.normpath(hub_cache)) if hub_cache else ""
    library_matches = bool(library_hub and resolved_hub and library_hub == resolved_hub)

    return {
        "schema_id": HF_CACHE_SCHEMA_ID,
        "status": "ready",
        "provider": HF_PROVIDER,
        "hub_cache": str(hub_path),
        "hf_home": str(hf_home_path),
        "source": source,
        "source_kind": source_kind,
        "exists": hub_path.exists(),
        "hf_home_exists": hf_home_path.exists(),
        "legacy_env_used": source == LEGACY_HUB_CACHE_ENV,
        "environment": {
            "hf_hub_cache_set": bool(explicit_hub),
            "legacy_huggingface_hub_cache_set": bool(legacy_hub),
            "hf_home_set": bool(explicit_home),
            "xdg_cache_home_set": bool(_clean(runtime_env.get("XDG_CACHE_HOME"))),
        },
        "library": {
            **library,
            "matches_resolved_hub_cache": library_matches,
        },
        "resolution_order": [
            "HF_HUB_CACHE",
            LEGACY_HUB_CACHE_ENV,
            "HF_HOME",
            "XDG_CACHE_HOME",
            "platform_default",
        ],
        "policy": {
            "read_only": True,
            "creates_directories": False,
            "persist_to_model_catalog": False,
            "persist_to_model_paths": False,
            "installer_may_create_cache_later": True,
        },
    }


def admin_huggingface_cache_payload() -> dict[str, Any]:
    return resolve_huggingface_cache()
