from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

PHASE: Final[str] = "V-G13"
SCHEMA_VERSION: Final[str] = "neo.video.low_vram_adapter.vg13"

WAN_BLOCK_SWAP_NODE_ALIASES: Final[tuple[str, ...]] = (
    "WanVideoBlockSwap",
    "WanVideoBlockSwapAdvanced",
    "WanVideoBlockSwapKJ",
    "WanBlockSwap",
    "WanVideoBlockSwapNode",
)
LTX_LOW_VRAM_NODE_ALIASES: Final[tuple[str, ...]] = (
    "LTXVModelLoaderLowVRAM",
    "LTXVModelLoaderAdvanced",
    "LTXVModelLoaderKJ",
    "LTXVModelLoader",
    "LTXVideoModelLoaderLowVRAM",
)
VAE_TILED_NODE_ALIASES: Final[tuple[str, ...]] = (
    "VAEDecodeTiled",
    "VAEDecodeTiledKJ",
    "TiledVAEDecode",
)
VAE_OFFLOAD_NODE_ALIASES: Final[tuple[str, ...]] = (
    "VAELoaderKJ",
    "VAELoaderGGUF",
    "VAELoader",
)
MODEL_FIELDS: Final[tuple[str, ...]] = ("model", "diffusion_model", "wan_model")
BLOCK_SWAP_FIELDS: Final[tuple[str, ...]] = ("blocks_to_swap", "num_blocks", "swap_blocks", "blocks", "block_count")
OFFLOAD_BOOL_FIELDS: Final[tuple[str, ...]] = (
    "offload_txt_emb",
    "offload_text_emb",
    "offload_text_encoder",
    "offload_img_emb",
    "offload_image_emb",
    "offload_image_encoder",
    "cpu_offload",
    "offload_to_cpu",
    "enable_offload",
)
DEVICE_FIELDS: Final[tuple[str, ...]] = ("device", "vae_device", "offload_device", "decode_device")
TARGETS: Final[set[str]] = {"both", "high", "low", "high_noise", "low_noise"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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


def _find_node_class(object_info: dict[str, Any], aliases: tuple[str, ...], fallback: str = "") -> str:
    if not isinstance(object_info, dict) or not object_info:
        return fallback
    folded = {str(key).casefold(): str(key) for key in object_info.keys()}
    for alias in aliases:
        exact = folded.get(alias.casefold())
        if exact:
            return exact
    normalized = {str(key).casefold().replace(" ", "").replace("_", ""): str(key) for key in object_info.keys()}
    for alias in aliases:
        hit = normalized.get(alias.casefold().replace(" ", "").replace("_", ""))
        if hit:
            return hit
    needles = tuple(alias.casefold().replace(" ", "").replace("_", "") for alias in aliases)
    return next((str(key) for key in object_info.keys() if any(needle in str(key).casefold().replace("_", "") for needle in needles)), "")


def _find_input_field(object_info: dict[str, Any], class_type: str, candidates: tuple[str, ...], fallback: str = "") -> str:
    required, optional = _input_groups(object_info, class_type)
    known = {**required, **optional}
    folded = {str(key).casefold(): str(key) for key in known.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


def _known_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    required, optional = _input_groups(object_info, class_type)
    return {**required, **optional}


def _first_default(spec: Any) -> Any:
    if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]:
        return spec[1]["default"]
    if isinstance(spec, list) and spec and isinstance(spec[0], list) and spec[0]:
        return spec[0][0]
    return None


def _combo_values(object_info: dict[str, Any], class_type: str, input_name: str) -> list[str]:
    required, optional = _input_groups(object_info, class_type)
    for group in (required, optional):
        spec = group.get(input_name)
        if isinstance(spec, list) and spec:
            values = spec[0]
            if isinstance(values, list):
                return [str(item) for item in values]
    return []


def _select_combo(values: list[str], needles: tuple[str, ...], fallback: str) -> str:
    if not values:
        return fallback
    lowered = [(value, value.casefold()) for value in values]
    for needle in needles:
        hit = next((value for value, low in lowered if needle.casefold() in low), None)
        if hit:
            return hit
    return values[0]


def _int_value(value: Any, fallback: int, minimum: int = 0, maximum: int = 99) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(parsed, maximum))


def build_wan_block_swap_node_inputs(
    object_info: dict[str, Any],
    class_type: str,
    *,
    model_link: list[Any],
    model_field: str = "model",
    blocks_to_swap: int = 12,
    cpu_offload: bool = True,
) -> dict[str, Any]:
    known = _known_inputs(object_info, class_type)
    inputs: dict[str, Any] = {model_field: model_link}
    block_field = _find_input_field(object_info, class_type, BLOCK_SWAP_FIELDS, "")
    if block_field in known:
        inputs[block_field] = _int_value(blocks_to_swap, 12, 0, 99)
    for field in OFFLOAD_BOOL_FIELDS:
        if field in known:
            inputs[field] = bool(cpu_offload)
    for name in ("device", "offload_device"):
        if name in known:
            values = _combo_values(object_info, class_type, name)
            inputs[name] = _select_combo(values, ("cpu", "auto", "default"), "cpu")
    for name, spec in _input_groups(object_info, class_type)[0].items():
        if name in inputs:
            continue
        default = _first_default(spec)
        if default is not None:
            inputs[name] = default
    return inputs


def build_vae_decode_inputs(
    object_info: dict[str, Any],
    class_type: str,
    *,
    samples_link: list[Any],
    vae_link: list[Any],
    tile_size: int = 384,
    temporal_tile_size: int = 4096,
) -> dict[str, Any]:
    known = _known_inputs(object_info, class_type)
    inputs: dict[str, Any] = {"samples": samples_link, "vae": vae_link}
    for field in ("tile_size", "tile_x", "tile_width", "tile"):  # common tiled decode names
        if field in known:
            inputs[field] = _int_value(tile_size, 384, 64, 4096)
    for field in ("tile_y", "tile_height"):
        if field in known:
            inputs[field] = _int_value(tile_size, 384, 64, 4096)
    for field in ("temporal_tile_size", "tile_t", "temporal_size"):
        if field in known:
            inputs[field] = _int_value(temporal_tile_size, 4096, 1, 999999)
    for field in ("overlap", "tile_overlap"):
        if field in known:
            inputs[field] = 64
    for field in DEVICE_FIELDS:
        if field in known:
            values = _combo_values(object_info, class_type, field)
            inputs[field] = _select_combo(values, ("cpu", "auto", "default"), "cpu")
    for name, spec in _input_groups(object_info, class_type)[0].items():
        if name in inputs:
            continue
        default = _first_default(spec)
        if default is not None:
            inputs[name] = default
    return inputs


@dataclass(frozen=True)
class LowVramBranch:
    role: str
    node_id: str
    source_model_link: list[Any]
    output_model_link: list[Any]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LowVramAdapterPlan:
    enabled: bool
    cpu_offload_enabled: bool
    block_swap_enabled: bool
    vae_offload_enabled: bool
    class_type: str
    model_field: str
    block_swap_target: str
    blocks_to_swap: int
    branches: tuple[LowVramBranch, ...]
    vae_decode_class_type: str
    vae_decode_tiled: bool
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
            "cpu_offload_enabled": self.cpu_offload_enabled,
            "block_swap_enabled": self.block_swap_enabled,
            "vae_offload_enabled": self.vae_offload_enabled,
            "class_type": self.class_type,
            "model_field": self.model_field,
            "block_swap_target": self.block_swap_target,
            "blocks_to_swap": self.blocks_to_swap,
            "branches": [branch.payload() for branch in self.branches],
            "vae_decode": {"class_type": self.vae_decode_class_type, "tiled": self.vae_decode_tiled},
            "graph_mutations": [
                {
                    "type": "insert_low_vram_model_patch",
                    "adapter": "wan_block_swap_cpu_offload",
                    "role": branch.role,
                    "node_id": branch.node_id,
                    "source_model_link": branch.source_model_link,
                    "output_model_link": branch.output_model_link,
                    "blocks_to_swap": self.blocks_to_swap,
                }
                for branch in self.branches
            ],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "queue_ready": self.queue_ready,
            "rules": [
                "Low-VRAM model patching is optional and inserted after LoRA/Sage/TeaCache, before ModelSamplingSD3.",
                "WAN dual-noise routes use separate block-swap/offload nodes per selected high/low branch.",
                "VAE offload prefers tiled decode and offload-aware VAE fields where ComfyUI exposes them.",
            ],
        }


def build_wan22_low_vram_plan(
    *,
    object_info: dict[str, Any] | None,
    enable_cpu_offload: bool = False,
    enable_vae_offload: bool = False,
    enable_block_swap: bool = False,
    block_swap_target: str | None = "both",
    block_swap_blocks: int | None = None,
    high_model_link: list[Any] | None = None,
    low_model_link: list[Any] | None = None,
) -> LowVramAdapterPlan:
    info = object_info or {}
    cpu_enabled = _as_bool(enable_cpu_offload)
    block_enabled = _as_bool(enable_block_swap)
    vae_enabled = _as_bool(enable_vae_offload)
    enabled = cpu_enabled or block_enabled or vae_enabled
    target = _normalize_target(block_swap_target)
    class_type = _find_node_class(info, WAN_BLOCK_SWAP_NODE_ALIASES, "WanVideoBlockSwap")
    model_field = _find_input_field(info, class_type, MODEL_FIELDS, "model") if class_type else "model"
    decode_class = _find_node_class(info, VAE_TILED_NODE_ALIASES, "VAEDecodeTiled")
    blocks = _int_value(block_swap_blocks, 12, 0, 99)
    warnings: list[str] = []
    errors: list[str] = []
    branches: list[LowVramBranch] = []

    if not enabled:
        return LowVramAdapterPlan(False, False, False, False, class_type if info else "", model_field, target, blocks, tuple(), decode_class if info else "", False, tuple(), tuple())

    if (cpu_enabled or block_enabled) and (not class_type or (info and class_type not in info)):
        errors.append("CPU offload/block swap is enabled, but no WAN block-swap/offload model patch node is visible in ComfyUI /object_info.")
    if (cpu_enabled or block_enabled) and class_type and info and model_field not in _known_inputs(info, class_type):
        errors.append(f"Detected WAN offload node {class_type}, but it does not expose a MODEL input that Neo can patch safely.")
    if vae_enabled and info and not decode_class:
        warnings.append("VAE offload/tiled decode was requested, but no tiled VAE decode node was detected; Neo will keep the route decoder unchanged.")
    if vae_enabled:
        warnings.append("VAE offload/tiled decode improves stability but can be slower; use it for memory pressure, not speed.")
    if cpu_enabled or block_enabled:
        warnings.append("Block swap / CPU offload reduces VRAM pressure but usually increases generation time.")

    high_link = list(high_model_link or ["129:95", 0])
    low_link = list(low_model_link or ["129:96", 0])
    if cpu_enabled or block_enabled:
        if target in {"both", "high"}:
            branches.append(LowVramBranch("high_noise", "9301", high_link, ["9301", 0]))
        if target in {"both", "low"}:
            branches.append(LowVramBranch("low_noise", "9302", low_link, ["9302", 0]))
        if not branches:
            errors.append("Block swap target did not resolve to any WAN model branch.")

    return LowVramAdapterPlan(
        enabled,
        cpu_enabled,
        block_enabled,
        vae_enabled,
        class_type or "WanVideoBlockSwap",
        model_field,
        target,
        blocks,
        tuple(branches),
        decode_class or "VAEDecodeTiled",
        bool(vae_enabled and decode_class),
        tuple(dict.fromkeys(warnings)),
        tuple(dict.fromkeys(errors)),
    )


def low_vram_probe_payload(object_info: dict[str, Any] | None, *, family: str | None = None, values: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    data = values or {}
    fam = str(family or data.get("family") or "").strip().lower()
    wan_node = _find_node_class(info, WAN_BLOCK_SWAP_NODE_ALIASES, "")
    ltx_node = _find_node_class(info, LTX_LOW_VRAM_NODE_ALIASES, "")
    vae_tiled = _find_node_class(info, VAE_TILED_NODE_ALIASES, "")
    vae_loader = _find_node_class(info, VAE_OFFLOAD_NODE_ALIASES, "")
    enable_cpu = _as_bool(data.get("enable_cpu_offload"))
    enable_vae = _as_bool(data.get("enable_vae_offload"))
    enable_block = _as_bool(data.get("enable_block_swap"))
    errors: list[str] = []
    warnings: list[str] = []
    action_items: list[str] = []

    if fam.startswith("wan") and (enable_cpu or enable_block) and not wan_node:
        errors.append("WAN CPU offload/block swap is enabled, but no WAN block-swap node was detected.")
        action_items.append("Install/enable a WAN video block-swap/offload node pack, restart ComfyUI, then refresh backend probe.")
    if fam.startswith("ltx") and (enable_cpu or enable_block) and not ltx_node:
        errors.append("LTX low-VRAM loader/offload is enabled, but no LTX low-VRAM loader node was detected.")
        action_items.append("Install/enable LTX low-VRAM loader nodes, restart ComfyUI, then refresh backend probe.")
    if enable_vae and not (vae_tiled or vae_loader):
        warnings.append("VAE offload was requested, but no tiled/offload-aware VAE node was detected.")
    if enable_cpu or enable_block:
        warnings.append("Offload/block-swap is a stability path and may be noticeably slower than keeping the model fully on GPU.")

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "family": fam,
        "nodes": {
            "wan_block_swap": {"available": bool(wan_node), "class_type": wan_node, "expected_any": list(WAN_BLOCK_SWAP_NODE_ALIASES)},
            "ltx_low_vram_loader": {"available": bool(ltx_node), "class_type": ltx_node, "expected_any": list(LTX_LOW_VRAM_NODE_ALIASES)},
            "vae_tiled_decode": {"available": bool(vae_tiled), "class_type": vae_tiled, "expected_any": list(VAE_TILED_NODE_ALIASES)},
            "vae_offload_loader": {"available": bool(vae_loader), "class_type": vae_loader, "expected_any": list(VAE_OFFLOAD_NODE_ALIASES)},
        },
        "selected": {
            "enable_cpu_offload": enable_cpu,
            "enable_vae_offload": enable_vae,
            "enable_block_swap": enable_block,
            "block_swap_target": str(data.get("block_swap_target") or "both"),
            "block_swap_blocks": _int_value(data.get("block_swap_blocks"), 12, 0, 99),
        },
        "queue_ready": not errors,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": list(dict.fromkeys(errors)),
        "action_items": list(dict.fromkeys(action_items)),
    }
