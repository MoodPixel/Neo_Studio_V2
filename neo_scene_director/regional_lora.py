"""IMG-SD2 regional LoRA MODEL isolation wrapper for Neo's lightweight Scene Director.

Krea 2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo use family-specific
spatial module policies. Standard LoRA A/B activation deltas are injected only
into proven image-token lanes and multiplied by the owning Scene Director region
mask. The base ModelPatcher is cloned; weights and CLIP are never globally
patched by this node. Unknown/padded token layouts fail closed.
"""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import math
import re
from typing import Any

import torch

try:  # Comfy runtime imports are optional for Neo-side metadata/tests.
    import folder_paths  # type: ignore
except Exception:  # pragma: no cover
    folder_paths = None

try:  # pragma: no cover - exercised in live Comfy rather than unit tests.
    import comfy.lora as comfy_lora  # type: ignore
    import comfy.model_base as comfy_model_base  # type: ignore
    import comfy.patcher_extension as comfy_patcher_extension  # type: ignore
    import comfy.utils as comfy_utils  # type: ignore
except Exception:  # pragma: no cover
    comfy_lora = None
    comfy_model_base = None
    comfy_patcher_extension = None
    comfy_utils = None

NODE_CLASS = "NeoRegionalLoRADelta"
WRAPPER_KEY = "neo_scene_director_regional_lora_delta_img_sd2"
RUNTIME_ATTACHMENT_KEY = "neo_scene_director_regional_lora_runtime"
SUPPORTED_FAMILIES = {"krea2", "krea2_turbo", "flux2_klein", "z_image", "z_image_turbo"}


def _norm_family(value: Any) -> str:
    family = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "krea_2": "krea2",
        "krea2_raw": "krea2",
        "krea_2_raw": "krea2",
        "krea_2_turbo": "krea2_turbo",
        "krea2turbo": "krea2_turbo",
        "flux_2_klein": "flux2_klein",
        "flux2klein": "flux2_klein",
        "klein": "flux2_klein",
        "zimage": "z_image",
        "z_image_base": "z_image",
        "zimage_base": "z_image",
        "zimage_turbo": "z_image_turbo",
    }.get(family, family)


def _norm_key(value: Any) -> str:
    """Loose fallback signature used only when Comfy's canonical key map misses."""
    text = str(value or "").strip().lower()
    for prefix in (
        "lora_unet_",
        "lora_",
        "diffusion_model.",
        "diffusion_model_",
        "transformer.",
        "model.diffusion_model.",
        "model.",
        "base_model.model.",
    ):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.replace(".", "").replace("_", "")


def _route_bbox_norm(route: dict[str, Any]) -> tuple[float, float, float, float]:
    bbox = route.get("bbox") if isinstance(route.get("bbox"), dict) else {}
    x = float(bbox.get("x", route.get("x", 0.0)) or 0.0)
    y = float(bbox.get("y", route.get("y", 0.0)) or 0.0)
    w = float(bbox.get("w", bbox.get("width", route.get("w", route.get("width", 1.0)))) or 1.0)
    h = float(bbox.get("h", bbox.get("height", route.get("h", route.get("height", 1.0)))) or 1.0)
    if max(abs(x), abs(y), abs(w), abs(h)) > 1.0:
        canvas = route.get("canvas") if isinstance(route.get("canvas"), dict) else {}
        cw = max(1.0, float(canvas.get("width", route.get("canvas_width", 1.0)) or 1.0))
        ch = max(1.0, float(canvas.get("height", route.get("canvas_height", 1.0)) or 1.0))
        x, w = x / cw, w / cw
        y, h = y / ch, h / ch
    x0 = max(0.0, min(1.0, x))
    y0 = max(0.0, min(1.0, y))
    x1 = max(x0, min(1.0, x + w))
    y1 = max(y0, min(1.0, y + h))
    return x0, y0, x1, y1


def _parse_routes(routes_json: str | list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(routes_json, str):
        try:
            raw = json.loads(routes_json or "[]")
        except Exception as exc:
            raise ValueError(f"NeoRegionalLoRADelta routes_json is invalid JSON: {exc}") from exc
    else:
        raw = deepcopy(routes_json)
    if isinstance(raw, dict):
        raw = raw.get("routes") if isinstance(raw.get("routes"), list) else [raw]
    if not isinstance(raw, list):
        raise ValueError("NeoRegionalLoRADelta routes_json must contain a JSON array.")
    routes: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        lora_name = str(item.get("lora_name") or item.get("name") or "").strip()
        if not lora_name:
            continue
        try:
            strength = float(item.get("strength", item.get("strength_model", 1.0)) or 1.0)
        except Exception:
            strength = 1.0
        routes.append({
            **deepcopy(item),
            "route_index": index,
            "region_id": str(item.get("region_id") or f"scene_region_{index}"),
            "lora_name": lora_name,
            "strength": max(-4.0, min(4.0, strength)),
            "bbox_norm": _route_bbox_norm(item),
        })
    return routes


def _load_lora_state(lora_name: str) -> dict[str, torch.Tensor]:
    if folder_paths is None:
        raise RuntimeError("NeoRegionalLoRADelta requires ComfyUI folder_paths at runtime.")
    path = None
    if hasattr(folder_paths, "get_full_path_or_raise"):
        try:
            path = folder_paths.get_full_path_or_raise("loras", lora_name)
        except Exception:
            path = None
    if not path and hasattr(folder_paths, "get_full_path"):
        path = folder_paths.get_full_path("loras", lora_name)
    if not path:
        raise FileNotFoundError(f"Regional LoRA file was not found in ComfyUI's loras search paths: {lora_name}")
    if comfy_utils is not None and hasattr(comfy_utils, "load_torch_file"):
        state = comfy_utils.load_torch_file(path, safe_load=True)
    else:  # pragma: no cover - Comfy normally supplies comfy.utils.
        import safetensors.torch
        state = safetensors.torch.load_file(path, device="cpu")
    if not isinstance(state, dict):
        raise ValueError(f"Regional LoRA file did not load as a state dict: {lora_name}")
    return state


def parse_standard_lora_pairs(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse standard LoRA A/B or down/up pairs into CPU fp32 matrices.

    SD-28.4 intentionally does not advertise LoKr/Tucker support. Unknown formats
    fail closed instead of silently becoming a global LoRA patch.
    """
    groups: dict[str, dict[str, torch.Tensor]] = {}
    alphas: dict[str, float] = {}
    for key, value in state.items():
        if not torch.is_tensor(value):
            continue
        key_s = str(key)
        alpha_match = re.match(r"^(.*?)(?:\.alpha|\.lora_alpha)$", key_s)
        if alpha_match:
            try:
                alphas[alpha_match.group(1)] = float(value.flatten()[0].item())
            except Exception:
                pass
            continue
        down_match = re.match(r"^(.*?)\.(?:lora_down|lora_A)(?:\.default)?\.weight$", key_s)
        if down_match:
            groups.setdefault(down_match.group(1), {})["down"] = value.detach().cpu()
            continue
        up_match = re.match(r"^(.*?)\.(?:lora_up|lora_B)(?:\.default)?\.weight$", key_s)
        if up_match:
            groups.setdefault(up_match.group(1), {})["up"] = value.detach().cpu()
            continue
    out: dict[str, dict[str, Any]] = {}
    for base, pair in groups.items():
        down = pair.get("down")
        up = pair.get("up")
        if down is None or up is None or down.ndim != 2 or up.ndim != 2:
            continue
        rank = int(down.shape[0])
        if rank <= 0 or int(up.shape[1]) != rank:
            continue
        alpha = float(alphas.get(base, rank))
        out[base] = {
            "down": down,
            "up": up,
            "rank": rank,
            "alpha": alpha,
            "base_scale": alpha / float(rank),
        }
    return out


def _comfy_key_map(base_model: Any) -> dict[str, Any]:
    if comfy_lora is None or not hasattr(comfy_lora, "model_lora_keys_unet"):
        return {}
    try:
        return dict(comfy_lora.model_lora_keys_unet(base_model, {}))
    except Exception as exc:  # fail-safe fallback to normalized matching.
        logging.warning("[NeoRegionalLoRADelta] Comfy LoRA key-map build failed: %s", exc)
        return {}


def _weight_key_mapping(weight_key: Any) -> tuple[str | None, tuple[int, int] | None]:
    """Resolve Comfy's LoRA key target into a live module plus optional output slice.

    Comfy maps Flux single-block ``linear1_qkv`` LoRAs to a tuple containing the
    real linear1 weight and a narrow() offset. Activation-delta execution must
    preserve that slice instead of pretending the LoRA targets the entire output.
    """
    output_slice: tuple[int, int] | None = None
    raw = weight_key
    if isinstance(raw, tuple):
        if not raw:
            return None, None
        weight_key = raw[0]
        offset = raw[1] if len(raw) > 1 else None
        if isinstance(offset, (list, tuple)) and len(offset) >= 3:
            try:
                dim, start, length = int(offset[0]), int(offset[1]), int(offset[2])
            except Exception:
                dim = start = length = -1
            # Linear weight dim 0 maps to the activation output's last feature dim.
            if dim == 0 and start >= 0 and length > 0:
                output_slice = (start, length)
            elif dim >= 0:
                return None, None
    if not isinstance(weight_key, str):
        return None, None
    key = weight_key
    if key.startswith("diffusion_model."):
        key = key[len("diffusion_model."):]
    if key.endswith(".weight"):
        key = key[:-len(".weight")]
    return (key or None), output_slice


def _weight_key_to_module_name(weight_key: Any) -> str | None:
    return _weight_key_mapping(weight_key)[0]

def krea2_spatial_module_scope(module_name: Any) -> str | None:
    """Return the only Krea2 module scopes that can be spatially masked safely.

    Krea2 concatenates text and image tokens only inside ``blocks`` and ``last``;
    ``first`` is image-token-only. Text fusion, text MLP, timestep MLP/projection,
    and other conditioning-only paths are intentionally excluded from regional
    LoRA execution because they do not have a trustworthy spatial token lane.
    """
    name = str(module_name or "").strip()
    if name == "first" or name.startswith("first."):
        return "image_only"
    if name.startswith("blocks."):
        return "combined_text_image"
    if name == "last.linear" or name.startswith("last.linear."):
        return "combined_text_image"
    return None



def krea2_isolation_exclusion_reason(module_name: Any) -> str | None:
    """Return strict-isolation exclusions for Krea2 LoRA targets.

    Krea2 uses single-stream self-attention over concatenated text + image tokens.
    A regional LoRA delta written into attention K/V projections for one image
    region can be consumed by queries from other image regions in the same block.
    IMG-SD2 therefore suppresses regional LoRA writes to ``wk`` and ``wv`` while
    retaining local-query/output/MLP/image-in/final projection deltas.
    """
    name = str(module_name or "").strip()
    if re.match(r"^blocks\.\d+\.attn\.(?:wk|wv)(?:\.|$)", name):
        return "cross_region_attention_key_value_write_suppressed"
    return None


def flux2_klein_spatial_module_scope(module_name: Any) -> str | None:
    """Return only FLUX.2 Klein linear paths with a provable spatial token lane.

    Double-stream image branches are image-only. Single-stream linear1/linear2
    run after text+image concatenation and therefore use the combined mask. Text,
    timestep/vector/guidance modulation, normalization and AdaLN paths are
    deliberately excluded.
    """
    name = str(module_name or "").strip()
    if name == "img_in" or name.startswith("img_in."):
        return "image_only"
    if name.startswith("double_blocks."):
        if ".img_attn.qkv" in name or ".img_attn.proj" in name or ".img_mlp." in name:
            return "image_only"
        return None
    if name.startswith("single_blocks.") and (name.endswith(".linear1") or name.endswith(".linear2")):
        return "combined_text_image"
    if name == "final_layer.linear" or name.startswith("final_layer.linear."):
        return "image_only"
    return None



def z_image_spatial_module_scope(module_name: Any) -> str | None:
    """Return only Z-Image NextDiT linear paths with a proven spatial lane.

    ``x_embedder`` receives unpadded image patch tokens. ``noise_refiner`` runs
    only on the (possibly padded) image-token sequence. ``layers`` and
    ``final_layer.linear`` run on padded caption + image tokens, so those paths
    use a combined mask whose caption and padding positions are always zero.
    Context-refiner, caption projection, timestep/AdaLN and normalization paths
    are deliberately excluded.
    """
    name = str(module_name or "").strip()
    if name == "x_embedder" or name.startswith("x_embedder."):
        return "image_unpadded"
    if name.startswith("noise_refiner."):
        if ".attention.qkv" in name or ".attention.out" in name:
            return "image_only"
        if any(name.endswith(f".feed_forward.{leaf}") for leaf in ("w1", "w2", "w3")):
            return "image_only"
        return None
    if name.startswith("layers."):
        if ".attention.qkv" in name or ".attention.out" in name:
            return "combined_text_image"
        if any(name.endswith(f".feed_forward.{leaf}") for leaf in ("w1", "w2", "w3")):
            return "combined_text_image"
        return None
    if name == "final_layer.linear" or name.startswith("final_layer.linear."):
        return "combined_text_image"
    return None

def _linear_dimensions(module: Any) -> tuple[int | None, int | None]:
    """Resolve linear input/output dimensions without requiring a torch weight.

    This keeps the activation-delta adapter compatible with quantized/GGUF
    operation wrappers whose live ``weight`` may not be a normal torch Tensor.
    """
    try:
        in_features = int(getattr(module, "in_features"))
        out_features = int(getattr(module, "out_features"))
        if in_features > 0 and out_features > 0:
            return in_features, out_features
    except Exception:
        pass
    weight = getattr(module, "weight", None)
    shape = getattr(weight, "shape", None)
    try:
        if shape is not None and len(shape) == 2:
            return int(shape[1]), int(shape[0])
    except Exception:
        pass
    return None, None


def resolve_lora_pairs_to_modules(
    pairs: dict[str, dict[str, Any]],
    *,
    base_model: Any,
    diffusion_model: Any,
    spatial_scope_resolver: Any = krea2_spatial_module_scope,
    isolation_exclusion_resolver: Any = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Resolve LoRA pair bases to live diffusion-model modules, fail-closed."""
    key_map = _comfy_key_map(base_model)
    named_modules = {name: module for name, module in diffusion_model.named_modules()}
    normalized_live: dict[str, list[str]] = {}
    for name in named_modules:
        normalized_live.setdefault(_norm_key(name), []).append(name)

    resolved: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    ambiguous: list[str] = []
    sliced: list[str] = []
    isolation_excluded: list[dict[str, str]] = []
    for base, data in pairs.items():
        candidates = [
            base,
            base.removeprefix("base_model.model."),
            base.removeprefix("model."),
            base.removeprefix("diffusion_model."),
        ]
        module_name = None
        output_slice: tuple[int, int] | None = None
        mapped_ambiguous = False
        for candidate in candidates:
            if candidate not in key_map:
                continue
            mapped_name, mapped_slice = _weight_key_mapping(key_map[candidate])
            if not mapped_name:
                continue
            if mapped_name in named_modules:
                module_name = mapped_name
                output_slice = mapped_slice
                break
            mapped_matches = normalized_live.get(_norm_key(mapped_name), [])
            if len(mapped_matches) == 1:
                module_name = mapped_matches[0]
                output_slice = mapped_slice
                break
            if len(mapped_matches) > 1:
                mapped_ambiguous = True
        if not module_name:
            sig = _norm_key(base)
            matches = normalized_live.get(sig, [])
            if len(matches) == 1:
                module_name = matches[0]
            elif len(matches) > 1 or mapped_ambiguous:
                ambiguous.append(base)
                continue
        if not module_name or module_name not in named_modules:
            unresolved.append(base)
            continue
        exclusion_reason = isolation_exclusion_resolver(module_name) if callable(isolation_exclusion_resolver) else None
        if exclusion_reason:
            isolation_excluded.append({
                "source_base": str(base),
                "module_name": str(module_name),
                "reason": str(exclusion_reason),
            })
            continue
        module = named_modules[module_name]
        scope = spatial_scope_resolver(module_name) if callable(spatial_scope_resolver) else None
        if scope is None:
            unresolved.append(base)
            continue
        in_features, out_features = _linear_dimensions(module)
        if in_features is None or out_features is None:
            unresolved.append(base)
            continue
        down = data["down"]
        up = data["up"]
        expected_out = int(out_features)
        if output_slice is not None:
            start_i, length_i = output_slice
            if start_i < 0 or length_i <= 0 or start_i + length_i > expected_out:
                unresolved.append(base)
                continue
            expected_out = int(length_i)
        if int(down.shape[1]) != int(in_features) or int(up.shape[0]) != expected_out:
            unresolved.append(base)
            continue
        record_key = module_name if module_name not in resolved else f"{module_name}::{base}"
        resolved[record_key] = {
            **data,
            "module": module,
            "module_name": module_name,
            "source_base": base,
            "spatial_scope": scope,
            "output_slice": output_slice,
            "module_out_features": int(out_features),
        }
        if output_slice is not None:
            sliced.append(base)
    return resolved, {
        "pair_count": len(pairs),
        "resolved_count": len(resolved),
        "unresolved": unresolved,
        "ambiguous": ambiguous,
        "sliced_targets": sliced,
        "isolation_excluded": isolation_excluded,
        "isolation_excluded_count": len(isolation_excluded),
        "spatial_scope_policy": getattr(spatial_scope_resolver, "__name__", "custom_spatial_scope"),
        "isolation_exclusion_policy": getattr(isolation_exclusion_resolver, "__name__", "none") if callable(isolation_exclusion_resolver) else "none",
    }

def rect_token_mask(
    rows: int,
    cols: int,
    bbox_norm: tuple[float, float, float, float],
    feather: float,
    *,
    device: torch.device | str = "cpu",
    dtype: torch.dtype = torch.float32,
) -> torch.Tensor:
    """Build a soft rectangular mask on the Krea image-token grid."""
    x0, y0, x1, y1 = bbox_norm
    c0, c1 = x0 * cols, x1 * cols
    r0, r1 = y0 * rows, y1 * rows
    cc = torch.arange(cols, device=device, dtype=torch.float32).unsqueeze(0) + 0.5
    rr = torch.arange(rows, device=device, dtype=torch.float32).unsqueeze(1) + 0.5
    if feather <= 0:
        mask = ((cc >= c0) & (cc < c1) & (rr >= r0) & (rr < r1)).float()
    else:
        fc = max(1e-3, float(feather) * cols)
        fr = max(1e-3, float(feather) * rows)
        in_x = torch.sigmoid((cc - c0) / fc) * torch.sigmoid((c1 - cc) / fc)
        in_y = torch.sigmoid((rr - r0) / fr) * torch.sigmoid((r1 - rr) / fr)
        mask = (in_y * in_x).clamp(0.0, 1.0)
    return mask.reshape(-1).to(dtype=dtype)


def full_sequence_mask_fail_closed(
    image_mask: torch.Tensor,
    *,
    seq_len: int,
    ndim: int,
    text_len: int | None,
) -> torch.Tensor:
    """Place an image-region mask into a token sequence without touching text.

    Unknown layouts return all zeros. This deliberately differs from older
    regional implementations that filled an unknown sequence with mask.mean().
    SD-28.4 would rather disable a LoRA delta than leak it into text/other tokens.
    """
    seq_len = int(seq_len)
    n_img = int(image_mask.numel())
    base = torch.zeros(seq_len, device=image_mask.device, dtype=image_mask.dtype)
    if n_img <= 0 or seq_len <= 0:
        return base.view(*([1] * max(0, ndim - 2)), seq_len, 1)
    if seq_len == n_img:
        base[:] = image_mask
    elif text_len is not None and int(text_len) == seq_len:
        pass  # known text-only lane
    elif text_len is not None and 0 <= int(text_len) and int(text_len) + n_img <= seq_len:
        start = int(text_len)
        base[start:start + n_img] = image_mask
    # Unknown/padded layouts with no trustworthy text span remain all-zero.
    return base.view(*([1] * max(0, ndim - 2)), seq_len, 1)


def sequence_mask_for_scope(
    image_mask: torch.Tensor,
    *,
    seq_len: int,
    ndim: int,
    text_len: int | None,
    scope: str,
) -> torch.Tensor:
    if scope == "image_only":
        if int(seq_len) != int(image_mask.numel()):
            return torch.zeros(
                *([1] * max(0, ndim - 2)), int(seq_len), 1,
                device=image_mask.device,
                dtype=image_mask.dtype,
            )
        return image_mask.view(*([1] * max(0, ndim - 2)), int(seq_len), 1)
    if scope == "combined_text_image":
        if text_len is None:
            return torch.zeros(
                *([1] * max(0, ndim - 2)), int(seq_len), 1,
                device=image_mask.device,
                dtype=image_mask.dtype,
            )
        return full_sequence_mask_fail_closed(
            image_mask,
            seq_len=int(seq_len),
            ndim=int(ndim),
            text_len=int(text_len),
        )
    return torch.zeros(
        *([1] * max(0, ndim - 2)), int(seq_len), 1,
        device=image_mask.device,
        dtype=image_mask.dtype,
    )


class _Krea2RegionalSession:
    def __init__(
        self,
        patcher: Any,
        region_entries: list[dict[str, Any]],
        *,
        seam_feather: float,
        runtime_proof: dict[str, Any],
    ) -> None:
        self.patcher = patcher
        self.region_entries = region_entries
        self.seam_feather = max(0.0, min(0.5, float(seam_feather)))
        self.runtime_proof = runtime_proof
        self.runtime_proof.setdefault("loader_supported", True)
        self.runtime_proof.setdefault("spatial_scope_filter_active", False)
        self.runtime_proof.setdefault("token_mask_scope_proven", False)
        self.layer_map: dict[str, tuple[Any, list[tuple[int, dict[str, Any]]]]] | None = None
        self.text_len: int | None = None
        self.image_masks: list[torch.Tensor] = []
        self.grid_shape: tuple[int, int] | None = None
        self.prepared_signature: tuple[Any, ...] | None = None
        self.full_mask_cache: dict[tuple[Any, ...], torch.Tensor] = {}

    def _diffusion_model(self) -> Any:
        base = getattr(self.patcher, "model", None)
        return getattr(base, "diffusion_model", base)

    def _extract_text_len(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | None:
        context = None
        if len(args) >= 3 and torch.is_tensor(args[2]) and args[2].dim() == 3:
            context = args[2]
        elif torch.is_tensor(kwargs.get("context")) and kwargs["context"].dim() == 3:
            context = kwargs["context"]
        return int(context.shape[1]) if context is not None else None

    def _resolve_grid(self, x: Any) -> tuple[int, int] | None:
        if not torch.is_tensor(x) or x.dim() < 4:
            return None
        dm = self._diffusion_model()
        try:
            patch = max(1, int(getattr(dm, "patch", 2) or 2))
        except Exception:
            patch = 2
        height, width = int(x.shape[-2]), int(x.shape[-1])
        rows, cols = math.ceil(height / patch), math.ceil(width / patch)
        self.runtime_proof["krea2_patch_size"] = patch
        if rows <= 0 or cols <= 0:
            return None
        return rows, cols

    def _build_layer_map(self, dm: Any) -> dict[str, tuple[Any, list[tuple[int, dict[str, Any]]]]]:
        by_module: dict[str, tuple[Any, list[tuple[int, dict[str, Any]]]]] = {}
        named_modules = {name: module for name, module in dm.named_modules()}
        matched_per_region: list[int] = [0] * len(self.region_entries)
        for region_index, region in enumerate(self.region_entries):
            modules = region.get("modules") if isinstance(region.get("modules"), dict) else {}
            for module_key, data in modules.items():
                module_name = str(data.get("module_name") or str(module_key).split("::", 1)[0])
                module = named_modules.get(module_name)
                if module is None:
                    continue
                if module_name not in by_module:
                    by_module[module_name] = (module, [])
                by_module[module_name][1].append((region_index, data))
                matched_per_region[region_index] += 1
        self.runtime_proof["matched_layers_per_region"] = matched_per_region
        self.runtime_proof["matched_layer_count"] = sum(matched_per_region)
        self.runtime_proof["spatial_scope_filter_active"] = True
        self.runtime_proof["spatial_module_policy"] = "first=image_only; blocks/last.linear=combined_text_image; attention wk/wv excluded by strict isolation"
        self.runtime_proof["identity_isolation_profile"] = "krea2_strict_no_attention_kv_write"
        self.runtime_proof["cross_region_attention_kv_write_suppressed"] = True
        if any(count <= 0 for count in matched_per_region):
            raise RuntimeError(
                "NeoRegionalLoRADelta could not match at least one regional LoRA to active family model layers; "
                "generation stopped instead of applying a global/fallback LoRA."
            )
        return by_module

    def _prepare(self, x: Any) -> None:
        grid = self._resolve_grid(x)
        if grid is None:
            self.runtime_proof["region_mask_bound"] = False
            raise RuntimeError("NeoRegionalLoRADelta could not derive the active family image-token grid from the runtime latent.")
        rows, cols = grid
        device = x.device
        compute_dtype = x.dtype if x.dtype in {torch.float16, torch.bfloat16, torch.float32} else torch.bfloat16
        signature = (rows, cols, str(device), str(compute_dtype))
        if signature == self.prepared_signature:
            return
        self.image_masks = [
            rect_token_mask(
                rows,
                cols,
                region["bbox_norm"],
                max(0.0, min(0.5, float(region.get("seam_feather", self.seam_feather)))),
                device=device,
                dtype=compute_dtype,
            )
            for region in self.region_entries
        ]
        for region in self.region_entries:
            modules = region.get("modules") if isinstance(region.get("modules"), dict) else {}
            for data in modules.values():
                data["down_device"] = data["down"].to(device=device, dtype=compute_dtype)
                data["up_device"] = data["up"].to(device=device, dtype=compute_dtype) * float(data["scale"])
        self.grid_shape = grid
        self.prepared_signature = signature
        self.full_mask_cache.clear()
        self.runtime_proof["region_mask_bound"] = bool(self.image_masks)
        self.runtime_proof["runtime_grid"] = {"rows": rows, "cols": cols, "image_tokens": rows * cols}

    def _full_mask(self, region_index: int, seq_len: int, ndim: int, scope: str) -> torch.Tensor:
        key = (region_index, int(seq_len), int(ndim), self.text_len, scope)
        cached = self.full_mask_cache.get(key)
        if cached is not None:
            return cached
        result = sequence_mask_for_scope(
            self.image_masks[region_index],
            seq_len=int(seq_len),
            ndim=int(ndim),
            text_len=self.text_len,
            scope=scope,
        )
        self.full_mask_cache[key] = result
        return result

    def _make_hook(self, entries: list[tuple[int, dict[str, Any]]]):
        def hook(_module: Any, inputs: tuple[Any, ...], output: Any):
            if not torch.is_tensor(output) or output.dim() < 2 or not inputs:
                return output
            x = inputs[0]
            if not torch.is_tensor(x) or x.dim() < 2:
                return output
            seq_len = int(x.shape[-2])
            result = None
            for region_index, data in entries:
                down = data.get("down_device")
                up = data.get("up_device")
                if down is None or up is None:
                    continue
                xf = x.to(dtype=down.dtype)
                try:
                    delta = torch.nn.functional.linear(torch.nn.functional.linear(xf, down), up)
                except Exception:
                    continue
                output_slice = data.get("output_slice")
                if output_slice is not None:
                    try:
                        start_i, length_i = int(output_slice[0]), int(output_slice[1])
                    except Exception:
                        continue
                    if start_i < 0 or length_i <= 0 or start_i + length_i > int(output.shape[-1]) or int(delta.shape[-1]) != length_i:
                        continue
                    expanded = torch.zeros(
                        *delta.shape[:-1], int(output.shape[-1]),
                        device=delta.device, dtype=delta.dtype,
                    )
                    expanded[..., start_i:start_i + length_i] = delta
                    delta = expanded
                    self.runtime_proof.setdefault("sliced_delta_evaluations", 0)
                    self.runtime_proof["sliced_delta_evaluations"] += 1
                scope = str(data.get("spatial_scope") or "")
                mask = self._full_mask(region_index, seq_len, output.dim(), scope)
                if torch.count_nonzero(mask).item() == 0:
                    self.runtime_proof.setdefault("zero_mask_evaluations", 0)
                    self.runtime_proof["zero_mask_evaluations"] += 1
                    continue
                self.runtime_proof["token_mask_scope_proven"] = True
                masked = mask * delta
                result = masked if result is None else result + masked
                self.runtime_proof["delta_eval_attempted"] = True
                if not self.runtime_proof.get("delta_nonzero"):
                    try:
                        self.runtime_proof["delta_nonzero"] = bool(torch.any(masked != 0).item())
                    except Exception:
                        pass
            if result is None:
                return output
            return output + result.to(dtype=output.dtype)
        return hook

    def run(self, executor: Any, *args: Any, **kwargs: Any):
        dm = self._diffusion_model()
        if dm is None:
            raise RuntimeError("NeoRegionalLoRADelta could not access the active lightweight diffusion model.")
        if self.layer_map is None:
            self.layer_map = self._build_layer_map(dm)
        self.text_len = self._extract_text_len(args, kwargs)
        x = args[0] if args else kwargs.get("x")
        self._prepare(x)
        self.runtime_proof["masked_delta_hook_active"] = True
        handles = []
        try:
            for _name, (module, entries) in self.layer_map.items():
                handles.append(module.register_forward_hook(self._make_hook(entries)))
            return executor(*args, **kwargs)
        finally:
            for handle in handles:
                try:
                    handle.remove()
                except Exception:
                    pass
            self.runtime_proof["forward_hooks_removed"] = True
            self.runtime_proof["runtime_gpu_proven"] = bool(
                self.runtime_proof.get("lora_loaded")
                and self.runtime_proof.get("model_family_match")
                and self.runtime_proof.get("region_mask_bound")
                and self.runtime_proof.get("masked_delta_hook_active")
                and self.runtime_proof.get("delta_eval_attempted")
                and self.runtime_proof.get("delta_nonzero")
                and self.runtime_proof.get("spatial_scope_filter_active")
                and self.runtime_proof.get("loader_supported")
                and self.runtime_proof.get("token_mask_scope_proven")
                and self.runtime_proof.get("global_model_mutation") is False
                and int(self.runtime_proof.get("sampler_count") or 0) == 1
                and self.runtime_proof.get("forward_hooks_removed")
            )


def _require_krea2_model(model: Any, requested_family: str) -> Any:
    base_model = getattr(model, "model", None)
    if base_model is None:
        raise RuntimeError("NeoRegionalLoRADelta expected a Comfy ModelPatcher with a .model BaseModel.")
    if comfy_model_base is not None and hasattr(comfy_model_base, "Krea2"):
        if not isinstance(base_model, comfy_model_base.Krea2):
            raise RuntimeError(
                f"NeoRegionalLoRADelta family={requested_family} received a non-Krea2 Comfy model; "
                "regional LoRA execution was blocked."
            )
    elif base_model.__class__.__name__ != "Krea2":
        raise RuntimeError("NeoRegionalLoRADelta could not prove that the active model is Comfy Krea2.")
    return base_model


def build_krea2_region_entries(model: Any, routes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_model = getattr(model, "model", None)
    dm = getattr(base_model, "diffusion_model", None)
    if base_model is None or dm is None:
        raise RuntimeError("NeoRegionalLoRADelta could not access Krea2 BaseModel.diffusion_model.")
    file_cache: dict[str, dict[str, dict[str, Any]]] = {}
    entries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for route in routes:
        lora_name = route["lora_name"]
        if lora_name not in file_cache:
            pairs = parse_standard_lora_pairs(_load_lora_state(lora_name))
            if not pairs:
                raise RuntimeError(
                    f"Regional LoRA '{lora_name}' contains no supported standard A/B pairs. "
                    "SD-28.4 does not fall back to a global loader."
                )
            file_cache[lora_name] = pairs
        resolved, stats = resolve_lora_pairs_to_modules(
            file_cache[lora_name],
            base_model=base_model,
            diffusion_model=dm,
            spatial_scope_resolver=krea2_spatial_module_scope,
            isolation_exclusion_resolver=krea2_isolation_exclusion_reason,
        )
        if not resolved:
            raise RuntimeError(
                f"Regional LoRA '{lora_name}' matched zero Krea2 model layers; execution was blocked to prevent a false regional-support claim."
            )
        strength = float(route.get("strength", 1.0))
        scaled = {
            name: {**data, "scale": float(data["base_scale"]) * strength}
            for name, data in resolved.items()
        }
        entries.append({
            "region_id": route["region_id"],
            "lora_name": lora_name,
            "strength": strength,
            "bbox_norm": tuple(route["bbox_norm"]),
            "seam_feather": max(0.0, min(0.5, float(route.get("seam_feather", 0.0) or 0.0))),
            "modules": scaled,
        })
        diagnostics.append({
            "region_id": route["region_id"],
            "lora_name": lora_name,
            "strength": strength,
            **stats,
        })
    return entries, {"routes": diagnostics, "file_count": len(file_cache)}



def _klein_scale_from_variant(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if re.search(r"(?:^|[^0-9a-z])9b(?:[^0-9a-z]|$)", text):
        return "9b"
    if re.search(r"(?:^|[^0-9a-z])4b(?:[^0-9a-z]|$)", text):
        return "4b"
    return ""


def _flux2_klein_signature(diffusion_model: Any) -> dict[str, Any]:
    try:
        double_count = len(getattr(diffusion_model, "double_blocks"))
    except Exception:
        double_count = -1
    try:
        single_count = len(getattr(diffusion_model, "single_blocks"))
    except Exception:
        single_count = -1
    try:
        hidden_size = int(getattr(diffusion_model, "hidden_size"))
    except Exception:
        hidden_size = -1
    # Block depth is the authoritative runtime signature. Current Klein 4B is
    # 5 double + 20 single blocks and Klein 9B is 8 + 24; FLUX.2 dev is 8 + 48.
    # Keep hidden_size as a diagnostic only. In particular, Qwen3-4B is
    # 2560-wide while the Klein 4B transformer itself is 3072-wide, so using
    # 2560 here would incorrectly reject the real 4B diffusion model.
    scale = ""
    hidden_reference = -1
    if double_count == 5 and single_count == 20:
        scale = "4b"
        hidden_reference = 3072
    elif double_count == 8 and single_count == 24:
        scale = "9b"
        hidden_reference = 4096
    return {
        "double_blocks": double_count,
        "single_blocks": single_count,
        "hidden_size": hidden_size,
        "transformer_hidden_reference": hidden_reference,
        "hidden_reference_match": bool(scale and hidden_size in {-1, hidden_reference}),
        "scale": scale,
        "klein_signature_proven": bool(scale),
    }


def _require_flux2_klein_model(model: Any, requested_variant: str = "auto") -> tuple[Any, dict[str, Any]]:
    base_model = getattr(model, "model", None)
    if base_model is None:
        raise RuntimeError("NeoRegionalLoRADelta expected a Comfy ModelPatcher with a .model BaseModel.")
    if comfy_model_base is not None and hasattr(comfy_model_base, "Flux2"):
        if not isinstance(base_model, comfy_model_base.Flux2):
            raise RuntimeError("NeoRegionalLoRADelta family=flux2_klein received a non-Flux2 Comfy model; execution was blocked.")
    elif base_model.__class__.__name__ != "Flux2":
        raise RuntimeError("NeoRegionalLoRADelta could not prove that the active model is Comfy Flux2.")
    dm = getattr(base_model, "diffusion_model", None)
    if dm is None:
        raise RuntimeError("NeoRegionalLoRADelta could not access Flux2 BaseModel.diffusion_model.")
    signature = _flux2_klein_signature(dm)
    if not signature.get("klein_signature_proven"):
        raise RuntimeError(
            "NeoRegionalLoRADelta received Flux2, but its transformer block depth does not match supported Klein 4B/9B signatures; "
            "FLUX.2 dev/unknown variants are blocked rather than treated as Klein."
        )
    requested_scale = _klein_scale_from_variant(requested_variant)
    if requested_scale and requested_scale != signature.get("scale"):
        raise RuntimeError(
            f"NeoRegionalLoRADelta requested Klein {requested_scale.upper()} but runtime transformer resolves to {str(signature.get('scale')).upper()}."
        )
    signature["requested_variant"] = str(requested_variant or "auto")
    signature["requested_scale"] = requested_scale
    return base_model, signature


class _Flux2KleinRegionalSession(_Krea2RegionalSession):
    def _resolve_grid(self, x: Any) -> tuple[int, int] | None:
        if not torch.is_tensor(x) or x.dim() < 4:
            return None
        dm = self._diffusion_model()
        raw_patch = getattr(dm, "patch_size", 2)
        try:
            patch = max(1, int(raw_patch[0] if isinstance(raw_patch, (tuple, list)) else raw_patch or 2))
        except Exception:
            patch = 2
        height, width = int(x.shape[-2]), int(x.shape[-1])
        rows, cols = math.ceil(height / patch), math.ceil(width / patch)
        self.runtime_proof["flux2_patch_size"] = patch
        if rows <= 0 or cols <= 0:
            return None
        return rows, cols

    def _build_layer_map(self, dm: Any) -> dict[str, tuple[Any, list[tuple[int, dict[str, Any]]]]]:
        result = super()._build_layer_map(dm)
        self.runtime_proof["spatial_module_policy"] = (
            "img_in/double_blocks.*.img_*/final_layer.linear=image_only; "
            "single_blocks.*.linear1/linear2=combined_text_image; text/modulation paths excluded"
        )
        return result


def build_flux2_klein_region_entries(model: Any, routes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_model = getattr(model, "model", None)
    dm = getattr(base_model, "diffusion_model", None)
    if base_model is None or dm is None:
        raise RuntimeError("NeoRegionalLoRADelta could not access Flux2 BaseModel.diffusion_model.")
    file_cache: dict[str, dict[str, dict[str, Any]]] = {}
    entries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for route in routes:
        lora_name = route["lora_name"]
        if lora_name not in file_cache:
            pairs = parse_standard_lora_pairs(_load_lora_state(lora_name))
            if not pairs:
                raise RuntimeError(
                    f"Regional LoRA '{lora_name}' contains no supported standard A/B pairs. "
                    "SD-28.5 does not fall back to a global loader."
                )
            file_cache[lora_name] = pairs
        resolved, stats = resolve_lora_pairs_to_modules(
            file_cache[lora_name],
            base_model=base_model,
            diffusion_model=dm,
            spatial_scope_resolver=flux2_klein_spatial_module_scope,
        )
        if not resolved:
            raise RuntimeError(
                f"Regional LoRA '{lora_name}' matched zero safe FLUX.2 Klein layers; execution was blocked to prevent a false regional-support claim."
            )
        strength = float(route.get("strength", 1.0))
        scaled = {
            name: {**data, "scale": float(data["base_scale"]) * strength}
            for name, data in resolved.items()
        }
        entries.append({
            "region_id": route["region_id"],
            "lora_name": lora_name,
            "strength": strength,
            "bbox_norm": tuple(route["bbox_norm"]),
            "seam_feather": max(0.0, min(0.5, float(route.get("seam_feather", 0.0) or 0.0))),
            "modules": scaled,
        })
        diagnostics.append({
            "region_id": route["region_id"],
            "lora_name": lora_name,
            "strength": strength,
            "compatibility": deepcopy(route.get("flux2_klein_compatibility") or {}),
            **stats,
        })
    return entries, {"routes": diagnostics, "file_count": len(file_cache)}




def _round_up_multiple(value: int, multiple: Any) -> int:
    try:
        m = int(multiple or 0)
    except Exception:
        m = 0
    value = max(0, int(value))
    if m <= 0:
        return value
    return int(math.ceil(value / float(m)) * m)


def _z_image_signature(diffusion_model: Any) -> dict[str, Any]:
    def _int_attr(name: str, default: int = -1) -> int:
        try:
            return int(getattr(diffusion_model, name))
        except Exception:
            return default

    try:
        layer_count = len(getattr(diffusion_model, "layers"))
    except Exception:
        layer_count = -1
    try:
        noise_refiner_count = len(getattr(diffusion_model, "noise_refiner"))
    except Exception:
        noise_refiner_count = -1
    try:
        context_refiner_count = len(getattr(diffusion_model, "context_refiner"))
    except Exception:
        context_refiner_count = -1
    patch_raw = getattr(diffusion_model, "patch_size", -1)
    try:
        patch_size = int(patch_raw[0] if isinstance(patch_raw, (tuple, list)) else patch_raw)
    except Exception:
        patch_size = -1
    signature = {
        "dim": _int_attr("dim"),
        "in_channels": _int_attr("in_channels"),
        "n_heads": _int_attr("n_heads"),
        "main_layers": layer_count,
        "noise_refiner_layers": noise_refiner_count,
        "context_refiner_layers": context_refiner_count,
        "patch_size": patch_size,
        "pad_tokens_multiple": getattr(diffusion_model, "pad_tokens_multiple", None),
    }
    signature["z_image_signature_proven"] = bool(
        signature["dim"] == 3840
        and signature["in_channels"] == 16
        and signature["n_heads"] == 30
        and signature["main_layers"] == 30
        and signature["noise_refiner_layers"] == 2
        and signature["context_refiner_layers"] == 2
        and signature["patch_size"] == 2
    )
    return signature


def _require_z_image_model(model: Any, requested_family: str) -> tuple[Any, dict[str, Any]]:
    base_model = getattr(model, "model", None)
    if base_model is None:
        raise RuntimeError("NeoRegionalLoRADelta expected a Comfy ModelPatcher with a .model BaseModel.")
    if comfy_model_base is not None and hasattr(comfy_model_base, "Lumina2"):
        if not isinstance(base_model, comfy_model_base.Lumina2):
            raise RuntimeError(f"NeoRegionalLoRADelta family={requested_family} received a non-Lumina2 Comfy model; execution was blocked.")
    elif base_model.__class__.__name__ != "Lumina2":
        raise RuntimeError("NeoRegionalLoRADelta could not prove that the active model is Comfy Lumina2/Z-Image.")
    dm = getattr(base_model, "diffusion_model", None)
    if dm is None:
        raise RuntimeError("NeoRegionalLoRADelta could not access Lumina2 BaseModel.diffusion_model.")
    signature = _z_image_signature(dm)
    if not signature.get("z_image_signature_proven"):
        raise RuntimeError(
            "NeoRegionalLoRADelta received Lumina2, but the live NextDiT signature does not match Z-Image 6B "
            "(dim=3840, 30 main layers, 2+2 refiners, 30 heads, patch=2); generic Lumina2 was blocked."
        )
    signature["requested_family"] = requested_family
    signature["requested_variant"] = "turbo" if requested_family == "z_image_turbo" else "base"
    signature["variant_runtime_identity_proven"] = False
    signature["variant_runtime_identity_note"] = "Base and Turbo share the proven transformer architecture; the route contract, not tensor shape alone, owns the variant label."
    return base_model, signature


class _ZImageRegionalSession(_Krea2RegionalSession):
    def _pad_multiple(self) -> int:
        dm = self._diffusion_model()
        try:
            return max(0, int(getattr(dm, "pad_tokens_multiple", 0) or 0))
        except Exception:
            return 0

    def _resolve_grid(self, x: Any) -> tuple[int, int] | None:
        if not torch.is_tensor(x) or x.dim() < 4:
            return None
        dm = self._diffusion_model()
        raw_patch = getattr(dm, "patch_size", 2)
        try:
            patch = max(1, int(raw_patch[0] if isinstance(raw_patch, (tuple, list)) else raw_patch or 2))
        except Exception:
            patch = 2
        height, width = int(x.shape[-2]), int(x.shape[-1])
        rows, cols = math.ceil(height / patch), math.ceil(width / patch)
        logical = rows * cols
        padded = _round_up_multiple(logical, self._pad_multiple())
        self.runtime_proof["z_image_patch_size"] = patch
        self.runtime_proof["z_image_pad_tokens_multiple"] = self._pad_multiple()
        self.runtime_proof["z_image_logical_image_tokens"] = logical
        self.runtime_proof["z_image_padded_image_tokens"] = padded
        if rows <= 0 or cols <= 0:
            return None
        return rows, cols

    def _extract_text_len(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | None:
        raw = super()._extract_text_len(args, kwargs)
        if raw is None:
            return None
        padded = _round_up_multiple(raw, self._pad_multiple())
        self.runtime_proof["z_image_raw_caption_tokens"] = int(raw)
        self.runtime_proof["z_image_padded_caption_tokens"] = int(padded)
        return padded

    def _full_mask(self, region_index: int, seq_len: int, ndim: int, scope: str) -> torch.Tensor:
        logical_mask = self.image_masks[region_index]
        logical_tokens = int(logical_mask.numel())
        padded_tokens = _round_up_multiple(logical_tokens, self._pad_multiple())
        key = ("z", region_index, int(seq_len), int(ndim), self.text_len, scope, logical_tokens, padded_tokens)
        cached = self.full_mask_cache.get(key)
        if cached is not None:
            return cached
        leading = [1] * max(0, int(ndim) - 2)
        base = torch.zeros(int(seq_len), device=logical_mask.device, dtype=logical_mask.dtype)
        if scope == "image_unpadded":
            if int(seq_len) == logical_tokens:
                base[:] = logical_mask
        elif scope == "image_only":
            if int(seq_len) == padded_tokens:
                base[:logical_tokens] = logical_mask
        elif scope == "combined_text_image" and self.text_len is not None:
            text_len = int(self.text_len)
            if int(seq_len) == text_len + padded_tokens:
                base[text_len:text_len + logical_tokens] = logical_mask
        result = base.view(*leading, int(seq_len), 1)
        self.full_mask_cache[key] = result
        return result

    def _build_layer_map(self, dm: Any) -> dict[str, tuple[Any, list[tuple[int, dict[str, Any]]]]]:
        result = super()._build_layer_map(dm)
        self.runtime_proof["spatial_module_policy"] = (
            "x_embedder=image_unpadded; noise_refiner.* attention/ffn=image_only_padded; "
            "layers.* attention/ffn + final_layer.linear=combined_padded_text_image; "
            "context/caption/timestep/AdaLN/norm paths excluded"
        )
        self.runtime_proof["padding_mask_policy"] = "caption_padding=0; image_padding=0; unknown_layout=all_zero"
        return result

    def run(self, executor: Any, *args: Any, **kwargs: Any):
        ref_latents = kwargs.get("ref_latents")
        if isinstance(ref_latents, (list, tuple)) and len(ref_latents) > 0:
            raise RuntimeError("NeoRegionalLoRADelta Z-Image Scene Director does not support omni/reference-latent token stacks; execution was blocked rather than guessing mask offsets.")
        return super().run(executor, *args, **kwargs)


def build_z_image_region_entries(model: Any, routes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_model = getattr(model, "model", None)
    dm = getattr(base_model, "diffusion_model", None)
    if base_model is None or dm is None:
        raise RuntimeError("NeoRegionalLoRADelta could not access Z-Image Lumina2 BaseModel.diffusion_model.")
    file_cache: dict[str, dict[str, dict[str, Any]]] = {}
    entries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for route in routes:
        lora_name = route["lora_name"]
        if lora_name not in file_cache:
            pairs = parse_standard_lora_pairs(_load_lora_state(lora_name))
            if not pairs:
                raise RuntimeError(
                    f"Regional LoRA '{lora_name}' contains no supported standard A/B pairs. "
                    "SD-28.6 does not fall back to a global loader."
                )
            file_cache[lora_name] = pairs
        resolved, stats = resolve_lora_pairs_to_modules(
            file_cache[lora_name],
            base_model=base_model,
            diffusion_model=dm,
            spatial_scope_resolver=z_image_spatial_module_scope,
        )
        if not resolved:
            raise RuntimeError(
                f"Regional LoRA '{lora_name}' matched zero safe Z-Image layers; execution was blocked to prevent a false regional-support claim."
            )
        strength = float(route.get("strength", 1.0))
        scaled = {name: {**data, "scale": float(data["base_scale"]) * strength} for name, data in resolved.items()}
        entries.append({
            "region_id": route["region_id"],
            "lora_name": lora_name,
            "strength": strength,
            "bbox_norm": tuple(route["bbox_norm"]),
            "seam_feather": max(0.0, min(0.5, float(route.get("seam_feather", 0.0) or 0.0))),
            "modules": scaled,
        })
        diagnostics.append({
            "region_id": route["region_id"],
            "lora_name": lora_name,
            "strength": strength,
            "compatibility": deepcopy(route.get("z_image_compatibility") or {}),
            **stats,
        })
    return entries, {"routes": diagnostics, "file_count": len(file_cache)}

class NeoRegionalLoRADelta:
    """Clone MODEL and arm family-specific region-masked LoRA activation deltas."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "routes_json": ("STRING", {"multiline": True, "default": "[]"}),
                "family": (["krea2", "krea2_turbo", "flux2_klein", "z_image", "z_image_turbo"], {"default": "krea2"}),
                "canvas_width": ("INT", {"default": 1024, "min": 64, "max": 16384, "step": 8}),
                "canvas_height": ("INT", {"default": 1024, "min": 64, "max": 16384, "step": 8}),
                "seam_feather": ("FLOAT", {"default": 0.04, "min": 0.0, "max": 0.5, "step": 0.01}),
                "sampler_count": ("INT", {"default": 1, "min": 0, "max": 64}),
            },
            "optional": {
                "loader": (["diffusion_model", "gguf"], {"default": "diffusion_model"}),
                "variant": ("STRING", {"default": "auto"}),
            },
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "apply"
    CATEGORY = "Neo Studio/Scene Director"
    DESCRIPTION = "IMG-SD2 modern regional LoRA isolation wrapper. Krea2 strict mode suppresses attention K/V LoRA writes; no sampler or CLIP mutation."

    def apply(
        self,
        model: Any,
        routes_json: str,
        family: str,
        canvas_width: int,
        canvas_height: int,
        seam_feather: float,
        sampler_count: int,
        loader: str = "diffusion_model",
        variant: str = "auto",
    ):
        family_norm = _norm_family(family)
        if family_norm not in SUPPORTED_FAMILIES:
            raise RuntimeError(f"NeoRegionalLoRADelta adapter is not implemented for family '{family_norm}'.")
        loader_norm = str(loader or "diffusion_model").strip().lower().replace("-", "_")
        if loader_norm not in {"diffusion_model", "gguf"}:
            raise RuntimeError(f"NeoRegionalLoRADelta loader '{loader_norm}' is outside the SD-28.6 support contract.")
        routes = _parse_routes(routes_json)
        if not routes:
            return (model,)
        for route in routes:
            route.setdefault("canvas", {"width": int(canvas_width), "height": int(canvas_height)})
            route["bbox_norm"] = _route_bbox_norm(route)
        if not hasattr(model, "clone"):
            raise RuntimeError("NeoRegionalLoRADelta requires Comfy ModelPatcher.clone().")

        family_diagnostics: dict[str, Any] = {}
        if family_norm in {"krea2", "krea2_turbo"}:
            _require_krea2_model(model, family_norm)
            entries, load_diagnostics = build_krea2_region_entries(model, routes)
            adapter = "krea2_activation_delta_v3_strict_isolation"
            session_type = _Krea2RegionalSession
        elif family_norm == "flux2_klein":
            _base_model, family_diagnostics = _require_flux2_klein_model(model, variant)
            entries, load_diagnostics = build_flux2_klein_region_entries(model, routes)
            adapter = "flux2_klein_activation_delta_v1"
            session_type = _Flux2KleinRegionalSession
        else:
            _base_model, family_diagnostics = _require_z_image_model(model, family_norm)
            entries, load_diagnostics = build_z_image_region_entries(model, routes)
            adapter = "z_image_activation_delta_v1"
            session_type = _ZImageRegionalSession

        patched = model.clone()
        runtime_proof: dict[str, Any] = {
            "schema": "neo.image.scene_director.regional_lora_delta.runtime_proof.v6",
            "phase": "IMG-SD2",
            "adapter": adapter,
            "family": family_norm,
            "loader": loader_norm,
            "variant": str(variant or "auto"),
            "route_count": len(entries),
            "loader_supported": loader_norm in {"diffusion_model", "gguf"},
            "lora_loaded": True,
            "model_family_match": True,
            "region_mask_bound": False,
            "masked_delta_hook_active": False,
            "spatial_scope_filter_active": False,
            "token_mask_scope_proven": False,
            "delta_eval_attempted": False,
            "delta_nonzero": False,
            "global_model_mutation": False,
            "sampler_count": int(sampler_count),
            "clip_delta_execution": "suppressed_model_side_only",
            "forward_hooks_removed": False,
            "runtime_gpu_proven": False,
            "identity_isolation_goal": "prevent_cross_character_lora_mixing",
            "identity_isolation_profile": "krea2_strict_no_attention_kv_write" if family_norm in {"krea2", "krea2_turbo"} else "spatial_activation_delta_best_effort",
            "cross_region_attention_kv_write_suppressed": family_norm in {"krea2", "krea2_turbo"},
            "hard_identity_isolation_claimed": False,
            "load_diagnostics": load_diagnostics,
            "family_diagnostics": family_diagnostics,
        }
        session = session_type(
            patched,
            entries,
            seam_feather=float(seam_feather),
            runtime_proof=runtime_proof,
        )

        def wrapper(executor: Any, *args: Any, **kwargs: Any):
            return session.run(executor, *args, **kwargs)

        wrapper_type = (
            comfy_patcher_extension.WrappersMP.DIFFUSION_MODEL
            if comfy_patcher_extension is not None
            else "diffusion_model"
        )
        if hasattr(patched, "add_wrapper_with_key"):
            patched.add_wrapper_with_key(wrapper_type, WRAPPER_KEY, wrapper)
        elif hasattr(patched, "add_wrapper"):
            patched.add_wrapper(wrapper_type, wrapper)
        else:
            raise RuntimeError("This ComfyUI build lacks ModelPatcher wrapper support; update ComfyUI.")
        if hasattr(patched, "set_attachments"):
            patched.set_attachments(RUNTIME_ATTACHMENT_KEY, runtime_proof)
        logging.info(
            "[NeoRegionalLoRADelta] armed IMG-SD2 %s regional LoRA isolation wrapper: %d route(s), loader=%s, model weights unchanged, CLIP untouched.",
            family_norm, len(entries), loader_norm,
        )
        return (patched,)


NODE_CLASS_MAPPINGS = {NODE_CLASS: NeoRegionalLoRADelta}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_CLASS: "Neo Scene Director · Regional LoRA Delta"}

__all__ = [
    "NODE_CLASS",
    "WRAPPER_KEY",
    "RUNTIME_ATTACHMENT_KEY",
    "NeoRegionalLoRADelta",
    "parse_standard_lora_pairs",
    "resolve_lora_pairs_to_modules",
    "krea2_spatial_module_scope",
    "flux2_klein_spatial_module_scope",
    "z_image_spatial_module_scope",
    "build_flux2_klein_region_entries",
    "build_z_image_region_entries",
    "rect_token_mask",
    "full_sequence_mask_fail_closed",
    "sequence_mask_for_scope",
    "krea2_isolation_exclusion_reason",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
]
