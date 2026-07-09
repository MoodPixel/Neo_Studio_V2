from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import os
import re
import urllib.parse
import urllib.request

from neo_app.memory.service import get_memory_service
from neo_app.tool_ledger import record_tool_ledger_event

INTERNET_SCHEMA_VERSION = "neo.internet_access.v1"
INTERNET_RUNTIME_VERSION = "0.1.0"
ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "neo_data" / "admin" / "internet_access.json"

ALLOWED_MODES = ["disabled", "ask_every_time", "research_only", "allowed_apis_only", "advanced"]
ALLOWED_PROVIDER_TYPES = ["search_api", "web_fetch", "openai_compatible", "image_api", "video_api", "voice_api", "custom_api"]

_DEFAULT_CONFIG: dict[str, Any] = {
    "schema_id": INTERNET_SCHEMA_VERSION,
    "runtime_version": INTERNET_RUNTIME_VERSION,
    "mode": "disabled",
    "default_requires_confirmation": True,
    "allow_raw_web_fetch": False,
    "allow_search_api": False,
    "allow_openai_compatible": False,
    "memory_writeback": True,
    "max_response_chars": 6000,
    "providers": [],
    "notes": "Optional internet/API access is disabled by default and must be enabled from Admin. API keys should come from environment variables, not stored directly in config.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_config_dir() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _safe_read_config() -> dict[str, Any]:
    config = dict(_DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update(loaded)
        except Exception:
            config["config_error"] = "Unable to read internet access config. Defaults are active."
    config["mode"] = config.get("mode") if config.get("mode") in ALLOWED_MODES else "disabled"
    providers = config.get("providers") if isinstance(config.get("providers"), list) else []
    config["providers"] = [_normalize_provider(item) for item in providers if isinstance(item, dict)]
    return config


def _write_config(config: dict[str, Any]) -> None:
    _ensure_config_dir()
    clean = dict(config)
    clean["schema_id"] = INTERNET_SCHEMA_VERSION
    clean["runtime_version"] = INTERNET_RUNTIME_VERSION
    clean["updated_at"] = _now()
    clean["providers"] = [_normalize_provider(item) for item in clean.get("providers") or [] if isinstance(item, dict)]
    CONFIG_PATH.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_provider(provider: dict[str, Any]) -> dict[str, Any]:
    provider_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(provider.get("provider_id") or provider.get("id") or provider.get("name") or f"provider_{uuid4().hex[:8]}")).strip("_").lower()
    provider_type = str(provider.get("provider_type") or provider.get("type") or "custom_api")
    if provider_type not in ALLOWED_PROVIDER_TYPES:
        provider_type = "custom_api"
    api_key_env = str(provider.get("api_key_env") or "").strip()
    endpoint = str(provider.get("endpoint") or "").strip()
    return {
        "provider_id": provider_id,
        "label": str(provider.get("label") or provider_id.replace("_", " ").title()),
        "provider_type": provider_type,
        "enabled": bool(provider.get("enabled", False)),
        "endpoint": endpoint,
        "api_key_env": api_key_env,
        "api_key_configured": bool(api_key_env and os.environ.get(api_key_env)),
        "requires_confirmation": bool(provider.get("requires_confirmation", True)),
        "allowed_use": str(provider.get("allowed_use") or "research"),
        "notes": str(provider.get("notes") or ""),
    }


def _capabilities(config: dict[str, Any]) -> dict[str, Any]:
    mode = str(config.get("mode") or "disabled")
    enabled_providers = [item for item in config.get("providers") or [] if item.get("enabled")]
    return {
        "internet_enabled": mode != "disabled",
        "mode": mode,
        "raw_web_fetch_allowed": bool(config.get("allow_raw_web_fetch") and mode in {"research_only", "advanced"}),
        "search_api_allowed": bool(config.get("allow_search_api") and mode in {"research_only", "allowed_apis_only", "advanced"}),
        "openai_compatible_allowed": bool(config.get("allow_openai_compatible") and mode in {"allowed_apis_only", "advanced"}),
        "provider_count": len(config.get("providers") or []),
        "enabled_provider_count": len(enabled_providers),
        "configured_provider_count": sum(1 for item in enabled_providers if item.get("api_key_configured") or item.get("provider_type") == "web_fetch"),
    }


def internet_access_status_payload() -> dict[str, Any]:
    config = _safe_read_config()
    caps = _capabilities(config)
    return {
        "schema_id": "neo.internet_access.status.v1",
        "status": "disabled" if config.get("mode") == "disabled" else "ready_for_permission",
        "label": "Optional Internet/API Access",
        "runtime_version": INTERNET_RUNTIME_VERSION,
        "mode": config.get("mode"),
        "config_path": str(CONFIG_PATH.relative_to(ROOT_DIR)),
        "permission_policy": {
            "default": "disabled" if config.get("mode") == "disabled" else ("confirmation_required" if config.get("default_requires_confirmation", True) else "allowed_by_policy"),
            "internet": config.get("mode"),
            "raw_web_fetch": "confirmation_required" if caps.get("raw_web_fetch_allowed") else "disabled",
            "search_api": "confirmation_required" if caps.get("search_api_allowed") else "disabled",
            "openai_compatible": "confirmation_required" if caps.get("openai_compatible_allowed") else "disabled",
        },
        "capabilities": caps,
        "providers": config.get("providers") or [],
        "allowed_modes": ALLOWED_MODES,
        "allowed_provider_types": ALLOWED_PROVIDER_TYPES,
        "safety": "Internet/API access is optional, disabled by default, permission-gated, and records external context as lower-trust memory with source metadata.",
    }


def update_internet_access_policy_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    config = _safe_read_config()
    if "mode" in data:
        mode = str(data.get("mode") or "disabled")
        if mode not in ALLOWED_MODES:
            mode = "disabled"
        config["mode"] = mode
    for key in ["default_requires_confirmation", "allow_raw_web_fetch", "allow_search_api", "allow_openai_compatible", "memory_writeback"]:
        if key in data:
            config[key] = bool(data.get(key))
    if "max_response_chars" in data:
        try:
            config["max_response_chars"] = max(1000, min(50000, int(data.get("max_response_chars") or 6000)))
        except Exception:
            config["max_response_chars"] = 6000
    if isinstance(data.get("providers"), list):
        config["providers"] = [_normalize_provider(item) for item in data.get("providers") if isinstance(item, dict)]
    _write_config(config)
    return {"ok": True, "schema_id": "neo.internet_access.update.v1", "status": internet_access_status_payload()}


def _normalize_query(payload: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(payload.get("query") or payload.get("command") or payload.get("text") or "").strip())


def _select_provider(config: dict[str, Any], provider_type: str | None = None, provider_id: str | None = None) -> dict[str, Any] | None:
    providers = [item for item in config.get("providers") or [] if item.get("enabled")]
    if provider_id:
        for item in providers:
            if item.get("provider_id") == provider_id:
                return item
    if provider_type:
        for item in providers:
            if item.get("provider_type") == provider_type:
                return item
    return providers[0] if providers else None


def plan_internet_access_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    config = _safe_read_config()
    caps = _capabilities(config)
    query = _normalize_query(data)
    requested_type = str(data.get("provider_type") or "search_api")
    provider = _select_provider(config, requested_type, data.get("provider_id"))
    mode = str(config.get("mode") or "disabled")
    actions: list[dict[str, Any]] = []
    allowed = mode != "disabled"
    reason = "ready"
    if not query:
        allowed = False
        reason = "empty_query"
    elif mode == "disabled":
        reason = "internet_access_disabled"
    elif requested_type == "web_fetch" and not caps.get("raw_web_fetch_allowed"):
        allowed = False
        reason = "raw_web_fetch_not_allowed"
    elif requested_type == "search_api" and not caps.get("search_api_allowed"):
        allowed = False
        reason = "search_api_not_allowed"
    elif requested_type == "openai_compatible" and not caps.get("openai_compatible_allowed"):
        allowed = False
        reason = "openai_compatible_not_allowed"
    elif requested_type != "web_fetch" and not provider:
        allowed = False
        reason = "no_enabled_provider"
    elif provider and provider.get("api_key_env") and not provider.get("api_key_configured"):
        allowed = False
        reason = "provider_api_key_missing"
    requires_confirmation = bool(config.get("default_requires_confirmation", True) or (provider or {}).get("requires_confirmation", True))
    actions.append({
        "action_id": f"net_act_{uuid4().hex[:12]}",
        "action_type": "internet_research",
        "label": f"Use {requested_type.replace('_', ' ')} for external context",
        "status": "planned" if allowed else "blocked",
        "reason": reason,
        "risk_level": "medium",
        "requires_confirmation": requires_confirmation,
        "payload": {"query": query, "provider_type": requested_type, "provider_id": (provider or {}).get("provider_id")},
    })
    return {
        "ok": True,
        "schema_id": "neo.internet_access.plan.v1",
        "status": "planned" if allowed else "blocked",
        "query": query,
        "mode": mode,
        "provider": provider,
        "capabilities": caps,
        "actions": actions,
        "permission_summary": {
            "allowed": allowed,
            "reason": reason,
            "confirmation_required": bool(allowed and requires_confirmation),
        },
    }


def _safe_fetch_url(url: str, max_chars: int) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return {"ok": False, "status": "blocked", "reason": "unsupported_url_scheme"}
    req = urllib.request.Request(url, headers={"User-Agent": "NeoStudio/1.0 local optional research"})
    with urllib.request.urlopen(req, timeout=12) as response:  # nosec - optional user-enabled local research helper
        raw = response.read(max_chars + 1)
        content_type = response.headers.get("content-type", "")
    text = raw.decode("utf-8", errors="replace")[:max_chars]
    return {"ok": True, "status": "completed", "url": url, "content_type": content_type, "text": text, "truncated": len(raw) > max_chars}


def _run_external_context(config: dict[str, Any], plan: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    action = (plan.get("actions") or [{}])[0]
    action_payload = action.get("payload") or {}
    provider_type = str(action_payload.get("provider_type") or payload.get("provider_type") or "search_api")
    query = str(action_payload.get("query") or plan.get("query") or "")
    max_chars = int(config.get("max_response_chars") or 6000)
    if provider_type == "web_fetch":
        url = str(payload.get("url") or query)
        return _safe_fetch_url(url, max_chars)
    provider = plan.get("provider") or {}
    if provider_type == "search_api":
        endpoint = provider.get("endpoint") or ""
        if not endpoint:
            return {"ok": False, "status": "blocked", "reason": "search_provider_endpoint_missing"}
        # Generic endpoint support: append ?q= when no placeholder exists.
        if "{query}" in endpoint:
            url = endpoint.replace("{query}", urllib.parse.quote(query))
        else:
            sep = "&" if "?" in endpoint else "?"
            url = f"{endpoint}{sep}q={urllib.parse.quote(query)}"
        return _safe_fetch_url(url, max_chars)
    return {"ok": False, "status": "blocked", "reason": f"provider_type_{provider_type}_not_implemented_yet"}


def run_internet_access_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    config = _safe_read_config()
    plan = plan_internet_access_payload(data)
    execute_confirmed = bool(data.get("execute_confirmed") or data.get("confirm"))
    if plan.get("status") == "blocked":
        result = {"ok": False, "schema_id": "neo.internet_access.run.v1", "status": "blocked", "plan": plan, "reason": (plan.get("permission_summary") or {}).get("reason")}
        _record_external_event(config, plan, result)
        _record_external_ledger(plan, result, execute_confirmed=execute_confirmed)
        return result
    action = (plan.get("actions") or [{}])[0]
    if action.get("requires_confirmation") and not execute_confirmed:
        result = {"ok": False, "schema_id": "neo.internet_access.run.v1", "status": "blocked", "plan": plan, "reason": "confirmation_required"}
        _record_external_event(config, plan, result)
        _record_external_ledger(plan, result, execute_confirmed=execute_confirmed)
        return result
    try:
        external = _run_external_context(config, plan, data)
    except Exception as exc:
        external = {"ok": False, "status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    result = {
        "ok": bool(external.get("ok")),
        "schema_id": "neo.internet_access.run.v1",
        "status": external.get("status") or ("completed" if external.get("ok") else "blocked"),
        "plan": plan,
        "external_context": external,
        "trust_level": "external",
        "memory_state": "active",
        "expires_policy": "external context can become stale; re-check before relying on time-sensitive facts.",
    }
    _record_external_event(config, plan, result)
    _record_external_ledger(plan, result, execute_confirmed=execute_confirmed)
    return result


def _record_external_ledger(plan: dict[str, Any], result: dict[str, Any], *, execute_confirmed: bool = False) -> None:
    try:
        action = (plan.get("actions") or [{}])[0]
        record_tool_ledger_event({
            "actor": "internet",
            "surface": "assistant",
            "intent": "internet_research",
            "status": result.get("status") or "unknown",
            "blocked": result.get("status") == "blocked",
            "confirmed": execute_confirmed,
            "action": action,
            "payload": {"query": plan.get("query"), "mode": plan.get("mode")},
            "result_summary": str(result.get("reason") or result.get("status") or "Internet/API access event")[:900],
            "metadata": {"phase": "25", "source": "internet.access.run"},
        })
    except Exception:
        return


def _record_external_event(config: dict[str, Any], plan: dict[str, Any], result: dict[str, Any]) -> None:
    if not config.get("memory_writeback", True):
        return
    try:
        query = str(plan.get("query") or "")
        external = result.get("external_context") or {}
        summary = external.get("text") or result.get("reason") or result.get("status") or "Internet/API access event"
        get_memory_service().record_event({
            "namespace": "internet",
            "surface": "assistant",
            "source": "internet_access",
            "event_type": "internet.access.ran",
            "title": "Internet/API access event",
            "summary": str(summary)[:900],
            "tags": ["internet", str(plan.get("mode") or "disabled"), str(result.get("status") or "unknown")],
            "payload": {"query": query, "plan": plan, "result_status": result.get("status"), "external_context": {k: v for k, v in external.items() if k != "text"}},
            "importance": "low",
            "trust_level": "external",
            "memory_state": "active",
            "should_embed": False,
        })
    except Exception:
        return
