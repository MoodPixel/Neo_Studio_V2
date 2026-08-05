from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from neo_app.admin.models.model_paths import load_model_paths

try:
    from neo_app.admin.image_node_manager import load_node_manager_settings
except Exception:  # pragma: no cover - optional local Admin subsystem.
    def load_node_manager_settings() -> dict[str, Any]:
        return {}


from neo_app.providers.comfy_model_paths import (
    resolve_comfy_extra_model_folders,
    resolve_comfy_extra_model_paths_config,
    resolve_comfy_model_paths,
)

FORGE_SHARED_MODEL_PATHS_SCHEMA_ID = "neo.provider.forge_shared_model_paths.v1"
SERVER_PATH_POLICY = "absolute_paths_server_side_only"

_ADETAILER_GENERIC_KEYS = {
    "ultralytics",
    "ultralytics_models",
    "yolo",
    "yolo_models",
    "detectors",
    "detector_models",
    "adetailer",
    "adetailer_models",
    "detailer",
    "detailer_models",
}
_ADETAILER_BBOX_KEYS = {
    "ultralytics_bbox",
    "yolo_bbox",
    "bbox_detectors",
    "adetailer_bbox",
    "detailer_bbox",
}
_ADETAILER_SEGM_KEYS = {
    "ultralytics_segm",
    "yolo_segm",
    "segm_detectors",
    "segmentation_detectors",
    "adetailer_segm",
    "adetailer_segmentation",
    "detailer_segm",
}
_ADETAILER_KEYS = _ADETAILER_GENERIC_KEYS | _ADETAILER_BBOX_KEYS | _ADETAILER_SEGM_KEYS
_IP_ADAPTER_KEYS = {
    "ipadapter",
    "ip_adapter",
    "ip_adapter_models",
    "ipadapter_models",
}
_CLIP_VISION_KEYS = {
    "clip_vision",
    "clip_visions",
    "clipvision",
    "clip_vision_models",
}
_LORA_KEYS = {
    "lora",
    "loras",
    "lora_models",
}
_IP_ADAPTER_EXTS = {".safetensors", ".bin", ".pt", ".pth"}
_CLIP_VISION_EXTS = {".safetensors", ".bin", ".pt", ".pth"}
_LORA_EXTS = {".safetensors", ".ckpt", ".pt"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _path(value: Any) -> Path | None:
    text = str(value or "").strip().strip('"').strip("'")
    if not text or "\x00" in text:
        return None
    try:
        return Path(text).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None


def _path_key(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        path = path.resolve(strict=False)
    except (OSError, RuntimeError):
        pass
    return str(path).replace("\\", "/").rstrip("/").casefold()


def _split_pipe_paths(value: Any) -> list[Path]:
    paths: list[Path] = []
    for raw in str(value or "").split("|"):
        path = _path(raw)
        if path:
            paths.append(path)
    return paths


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = _path_key(path)
        if key and key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _shared_comfy_details(
    model_paths: Mapping[str, Any],
    node_manager_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Deliberately resolve through the Comfy path authority even when the active
    # generation profile is Forge. The central library remains Comfy-shaped;
    # Forge Neo references it natively rather than receiving a duplicate tree.
    return resolve_comfy_model_paths(
        {"provider_id": "comfyui"},
        model_paths=model_paths,
        node_manager_settings=node_manager_settings or {},
    )


def _folder_has_adetailer_pt(folder: Path) -> bool:
    try:
        if not folder.is_dir():
            return False
        return next(folder.rglob("*.pt"), None) is not None
    except (OSError, RuntimeError):
        return False


def _path_covers(configured: Path, detector_folder: Path) -> bool:
    """Return True when ADetailer's recursive configured path covers a folder."""

    try:
        configured_resolved = configured.resolve(strict=False)
        detector_resolved = detector_folder.resolve(strict=False)
        detector_resolved.relative_to(configured_resolved)
        return True
    except (OSError, RuntimeError, ValueError):
        return _path_key(configured) == _path_key(detector_folder)


def _scan_model_names(folders: list[Path], extensions: set[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        try:
            candidates = sorted(folder.rglob("*"), key=lambda item: str(item).casefold()) if folder.is_dir() else []
        except OSError:
            candidates = []
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.casefold() not in extensions:
                continue
            name = candidate.name.strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _scan_relative_model_names(folders: list[Path], extensions: set[str]) -> list[str]:
    """Return portable provider catalog names without exposing root paths."""

    names: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        try:
            candidates = sorted(folder.rglob("*"), key=lambda item: str(item).casefold()) if folder.is_dir() else []
        except OSError:
            candidates = []
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.casefold() not in extensions:
                continue
            try:
                name = candidate.relative_to(folder).as_posix().strip()
            except (OSError, ValueError):
                name = candidate.name.strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _scan_adetailer_pt_names(folders: list[Path]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        try:
            candidates = sorted(folder.rglob("*.pt"), key=lambda item: str(item).casefold()) if folder.is_dir() else []
        except OSError:
            candidates = []
        for candidate in candidates:
            name = candidate.name.strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names



def forge_cmd_flags_probe_policy(
    *,
    model_paths: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether Forge's ``/sdapi/v1/cmd-flags`` is safe to probe.

    Forge Neo currently declares ``--forge-ref-comfy-yaml`` as ``Path`` while
    the public SDAPI response model requires a string. A configured shared YAML
    can therefore make the provider endpoint raise a FastAPI response validation
    error. Neo skips that diagnostic endpoint and uses its local Admin model-path
    authority until Forge serializes the flag safely. No absolute path is exposed.
    """

    if model_paths is None:
        try:
            model_paths = load_model_paths(create=False)
        except Exception:  # pragma: no cover - local settings are optional.
            model_paths = {}
    try:
        node_manager_settings = load_node_manager_settings()
    except Exception:  # pragma: no cover - local settings are optional.
        node_manager_settings = {}
    details = _shared_comfy_details(_mapping(model_paths), _mapping(node_manager_settings))
    resolution = resolve_comfy_extra_model_paths_config(details)
    yaml_configured = isinstance(resolution.get("yaml_path"), Path)
    return {
        "probe": not yaml_configured,
        "status": "probe" if not yaml_configured else "skipped_provider_path_serialization_guard",
        "local_reference_fallback": yaml_configured,
        "shared_yaml_configured": yaml_configured,
        "reason": (
            "Forge command-flag diagnostics are safe to probe."
            if not yaml_configured
            else "Skipped Forge command-flag diagnostics because the provider may return forge_ref_comfy_yaml as a non-serializable Path value."
        ),
    }

def build_forge_shared_model_paths_capability(
    *,
    command_flags: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
    model_paths: Mapping[str, Any] | None = None,
    allow_local_reference_fallback: bool = False,
    command_flags_status: str = "available",
) -> dict[str, Any]:
    """Build a path-free Forge view of Neo's central Comfy-style model library.

    Forge Neo owns actual loading. Neo verifies either the same
    ``extra_model_paths.yaml`` or a ``--model-ref`` root matching Neo's shared
    model authority, then checks that ADetailer's native model directories
    cover the shared detector folders.
    """

    if model_paths is None:
        try:
            model_paths = load_model_paths(create=False)
        except Exception:  # pragma: no cover - local settings are optional.
            model_paths = {}
    model_paths = _mapping(model_paths)
    try:
        node_manager_settings = load_node_manager_settings()
    except Exception:  # pragma: no cover - local settings are optional.
        node_manager_settings = {}
    details = _shared_comfy_details(model_paths, _mapping(node_manager_settings))
    yaml_resolution = resolve_comfy_extra_model_paths_config(details)
    yaml_path = yaml_resolution.get("yaml_path") if isinstance(yaml_resolution.get("yaml_path"), Path) else None
    yaml_diagnostics = _mapping(yaml_resolution.get("diagnostics"))

    flags = _mapping(command_flags)
    forge_yaml_path = _path(flags.get("forge_ref_comfy_yaml") or flags.get("forge-ref-comfy-yaml"))
    forge_yaml_active = bool(forge_yaml_path)
    forge_yaml_matches = bool(yaml_path and forge_yaml_path and _path_key(yaml_path) == _path_key(forge_yaml_path))

    detector_resolution = resolve_comfy_extra_model_folders(details, categories=_ADETAILER_KEYS)

    # The shared authority is Comfy-shaped, so preserve its conventional native
    # detector locations as well as any detector-specific extra_model_paths.yaml
    # entries. Forge ADetailer can point at these directories through its own
    # ``ad_extra_models_dir`` setting and recursively discovers ``*.pt`` files.
    shared_models_root = next(
        (
            candidate
            for candidate in (
                _path(details.get("resolved_models_root")),
                _path(details.get("models_root")),
                _path(details.get("configured_models_root")),
            )
            if candidate is not None
        ),
        None,
    )
    forge_model_ref = _path(flags.get("model_ref") or flags.get("model-ref"))
    forge_model_ref_active = bool(forge_model_ref)
    forge_model_ref_matches = bool(
        shared_models_root
        and forge_model_ref
        and _path_key(shared_models_root) == _path_key(forge_model_ref)
    )
    local_reference_fallback = bool(
        allow_local_reference_fallback
        and yaml_path
        and shared_models_root
    )
    shared_reference_ready = bool(forge_yaml_matches or forge_model_ref_matches or local_reference_fallback)
    reference_mode = (
        "forge_ref_comfy_yaml"
        if forge_yaml_matches
        else "model_ref"
        if forge_model_ref_matches
        else "neo_admin_local_authority"
        if local_reference_fallback
        else ""
    )

    native_detector_folders = []
    if shared_models_root is not None:
        native_detector_folders = [
            shared_models_root / "adetailer",
            shared_models_root / "ultralytics",
        ]

    detector_folders = _dedupe_paths([
        *native_detector_folders,
        *(folder for folder in detector_resolution.get("folders", []) if isinstance(folder, Path)),
    ])
    existing_detector_folders = [folder for folder in detector_folders if folder.exists() and folder.is_dir()]
    active_detector_folders = [folder for folder in existing_detector_folders if _folder_has_adetailer_pt(folder)]
    detector_names = _scan_adetailer_pt_names(active_detector_folders)

    ip_adapter_resolution = resolve_comfy_extra_model_folders(details, categories=_IP_ADAPTER_KEYS)
    clip_vision_resolution = resolve_comfy_extra_model_folders(details, categories=_CLIP_VISION_KEYS)
    lora_resolution = resolve_comfy_extra_model_folders(details, categories=_LORA_KEYS)
    native_ip_adapter_folders: list[Path] = []
    native_clip_vision_folders: list[Path] = []
    native_lora_folders: list[Path] = []
    if shared_models_root is not None:
        native_ip_adapter_folders = [shared_models_root / "ipadapter", shared_models_root / "ip_adapter"]
        native_clip_vision_folders = [shared_models_root / "clip_vision", shared_models_root / "clip_visions"]
        native_lora_folders = [shared_models_root / "loras", shared_models_root / "lora"]
    ip_adapter_folders = _dedupe_paths([
        *native_ip_adapter_folders,
        *(folder for folder in ip_adapter_resolution.get("folders", []) if isinstance(folder, Path)),
    ])
    clip_vision_folders = _dedupe_paths([
        *native_clip_vision_folders,
        *(folder for folder in clip_vision_resolution.get("folders", []) if isinstance(folder, Path)),
    ])
    lora_folders = _dedupe_paths([
        *native_lora_folders,
        *(folder for folder in lora_resolution.get("folders", []) if isinstance(folder, Path)),
    ])
    active_ip_adapter_folders = [folder for folder in ip_adapter_folders if folder.exists() and folder.is_dir()]
    active_clip_vision_folders = [folder for folder in clip_vision_folders if folder.exists() and folder.is_dir()]
    active_lora_folders = [folder for folder in lora_folders if folder.exists() and folder.is_dir()]
    ip_adapter_names = _scan_model_names(active_ip_adapter_folders, _IP_ADAPTER_EXTS)
    clip_vision_names = _scan_model_names(active_clip_vision_folders, _CLIP_VISION_EXTS)
    lora_names = _scan_relative_model_names(active_lora_folders, _LORA_EXTS)

    forge_adetailer_paths = _split_pipe_paths(_mapping(options).get("ad_extra_models_dir"))
    # Forge ADetailer always includes <Forge models_path>/adetailer in its native
    # startup model map. When --model-ref points at the shared root, that default
    # directory is already active even when ad_extra_models_dir is empty.
    if forge_model_ref:
        forge_adetailer_paths.append(forge_model_ref / "adetailer")
    elif local_reference_fallback and shared_models_root:
        # When Forge's command-flags endpoint is guarded because of its Path
        # serialization bug, retain the configured shared root's native
        # ADetailer directory as the local authority fallback.
        forge_adetailer_paths.append(shared_models_root / "adetailer")
    forge_adetailer_paths = _dedupe_paths(forge_adetailer_paths)
    matching_detector_folders = [
        folder
        for folder in active_detector_folders
        if any(_path_covers(configured, folder) for configured in forge_adetailer_paths)
    ]
    covered_detector_names = _scan_adetailer_pt_names(matching_detector_folders)
    covered_detector_keys = {name.casefold() for name in covered_detector_names}
    uncovered_detector_names = [name for name in detector_names if name.casefold() not in covered_detector_keys]
    detector_dirs_ready = bool(active_detector_folders) and len(matching_detector_folders) == len(active_detector_folders)

    if forge_yaml_matches:
        status = "ready"
        reason = "Forge Neo is referencing Neo Studio's shared Comfy extra-model-path configuration."
    elif forge_model_ref_matches:
        status = "ready"
        reason = "Forge Neo --model-ref matches Neo Studio's shared models root."
    elif local_reference_fallback:
        status = "ready"
        reason = "Neo Studio is using its configured shared model-path authority because Forge command-flag diagnostics were skipped for the provider Path serialization bug."
    elif forge_yaml_active:
        status = "forge_reference_mismatch"
        reason = "Forge Neo is referencing a different Comfy YAML than Neo Studio's shared model-path authority."
    elif forge_model_ref_active:
        status = "forge_model_ref_mismatch"
        reason = "Forge Neo --model-ref does not match Neo Studio's shared models root."
    elif not yaml_path:
        status = "not_configured"
        reason = "No shared Comfy extra_model_paths.yaml or matching shared models root is configured in Admin Models."
    else:
        status = "forge_restart_required"
        reason = "Forge Neo is not reporting a matching --forge-ref-comfy-yaml or --model-ref for this process."

    adetailer_status = "not_discovered"
    adetailer_reason = "No shared ADetailer/Ultralytics .pt models were discovered."
    if detector_names and not covered_detector_names:
        adetailer_status = "forge_adetailer_paths_required"
        adetailer_reason = "Shared detector models are visible to Neo, but none are covered by Forge ADetailer's active native or extra-model directories."
    elif detector_names and not detector_dirs_ready:
        adetailer_status = "partial_coverage"
        adetailer_reason = "Forge ADetailer can load the covered detector subset; additional shared detector folders remain outside its active native or extra-model directories."
    elif detector_names and detector_dirs_ready:
        adetailer_status = "ready"
        adetailer_reason = "Forge ADetailer active model directories cover the shared detector library. Restart Forge after changing model-directory settings so ADetailer's startup model map is rebuilt."

    return {
        "schema_id": FORGE_SHARED_MODEL_PATHS_SCHEMA_ID,
        "status": status,
        "available": status == "ready",
        "reason": reason,
        "path_policy": SERVER_PATH_POLICY,
        "shared_yaml_configured": bool(yaml_path),
        "shared_yaml_config_files_found": int(yaml_diagnostics.get("config_files_found") or 0),
        "forge_yaml_reference_active": forge_yaml_active,
        "forge_yaml_reference_matches": forge_yaml_matches,
        "forge_model_ref_active": forge_model_ref_active,
        "forge_model_ref_matches_models_root": forge_model_ref_matches,
        "reference_mode": reference_mode,
        "runtime_reference_verified": bool(forge_yaml_matches or forge_model_ref_matches),
        "local_reference_fallback_active": local_reference_fallback,
        "command_flags_status": str(command_flags_status or "unknown"),
        "native_forge_flag": "--forge-ref-comfy-yaml",
        "native_forge_flags": ["--forge-ref-comfy-yaml", "--model-ref"],
        "loras": {
            "available": bool(shared_reference_ready and lora_names),
            "shared_path_reference_ready": shared_reference_ready,
            "shared_models_discovered": bool(lora_names),
            "shared_model_count": len(lora_names),
            "shared_model_names": lora_names,
            "catalog_authority": "forge_native_catalog_plus_verified_shared_paths",
            "path_policy": SERVER_PATH_POLICY,
            "reason": (
                "The selected Forge process references Neo Studio's shared LoRA library; only portable catalog names are exposed."
                if shared_reference_ready and lora_names
                else "No verified shared Forge LoRA catalog is available. " + reason
            ),
        },
        "ip_adapter": {
            "shared_path_reference_ready": shared_reference_ready,
            "shared_models_discovered": bool(ip_adapter_names),
            "shared_model_count": len(ip_adapter_names),
            "shared_model_names": ip_adapter_names,
            "shared_encoders_discovered": bool(clip_vision_names),
            "shared_encoder_count": len(clip_vision_names),
            "shared_encoder_names": clip_vision_names,
            "catalog_authority": "forge_controlnet_plus_verified_shared_paths",
            "reason": (
                "Forge Integrated ControlNet remains the execution contract; verified shared IP-Adapter and CLIP-Vision files may supplement an incomplete public ControlNet model catalog."
                if shared_reference_ready
                else reason
            ),
        },
        "adetailer": {
            "status": adetailer_status,
            "shared_models_discovered": bool(detector_names),
            "shared_model_count": len(detector_names),
            "shared_model_names": detector_names,
            "covered_model_names": covered_detector_names,
            "covered_model_count": len(covered_detector_names),
            "uncovered_model_names": uncovered_detector_names,
            "uncovered_model_count": len(uncovered_detector_names),
            "shared_detector_folder_count": len(active_detector_folders),
            "forge_extra_folder_count": len(_split_pipe_paths(_mapping(options).get("ad_extra_models_dir"))),
            "forge_default_model_ref_adetailer_active": bool(forge_model_ref),
            "local_default_adetailer_fallback_active": bool(local_reference_fallback and shared_models_root),
            "matching_detector_folder_count": len(matching_detector_folders),
            "extra_model_dirs_ready": detector_dirs_ready,
            "requires_restart_after_path_change": True,
            "reason": adetailer_reason,
        },
    }
