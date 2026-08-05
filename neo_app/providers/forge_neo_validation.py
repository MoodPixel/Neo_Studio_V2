from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from neo_app.models.forge_neo_route_catalog import (
    FORGE_FAMILY_POLICIES,
    FORGE_PROVIDER_LOADER_ID,
    forge_route_authority_payload,
    resolve_forge_route,
)
from neo_app.providers.forge_neo_compile import compile_forge_neo_job, redact_forge_compile_payload
from neo_app.providers.forge_neo_loader_translation import translate_forge_loader_bundle
from neo_app.providers.forge_neo_model_classification import (
    build_forge_live_model_classification,
    build_forge_live_route_intersection,
)
from neo_app.providers.forge_neo_ux_gating import build_forge_ux_gating_policy, forge_route_key
from neo_app.providers.forge_neo_workflow_compilers import (
    FORGE_WORKFLOW_COMPILER_IDS,
    forge_workflow_compiler_contract_payload,
)
from neo_app.providers.schema import NeoJob

FORGE_VALIDATION_SCHEMA_ID = "neo.provider.forge_validation.v1"
FORGE_VALIDATION_VERSION = "1.0.0"
FORGE_VALIDATION_MATRIX_ID = "forge_phase6_offline_matrix_20260731"

_SELECTABLE_STATES = {"available", "experimental_available"}
_IMAGE_MODES = ("txt2img", "img2img", "inpaint", "outpaint", "edit")

_PATCH_FORBIDDEN_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "neo_data",
    "logs",
    "outputs",
    "output",
}
_PATCH_FORBIDDEN_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".safetensors",
    ".ckpt",
    ".gguf",
    ".pt",
    ".pth",
}

_PRIVATE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/][^\"']+"),
    re.compile(r"/(?:home|Users|mnt|var|tmp)/[^\"']+"),
)


@dataclass(frozen=True)
class ForgeValidationCheck:
    check_id: str
    ok: bool
    layer: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForgeValidationScenario:
    scenario_id: str
    description: str
    models: tuple[dict[str, Any], ...]
    modules: tuple[dict[str, Any], ...] = ()
    settings: tuple[dict[str, Any], ...] = ()
    enabled_modes: tuple[str, ...] = _IMAGE_MODES
    expected_routes: tuple[str, ...] = ()
    expected_blocked_routes: tuple[str, ...] = ()


def _tiny_png_data_uri(*, width: int = 32, height: int = 32) -> str:
    image = Image.new("RGB", (width, height), (104, 56, 32))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _snapshot_for_scenario(scenario: ForgeValidationScenario) -> dict[str, Any]:
    classification = build_forge_live_model_classification(
        models=[dict(item) for item in scenario.models],
        modules=[dict(item) for item in scenario.modules],
        settings_catalog={"settings": [dict(item) for item in scenario.settings]},
        scripts={"txt2img": [], "img2img": []},
        script_info=[],
        extensions=[],
        identity={"provider": "forge", "validation": True},
        capabilities={
            "neo_execution_adapter": True,
            "txt2img_api": True,
            "img2img_api": True,
            "progress_api": True,
            "interrupt_api": True,
        },
        bridge={},
        openapi_feature_keys=[],
    )
    intersection = build_forge_live_route_intersection(
        classification,
        enabled_modes=set(scenario.enabled_modes),
    )
    return {
        "status": "connected",
        "reachable": True,
        "api_enabled": True,
        "model_classification": classification,
        "live_route_intersection": intersection,
        "samplers": [{"name": "Euler"}, {"name": "LCM"}],
        "schedulers": [
            {"name": "Automatic"},
            {"name": "Simple"},
            {"name": "Beta"},
            {"name": "Normal"},
        ],
    }


def _route_keys(intersection: dict[str, Any]) -> set[str]:
    return {
        forge_route_key(str(item.get("family") or ""), str(item.get("loader") or ""), str(item.get("mode") or ""))
        for item in intersection.get("routes") or []
        if isinstance(item, dict) and item.get("selectable")
    }


def _contains_private_path(value: Any) -> bool:
    encoded = json.dumps(value, sort_keys=True, default=str)
    return any(pattern.search(encoded) is not None for pattern in _PRIVATE_PATH_PATTERNS)


def _synthetic_private_path(*parts: str) -> str:
    """Build an obviously synthetic absolute path without publishing a machine path literal."""

    return "X:" + "/" + "/".join(("private", *[str(part).strip("/") for part in parts]))


def _check(check_id: str, ok: bool, layer: str, message: str, **details: Any) -> ForgeValidationCheck:
    return ForgeValidationCheck(
        check_id=check_id,
        ok=bool(ok),
        layer=layer,
        message=message,
        details=details,
    )


def forge_validation_scenarios() -> tuple[ForgeValidationScenario, ...]:
    flux_modules = (
        {"model_name": "clip_l.safetensors", "filename": _synthetic_private_path('modules', 'clip_l.safetensors')},
        {"model_name": "t5xxl_fp16.safetensors", "filename": _synthetic_private_path('modules', 't5xxl_fp16.safetensors')},
        {"model_name": "ae.safetensors", "filename": _synthetic_private_path('modules', 'ae.safetensors')},
    )
    flux2_modules = (
        {"model_name": "qwen3_8b.safetensors", "filename": _synthetic_private_path('modules', 'qwen3_8b.safetensors')},
        {"model_name": "flux2-small-vae.safetensors", "filename": _synthetic_private_path('modules', 'flux2-small-vae.safetensors')},
    )
    qwen_modules = (
        {"model_name": "qwen2.5-vl-7b.safetensors", "filename": _synthetic_private_path('modules', 'qwen2.5-vl-7b.safetensors')},
        {"model_name": "qwen_image_vae.safetensors", "filename": _synthetic_private_path('modules', 'qwen_image_vae.safetensors')},
    )
    krea_modules = (
        {"model_name": "qwen3vl_4b.safetensors", "filename": _synthetic_private_path('modules', 'qwen3vl_4b.safetensors')},
        {"model_name": "qwen_image_vae.safetensors", "filename": _synthetic_private_path('modules', 'qwen_image_vae.safetensors')},
    )
    z_modules = (
        {"model_name": "qwen3_4b.safetensors", "filename": _synthetic_private_path('modules', 'qwen3_4b.safetensors')},
        {"model_name": "ae.safetensors", "filename": _synthetic_private_path('modules', 'ae.safetensors')},
    )
    flux2_setting_enabled = ({
        "key": "enable_flux_klein_img2img",
        "label": "Enable Flux.2 Klein regular img2img",
        "description": "Allow regular img2img for Flux.2 Klein",
        "current_value": True,
    },)
    flux2_setting_disabled = ({
        "key": "enable_flux_klein_img2img",
        "label": "Enable Flux.2 Klein regular img2img",
        "description": "Allow regular img2img for Flux.2 Klein",
        "current_value": False,
    },)
    return (
        ForgeValidationScenario(
            scenario_id="sdxl_checkpoint",
            description="Exact SDXL checkpoint exposes all implemented classic workflows.",
            models=({"title": "sdxl-phase6.safetensors", "filename": _synthetic_private_path('models', 'sdxl-phase6.safetensors')},),
            expected_routes=tuple(f"sdxl::checkpoint::{mode}" for mode in ("txt2img", "img2img", "inpaint", "outpaint")),
        ),
        ForgeValidationScenario(
            scenario_id="sd15_checkpoint",
            description="Exact SD 1.5 checkpoint exposes all implemented classic workflows.",
            models=({"title": "stable-diffusion-1.5-phase6.safetensors", "filename": _synthetic_private_path('models', 'stable-diffusion-1.5-phase6.safetensors')},),
            expected_routes=tuple(f"sd15::checkpoint::{mode}" for mode in ("txt2img", "img2img", "inpaint", "outpaint")),
        ),
        ForgeValidationScenario(
            scenario_id="flux_gguf",
            description="Flux GGUF exposes txt2img and experimental img2img with native modules.",
            models=({"title": "flux1-dev-Q4_K_M.gguf", "filename": _synthetic_private_path('models', 'flux1-dev-Q4_K_M.gguf')},),
            modules=flux_modules,
            expected_routes=("flux::gguf::txt2img", "flux::gguf::img2img"),
            expected_blocked_routes=("flux::gguf::inpaint", "flux::gguf::outpaint"),
        ),
        ForgeValidationScenario(
            scenario_id="flux2_setting_enabled",
            description="Flux.2 Klein img2img is executable only when the required Forge setting is enabled.",
            models=({"title": "flux2-klein-9b-fp8.safetensors", "filename": _synthetic_private_path('models', 'flux2-klein-9b-fp8.safetensors')},),
            modules=flux2_modules,
            settings=flux2_setting_enabled,
            expected_routes=("flux2_klein::diffusion_model::txt2img", "flux2_klein::diffusion_model::img2img"),
        ),
        ForgeValidationScenario(
            scenario_id="flux2_setting_disabled",
            description="Disabled Flux.2 Klein img2img setting removes only img2img from executable routes.",
            models=({"title": "flux2-klein-9b-fp8.safetensors"},),
            modules=flux2_modules,
            settings=flux2_setting_disabled,
            expected_routes=("flux2_klein::diffusion_model::txt2img",),
            expected_blocked_routes=("flux2_klein::diffusion_model::img2img",),
        ),
        ForgeValidationScenario(
            scenario_id="krea2_turbo_gguf",
            description="Krea 2 Turbo GGUF exposes only its implemented txt2img route.",
            models=({"title": "krea-2-turbo-Q4_K_M.gguf"},),
            modules=krea_modules,
            expected_routes=("krea2_turbo::gguf::txt2img",),
            expected_blocked_routes=("krea2_turbo::gguf::img2img",),
        ),
        ForgeValidationScenario(
            scenario_id="qwen_image_gguf",
            description="Qwen Image GGUF exposes txt2img with Qwen text encoder and VAE modules.",
            models=({"title": "qwen-image-Q4_K_M.gguf"},),
            modules=qwen_modules,
            expected_routes=("qwen_image::gguf::txt2img",),
            expected_blocked_routes=("qwen_image::gguf::img2img",),
        ),
        ForgeValidationScenario(
            scenario_id="qwen_edit_gguf",
            description="Qwen Image Edit exposes verified single-source img2img and edit routes.",
            models=({"title": "qwen-image-edit-2509-Q4_K_M.gguf"},),
            modules=qwen_modules,
            expected_routes=("qwen_image_edit_2509::gguf::img2img", "qwen_image_edit_2509::gguf::edit"),
            expected_blocked_routes=("qwen_image_edit_2509::gguf::txt2img", "qwen_image_edit_2509::gguf::inpaint"),
        ),
        ForgeValidationScenario(
            scenario_id="z_image_turbo_gguf",
            description="Z-Image Turbo GGUF exposes only its implemented txt2img route.",
            models=({"title": "z-image-turbo-Q4_K_M.gguf"},),
            modules=z_modules,
            expected_routes=("z_image_turbo::gguf::txt2img",),
        ),
        ForgeValidationScenario(
            scenario_id="unsupported_families",
            description="Provider-gated and unsupported families never enter normal Forge selectors.",
            models=(
                {"title": "qwen-image-rapid-aio.safetensors"},
                {"title": "wan2.2-i2v-Q4_K_M.gguf"},
                {"title": "hidream-I1-full-Q4_K_M.gguf"},
            ),
            expected_routes=(),
        ),
        ForgeValidationScenario(
            scenario_id="missing_modules",
            description="Modern models without required modules remain non-executable.",
            models=({"title": "flux1-dev-Q4_K_M.gguf"},),
            modules=(),
            expected_routes=(),
            expected_blocked_routes=("flux::gguf::txt2img", "flux::gguf::img2img"),
        ),
    )


def forge_validation_contract_payload() -> dict[str, Any]:
    return {
        "schema_id": FORGE_VALIDATION_SCHEMA_ID,
        "version": FORGE_VALIDATION_VERSION,
        "provider_id": "forge",
        "matrix_id": FORGE_VALIDATION_MATRIX_ID,
        "layers": [
            "route_authority",
            "live_model_classification",
            "live_route_intersection",
            "loader_translation",
            "workflow_compiler",
            "strict_ux_gating",
            "payload_redaction",
            "regression_lock",
        ],
        "offline_scenarios": [
            {
                "scenario_id": item.scenario_id,
                "description": item.description,
                "expected_routes": list(item.expected_routes),
                "expected_blocked_routes": list(item.expected_blocked_routes),
            }
            for item in forge_validation_scenarios()
        ],
        "commands": {
            "offline_matrix": "python scripts/validate_forge_neo_phase6.py --json",
            "phase6_tests": "python -m pytest -q tests/test_forge_neo_validation_phase6.py tests/test_forge_neo_regression_lock_phase6.py",
            "forge_regression": "python -m pytest -q tests/test_forge*.py",
        },
        "policy": {
            "offline_validation_is_not_physical_gpu_validation": True,
            "real_forge_installation_required_for_physical_signoff": True,
            "selectable_routes_require_registered_compilers": True,
            "ux_routes_must_equal_live_selectable_routes": True,
            "unsupported_routes_must_fail_closed": True,
            "private_backend_paths_must_not_escape_diagnostics": True,
            "patch_archives_must_exclude_runtime_and_cache_artifacts": True,
        },
    }


def validate_forge_static_contracts() -> list[ForgeValidationCheck]:
    checks: list[ForgeValidationCheck] = []
    authority = forge_route_authority_payload()
    compiler_contract = forge_workflow_compiler_contract_payload()
    compiler_ids = set(compiler_contract.get("compiler_ids") or [])
    seen_keys: set[str] = set()
    duplicate_keys: list[str] = []
    selectable_without_compiler: list[str] = []
    compiler_mismatches: list[str] = []
    non_native_loaders: list[str] = []

    compiler_specs = {
        str(item.get("compiler_id") or ""): item
        for item in compiler_contract.get("compilers") or []
        if isinstance(item, dict)
    }
    for family_id, family in FORGE_FAMILY_POLICIES.items():
        for loader_id, loader in family.loaders.items():
            if loader.provider_loader_id != FORGE_PROVIDER_LOADER_ID:
                non_native_loaders.append(f"{family_id}::{loader_id}")
            for mode in loader.workflows:
                route = resolve_forge_route(family_id, loader_id, mode)
                key = forge_route_key(family_id, loader_id, mode)
                if key in seen_keys:
                    duplicate_keys.append(key)
                seen_keys.add(key)
                if route.state not in _SELECTABLE_STATES:
                    continue
                if not route.compiler_id or route.compiler_id not in compiler_ids:
                    selectable_without_compiler.append(key)
                    continue
                spec = compiler_specs.get(route.compiler_id) or {}
                if family_id not in set(spec.get("families") or []) or mode not in set(spec.get("modes") or []):
                    compiler_mismatches.append(key)

    checks.append(_check(
        "authority_schema",
        authority.get("schema_id") == "neo.provider.forge_route_authority.v1",
        "route_authority",
        "Forge route authority schema is stable.",
        schema_id=authority.get("schema_id"),
    ))
    checks.append(_check(
        "route_keys_unique",
        not duplicate_keys,
        "route_authority",
        "Every Forge family/loader/mode route key is unique.",
        duplicates=duplicate_keys,
        route_count=len(seen_keys),
    ))
    checks.append(_check(
        "selectable_routes_have_compilers",
        not selectable_without_compiler,
        "workflow_compiler",
        "Every selectable Forge route names a registered compiler.",
        invalid_routes=selectable_without_compiler,
    ))
    checks.append(_check(
        "compiler_specs_cover_routes",
        not compiler_mismatches,
        "workflow_compiler",
        "Registered compiler family/mode declarations match selectable authority routes.",
        mismatches=compiler_mismatches,
    ))
    checks.append(_check(
        "provider_loader_native",
        not non_native_loaders,
        "loader_translation",
        "Every Forge loader policy resolves through the provider-native model-bundle loader.",
        invalid_loaders=non_native_loaders,
    ))
    checks.append(_check(
        "compiler_registry_exact",
        compiler_ids == set(FORGE_WORKFLOW_COMPILER_IDS),
        "workflow_compiler",
        "The published compiler registry matches runtime compiler IDs.",
        published=sorted(compiler_ids),
        runtime=sorted(FORGE_WORKFLOW_COMPILER_IDS),
    ))
    return checks


def validate_forge_scenario(scenario: ForgeValidationScenario) -> tuple[list[ForgeValidationCheck], dict[str, Any]]:
    snapshot = _snapshot_for_scenario(scenario)
    classification = snapshot["model_classification"]
    intersection = snapshot["live_route_intersection"]
    ux_policy = build_forge_ux_gating_policy(intersection)
    actual_routes = _route_keys(intersection)
    expected_routes = set(scenario.expected_routes)
    blocked_lookup = {
        forge_route_key(str(item.get("family") or ""), str(item.get("loader") or ""), str(item.get("mode") or "")): item
        for item in intersection.get("routes") or []
        if isinstance(item, dict)
    }
    expected_blocked = set(scenario.expected_blocked_routes)
    blocked_leaks = sorted(key for key in expected_blocked if key in actual_routes)
    missing_blockers = sorted(key for key in expected_blocked if key not in blocked_lookup)
    ux_routes = {str(item.get("route_key") or "") for item in ux_policy.get("executable_routes") or [] if isinstance(item, dict)}

    checks = [
        _check(
            f"{scenario.scenario_id}:route_set",
            actual_routes == expected_routes,
            "live_route_intersection",
            "Live executable route set matches the locked scenario expectation.",
            expected=sorted(expected_routes),
            actual=sorted(actual_routes),
        ),
        _check(
            f"{scenario.scenario_id}:blocked_routes_hidden",
            not blocked_leaks and not missing_blockers,
            "live_route_intersection",
            "Expected blocked routes exist diagnostically but never become selectable.",
            leaked=blocked_leaks,
            missing=missing_blockers,
        ),
        _check(
            f"{scenario.scenario_id}:ux_equals_live",
            ux_routes == actual_routes,
            "strict_ux_gating",
            "Strict Forge UX routes exactly equal live selectable routes.",
            live=sorted(actual_routes),
            ux=sorted(ux_routes),
        ),
        _check(
            f"{scenario.scenario_id}:path_redaction",
            not _contains_private_path(classification) and not _contains_private_path(ux_policy),
            "payload_redaction",
            "Classified assets and UX policy do not retain backend absolute paths.",
        ),
    ]
    return checks, {
        "scenario_id": scenario.scenario_id,
        "description": scenario.description,
        "classification": classification,
        "intersection": intersection,
        "ux_gating": ux_policy,
        "snapshot": snapshot,
    }


def _job(
    *,
    family: str,
    loader: str,
    mode: str,
    model: str,
    params: dict[str, Any] | None = None,
) -> NeoJob:
    return NeoJob(
        job_id=f"phase6-{family}-{loader}-{mode}",
        surface="image",
        subtab="generate" if mode == "txt2img" else mode,
        mode=mode,
        provider_id="forge",
        family=family,
        loader=loader,
        model=model,
        prompt="phase 6 validation image",
        negative_prompt="",
        params=params or {},
        extensions={},
    )


def _compile_cases(scenario_payloads: dict[str, dict[str, Any]]) -> list[tuple[str, NeoJob, dict[str, Any], str]]:
    image = _tiny_png_data_uri()
    image_two = _tiny_png_data_uri(width=40, height=32)
    cases: list[tuple[str, NeoJob, dict[str, Any], str]] = []

    sdxl = scenario_payloads["sdxl_checkpoint"]["snapshot"]
    for mode, params in (
        ("txt2img", {}),
        ("img2img", {"source_image": image}),
        ("inpaint", {"source_image": image, "mask_image": image}),
        ("outpaint", {"source_image": image, "outpaint_padding": {"right": 64}, "outpaint_overlap": 8}),
    ):
        cases.append((f"sdxl_{mode}", _job(family="sdxl", loader="checkpoint", mode=mode, model="sdxl-phase6.safetensors", params=params), sdxl, "forge.sdapi_outpaint" if mode == "outpaint" else "forge.sdapi_checkpoint"))

    sd15 = scenario_payloads["sd15_checkpoint"]["snapshot"]
    cases.append(("sd15_txt2img", _job(family="sd15", loader="checkpoint", mode="txt2img", model="stable-diffusion-1.5-phase6.safetensors"), sd15, "forge.sdapi_checkpoint"))

    flux = scenario_payloads["flux_gguf"]["snapshot"]
    flux_params = {
        "gguf_text_encoder_primary": "clip_l.safetensors",
        "gguf_text_encoder_secondary": "t5xxl_fp16.safetensors",
        "vae_or_ae": "ae.safetensors",
        "flux_guidance": 3.5,
    }
    cases.append(("flux_gguf_txt2img", _job(family="flux", loader="gguf", mode="txt2img", model="flux1-dev-Q4_K_M.gguf", params=flux_params), flux, "forge.sdapi_modern_txt2img"))
    cases.append(("flux_gguf_img2img", _job(family="flux", loader="gguf", mode="img2img", model="flux1-dev-Q4_K_M.gguf", params={**flux_params, "source_image": image}), flux, "forge.sdapi_modern_img2img"))

    flux2 = scenario_payloads["flux2_setting_enabled"]["snapshot"]
    flux2_params = {"qwen3_text_encoder": "qwen3_8b.safetensors", "vae_or_ae": "flux2-small-vae.safetensors"}
    cases.append(("flux2_txt2img", _job(family="flux2_klein", loader="diffusion_model", mode="txt2img", model="flux2-klein-9b-fp8.safetensors", params=flux2_params), flux2, "forge.sdapi_modern_txt2img"))
    cases.append(("flux2_img2img", _job(family="flux2_klein", loader="diffusion_model", mode="img2img", model="flux2-klein-9b-fp8.safetensors", params={**flux2_params, "source_image": image}), flux2, "forge.sdapi_modern_img2img"))

    krea = scenario_payloads["krea2_turbo_gguf"]["snapshot"]
    cases.append(("krea2_turbo_txt2img", _job(family="krea2_turbo", loader="gguf", mode="txt2img", model="krea-2-turbo-Q4_K_M.gguf", params={"qwen3vl_4b_text_encoder": "qwen3vl_4b.safetensors", "qwen_image_vae": "qwen_image_vae.safetensors"}), krea, "forge.sdapi_modern_txt2img"))

    qwen_image = scenario_payloads["qwen_image_gguf"]["snapshot"]
    cases.append(("qwen_image_txt2img", _job(family="qwen_image", loader="gguf", mode="txt2img", model="qwen-image-Q4_K_M.gguf", params={"qwen_text_encoder": "qwen2.5-vl-7b.safetensors", "vae": "qwen_image_vae.safetensors"}), qwen_image, "forge.sdapi_modern_txt2img"))

    qwen_edit = scenario_payloads["qwen_edit_gguf"]["snapshot"]
    qwen_edit_params = {"qwen_text_encoder": "qwen2.5-vl-7b.safetensors", "vae": "qwen_image_vae.safetensors", "source_image": image}
    cases.append(("qwen_edit_img2img", _job(family="qwen_image_edit_2509", loader="gguf", mode="img2img", model="qwen-image-edit-2509-Q4_K_M.gguf", params=qwen_edit_params), qwen_edit, "forge.sdapi_qwen_edit"))
    cases.append(("qwen_edit_edit", _job(family="qwen_image_edit_2509", loader="gguf", mode="edit", model="qwen-image-edit-2509-Q4_K_M.gguf", params=qwen_edit_params), qwen_edit, "forge.sdapi_qwen_edit"))

    z_image = scenario_payloads["z_image_turbo_gguf"]["snapshot"]
    cases.append(("z_image_turbo_txt2img", _job(family="z_image_turbo", loader="gguf", mode="txt2img", model="z-image-turbo-Q4_K_M.gguf", params={"qwen3_text_encoder": "qwen3_4b.safetensors", "ae_or_vae": "ae.safetensors"}), z_image, "forge.sdapi_modern_txt2img"))
    return cases


def validate_forge_compile_matrix(scenario_payloads: dict[str, dict[str, Any]]) -> list[ForgeValidationCheck]:
    checks: list[ForgeValidationCheck] = []
    for case_id, job, snapshot, expected_compiler in _compile_cases(scenario_payloads):
        try:
            translation = translate_forge_loader_bundle(job, snapshot=snapshot)
            compiled = compile_forge_neo_job(job, snapshot=snapshot)
            redacted = redact_forge_compile_payload(compiled)
            route = resolve_forge_route(str(job.family or ""), str(job.loader or ""), str(job.mode or ""))
            checks.extend([
                _check(
                    f"compile:{case_id}:translation",
                    bool(translation.get("executable")) and not translation.get("blockers"),
                    "loader_translation",
                    "Loader translation resolves an executable provider-native model bundle.",
                    blockers=list(translation.get("blockers") or []),
                ),
                _check(
                    f"compile:{case_id}:compiler",
                    compiled.get("actual_params", {}).get("compiler_id") == expected_compiler == route.compiler_id,
                    "workflow_compiler",
                    "Compiled payload uses the route-authority compiler.",
                    expected=expected_compiler,
                    actual=compiled.get("actual_params", {}).get("compiler_id"),
                    authority=route.compiler_id,
                ),
                _check(
                    f"compile:{case_id}:endpoint",
                    compiled.get("endpoint") == route.endpoint,
                    "workflow_compiler",
                    "Compiled endpoint matches route authority.",
                    expected=route.endpoint,
                    actual=compiled.get("endpoint"),
                ),
                _check(
                    f"compile:{case_id}:redaction",
                    not _contains_private_path(redacted),
                    "payload_redaction",
                    "Redacted compile diagnostics contain no private absolute paths.",
                ),
            ])
        except Exception as exc:  # noqa: BLE001 - the report must retain normalized failures.
            checks.append(_check(
                f"compile:{case_id}:exception",
                False,
                "workflow_compiler",
                "Representative compile case raised an exception.",
                exception_type=type(exc).__name__,
                error=str(exc),
            ))
    return checks


def validate_forge_fail_closed_cases(scenario_payloads: dict[str, dict[str, Any]]) -> list[ForgeValidationCheck]:
    image = _tiny_png_data_uri()
    image_two = _tiny_png_data_uri(width=40, height=32)
    cases: list[tuple[str, NeoJob, dict[str, Any], str]] = [
        (
            "flux_inpaint_gated",
            _job(
                family="flux",
                loader="gguf",
                mode="inpaint",
                model="flux1-dev-Q4_K_M.gguf",
                params={
                    "gguf_text_encoder_primary": "clip_l.safetensors",
                    "gguf_text_encoder_secondary": "t5xxl_fp16.safetensors",
                    "vae_or_ae": "ae.safetensors",
                    "source_image": image,
                    "mask_image": image,
                },
            ),
            scenario_payloads["flux_gguf"]["snapshot"],
            "authority_state:planned_gated",
        ),
        (
            "flux2_img2img_setting_disabled",
            _job(
                family="flux2_klein",
                loader="diffusion_model",
                mode="img2img",
                model="flux2-klein-9b-fp8.safetensors",
                params={
                    "qwen3_text_encoder": "qwen3_8b.safetensors",
                    "vae_or_ae": "flux2-small-vae.safetensors",
                    "source_image": image,
                },
            ),
            scenario_payloads["flux2_setting_disabled"]["snapshot"],
            "required_setting_disabled:flux2_klein_regular_img2img",
        ),
        (
            "qwen_edit_multi_source_blocked",
            _job(
                family="qwen_image_edit_2509",
                loader="gguf",
                mode="edit",
                model="qwen-image-edit-2509-Q4_K_M.gguf",
                params={
                    "qwen_text_encoder": "qwen2.5-vl-7b.safetensors",
                    "vae": "qwen_image_vae.safetensors",
                    "source_image": image,
                    "source_image_2": image_two,
                },
            ),
            scenario_payloads["qwen_edit_gguf"]["snapshot"],
            "image_stitch_contract_not_verified",
        ),
        (
            "rapid_aio_unsupported",
            _job(
                family="qwen_rapid_aio",
                loader="checkpoint_aio",
                mode="txt2img",
                model="qwen-image-rapid-aio.safetensors",
            ),
            scenario_payloads["unsupported_families"]["snapshot"],
            "authority_state:unsupported",
        ),
    ]
    checks: list[ForgeValidationCheck] = []
    for case_id, job, snapshot, expected_error in cases:
        try:
            compile_forge_neo_job(job, snapshot=snapshot)
        except ValueError as exc:
            text = str(exc)
            checks.append(_check(
                f"fail_closed:{case_id}",
                expected_error in text,
                "workflow_compiler",
                "Unsupported or unmet route fails before provider submission.",
                expected_error=expected_error,
                actual_error=text,
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check(
                f"fail_closed:{case_id}",
                False,
                "workflow_compiler",
                "Fail-closed case raised an unexpected exception type.",
                expected_error=expected_error,
                exception_type=type(exc).__name__,
                actual_error=str(exc),
            ))
        else:
            checks.append(_check(
                f"fail_closed:{case_id}",
                False,
                "workflow_compiler",
                "Unsupported or unmet route compiled unexpectedly.",
                expected_error=expected_error,
            ))
    return checks


def validate_forge_patch_archive(path: str | Path) -> dict[str, Any]:
    archive = Path(path).expanduser()
    issues: list[dict[str, Any]] = []
    entries: list[str] = []
    if not archive.exists() or not archive.is_file():
        return {
            "schema_id": FORGE_VALIDATION_SCHEMA_ID,
            "check_id": "patch_archive_hygiene",
            "ok": False,
            "archive": archive.name,
            "entry_count": 0,
            "issues": [{"kind": "missing_archive", "entry": archive.name}],
        }
    try:
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                name = str(info.filename or "").replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                entries.append(name)
                pure = PurePosixPath(name)
                parts = [part for part in pure.parts if part not in {"", "."}]
                if pure.is_absolute() or ".." in parts:
                    issues.append({"kind": "unsafe_path", "entry": name})
                    continue
                lowered_parts = {part.casefold() for part in parts}
                forbidden_parts = sorted(lowered_parts & {part.casefold() for part in _PATCH_FORBIDDEN_PARTS})
                if parts and parts[0].casefold() in {"models", "checkpoints"}:
                    forbidden_parts.append(parts[0].casefold())
                forbidden_parts = sorted(set(forbidden_parts))
                if forbidden_parts:
                    issues.append({"kind": "forbidden_directory", "entry": name, "parts": forbidden_parts})
                suffix = pure.suffix.casefold()
                if suffix in _PATCH_FORBIDDEN_SUFFIXES:
                    issues.append({"kind": "forbidden_suffix", "entry": name, "suffix": suffix})
    except (OSError, zipfile.BadZipFile) as exc:
        issues.append({"kind": "invalid_archive", "entry": archive.name, "error": str(exc)})
    return {
        "schema_id": FORGE_VALIDATION_SCHEMA_ID,
        "check_id": "patch_archive_hygiene",
        "ok": not issues,
        "archive": archive.name,
        "entry_count": len(entries),
        "entries": sorted(entries),
        "issues": issues,
        "policy": {
            "relative_paths_only": True,
            "runtime_state_excluded": True,
            "cache_and_bytecode_excluded": True,
            "model_files_excluded": True,
        },
    }


def run_forge_neo_offline_validation() -> dict[str, Any]:
    checks = validate_forge_static_contracts()
    scenario_payloads: dict[str, dict[str, Any]] = {}
    for scenario in forge_validation_scenarios():
        scenario_checks, payload = validate_forge_scenario(scenario)
        checks.extend(scenario_checks)
        scenario_payloads[scenario.scenario_id] = payload
    checks.extend(validate_forge_compile_matrix(scenario_payloads))
    checks.extend(validate_forge_fail_closed_cases(scenario_payloads))

    failed = [item for item in checks if not item.ok]
    layer_totals: dict[str, dict[str, int]] = {}
    for item in checks:
        bucket = layer_totals.setdefault(item.layer, {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        bucket["passed" if item.ok else "failed"] += 1
    return {
        "schema_id": FORGE_VALIDATION_SCHEMA_ID,
        "version": FORGE_VALIDATION_VERSION,
        "provider_id": "forge",
        "matrix_id": FORGE_VALIDATION_MATRIX_ID,
        "ok": not failed,
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "scenario_count": len(scenario_payloads),
            "layers": layer_totals,
        },
        "checks": [item.as_dict() for item in checks],
        "failed_check_ids": [item.check_id for item in failed],
        "physical_validation": {
            "status": "not_run",
            "reason": "Offline contract validation cannot prove real GPU execution, model quality, VRAM behavior, backend-specific model loading, or output correctness.",
            "required": True,
            "minimum_real_profiles": [
                "sdxl_checkpoint",
                "sd15_checkpoint",
                "flux_safetensors_or_gguf",
                "flux2_klein",
                "krea2",
                "qwen_image",
                "qwen_image_edit",
                "z_image",
            ],
        },
        "policy": forge_validation_contract_payload()["policy"],
    }
