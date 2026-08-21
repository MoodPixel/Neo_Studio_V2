from __future__ import annotations

import gc
import importlib.util
import os
import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


from neo_voice_engine.qwen3_tts_model_registry import (
    MODEL_17B_CUSTOM,
    MODEL_06B_CUSTOM,
    MODEL_17B_BASE,
    MODEL_06B_BASE,
    MODEL_17B_DESIGN,
    MODEL_SPECS,
    SUPPORTED_MODELS,
)
from neo_voice_engine.qwen3_tts_runtime_resolver import probe_qwen3_tts_runtime_model

LANGUAGE_NAMES = {
    "auto": "Auto",
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}
LANGUAGE_NAME_LOOKUP = {value.lower(): value for value in LANGUAGE_NAMES.values()}

BUILT_IN_SPEAKERS = {
    "vivian": {"provider_name": "Vivian", "native_language": "Chinese"},
    "serena": {"provider_name": "Serena", "native_language": "Chinese"},
    "uncle_fu": {"provider_name": "Uncle_Fu", "native_language": "Chinese"},
    "dylan": {"provider_name": "Dylan", "native_language": "Chinese (Beijing Dialect)"},
    "eric": {"provider_name": "Eric", "native_language": "Chinese (Sichuan Dialect)"},
    "ryan": {"provider_name": "Ryan", "native_language": "English"},
    "aiden": {"provider_name": "Aiden", "native_language": "English"},
    "ono_anna": {"provider_name": "Ono_Anna", "native_language": "Japanese"},
    "sohee": {"provider_name": "Sohee", "native_language": "Korean"},
}


class Qwen3TTSAdapterError(RuntimeError):
    """Normalized Qwen3-TTS runtime error safe to surface through Neo."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "qwen3_tts_error",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "qwen3_tts_error")
        self.retryable = bool(retryable)
        self.details = dict(details or {})


@dataclass
class GeneratedAudio:
    path: Path
    media_type: str
    model_id: str
    sample_rate: int
    warnings: list[str]


def dependency_status() -> dict[str, Any]:
    return {
        "qwen_tts": importlib.util.find_spec("qwen_tts") is not None,
        "torch": importlib.util.find_spec("torch") is not None,
        "torchaudio": importlib.util.find_spec("torchaudio") is not None,
        "transformers": importlib.util.find_spec("transformers") is not None,
        "accelerate": importlib.util.find_spec("accelerate") is not None,
        "soundfile": importlib.util.find_spec("soundfile") is not None,
        "flash_attention_2": importlib.util.find_spec("flash_attn") is not None,
    }


def normalize_language(value: Any) -> str:
    raw = str(value or "Auto").strip()
    if not raw:
        return "Auto"
    lower = raw.lower().replace("_", "-")
    if lower in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[lower]
    if lower in LANGUAGE_NAME_LOOKUP:
        return LANGUAGE_NAME_LOOKUP[lower]
    raise Qwen3TTSAdapterError(
        f"Unsupported Qwen3-TTS language '{raw}'. Supported: Auto, {', '.join(LANGUAGE_NAMES[key] for key in LANGUAGE_NAMES if key != 'auto')}."
    )


def normalize_speaker(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "provider_default":
        return "Ryan"
    key = raw.lower().replace("-", "_").replace(" ", "_")
    if key in BUILT_IN_SPEAKERS:
        return str(BUILT_IN_SPEAKERS[key]["provider_name"])
    # Preserve a live-discovered provider spelling; upstream validation is case-insensitive.
    return raw


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _is_within(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        allowed = root.resolve()
    except OSError:
        return False
    return resolved == allowed or allowed in resolved.parents


class Qwen3TTSEngine:
    """Lazy single-model Qwen3-TTS runtime.

    Only one Qwen model is held resident in a worker process at a time. This keeps
    model switching deterministic and lets Neo's existing worker lifecycle reclaim
    GPU memory between families.
    """

    def __init__(
        self,
        *,
        neo_root: Path | None = None,
        voice_runtime_root: Path | None = None,
        runtime_dir: Path | None = None,
        model_root: Path | None = None,
        model_loader: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.neo_root = (neo_root or Path(os.getenv("NEO_QWEN3_TTS_NEO_ROOT") or Path.cwd())).resolve()
        configured_voice_root = Path(
            os.getenv("NEO_VOICE_RUNTIME_ROOT")
            or voice_runtime_root
            or (self.neo_root.parent / "Neo_Runtime" / "voice")
        )
        self.voice_runtime_root = configured_voice_root.resolve()
        self.reference_root = (self.neo_root / "neo_data" / "outputs" / "voice" / "reference").resolve()
        self.runtime_dir = (runtime_dir or self.voice_runtime_root / "temp" / "qwen3_tts_worker").resolve()
        self.model_root = (model_root or Path(os.getenv("NEO_QWEN3_TTS_MODEL_ROOT") or self.voice_runtime_root / "models" / "qwen3_tts")).resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.model_root.mkdir(parents=True, exist_ok=True)
        self._model: Any = None
        self._model_id = ""
        self._device = ""
        self._load_source = ""
        self._load_source_kind = ""
        self._source_resolution: dict[str, Any] = {}
        self._model_loader = model_loader
        self._lock = threading.RLock()

    @property
    def loaded_model_id(self) -> str:
        return self._model_id

    @property
    def device(self) -> str:
        return self._device or "not_initialized"

    @property
    def load_source(self) -> str:
        return self._load_source

    @property
    def load_source_kind(self) -> str:
        return self._load_source_kind

    @property
    def source_resolution(self) -> dict[str, Any]:
        return dict(self._source_resolution)

    def _torch(self):
        try:
            import torch
        except Exception as exc:  # pragma: no cover - physical worker only.
            raise Qwen3TTSAdapterError(f"PyTorch is unavailable in the Qwen3-TTS environment: {exc}") from exc
        return torch

    def _normalize_requested_device(self, device: str = "", device_index: int | None = None) -> str:
        requested = str(device or os.getenv("NEO_QWEN3_TTS_DEVICE") or "auto").strip().lower()
        torch = self._torch()
        if requested in {"", "auto"}:
            return "cuda:0" if torch.cuda.is_available() else "cpu"
        if requested == "cuda" and device_index is not None:
            requested = f"cuda:{max(0, int(device_index))}"
        if requested == "cuda":
            requested = "cuda:0"
        if requested not in {"cpu"} and not requested.startswith("cuda:"):
            raise Qwen3TTSAdapterError(f"Unsupported Qwen3-TTS execution device '{requested}'.")
        if requested.startswith("cuda:") and not torch.cuda.is_available():
            raise Qwen3TTSAdapterError("Neo requested CUDA for Qwen3-TTS but CUDA is not available in this worker environment.")
        return requested

    def _dtype_for_device(self, device: str):
        torch = self._torch()
        forced = str(os.getenv("NEO_QWEN3_TTS_DTYPE") or "auto").strip().lower()
        if forced in {"fp32", "float32"}:
            return torch.float32
        if forced in {"fp16", "float16"}:
            return torch.float16
        if forced in {"bf16", "bfloat16"}:
            return torch.bfloat16
        if device.startswith("cuda"):
            is_bf16_supported = getattr(torch.cuda, "is_bf16_supported", None)
            if callable(is_bf16_supported) and is_bf16_supported():
                return torch.bfloat16
            return torch.float16
        return torch.float32

    def _resolve_model_source(self, model_id: str) -> str:
        spec = MODEL_SPECS[model_id]
        upstream = str(spec["upstream_model_id"])
        resolution = probe_qwen3_tts_runtime_model(
            project_root=self.neo_root,
            voice_runtime_root=self.voice_runtime_root,
            model_id=model_id,
            legacy_model_root=self.model_root,
        )
        self._source_resolution = dict(resolution)
        if resolution.get("state") == "installed" and str(resolution.get("source_path") or "").strip():
            self._load_source_kind = str(resolution.get("source_kind") or "local_snapshot")
            return str(Path(str(resolution["source_path"])).expanduser().resolve())

        if _as_bool(os.getenv("NEO_QWEN3_TTS_LOCAL_ONLY"), False):
            self._load_source_kind = ""
            detail = str(resolution.get("message") or "No executable local snapshot is available.")
            raise Qwen3TTSAdapterError(
                f"Qwen3-TTS model '{model_id}' is not installed for local execution. {detail} "
                "Install or repair it in Neo Studio Admin → Models. Generation will not download model weights.",
                code="model_not_installed",
                retryable=False,
                details={
                    "model_id": model_id,
                    "runtime_state": str(resolution.get("state") or "not_installed"),
                    "catalog_error": str(resolution.get("catalog_error") or ""),
                },
            )

        # Explicit development/test escape hatch only. Managed Neo launch sets
        # NEO_QWEN3_TTS_LOCAL_ONLY=1, so normal generation can never reach this.
        self._load_source_kind = "remote_repo_development_fallback"
        return upstream

    def _default_model_loader(self, source: str, options: dict[str, Any]) -> Any:
        try:
            from qwen_tts import Qwen3TTSModel
        except Exception as exc:  # pragma: no cover - physical worker only.
            raise Qwen3TTSAdapterError(f"qwen-tts is unavailable or failed to import: {exc}") from exc
        return Qwen3TTSModel.from_pretrained(source, **options)

    def _load_model(self, model_id: str) -> Any:
        if model_id not in MODEL_SPECS:
            raise Qwen3TTSAdapterError(f"Unsupported Qwen3-TTS model '{model_id}'.")
        if self._model is not None and self._model_id == model_id:
            return self._model
        self._unload_model()
        device = self._device or self._normalize_requested_device()
        self._device = device
        source = self._resolve_model_source(model_id)
        options: dict[str, Any] = {
            "device_map": device,
            "dtype": self._dtype_for_device(device),
        }
        attn = str(os.getenv("NEO_QWEN3_TTS_ATTN") or "auto").strip().lower()
        if attn == "flash_attention_2" or (attn == "auto" and device.startswith("cuda") and importlib.util.find_spec("flash_attn") is not None):
            options["attn_implementation"] = "flash_attention_2"
        elif attn not in {"", "auto", "default"}:
            options["attn_implementation"] = attn
        loader = self._model_loader or self._default_model_loader
        try:
            model = loader(source, options)
        except Qwen3TTSAdapterError:
            raise
        except Exception as exc:  # pragma: no cover - physical model loader failures vary.
            raise Qwen3TTSAdapterError(f"Qwen3-TTS model '{model_id}' failed to load: {exc}") from exc
        self._model = model
        self._model_id = model_id
        self._load_source = source
        return model

    def _clear_transient_cuda_cache(self) -> None:
        """Release unoccupied CUDA allocator cache without unloading the resident model."""
        try:
            torch = self._torch()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def _unload_model(self) -> None:
        model = self._model
        self._model = None
        self._model_id = ""
        self._load_source = ""
        self._load_source_kind = ""
        self._source_resolution = {}
        if model is not None:
            del model
        gc.collect()
        self._clear_transient_cuda_cache()

    def apply_execution_hint(self, *, device: str = "", device_index: int | None = None) -> dict[str, Any]:
        """Apply gateway device authority without synchronously loading weights.

        Qwen model download/load can outlive a normal control HTTP timeout. The
        managed gateway therefore may call the worker load endpoint in deferred
        mode, then let the asynchronous render job perform the heavy load.
        """
        with self._lock:
            requested = self._normalize_requested_device(device, device_index)
            if self._device and requested != self._device:
                self._unload_model()
            self._device = requested
            return {
                "supported": False,
                "state": "implicit",
                "model_id": self._model_id,
                "device": self.device,
                "message": "Execution hint accepted; Qwen model loading is deferred to the asynchronous generation job.",
            }

    def prepare_model(self, model_id: str, *, device: str = "", device_index: int | None = None) -> dict[str, Any]:
        with self._lock:
            requested = self._normalize_requested_device(device, device_index)
            if self._device and requested != self._device:
                self._unload_model()
            self._device = requested
            model = self._load_model(model_id)
            languages = self._live_languages(model)
            speakers = self._live_speakers(model)
            return {
                "supported": True,
                "state": "resident",
                "model_id": self._model_id,
                "device": self.device,
                "load_source": self.load_source,
                "load_source_kind": self.load_source_kind,
                "source_resolution": self.source_resolution,
                "languages": languages,
                "speakers": speakers,
            }

    def unload_model(self, model_id: str = "") -> dict[str, Any]:
        with self._lock:
            requested = str(model_id or "").strip()
            if requested and self._model_id and requested != self._model_id:
                return {
                    "supported": True,
                    "state": "unloaded",
                    "model_id": requested,
                    "loaded_model_id": self._model_id,
                    "changed": False,
                }
            previous = self._model_id
            self._unload_model()
            return {
                "supported": True,
                "state": "unloaded",
                "model_id": requested or previous,
                "changed": bool(previous),
                "device": self.device,
            }

    def model_lifecycle(self, model_id: str) -> dict[str, Any]:
        if model_id not in MODEL_SPECS:
            raise Qwen3TTSAdapterError(f"Unsupported Qwen3-TTS model '{model_id}'.")
        with self._lock:
            resident = self._model is not None and self._model_id == model_id
            return {
                "supported": True,
                "model_id": model_id,
                "state": "resident" if resident else "unloaded",
                "loaded_model_id": self._model_id,
                "device": self.device,
                "load_source": self.load_source if resident else "",
                "load_source_kind": self.load_source_kind if resident else "",
                "source_resolution": self.source_resolution if resident else {},
            }

    def _live_languages(self, model: Any | None = None) -> list[str]:
        current = model or self._model
        if current is not None:
            getter = getattr(current, "get_supported_languages", None)
            if callable(getter):
                try:
                    values = getter()
                    if values:
                        return [str(item) for item in values]
                except Exception:
                    pass
        return [LANGUAGE_NAMES[key] for key in LANGUAGE_NAMES]

    def _live_speakers(self, model: Any | None = None) -> list[str]:
        current = model or self._model
        if current is not None:
            getter = getattr(current, "get_supported_speakers", None)
            if callable(getter):
                try:
                    values = getter()
                    if values:
                        return [str(item) for item in values]
                except Exception:
                    pass
        return [str(item["provider_name"]) for item in BUILT_IN_SPEAKERS.values()]

    def discovered_languages(self, model_id: str = "") -> list[str]:
        with self._lock:
            if not model_id or self._model_id != model_id or self._model is None:
                return []
            return self._live_languages(self._model)

    def discovered_speakers(self, model_id: str = "") -> list[str]:
        if model_id and not bool(MODEL_SPECS.get(model_id, {}).get("built_in_speakers")):
            return []
        with self._lock:
            if not model_id or self._model_id != model_id or self._model is None:
                return []
            return self._live_speakers(self._model)

    def _resolve_reference(self, payload: dict[str, Any]) -> Path:
        reference = payload.get("reference_audio") if isinstance(payload.get("reference_audio"), dict) else {}
        if reference.get("authorization_confirmed") is not True:
            raise Qwen3TTSAdapterError("Voice clone reference authorization is required.")
        qc_status = str(reference.get("qc_status") or "").strip()
        if qc_status not in {"usable", "usable_with_warnings"}:
            raise Qwen3TTSAdapterError("Voice clone reference is not QC-ready.")
        transport = str(reference.get("transport") or "neo_owned_local_path").strip()
        if transport != "neo_owned_local_path":
            raise Qwen3TTSAdapterError("Qwen3-TTS only accepts Neo-owned local reference paths in the local worker.")
        raw = str(reference.get("local_path") or "").strip()
        if not raw:
            raise Qwen3TTSAdapterError("Voice clone reference local path is required.")
        path = Path(raw).expanduser().resolve()
        allow_external = _as_bool(os.getenv("NEO_QWEN3_TTS_ALLOW_EXTERNAL_REFERENCE"), False)
        if not allow_external and not _is_within(path, self.reference_root):
            raise Qwen3TTSAdapterError(f"Voice clone reference must stay under Neo's reference root: {self.reference_root}")
        if not path.exists() or not path.is_file():
            raise Qwen3TTSAdapterError("Voice clone reference file does not exist.")
        return path

    def _generation_kwargs(self, controls: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if "do_sample" in controls:
            kwargs["do_sample"] = _as_bool(controls.get("do_sample"), True)
        if "top_k" in controls:
            kwargs["top_k"] = _bounded_int(controls.get("top_k"), 50, 1, 1000)
        if "top_p" in controls:
            kwargs["top_p"] = _bounded_float(controls.get("top_p"), 1.0, 0.01, 1.0)
        if "temperature" in controls:
            kwargs["temperature"] = _bounded_float(controls.get("temperature"), 0.9, 0.01, 4.0)
        if "repetition_penalty" in controls:
            kwargs["repetition_penalty"] = _bounded_float(controls.get("repetition_penalty"), 1.05, 0.1, 4.0)
        if "subtalker_dosample" in controls:
            kwargs["subtalker_dosample"] = _as_bool(controls.get("subtalker_dosample"), True)
        if "subtalker_top_k" in controls:
            kwargs["subtalker_top_k"] = _bounded_int(controls.get("subtalker_top_k"), 50, 1, 1000)
        if "subtalker_top_p" in controls:
            kwargs["subtalker_top_p"] = _bounded_float(controls.get("subtalker_top_p"), 1.0, 0.01, 1.0)
        if "subtalker_temperature" in controls:
            kwargs["subtalker_temperature"] = _bounded_float(controls.get("subtalker_temperature"), 0.9, 0.01, 4.0)
        if "max_new_tokens" in controls:
            kwargs["max_new_tokens"] = _bounded_int(controls.get("max_new_tokens"), 2048, 64, 16384)
        return kwargs

    def _write_wav(self, wav: Any, sample_rate: int, model_id: str) -> Path:
        try:
            import soundfile as sf
        except Exception as exc:  # pragma: no cover - physical worker only.
            raise Qwen3TTSAdapterError(f"soundfile is unavailable in the Qwen3-TTS worker: {exc}") from exc
        path = self.runtime_dir / f"qwen3_tts_{model_id}_{random.randint(100000, 999999)}.wav"
        sf.write(str(path), wav, int(sample_rate))
        if not path.exists() or path.stat().st_size <= 0:
            raise Qwen3TTSAdapterError("Qwen3-TTS did not produce a valid WAV output.")
        return path

    def generate(self, payload: dict[str, Any], *, progress: Callable[[int, str, str], None] | None = None) -> GeneratedAudio:
        text = str(payload.get("text") or payload.get("script") or "").strip()
        if not text:
            raise Qwen3TTSAdapterError("Text is required.")
        model_id = str(payload.get("model_id") or payload.get("model") or "").strip()
        if model_id not in MODEL_SPECS:
            raise Qwen3TTSAdapterError(f"Unsupported Qwen3-TTS model '{model_id}'.")
        spec = MODEL_SPECS[model_id]
        role = str(spec["role"])
        mode = str(payload.get("mode") or ("tts" if role == "custom_voice" else role)).strip().lower()
        expected = {"custom_voice": "tts", "base_clone": "voice_clone", "voice_design": "voice_design"}[role]
        if mode != expected:
            raise Qwen3TTSAdapterError(f"Model '{model_id}' requires mode '{expected}', not '{mode}'.")
        output_format = str(payload.get("output_format") or "wav").strip().lower()
        if output_format != "wav":
            raise Qwen3TTSAdapterError("Qwen3-TTS worker emits WAV only; other formats remain Neo Finish/output work.")
        controls = payload.get("provider_controls") if isinstance(payload.get("provider_controls"), dict) else {}
        language = normalize_language(controls.get("language") or payload.get("language") or "Auto")
        execution = payload.get("_neo_execution") if isinstance(payload.get("_neo_execution"), dict) else {}
        with self._lock:
            requested_device = self._normalize_requested_device(str(execution.get("device") or ""), execution.get("device_index"))
            if self._device and requested_device != self._device:
                self._unload_model()
            self._device = requested_device
            try:
                if progress:
                    progress(25, "loading_model", f"Loading {spec['label']}")
                model = self._load_model(model_id)
                if progress:
                    progress(55, "synthesizing", "Generating speech")
                seed = _bounded_int(controls.get("seed"), -1, -1, 2147483647)
                if seed >= 0:
                    random.seed(seed)
                    try:
                        torch = self._torch()
                        torch.manual_seed(seed)
                        if torch.cuda.is_available():
                            torch.cuda.manual_seed_all(seed)
                    except Exception:
                        pass
                kwargs = self._generation_kwargs(controls)
                non_streaming_mode = _as_bool(controls.get("non_streaming_mode"), True)
                warnings: list[str] = []
                try:
                    if role == "custom_voice":
                        speaker = normalize_speaker(controls.get("speaker") or payload.get("voice_id"))
                        instruct = str(controls.get("voice_instruction") or controls.get("instruct") or "").strip()
                        if not bool(spec.get("instruction_control")):
                            if instruct:
                                warnings.append("Instruction text was ignored because Qwen3-TTS 0.6B CustomVoice does not support instruct in the upstream runtime wrapper.")
                            instruct = ""
                        wavs, sample_rate = model.generate_custom_voice(
                            text=text,
                            language=language,
                            speaker=speaker,
                            instruct=instruct or None,
                            non_streaming_mode=non_streaming_mode,
                            **kwargs,
                        )
                    elif role == "base_clone":
                        reference = self._resolve_reference(payload)
                        x_vector_only = _as_bool(controls.get("x_vector_only_mode"), False)
                        ref_text = str(
                            controls.get("reference_transcript")
                            or controls.get("ref_text")
                            or payload.get("reference_transcript")
                            or payload.get("reference_text")
                            or ""
                        ).strip()
                        if not x_vector_only and not ref_text:
                            raise Qwen3TTSAdapterError(
                                "Qwen3-TTS ICL clone requires a reference transcript. Enable x_vector_only_mode for transcript-free speaker-embedding cloning."
                            )
                        wavs, sample_rate = model.generate_voice_clone(
                            text=text,
                            language=language,
                            ref_audio=str(reference),
                            ref_text=ref_text or None,
                            x_vector_only_mode=x_vector_only,
                            non_streaming_mode=non_streaming_mode,
                            **kwargs,
                        )
                    else:
                        instruct = str(
                            controls.get("voice_description")
                            or controls.get("voice_instruction")
                            or controls.get("instruct")
                            or payload.get("voice_description")
                            or ""
                        ).strip()
                        if not instruct:
                            raise Qwen3TTSAdapterError("Qwen3-TTS VoiceDesign requires a voice description/instruction.")
                        wavs, sample_rate = model.generate_voice_design(
                            text=text,
                            language=language,
                            instruct=instruct,
                            non_streaming_mode=non_streaming_mode,
                            **kwargs,
                        )
                except Qwen3TTSAdapterError:
                    raise
                except Exception as exc:  # pragma: no cover - upstream failures vary.
                    raise Qwen3TTSAdapterError(f"Qwen3-TTS generation failed: {exc}") from exc
                if not wavs:
                    raise Qwen3TTSAdapterError("Qwen3-TTS returned no waveform.")
                if progress:
                    progress(88, "writing_output", "Writing WAV output")
                path = self._write_wav(wavs[0], int(sample_rate), model_id)
                return GeneratedAudio(
                    path=path,
                    media_type="audio/wav",
                    model_id=model_id,
                    sample_rate=int(sample_rate),
                    warnings=warnings,
                )
            finally:
                # Keep model weights resident for fast reuse, but return transient
                # allocator blocks from inference/output work to CUDA.
                self._clear_transient_cuda_cache()
