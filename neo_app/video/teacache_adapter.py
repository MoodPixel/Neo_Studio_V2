from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

PHASE: Final[str] = "V-G12"
SCHEMA_VERSION: Final[str] = "neo.video.teacache_adapter.vg13_10"

TEACACHE_NODE_ALIASES: Final[tuple[str, ...]] = (
    "TeaCache",
    "ApplyTeaCache",
    "TeaCachePatch",
    "TeaCacheModelPatch",
    "TeaCachePatchModel",
    "ApplyTeaCachePatch",
    "WanVideoTeaCacheKJ",
    "WanVideoTeaCache",
    "WanVideoApplyTeaCache",
    "TeaCacheForWanVideo",
    "TeaCacheWanVideo",
    "LTXVTeaCache",
    "LTXVApplyTeaCache",
)
MODEL_FIELDS: Final[tuple[str, ...]] = ("model", "model_in", "diffusion_model")
THRESHOLD_FIELDS: Final[tuple[str, ...]] = (
    "rel_l1_thresh",
    "rel_l1_threshold",
    "threshold",
    "cache_threshold",
    "teacache_threshold",
    "thresh",
)
START_FIELDS: Final[tuple[str, ...]] = ("start_percent", "start", "start_at", "start_step_percent")
END_FIELDS: Final[tuple[str, ...]] = ("end_percent", "end", "end_at", "end_step_percent")
MODE_FIELDS: Final[tuple[str, ...]] = ("mode", "cache_mode", "profile", "teacache_profile")
TARGETS: Final[set[str]] = {"both", "high", "low", "high_noise", "low_noise"}

PROFILE_PRESETS: Final[dict[str, dict[str, Any]]] = {
    "conservative": {
        "label": "Conservative",
        "threshold": 0.10,
        "start_percent": 0.10,
        "end_percent": 0.90,
        "notes": "Safest first TeaCache test; smallest visual risk.",
    },
    "balanced": {
        "label": "Balanced",
        "threshold": 0.20,
        "start_percent": 0.08,
        "end_percent": 0.92,
        "notes": "Useful preview speed boost after conservative mode works.",
    },
    "aggressive": {
        "label": "Aggressive",
        "threshold": 0.30,
        "start_percent": 0.05,
        "end_percent": 0.95,
        "notes": "Fastest experimental mode; use only after base and conservative runs are stable.",
    },
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_teacache_profile(value: str | None) -> str:
    key = str(value or "conservative").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "safe": "conservative",
        "low": "conservative",
        "default": "conservative",
        "normal": "balanced",
        "medium": "balanced",
        "fast": "aggressive",
        "high": "aggressive",
    }
    key = aliases.get(key, key)
    return key if key in PROFILE_PRESETS else "conservative"


def _normalize_target(value: str | None) -> str:
    key = str(value or "both").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in TARGETS:
        return "both"
    return "high" if key == "high_noise" else "low" if key == "low_noise" else key


def _input_groups(object_info: dict[str, Any], class_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    optional = inputs.get("optional", {}) if isinstance(inputs, dict) else {}
    return (required if isinstance(required, dict) else {}, optional if isinstance(optional, dict) else {})


def _node_output_types(object_info: dict[str, Any], class_type: str) -> tuple[str, ...]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    if not isinstance(entry, dict):
        return tuple()
    raw = entry.get("output") or entry.get("outputs") or entry.get("return_types") or entry.get("return")
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip().upper() for item in raw if str(item).strip())
    if isinstance(raw, str):
        return (raw.strip().upper(),) if raw.strip() else tuple()
    return tuple()


def _node_has_model_input(object_info: dict[str, Any], class_type: str) -> bool:
    required, optional = _input_groups(object_info, class_type)
    known = {str(key).casefold() for key in {**required, **optional}.keys()}
    return any(candidate.casefold() in known for candidate in MODEL_FIELDS)


def _node_returns_cacheargs_only(object_info: dict[str, Any], class_type: str) -> bool:
    outputs = set(_node_output_types(object_info, class_type))
    return bool(outputs and "CACHEARGS" in outputs and "MODEL" not in outputs)


def _node_model_patch_compatible(object_info: dict[str, Any], class_type: str) -> bool:
    outputs = set(_node_output_types(object_info, class_type))
    if outputs:
        return "MODEL" in outputs
    # Older/fake object_info snapshots in tests may omit output metadata. In that
    # case, only treat the node as a model patch if it exposes a model input.
    return _node_has_model_input(object_info, class_type)


def _combo_values(object_info: dict[str, Any], class_type: str, input_name: str) -> list[str]:
    required, optional = _input_groups(object_info, class_type)
    for group in (required, optional):
        spec = group.get(input_name)
        if isinstance(spec, list) and spec:
            values = spec[0]
            if isinstance(values, list):
                return [str(item) for item in values]
    return []


def _first_default(spec: Any) -> Any:
    if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]:
        return spec[1]["default"]
    if isinstance(spec, list) and spec and isinstance(spec[0], list) and spec[0]:
        return spec[0][0]
    return None


def _find_node_class(object_info: dict[str, Any]) -> str:
    if not isinstance(object_info, dict) or not object_info:
        return "TeaCache"

    candidates: list[str] = []
    folded = {str(key).casefold(): str(key) for key in object_info.keys()}
    for alias in TEACACHE_NODE_ALIASES:
        exact = folded.get(alias.casefold())
        if exact and exact not in candidates:
            candidates.append(exact)

    normalized = {str(key).casefold().replace(" ", "").replace("_", ""): str(key) for key in object_info.keys()}
    for alias in TEACACHE_NODE_ALIASES:
        hit = normalized.get(alias.casefold().replace(" ", "").replace("_", ""))
        if hit and hit not in candidates:
            candidates.append(hit)

    # Last-resort fuzzy: many custom nodes append provider names around TeaCache.
    for key in object_info.keys():
        node = str(key)
        if "teacache" in node.casefold().replace("_", "") and node not in candidates:
            candidates.append(node)

    for candidate in candidates:
        if _node_model_patch_compatible(object_info, candidate):
            return candidate
    return candidates[0] if candidates else ""


def _find_input_field(object_info: dict[str, Any], class_type: str, candidates: tuple[str, ...], fallback: str) -> str:
    required, optional = _input_groups(object_info, class_type)
    known = {**required, **optional}
    folded = {str(key).casefold(): str(key) for key in known.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


def _select_combo_value(values: list[str], needles: tuple[str, ...], fallback: str) -> str:
    if not values:
        return fallback
    lowered = [(value, value.casefold()) for value in values]
    for needle in needles:
        hit = next((value for value, low in lowered if needle.casefold() in low), None)
        if hit:
            return hit
    return values[0]


def build_teacache_node_inputs(
    object_info: dict[str, Any],
    class_type: str,
    *,
    model_link: list[Any],
    profile: str = "conservative",
    model_field: str = "model",
) -> dict[str, Any]:
    """Build a TeaCache node payload across common Comfy custom-node schemas.

    TeaCache node packs are inconsistent, so this only writes fields exposed by /object_info,
    except the core model link. Unknown required fields are filled only when Comfy reports defaults.
    """
    normalized_profile = normalize_teacache_profile(profile)
    preset = PROFILE_PRESETS[normalized_profile]
    required, optional = _input_groups(object_info, class_type)
    known = {**required, **optional}
    inputs: dict[str, Any] = {model_field: model_link}

    threshold_field = _find_input_field(object_info, class_type, THRESHOLD_FIELDS, "")
    if threshold_field in known:
        inputs[threshold_field] = float(preset["threshold"])
    start_field = _find_input_field(object_info, class_type, START_FIELDS, "")
    if start_field in known:
        inputs[start_field] = float(preset["start_percent"])
    end_field = _find_input_field(object_info, class_type, END_FIELDS, "")
    if end_field in known:
        inputs[end_field] = float(preset["end_percent"])

    mode_field = _find_input_field(object_info, class_type, MODE_FIELDS, "")
    if mode_field in known:
        values = _combo_values(object_info, class_type, mode_field)
        inputs[mode_field] = _select_combo_value(values, (normalized_profile, "wan", "video"), normalized_profile)

    for name in ("model_type", "model_family", "backend"):
        if name in known:
            values = _combo_values(object_info, class_type, name)
            inputs[name] = _select_combo_value(values, ("wan", "wan2", "video", "default", "auto"), "wan")
    for name in ("cache_device", "device", "offload_device"):
        if name in known:
            values = _combo_values(object_info, class_type, name)
            inputs[name] = _select_combo_value(values, ("cuda", "default", "auto"), "default")
    for name in ("enabled", "enable", "use_teacache"):
        if name in known:
            inputs[name] = True

    for name, spec in required.items():
        if name in inputs:
            continue
        default = _first_default(spec)
        if default is not None:
            inputs[name] = default
    return inputs


@dataclass(frozen=True)
class TeaCacheBranch:
    role: str
    node_id: str
    source_model_link: list[Any]
    output_model_link: list[Any]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TeaCacheAdapterPlan:
    enabled: bool
    class_type: str
    model_field: str
    profile: str
    target: str
    preset: dict[str, Any]
    branches: tuple[TeaCacheBranch, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def queue_ready(self) -> bool:
        return (not self.enabled) or not self.errors

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "phase": PHASE,
            "enabled": self.enabled,
            "class_type": self.class_type,
            "model_field": self.model_field,
            "profile": self.profile,
            "target": self.target,
            "preset": dict(self.preset),
            "compatibility": {
                "model_patch_required": True,
                "selected_class_type": self.class_type,
                "note": "WAN GGUF KSamplerAdvanced route can only use TeaCache nodes that return MODEL. CACHEARGS TeaCache nodes are for WanVideoWrapper sampler routes."
            },
            "branches": [branch.payload() for branch in self.branches],
            "graph_mutations": [
                {
                    "type": "insert_model_cache_patch",
                    "adapter": "teacache",
                    "role": branch.role,
                    "node_id": branch.node_id,
                    "source_model_link": branch.source_model_link,
                    "output_model_link": branch.output_model_link,
                    "profile": self.profile,
                }
                for branch in self.branches
            ],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "queue_ready": self.queue_ready,
            "rules": [
                "TeaCache is optional and is inserted after GGUF/Video-LoRA/Sage model patching, before ModelSamplingSD3.",
                "WAN dual-noise routes need separate TeaCache nodes for high-noise and low-noise model branches when target=both.",
                "Start with conservative mode; raise to balanced/aggressive only after base generation is stable.",
            ],
        }


def build_wan22_teacache_plan(
    *,
    object_info: dict[str, Any] | None,
    enable_teacache: bool = False,
    teacache_profile: str | None = "conservative",
    teacache_target: str | None = "both",
    high_model_link: list[Any] | None = None,
    low_model_link: list[Any] | None = None,
) -> TeaCacheAdapterPlan:
    info = object_info or {}
    enabled = _as_bool(enable_teacache)
    profile = normalize_teacache_profile(teacache_profile)
    target = _normalize_target(teacache_target)
    class_type = _find_node_class(info)
    model_field = _find_input_field(info, class_type, MODEL_FIELDS, "model") if class_type else "model"
    warnings: list[str] = []
    errors: list[str] = []
    branches: list[TeaCacheBranch] = []

    if not enabled:
        return TeaCacheAdapterPlan(False, class_type or "", model_field, profile, target, PROFILE_PRESETS[profile], tuple(), tuple(), tuple())

    if not class_type or (info and class_type not in info):
        errors.append("TeaCache is enabled, but no TeaCache node is visible in ComfyUI /object_info.")
    if not info:
        warnings.append("ComfyUI /object_info was unavailable; Neo will compile TeaCache with a generic node schema, but runtime preflight should verify the node before queueing.")
    if class_type and info and _node_returns_cacheargs_only(info, class_type):
        outputs = ", ".join(_node_output_types(info, class_type)) or "unknown"
        errors.append(
            f"TeaCache node '{class_type}' returns {outputs}, not MODEL. This node belongs to the WanVideoWrapper CACHEARGS sampler flow and cannot be linked into the current ModelSamplingSD3/KSamplerAdvanced WAN GGUF route. Disable TeaCache for this route, or install/select a MODEL-returning TeaCache patch node."
        )
    elif class_type and info and not _node_model_patch_compatible(info, class_type):
        outputs = ", ".join(_node_output_types(info, class_type)) or "unknown"
        errors.append(
            f"TeaCache node '{class_type}' is not compatible with the current WAN GGUF route because it does not expose a MODEL output. Detected output: {outputs}."
        )
    if profile == "aggressive":
        warnings.append("TeaCache aggressive mode can affect motion/detail stability; test conservative or balanced first.")

    if not errors:
        high_link = list(high_model_link or ["129:95", 0])
        low_link = list(low_model_link or ["129:96", 0])
        if target in {"both", "high"}:
            branches.append(TeaCacheBranch("high_noise", "9201", high_link, ["9201", 0]))
        if target in {"both", "low"}:
            branches.append(TeaCacheBranch("low_noise", "9202", low_link, ["9202", 0]))
        if not branches:
            errors.append("TeaCache target did not resolve to any WAN model branch.")

    return TeaCacheAdapterPlan(
        True,
        class_type or "TeaCache",
        model_field,
        profile,
        target,
        PROFILE_PRESETS[profile],
        tuple(branches),
        tuple(dict.fromkeys(warnings)),
        tuple(dict.fromkeys(errors)),
    )
