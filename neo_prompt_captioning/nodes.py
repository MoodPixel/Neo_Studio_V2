from __future__ import annotations

import base64
import gc
import io
import os
import threading
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths
import comfy.model_management as mm

try:
    from llama_cpp import Llama
except Exception:  # pragma: no cover - surfaced by Comfy node execution/readiness.
    Llama = None


LLM_EXTENSIONS = [".ckpt", ".pt", ".bin", ".pth", ".safetensors", ".gguf"]
if "LLM" not in folder_paths.folder_names_and_paths:
    folder_paths.folder_names_and_paths["LLM"] = ([os.path.join(folder_paths.models_dir, "LLM")], LLM_EXTENSIONS)


def _llm_names() -> tuple[list[str], list[str]]:
    values = list(folder_paths.get_filename_list("LLM"))
    models = [name for name in values if "mmproj" not in name.casefold()]
    projectors = [name for name in values if "mmproj" in name.casefold()]
    return models, projectors


def _full_llm_path(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("LLM model filename is empty.")
    try:
        path = folder_paths.get_full_path("LLM", value)
    except Exception:
        path = None
    path = path or os.path.join(folder_paths.models_dir, "LLM", value)
    if not os.path.isfile(path):
        raise ValueError(f"LLM model file was not found: {value}")
    return path


def _gguf_block_count(path: str) -> int:
    try:
        from gguf import GGUFReader

        reader = GGUFReader(path)
        for field in reader.fields.values():
            name = str(getattr(field, "name", "") or "")
            if not name.casefold().endswith(".block_count"):
                continue
            data = getattr(field, "data", None)
            parts = getattr(field, "parts", None)
            try:
                if data is not None and len(data) and parts is not None:
                    value = parts[data[0]]
                else:
                    value = None
                if isinstance(value, (list, tuple, np.ndarray)):
                    value = value[0] if len(value) else None
                if value is not None:
                    return max(1, int(value))
            except Exception:
                continue
    except Exception:
        pass
    return 32


def _gpu_layers_for_budget(model_path: str, mmproj_path: str, vram_limit: int) -> int:
    if int(vram_limit) < 0:
        return -1
    if int(vram_limit) == 0:
        return 0
    layers = _gguf_block_count(model_path)
    model_gb = os.path.getsize(model_path) * 1.55 / (1024**3)
    projector_gb = os.path.getsize(mmproj_path) * 1.55 / (1024**3)
    per_layer = model_gb / max(1, layers)
    available = max(0.0, float(vram_limit) - projector_gb)
    if available <= 0 or per_layer <= 0:
        return 0
    return max(0, min(layers, int(available / per_layer)))


def _image_tensor_to_data_uri(image: torch.Tensor, max_size: int) -> str:
    tensor = image
    if tensor.ndim == 4:
        tensor = tensor[0]
    array = np.clip(tensor.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    pil = Image.fromarray(array).convert("RGB")
    limit = max(128, int(max_size or 512))
    if max(pil.size) > limit:
        scale = limit / float(max(pil.size))
        pil = pil.resize((max(1, int(pil.width * scale)), max(1, int(pil.height * scale))), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    pil.save(buffer, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class _NeoGenericMTMDStorage:
    lock = threading.RLock()
    llm = None
    config: dict[str, Any] | None = None

    @classmethod
    def clean(cls) -> None:
        with cls.lock:
            try:
                if cls.llm is not None:
                    cls.llm.close()
            except Exception:
                pass
            cls.llm = None
            cls.config = None
        gc.collect()
        try:
            mm.soft_empty_cache()
        except Exception:
            pass

    @classmethod
    def ensure(cls, config: dict[str, Any]):
        if Llama is None:
            raise RuntimeError("llama-cpp-python is unavailable. Install the ComfyUI-llama-cpp_vlm requirements and restart ComfyUI.")
        with cls.lock:
            if cls.llm is not None and cls.config == config:
                return cls.llm
            cls.clean()
            model_path = _full_llm_path(config["model"])
            mmproj_path = _full_llm_path(config["mmproj"])
            n_gpu_layers = _gpu_layers_for_budget(model_path, mmproj_path, int(config.get("vram_limit", -1)))
            kwargs: dict[str, Any] = {
                "model_path": model_path,
                "n_gpu_layers": n_gpu_layers,
                "n_ctx": int(config.get("n_ctx", 8192) or 8192),
                "verbose": False,
            }
            kwargs["mmproj_path"] = mmproj_path
            kwargs["chat_handler_kwargs"] = {"verbose": False}
            print(f"[Neo Generic MTMD] Loading model: {config['model']}")
            print(f"[Neo Generic MTMD] Loading mmproj: {config['mmproj']}")
            print(f"[Neo Generic MTMD] n_gpu_layers = {n_gpu_layers}")
            try:
                cls.llm = Llama(**kwargs)
            except TypeError as exc:
                if "mmproj_path" in str(exc) or "chat_handler_kwargs" in str(exc):
                    raise RuntimeError(
                        "Installed llama-cpp-python does not expose the Generic MTMD constructor API required by Neo. "
                        "Update the llama-cpp-python build used by ComfyUI."
                    ) from exc
                raise
            cls.config = dict(config)
            return cls.llm


# Make Comfy's normal /free -> unload_models path also clear Neo's generic MTMD
# cache. The wrapper uses its own backup attribute so it composes safely with the
# third-party llama.cpp node pack's unload hook regardless of import order.
if not hasattr(mm, "_neo_generic_mtmd_unload_all_models_backup"):
    mm._neo_generic_mtmd_unload_all_models_backup = mm.unload_all_models

    def _neo_generic_mtmd_unload_all_models(*args, **kwargs):
        _NeoGenericMTMDStorage.clean()
        return mm._neo_generic_mtmd_unload_all_models_backup(*args, **kwargs)

    mm.unload_all_models = _neo_generic_mtmd_unload_all_models
    print("[Neo Generic MTMD] Comfy model-unload cleanup hook applied.")


class NeoPromptCaptionImageInput:
    """Decode a local Neo image data URI into a Comfy IMAGE tensor."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_data_uri": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": "Neo-owned image payload for Prompt & Captioning VLM workflows.",
                    },
                )
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "decode"
    CATEGORY = "Neo Studio/Prompt & Captioning"

    def decode(self, image_data_uri: str):
        raw = str(image_data_uri or "").strip()
        if not raw:
            raise ValueError("Neo Prompt & Captioning image payload is empty.")
        encoded = raw
        if raw.startswith("data:"):
            if "," not in raw:
                raise ValueError("Neo Prompt & Captioning image data URI is malformed.")
            header, encoded = raw.split(",", 1)
            if ";base64" not in header.lower():
                raise ValueError("Neo Prompt & Captioning image data URI must be base64 encoded.")
        try:
            payload = base64.b64decode(encoded, validate=False)
            image = Image.open(io.BytesIO(payload))
            image = ImageOps.exif_transpose(image).convert("RGB")
        except Exception as exc:  # noqa: BLE001 - Comfy should surface a useful node error.
            raise ValueError(f"Could not decode Neo Prompt & Captioning image payload: {exc}") from exc
        array = np.asarray(image).astype(np.float32) / 255.0
        return (torch.from_numpy(array).unsqueeze(0),)


class NeoGenericMTMDModelLoader:
    """Neo-owned template-driven MTMD loader for VLMs without a dedicated handler."""

    @classmethod
    def INPUT_TYPES(cls):
        models, projectors = _llm_names()
        return {
            "required": {
                "model": (models,),
                "mmproj": (projectors,),
                "n_ctx": ("INT", {"default": 8192, "min": 1024, "max": 327680, "step": 128}),
                "vram_limit": ("INT", {"default": -1, "min": -1, "max": 1024, "step": 1}),
            }
        }

    RETURN_TYPES = ("NEO_GENERIC_MTMD_MODEL",)
    RETURN_NAMES = ("mtmd_model",)
    FUNCTION = "load"
    CATEGORY = "Neo Studio/Prompt & Captioning"

    def load(self, model: str, mmproj: str, n_ctx: int, vram_limit: int):
        config = {
            "model": str(model),
            "mmproj": str(mmproj),
            "n_ctx": int(n_ctx),
            "vram_limit": int(vram_limit),
        }
        # Return config only. The inference node owns the retained runtime cache,
        # which allows batch captioning to reuse one loaded VLM across prompts.
        return (config,)


class NeoGenericMTMDInstruct:
    """Run one image+text completion through llama-cpp-python Generic MTMD."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mtmd_model": ("NEO_GENERIC_MTMD_MODEL",),
                "image": ("IMAGE",),
                "custom_prompt": ("STRING", {"default": "Describe this image.", "multiline": True}),
                "system_prompt": ("STRING", {"default": "", "multiline": True}),
                "max_size": ("INT", {"default": 512, "min": 128, "max": 4096, "step": 64}),
                "max_tokens": ("INT", {"default": 512, "min": 1, "max": 8192, "step": 1}),
                "top_k": ("INT", {"default": 40, "min": 0, "max": 1000, "step": 1}),
                "top_p": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.01}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0x7FFFFFFF, "step": 1}),
                "force_offload": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "process"
    CATEGORY = "Neo Studio/Prompt & Captioning"

    def process(
        self,
        mtmd_model: dict[str, Any],
        image: torch.Tensor,
        custom_prompt: str,
        system_prompt: str,
        max_size: int,
        max_tokens: int,
        top_k: int,
        top_p: float,
        temperature: float,
        seed: int,
        force_offload: bool,
    ):
        config = dict(mtmd_model or {})
        llm = _NeoGenericMTMDStorage.ensure(config)
        content = [
            {"type": "image_url", "image_url": {"url": _image_tensor_to_data_uri(image, max_size)}},
            {"type": "text", "text": str(custom_prompt or "Describe this image.")},
        ]
        messages: list[dict[str, Any]] = []
        if str(system_prompt or "").strip():
            messages.append({"role": "system", "content": str(system_prompt).strip()})
        messages.append({"role": "user", "content": content})
        try:
            response = llm.create_chat_completion(
                messages=messages,
                max_tokens=int(max_tokens),
                top_k=int(top_k),
                top_p=float(top_p),
                temperature=float(temperature),
                seed=int(seed),
            )
            text = str(response["choices"][0]["message"]["content"] or "").strip()
            if not text:
                raise RuntimeError("Generic MTMD returned an empty caption.")
            return (text,)
        finally:
            if force_offload:
                _NeoGenericMTMDStorage.clean()


class NeoPromptCaptionTextOutput:
    """Stable terminal output for Neo Prompt & Captioning text workflows."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": (
                    "STRING",
                    {
                        "forceInput": True,
                        "multiline": True,
                        "tooltip": "Text returned to Neo Studio.",
                    },
                )
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "output"
    OUTPUT_NODE = True
    CATEGORY = "Neo Studio/Prompt & Captioning"

    def output(self, text: Any):
        value = "" if text is None else str(text)
        return {"ui": {"text": [value]}, "result": ()}


NODE_CLASS_MAPPINGS = {
    "NeoPromptCaptionImageInput": NeoPromptCaptionImageInput,
    "NeoGenericMTMDModelLoader": NeoGenericMTMDModelLoader,
    "NeoGenericMTMDInstruct": NeoGenericMTMDInstruct,
    "NeoPromptCaptionTextOutput": NeoPromptCaptionTextOutput,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "NeoPromptCaptionImageInput": "Neo Prompt/Caption Image Input",
    "NeoGenericMTMDModelLoader": "Neo Generic MTMD Model Loader",
    "NeoGenericMTMDInstruct": "Neo Generic MTMD Instruct",
    "NeoPromptCaptionTextOutput": "Neo Prompt/Caption Text Output",
}
