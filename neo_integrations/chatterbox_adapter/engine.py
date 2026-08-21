from __future__ import annotations

import gc
import importlib.util
import os
import random
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ADAPTER_MODEL_TURBO = "chatterbox_turbo"
ADAPTER_MODEL_MULTILINGUAL = "chatterbox_multilingual"
SUPPORTED_MODELS = (ADAPTER_MODEL_TURBO, ADAPTER_MODEL_MULTILINGUAL)
SUPPORTED_LANGUAGES = {
    "ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko",
    "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh",
}


class ChatterboxAdapterError(RuntimeError):
    """Normalized adapter/runtime error safe to surface through Neo."""


@dataclass
class GeneratedAudio:
    path: Path
    media_type: str
    model_id: str
    sample_rate: int
    warnings: list[str]


def perth_watermarker_status() -> dict[str, Any]:
    """Return whether Chatterbox's required PerTh watermarker can be constructed.

    PerTh currently imports ``pkg_resources``. Setuptools 82+ removed that
    module, and affected PerTh builds expose ``PerthImplicitWatermarker`` as
    ``None`` instead of raising a useful import error. Keep this preflight
    explicit so Neo reports the dependency fault before model construction.
    """
    if importlib.util.find_spec("perth") is None:
        return {"installed": False, "callable": False, "error": "perth_not_installed"}
    try:
        import perth
    except Exception as exc:  # pragma: no cover - physical adapter environment.
        return {"installed": True, "callable": False, "error": str(exc)}
    candidate = getattr(perth, "PerthImplicitWatermarker", None)
    return {
        "installed": True,
        "callable": callable(candidate),
        "error": "" if callable(candidate) else "PerthImplicitWatermarker is unavailable",
    }


def dependency_status() -> dict[str, Any]:
    perth_status = perth_watermarker_status()
    return {
        "chatterbox_tts": importlib.util.find_spec("chatterbox") is not None,
        "torch": importlib.util.find_spec("torch") is not None,
        "torchaudio": importlib.util.find_spec("torchaudio") is not None,
        "perth": perth_status["installed"],
        "perth_watermarker": perth_status["callable"],
        "perth_error": perth_status["error"],
        "ffmpeg": bool(shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")),
    }


def split_text(text: str, max_chars: int) -> list[str]:
    text = " ".join(str(text or "").split())
    if not text:
        return []
    max_chars = max(160, min(2400, int(max_chars or 650)))
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    break_chars = ".!?;,:"
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        split_at = max(window.rfind(char) for char in break_chars)
        if split_at < max_chars // 3:
            split_at = window.rfind(" ")
        if split_at < max_chars // 4:
            split_at = max_chars
        else:
            split_at += 1
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_within(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        allowed = root.resolve()
    except OSError:
        return False
    return resolved == allowed or allowed in resolved.parents


class ChatterboxEngine:
    """Lazy single-model Chatterbox runtime.

    The adapter intentionally keeps a single model resident at a time. This avoids
    retaining Turbo and Multilingual checkpoints simultaneously on smaller GPUs.
    """

    def __init__(self, *, neo_root: Path | None = None, runtime_dir: Path | None = None):
        self.neo_root = (neo_root or Path(os.getenv("NEO_CHATTERBOX_NEO_ROOT") or Path.cwd())).resolve()
        self.reference_root = (self.neo_root / "neo_data" / "outputs" / "voice" / "reference").resolve()
        self.runtime_dir = (runtime_dir or self.neo_root / "neo_data" / "runtime" / "chatterbox_adapter").resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._model: Any = None
        self._model_id = ""
        self._model_source_kind = ""
        self._model_source_path = ""
        self._device = ""
        self._lock = threading.RLock()

    @property
    def loaded_model_id(self) -> str:
        return self._model_id

    @property
    def model_source_kind(self) -> str:
        return self._model_source_kind

    @property
    def model_source_path(self) -> str:
        return self._model_source_path

    @property
    def device(self) -> str:
        return self._device or "not_initialized"

    def _torch(self):
        try:
            import torch
        except Exception as exc:  # pragma: no cover - exercised on physical backend host.
            raise ChatterboxAdapterError(f"PyTorch is unavailable in the Chatterbox adapter environment: {exc}") from exc
        return torch

    def _select_device(self, torch_module: Any) -> str:
        forced = str(os.getenv("NEO_CHATTERBOX_DEVICE") or "auto").strip().lower()
        if forced and forced != "auto":
            if forced.startswith("cuda") and not torch_module.cuda.is_available():
                raise ChatterboxAdapterError("NEO_CHATTERBOX_DEVICE requested CUDA but CUDA is not available to PyTorch.")
            return forced
        if torch_module.cuda.is_available():
            return "cuda"
        if getattr(torch_module.backends, "mps", None) and torch_module.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _normalize_requested_device(self, device: str = "", device_index: int | None = None) -> str:
        requested = str(device or "").strip().lower()
        if not requested:
            return ""
        if requested == "cuda" and device_index is not None:
            requested = f"cuda:{max(0, int(device_index))}"
        if requested not in {"cpu", "mps"} and not requested.startswith("cuda"):
            raise ChatterboxAdapterError(f"Unsupported Chatterbox execution device '{requested}'.")
        torch = self._torch()
        if requested.startswith("cuda") and not torch.cuda.is_available():
            raise ChatterboxAdapterError("Neo Voice Engine requested CUDA but CUDA is not available to the Chatterbox worker.")
        if requested == "mps" and not (getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()):
            raise ChatterboxAdapterError("Neo Voice Engine requested MPS but MPS is not available to the Chatterbox worker.")
        return requested

    def apply_execution_hint(self, hint: dict[str, Any] | None) -> str:
        data = hint if isinstance(hint, dict) else {}
        requested = self._normalize_requested_device(str(data.get("device") or ""), data.get("device_index"))
        with self._lock:
            if requested and requested != self._device:
                self._unload_model()
                self._device = requested
            return self.device

    def prepare_model(self, model_id: str, *, device: str = "", device_index: int | None = None) -> dict[str, Any]:
        """Prepare one Chatterbox model for gateway-managed execution.

        VO-E5 keeps the physical Chatterbox environment isolated. The gateway may
        select CUDA or CPU during admission, and this method makes that selection
        authoritative before the worker loads model weights.
        """
        with self._lock:
            requested = self._normalize_requested_device(device, device_index)
            if requested and requested != self._device:
                self._unload_model()
                self._device = requested
            model = self._load_model(model_id)
            return {
                "supported": True,
                "state": "resident",
                "model_id": self._model_id,
                "device": self.device,
                "source_kind": self._model_source_kind,
                "source_path": self._model_source_path,
                "sample_rate": int(getattr(model, "sr", 24000) or 24000),
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
                    "message": "Requested model is not the currently resident Chatterbox model.",
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
        requested = str(model_id or "").strip()
        if requested not in SUPPORTED_MODELS:
            raise ChatterboxAdapterError(f"Unsupported Chatterbox model '{requested}'.")
        with self._lock:
            resident = self._model is not None and self._model_id == requested
            return {
                "supported": True,
                "model_id": requested,
                "state": "resident" if resident else "unloaded",
                "loaded_model_id": self._model_id,
                "device": self.device,
                "source_kind": self._model_source_kind if resident else "",
                "source_path": self._model_source_path if resident else "",
                "evictable": True,
            }

    def runtime_model_status(self, model_id: str) -> dict[str, Any]:
        requested = str(model_id or "").strip()
        if requested not in SUPPORTED_MODELS:
            raise ChatterboxAdapterError(f"Unsupported Chatterbox model '{requested}'.")
        try:
            from neo_voice_engine.chatterbox_runtime_resolver import probe_chatterbox_runtime_model

            return probe_chatterbox_runtime_model(project_root=self.neo_root, model_id=requested)
        except ChatterboxAdapterError:
            raise
        except Exception as exc:
            return {
                "model_id": requested,
                "state": "not_installed",
                "installed": False,
                "source_kind": "",
                "source_path": "",
                "message": f"Chatterbox runtime resolver failed ({type(exc).__name__}).",
                "catalog_error": "runtime_resolver_failed",
            }

    def _unload_model(self) -> None:
        if self._model is None:
            return
        self._model = None
        self._model_id = ""
        self._model_source_kind = ""
        self._model_source_path = ""
        gc.collect()
        try:
            torch = self._torch()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def _load_model(self, model_id: str, progress: Callable[[int, str, str], None] | None = None):
        if model_id not in SUPPORTED_MODELS:
            raise ChatterboxAdapterError(f"Unsupported Chatterbox model '{model_id}'.")
        if self._model is not None and self._model_id == model_id:
            return self._model

        local_only = str(os.getenv("NEO_CHATTERBOX_LOCAL_ONLY") or "1").strip().lower() not in {"0", "false", "no", "off"}
        runtime = self.runtime_model_status(model_id)
        source_path = str(runtime.get("source_path") or "").strip()
        source_kind = str(runtime.get("source_kind") or "").strip()
        if local_only and (runtime.get("state") != "installed" or not source_path):
            raise ChatterboxAdapterError(
                f"{model_id} is not installed for local Voice execution. "
                "Install or repair it in Neo Studio Admin → Models. Managed Chatterbox generation never downloads model weights."
            )

        if progress:
            label = f"Loading {model_id} from verified local Hugging Face cache" if source_path else f"Loading {model_id} in developer remote-fallback mode"
            progress(12, "loading_model", label)
        self._unload_model()
        perth_status = perth_watermarker_status()
        if not perth_status["callable"]:
            raise ChatterboxAdapterError(
                "PerTh watermarker is unavailable. Chatterbox currently requires the legacy "
                "pkg_resources module, which was removed in setuptools 82+. "
                "Rerun setup_chatterbox_backend.bat to install the supported setuptools<82 pin."
            )
        torch = self._torch()
        if not self._device:
            self._device = self._select_device(torch)
        elif self._device.startswith("cuda") and not torch.cuda.is_available():
            raise ChatterboxAdapterError("Chatterbox was prepared for CUDA, but CUDA is no longer available to PyTorch.")
        try:
            if model_id == ADAPTER_MODEL_TURBO:
                from chatterbox.tts_turbo import ChatterboxTurboTTS

                if source_path:
                    model = ChatterboxTurboTTS.from_local(Path(source_path), self._device, nano=False)
                elif not local_only:  # explicit developer escape hatch only
                    model = ChatterboxTurboTTS.from_pretrained(device=self._device)
                else:  # pragma: no cover - guarded above
                    raise ChatterboxAdapterError("Chatterbox Turbo local snapshot is unavailable.")
            else:
                from chatterbox.mtl_tts import ChatterboxMultilingualTTS

                if source_path:
                    model = ChatterboxMultilingualTTS.from_local(Path(source_path), self._device, t3_model="v3")
                elif not local_only:  # explicit developer escape hatch only
                    model = ChatterboxMultilingualTTS.from_pretrained(device=self._device, t3_model="v3")
                else:  # pragma: no cover - guarded above
                    raise ChatterboxAdapterError("Chatterbox Multilingual local snapshot is unavailable.")
        except ChatterboxAdapterError:
            self._unload_model()
            raise
        except Exception as exc:  # pragma: no cover - physical model load.
            self._unload_model()
            raise ChatterboxAdapterError(f"Failed to load {model_id} from the local model snapshot: {exc}") from exc
        self._model = model
        self._model_id = model_id
        self._model_source_kind = source_kind or ("developer_remote_fallback" if not source_path else "huggingface_cache_snapshot")
        self._model_source_path = source_path
        return model

    def _resolve_reference(self, request_payload: dict[str, Any]) -> Path | None:
        mode = str(request_payload.get("mode") or "tts").strip().lower()
        reference = request_payload.get("reference_audio") if isinstance(request_payload.get("reference_audio"), dict) else {}
        raw = str(reference.get("local_path") or "").strip()
        if mode != "voice_clone" and not raw:
            return None
        if not raw:
            raise ChatterboxAdapterError("Voice clone mode requires Neo-owned reference audio.")
        if reference.get("authorization_confirmed") is not True:
            raise ChatterboxAdapterError("Voice clone request is missing Neo authorization confirmation.")
        qc_status = str(reference.get("qc_status") or "").strip().lower()
        if qc_status not in {"usable", "usable_with_warnings"}:
            raise ChatterboxAdapterError("Voice clone reference did not pass Neo reference QC.")
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ChatterboxAdapterError("Voice clone reference file is unavailable on the adapter host.")
        allow_external = str(os.getenv("NEO_CHATTERBOX_ALLOW_EXTERNAL_REFERENCE") or "").strip().lower() in {"1", "true", "yes"}
        if not allow_external and not _is_within(path, self.reference_root):
            raise ChatterboxAdapterError("Voice clone reference is outside Neo-owned Voice reference storage.")
        return path

    def _seed(self, seed: int, torch_module: Any) -> None:
        if seed < 0:
            return
        torch_module.manual_seed(seed)
        if torch_module.cuda.is_available():
            torch_module.cuda.manual_seed(seed)
            torch_module.cuda.manual_seed_all(seed)
        random.seed(seed)
        try:
            import numpy as np

            np.random.seed(seed)
        except Exception:
            pass

    def _postprocess(self, source_wav: Path, *, speaking_rate: float, output_format: str) -> tuple[Path, str]:
        output_format = str(output_format or "wav").strip().lower()
        if output_format not in {"wav", "mp3"}:
            raise ChatterboxAdapterError("Chatterbox adapter currently supports WAV or MP3 output.")
        needs_ffmpeg = abs(speaking_rate - 1.0) > 0.001 or output_format == "mp3"
        if not needs_ffmpeg:
            return source_wav, "audio/wav"
        ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if not ffmpeg:
            raise ChatterboxAdapterError("FFmpeg is required for non-1.0 speaking rate or MP3 output. Use WAV at 1.0x or install FFmpeg.")
        target = source_wav.with_suffix(f".final.{output_format}")
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_wav)]
        if abs(speaking_rate - 1.0) > 0.001:
            command += ["-filter:a", f"atempo={speaking_rate:.4f}"]
        if output_format == "mp3":
            command += ["-codec:a", "libmp3lame", "-q:a", "2"]
        command.append(str(target))
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not target.exists():
            detail = (completed.stderr or completed.stdout or "FFmpeg post-processing failed.").strip()
            raise ChatterboxAdapterError(detail[-1200:])
        try:
            source_wav.unlink(missing_ok=True)
        except OSError:
            pass
        return target, "audio/mpeg" if output_format == "mp3" else "audio/wav"

    def generate(self, request_payload: dict[str, Any], *, progress: Callable[[int, str, str], None] | None = None) -> GeneratedAudio:
        text = str(request_payload.get("text") or request_payload.get("script") or request_payload.get("script_body") or "").strip()
        if not text:
            raise ChatterboxAdapterError("Text is required for Chatterbox generation.")
        model_id = str(request_payload.get("model_id") or request_payload.get("model") or ADAPTER_MODEL_TURBO).strip()
        if model_id in {"", "provider_default"}:
            model_id = ADAPTER_MODEL_TURBO
        language = str(request_payload.get("language") or "en").strip().lower().split("-", 1)[0]
        if language in {"", "auto"}:
            language = "en"
        if model_id == ADAPTER_MODEL_TURBO and language != "en":
            raise ChatterboxAdapterError("Chatterbox Turbo is English-only. Select Chatterbox Multilingual V3 for other languages.")
        if model_id == ADAPTER_MODEL_MULTILINGUAL and language not in SUPPORTED_LANGUAGES:
            raise ChatterboxAdapterError(f"Language '{language}' is not supported by Chatterbox Multilingual V3.")

        reference_path = self._resolve_reference(request_payload)
        provider_controls = request_payload.get("provider_controls") if isinstance(request_payload.get("provider_controls"), dict) else {}
        seed = _int(provider_controls.get("seed"), -1)
        split_long = bool(request_payload.get("split_long_text", True))
        max_chunk_chars = _int(request_payload.get("max_chunk_chars"), 650)
        chunks = split_text(text, max_chunk_chars) if split_long else [text]
        if not chunks:
            raise ChatterboxAdapterError("Text is empty after normalization.")
        speaking_rate = _clamp_float(request_payload.get("speaking_rate"), 1.0, 0.5, 2.0)
        output_format = str(request_payload.get("output_format") or "wav").strip().lower()
        warnings: list[str] = []
        if model_id == ADAPTER_MODEL_TURBO and "expression_strength" in provider_controls:
            warnings.append("Expression Strength is not native to Chatterbox Turbo and was not applied.")
        if "reference_strength" in provider_controls:
            warnings.append("Reference Strength has no direct Chatterbox parameter and was not remapped.")

        with self._lock:
            self.apply_execution_hint(request_payload.get("_neo_execution") if isinstance(request_payload.get("_neo_execution"), dict) else {})
            model = self._load_model(model_id, progress=progress)
            torch = self._torch()
            self._seed(seed, torch)
            waveforms = []
            sample_rate = int(getattr(model, "sr", 24000) or 24000)
            for index, chunk in enumerate(chunks):
                if progress:
                    percent = 35 + int(50 * (index / max(1, len(chunks))))
                    progress(min(84, percent), "synthesizing", f"Synthesizing chunk {index + 1} of {len(chunks)}")
                prompt = str(reference_path) if reference_path is not None and index == 0 else None
                try:
                    if model_id == ADAPTER_MODEL_TURBO:
                        kwargs: dict[str, Any] = {
                            "audio_prompt_path": prompt,
                            "temperature": _clamp_float(provider_controls.get("temperature"), 0.8, 0.05, 5.0),
                            "top_p": _clamp_float(provider_controls.get("top_p"), 0.95, 0.05, 1.0),
                            "repetition_penalty": _clamp_float(provider_controls.get("repetition_penalty"), 1.2, 1.0, 2.0),
                            "top_k": max(1, _int(provider_controls.get("top_k"), 1000)),
                            "norm_loudness": bool(provider_controls.get("norm_loudness", True)),
                        }
                        wav = model.generate(chunk, **kwargs)
                    else:
                        kwargs = {
                            "language_id": language,
                            "audio_prompt_path": prompt,
                            "exaggeration": _clamp_float(provider_controls.get("exaggeration", provider_controls.get("expression_strength")), 0.5, 0.0, 2.0),
                            "cfg_weight": _clamp_float(provider_controls.get("cfg_weight"), 0.5, 0.0, 1.0),
                            "temperature": _clamp_float(provider_controls.get("temperature"), 0.8, 0.05, 5.0),
                            "repetition_penalty": _clamp_float(provider_controls.get("repetition_penalty"), 1.2, 1.0, 2.0),
                            "min_p": _clamp_float(provider_controls.get("min_p"), 0.05, 0.0, 1.0),
                            "top_p": _clamp_float(provider_controls.get("top_p"), 1.0, 0.05, 1.0),
                        }
                        wav = model.generate(chunk, **kwargs)
                except AssertionError as exc:
                    message = str(exc) or "Chatterbox rejected the generation request."
                    raise ChatterboxAdapterError(message) from exc
                except Exception as exc:  # pragma: no cover - physical synthesis.
                    raise ChatterboxAdapterError(f"Chatterbox synthesis failed: {exc}") from exc
                waveforms.append(wav.detach().cpu())
                if index + 1 < len(chunks):
                    waveforms.append(torch.zeros((1, max(1, int(sample_rate * 0.12))), dtype=wav.dtype))

            if progress:
                progress(88, "finalizing", "Writing generated audio")
            combined = torch.cat(waveforms, dim=-1) if len(waveforms) > 1 else waveforms[0]
            try:
                import torchaudio
            except Exception as exc:  # pragma: no cover
                raise ChatterboxAdapterError(f"torchaudio is unavailable: {exc}") from exc
            fd, raw_name = tempfile.mkstemp(prefix="chatterbox_", suffix=".wav", dir=self.runtime_dir)
            os.close(fd)
            raw_path = Path(raw_name)
            try:
                torchaudio.save(str(raw_path), combined, sample_rate)
                final_path, media_type = self._postprocess(raw_path, speaking_rate=speaking_rate, output_format=output_format)
            except Exception:
                raw_path.unlink(missing_ok=True)
                raise
            return GeneratedAudio(path=final_path, media_type=media_type, model_id=model_id, sample_rate=sample_rate, warnings=warnings)
