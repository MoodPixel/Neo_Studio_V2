from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from typing import Any

ROUTE_REGRESSION_LOCK_VERSION = "0.1.4"

STABILIZATION_PHASE_ORDER: tuple[str, ...] = (
    "12.24_route_matrix_contract",
    "12.25_dynamic_mode_filtering",
    "12.26_parameter_visibility_contract",
    "12.27_outpaint_canvas_v1_parity",
    "12.28_checkpoint_route_repair",
    "12.29_flux_gguf_route_guard",
    "12.30_qwen_gguf_route_lock",
    "12.31_backend_plugin_contract",
    "12.32_route_snapshot_debug_upgrade",
    "12.33_physical_validation_matrix",
    "12.34_route_regression_lock",
)

PATCH_TEMPLATE_FIELDS: tuple[str, ...] = (
    "touched_routes",
    "untouched_routes",
    "expected_ui_changes",
    "expected_backend_changes",
    "regression_tests",
    "physical_validation_needed",
)


@dataclass(frozen=True)
class RegressionTestGroup:
    group_id: str
    label: str
    command: str
    protects: list[str] = field(default_factory=list)
    required_for_path_patterns: list[str] = field(default_factory=list)
    phase: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


REGRESSION_TEST_GROUPS: tuple[RegressionTestGroup, ...] = (
    RegressionTestGroup(
        group_id="route_matrix",
        label="Route matrix contract",
        phase="12.24",
        command="pytest tests/test_phase12_24_route_matrix_contract.py",
        protects=["backend/family/loader/mode route state source of truth"],
        required_for_path_patterns=[
            "neo_app/models/route_matrix.py",
            "neo_app/models/model_family_manifest.json",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/backend_route_contract.py",
        ],
    ),
    RegressionTestGroup(
        group_id="mode_filtering",
        label="Dynamic workflow mode filtering",
        phase="12.25",
        command="pytest tests/test_phase12_25_dynamic_mode_filtering.py",
        protects=["Workflow Mode dropdown exposes only selectable route states"],
        required_for_path_patterns=[
            "neo_app/static/js/neo.js",
            "neo_app/static/css/neo.css",
            "neo_app/models/registry.py",
            "neo_app/models/route_matrix.py",
        ],
    ),
    RegressionTestGroup(
        group_id="parameter_visibility",
        label="Parameter visibility contract",
        phase="12.26",
        command="pytest tests/test_phase12_26_parameter_visibility.py",
        protects=["denoise/source/mask/outpaint fields only show and submit for matching routes"],
        required_for_path_patterns=[
            "neo_app/static/js/neo.js",
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/registry.py",
        ],
    ),
    RegressionTestGroup(
        group_id="outpaint_canvas",
        label="Outpaint canvas V1 parity",
        phase="12.27",
        command="pytest tests/test_phase12_27_outpaint_canvas_parity.py",
        protects=["Outpaint modal, edge/corner hit-test drag, image reposition, padding sync"],
        required_for_path_patterns=[
            "neo_app/static/js/neo.js",
            "neo_app/static/css/neo.css",
            "neo_app/image/outpaint_contract.py",
        ],
    ),
    RegressionTestGroup(
        group_id="checkpoint_routes",
        label="SDXL / SD1.5 checkpoint routes",
        phase="12.28",
        command="pytest tests/test_phase12_28_checkpoint_route_repair.py tests/test_phase12_8_sd15_checkpoint_workflows.py",
        protects=["checkpoint txt2img/img2img/inpaint/outpaint do not drift into GGUF/Flux/Qwen routes"],
        required_for_path_patterns=[
            "neo_app/providers/comfy_workflows/checkpoint_sd.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/compile_router.py",
        ],
    ),
    RegressionTestGroup(
        group_id="flux_gguf_guard",
        label="Flux GGUF guard",
        phase="12.29",
        command="pytest tests/test_phase12_29_flux_gguf_route_guard.py",
        protects=["Flux GGUF exposes validated txt2img/img2img/inpaint/outpaint without fallback and blocks invalid source/mask/padding before queue"],
        required_for_path_patterns=[
            "neo_app/providers/comfy_workflows/flux_gguf.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/models/route_matrix.py",
        ],
    ),
    RegressionTestGroup(
        group_id="qwen_gguf_lock",
        label="Qwen GGUF route lock",
        phase="12.30",
        command="pytest tests/test_phase12_30_qwen_gguf_route_lock.py tests/test_phase12_14_qwen_rapid_inpaint_parity.py",
        protects=["Qwen mmproj requirements, non-LanPaint inpaint path, editable non-Flux GGUF CFG, no Flux fallback"],
        required_for_path_patterns=[
            "neo_app/providers/comfy_workflows/qwen_gguf.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/models/route_matrix.py",
        ],
    ),
    RegressionTestGroup(
        group_id="backend_plugin_contract",
        label="Backend plug-in contract",
        phase="12.31",
        command="pytest tests/test_phase12_31_backend_plugin_contract.py",
        protects=["Comfy support does not imply Forge/A1111 support"],
        required_for_path_patterns=[
            "neo_app/providers/backend_route_contract.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/provider_manifest.json",
            "neo_app/models/route_matrix.py",
        ],
    ),
    RegressionTestGroup(
        group_id="route_snapshot",
        label="Output metadata and route snapshot",
        phase="12.32",
        command="pytest tests/test_phase12_32_route_snapshot_debug_upgrade.py tests/test_phase12_21_output_metadata_upgrade.py",
        protects=["output sidecars record route, UI fields, payload keys, provider node classes"],
        required_for_path_patterns=[
            "neo_app/image/output_records.py",
            "neo_app/image/output_service.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/main.py",
            "neo_app/static/js/neo.js",
        ],
    ),

    RegressionTestGroup(
        group_id="sdxl_family_lock",
        label="SDXL family audit and checkpoint-only lock",
        phase="V25.9.20 Pass A",
        command="pytest tests/test_v25_9_20_pass_a_sdxl_family_lock.py",
        protects=[
            "SDXL normal Image routes stay checkpoint-only",
            "SDXL unsupported loaders cannot compile through fallbacks",
            "SDXL checkpoint extension support remains route-specific",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="sd15_family_lock",
        label="SD 1.5 family audit and checkpoint-only lock",
        phase="V25.9.20 Pass B",
        command="pytest tests/test_v25_9_20_pass_b_sd15_family_lock.py",
        protects=[
            "SD 1.5 normal Image routes stay checkpoint-only",
            "SD 1.5 legacy unet/split loaders cannot compile through fallbacks",
            "SD 1.5 checkpoint extension support remains route-specific and experimental where not validated",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="flux1_family_lock",
        label="Flux 1 family audit and route lock",
        phase="V25.9.20 Pass C",
        command="pytest tests/test_v25_9_20_pass_c_flux1_family_lock.py",
        protects=[
            "Flux 1 normal Image routes expose only Safetensors / Components and GGUF",
            "Flux 1 safetensors/components image-conditioned modes stay gated",
            "Flux 1 GGUF routes stay available without fallback across families/loaders",
            "Flux 2 Klein variant behavior remains untouched until its separate family pass",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/flux_native.py",
            "neo_app/providers/comfy_workflows/flux_gguf.py",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="flux2_klein_family_lock",
        label="Flux 2 Klein family audit and route lock",
        phase="V25.9.20 Pass D",
        command="pytest tests/test_v25_9_20_pass_d_flux2_klein_family_lock.py",
        protects=[
            "Flux 2 Klein is a visible Image family separate from Flux 1",
            "Flux 2 Klein Safetensors / Components owns P4 txt2img/img2img/edit/inpaint/outpaint native workflows",
            "Flux 2 Klein GGUF keeps the single-Qwen3 Flux2/Klein route for txt2img/img2img/inpaint/outpaint",
            "Flux 2 Klein extensions remain route-specific and do not inherit broad Flux 1 unlocks",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/flux_native.py",
            "neo_app/providers/comfy_workflows/flux_gguf.py",
            "neo_app/static/js/neo.js",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="flux2_klein_img2img_edit_workflow_validation_o1",
        label="Flux 2 Klein img2img/edit workflow validation",
        phase="V25.9.20 Pass O1",
        command="pytest tests/test_v25_9_20_pass_o1_flux2_klein_img2img_edit_workflow_validation.py",
        protects=[
            "Flux 2 Klein GGUF edit resolves to comfy.flux_gguf.klein",
            "Flux 2 Klein GGUF img2img/edit uses Image 1 as the VAEEncode latent anchor",
            "Optional Image 2/Image 3 stay metadata/replay lanes until native multi-reference conditioning is validated",
            "Flux 2 Klein native/safetensors edit remains available through the P4 Klein-native compiler",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_workflows/flux_gguf.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/static/js/neo.js",
        ],
    ),
    RegressionTestGroup(
        group_id="flux1_internal_fill_route_cleanup_p1",
        label="Flux 1 internal Flux Fill route cleanup",
        phase="V25.9.20 P1",
        command="pytest tests/test_v25_9_20_p1_flux1_internal_fill_route_cleanup.py tests/test_v25_9_20_pass_o2_flux1_fill_workflow_implementation.py",
        protects=[
            "Flux 1 native img2img resolves to comfy.flux_native as Image-1 latent-anchor",
            "Normal Flux 1 diffusion_model inpaint/outpaint resolve to comfy.flux_fill",
            "Flux 1 Fill is removed from the normal Image Model Family dropdown",
            "Legacy flux1_fill saved jobs remain a compatibility alias without becoming normal UI routes",
            "Flux Fill does not fallback into SD/Qwen/ZImage/Flux GGUF compilers",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_workflows/flux_native.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/static/js/neo.js",
        ],
    ),
    RegressionTestGroup(
        group_id="qwen_rapid_aio_checkpoint_route_cleanup_p2",
        label="Qwen Rapid AIO checkpoint route cleanup",
        phase="V25.9.20 P2",
        command="pytest tests/test_v25_9_20_p2_qwen_rapid_aio_checkpoint_route_cleanup.py tests/test_v25_9_20_pass_n3_qwen_rapid_aio_workflow_implementation.py",
        protects=[
            "Qwen Rapid AIO bundled checkpoint routes compile through comfy.qwen_rapid_aio_checkpoint",
            "qwen_rapid_aio_checkpoint is the primary checkpoint selector when job.model is provider_default",
            "External encoder/VAE/MMProj/GGUF/split-model params are pruned from checkpoint_aio actual_params",
            "Bundled checkpoint graph does not contain split component or GGUF loader nodes",
            "Image 2/Image 3 remain available only for img2img/edit; inpaint/outpaint stay single-source mask/canvas",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_workflows/qwen_aio.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/static/js/neo.js",
        ],
    ),
    RegressionTestGroup(
        group_id="qwen_rapid_aio_family_lock",
        label="Qwen Rapid AIO family audit and route lock",
        phase="V25.9.20 Pass E",
        command="pytest tests/test_v25_9_20_pass_e_qwen_rapid_aio_family_lock.py",
        protects=[
            "Qwen Rapid AIO is a visible Image family separate from Qwen Image Edit",
            "Qwen Rapid AIO exposes both Safetensors / Bundled and GGUF as normal Main Model Types",
            "Qwen Rapid AIO GGUF uses the Qwen single-encoder GGUF compiler with mmproj only for image-conditioned routes",
            "Qwen Rapid AIO extension support remains route-specific and does not inherit broad Qwen Image Edit assumptions",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/qwen_aio.py",
            "neo_app/providers/comfy_workflows/qwen_gguf.py",
            "neo_app/static/js/neo.js",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="qwen_image_edit_family_lock",
        label="Qwen Image Edit family audit and single-source route lock",
        phase="V25.9.20 Pass F",
        command="pytest tests/test_v25_9_20_pass_f_qwen_image_edit_family_lock.py",
        protects=[
            "Qwen Image Edit is a visible single-source family separate from Qwen Rapid AIO",
            "Qwen Image Edit exposes only Safetensors / Components and GGUF as normal Main Model Types",
            "Qwen Image Edit checkpoint_aio and api_model loaders cannot compile through legacy aliases",
            "source_image_2/source_image_3 are hidden/ignored until Qwen Image Edit 2509 is added as a separate family",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/qwen_aio.py",
            "neo_app/providers/comfy_workflows/qwen_gguf.py",
            "neo_app/static/js/neo.js",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="qwen_image_edit_2509_family_lock",
        label="Qwen Image Edit 2509 family audit and multi-source route lock",
        phase="V25.9.20 Pass G",
        command="pytest tests/test_v25_9_20_pass_g_qwen_image_edit_2509_family_lock.py",
        protects=[
            "Qwen Image Edit 2509 is a visible multi-source family separate from normal Qwen Image Edit",
            "Qwen Image Edit 2509 exposes Safetensors / Components and GGUF without checkpoint_aio aliasing",
            "Qwen Image Edit 2509 img2img/edit can consume source_image plus optional source_image_2/source_image_3",
            "Normal Qwen Image Edit remains single-source and cannot borrow 2509 multi-source lanes",
            "Qwen Image Edit 2509 extensions remain route-specific and do not inherit broad Qwen unlocks",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/qwen_aio.py",
            "neo_app/providers/comfy_workflows/qwen_gguf.py",
            "neo_app/static/js/neo.js",
            "neo_extensions/built_in/image.*/extension_manifest.json",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="zimage_family_lock",
        label="ZImage family audit and base txt2img route lock",
        phase="V25.9.20 Pass H",
        command="pytest tests/test_v25_9_20_pass_h_zimage_family_lock.py",
        protects=[
            "ZImage base exposes only Safetensors / Components and GGUF",
            "ZImage base txt2img routes compile through ZImage native/GGUF only",
            "ZImage base Safetensors / Components img2img/inpaint/outpaint compile through the native ZImage compiler after P5",
            "ZImage base GGUF image-conditioned modes remain implementation targets until their separate GGUF pass",
            "ZImage Turbo remains a separate visible family and P6 owns its Safetensors / Components image-mode routes",
            "ZImage extension support stays route-specific without broad unlocks",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/z_image.py",
            "neo_app/static/js/neo.js",
            "neo_extensions/built_in/image.*/extension_manifest.json",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),

    RegressionTestGroup(
        group_id="zimage_turbo_family_lock",
        label="ZImage Turbo family audit and low-step route lock",
        phase="V25.9.20 Pass I/P6",
        command="pytest tests/test_v25_9_20_pass_i_zimage_turbo_family_lock.py tests/test_v25_9_20_p6_zimage_turbo_checkpoint_safetensors_workflows.py",
        protects=[
            "ZImage Turbo is a visible family separate from base ZImage",
            "ZImage Turbo exposes Safetensors / Components txt2img/img2img/inpaint/outpaint and GGUF txt2img routes",
            "ZImage Turbo uses family-owned low-step/low-CFG defaults without a normal turbo_mode dropdown",
            "ZImage Turbo component image-conditioned routes compile through the ZImage compiler with family-forced Turbo defaults",
            "ZImage Turbo GGUF image-conditioned routes remain implementation targets for their own GGUF pass",
            "ZImage Turbo extension support stays route-specific without broad unlocks",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/z_image.py",
            "neo_app/static/js/neo.js",
            "neo_extensions/built_in/image.*/extension_manifest.json",
            "neo_extensions/built_in/image.*/backend/support_matrix.py",
            "neo_extensions/built_in/image.*/backend/support_matrix_data.json",
        ],
    ),
    RegressionTestGroup(
        group_id="zimage_checkpoint_safetensors_workflows_p5",
        label="ZImage checkpoint/safetensors workflows",
        phase="V25.9.20 P5",
        command="pytest tests/test_v25_9_20_p5_zimage_checkpoint_safetensors_workflows.py",
        protects=[
            "ZImage Safetensors / Components img2img/inpaint/outpaint resolve to comfy.z_image_native",
            "ZImage component image modes use Image 1 as the VAEEncode latent anchor",
            "ZImage component inpaint/outpaint use SetLatentNoiseMask + DifferentialDiffusion",
            "ZImage component routes do not fallback to ZImage Turbo, Flux, Qwen, SD, or GGUF compilers",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/z_image.py",
            "neo_app/static/js/neo.js",
        ],
    ),


    RegressionTestGroup(
        group_id="zimage_turbo_checkpoint_safetensors_workflows_p6",
        label="ZImage Turbo checkpoint/safetensors workflows",
        phase="V25.9.20 P6",
        command="pytest tests/test_v25_9_20_p6_zimage_turbo_checkpoint_safetensors_workflows.py",
        protects=[
            "ZImage Turbo Safetensors / Components img2img/inpaint/outpaint resolve to comfy.z_image_native",
            "ZImage Turbo component image modes keep ConditioningZeroOut and family-forced Turbo defaults",
            "ZImage Turbo component inpaint/outpaint use SetLatentNoiseMask + DifferentialDiffusion",
            "ZImage Turbo component routes do not fallback to base high-step ZImage, Flux, Qwen, SD, or GGUF compilers",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/z_image.py",
            "neo_app/static/js/neo.js",
        ],
    ),

    RegressionTestGroup(
        group_id="flux2_klein_checkpoint_safetensors_workflows_p4",
        label="Flux 2 Klein checkpoint/safetensors workflows",
        phase="V25.9.20 P4",
        command="pytest tests/test_v25_9_20_p4_flux_klein_checkpoint_safetensors_workflows.py",
        protects=[
            "Flux 2 Klein Safetensors / Components img2img/edit/inpaint/outpaint resolve to comfy.flux_klein",
            "Flux 2 Klein component image modes use Image 1 as the Flux2 VAEEncode latent anchor",
            "Flux 2 Klein component inpaint/outpaint use SetLatentNoiseMask + DifferentialDiffusion",
            "Flux 2 Klein component routes do not fallback to Flux 1 Fill, Flux GGUF, SD, or Qwen compilers",
        ],
        required_for_path_patterns=[
            "neo_app/models/model_family_manifest.json",
            "neo_app/models/route_matrix.py",
            "neo_app/providers/compile_router.py",
            "neo_app/providers/comfy_provider.py",
            "neo_app/providers/comfy_workflows/flux_native.py",
            "neo_app/static/js/neo.js",
        ],
    ),
    RegressionTestGroup(
        group_id="physical_validation",
        label="Physical validation matrix",
        phase="12.33",
        command="pytest tests/test_phase12_33_physical_validation_matrix.py",
        protects=["unit-test support remains separate from real local-model validation"],
        required_for_path_patterns=[
            "neo_app/models/physical_validation.py",
            "neo_app/models/registry.py",
            "neo_app/models/route_matrix.py",
        ],
    ),
)

ALWAYS_RUN_GROUP_IDS: tuple[str, ...] = (
    "route_matrix",
    "mode_filtering",
    "parameter_visibility",
)

ROUTE_CRITICAL_PATH_PATTERNS: tuple[str, ...] = tuple(
    sorted({pattern for group in REGRESSION_TEST_GROUPS for pattern in group.required_for_path_patterns})
)


def _normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./")


def required_regression_groups_for_paths(paths: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    """Return the regression groups that must run for changed source paths.

    The lock intentionally over-selects the baseline route/profile groups for
    any route-critical change. That cost is small compared with re-breaking an
    already validated family/loader/mode route.
    """
    normalized = [_normalize_path(path) for path in paths]
    matched_ids: set[str] = set()
    for group in REGRESSION_TEST_GROUPS:
        for path in normalized:
            if any(fnmatch(path, pattern) for pattern in group.required_for_path_patterns):
                matched_ids.add(group.group_id)
                break
    if matched_ids:
        matched_ids.update(ALWAYS_RUN_GROUP_IDS)
    return [group.as_dict() for group in REGRESSION_TEST_GROUPS if group.group_id in matched_ids]


def validate_patch_plan(plan: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in PATCH_TEMPLATE_FIELDS if field not in plan or plan.get(field) in (None, "", [])]
    return {
        "ok": not missing,
        "missing_fields": missing,
        "required_fields": list(PATCH_TEMPLATE_FIELDS),
    }


def regression_lock_payload() -> dict[str, Any]:
    return {
        "version": ROUTE_REGRESSION_LOCK_VERSION,
        "contract": "Route-critical changes must declare touched/untouched routes and run mapped regression groups before shipping.",
        "stabilization_phase_order": list(STABILIZATION_PHASE_ORDER),
        "patch_template_fields": list(PATCH_TEMPLATE_FIELDS),
        "always_run_group_ids": list(ALWAYS_RUN_GROUP_IDS),
        "route_critical_path_patterns": list(ROUTE_CRITICAL_PATH_PATTERNS),
        "test_groups": [group.as_dict() for group in REGRESSION_TEST_GROUPS],
    }
