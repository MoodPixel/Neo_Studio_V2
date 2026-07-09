from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from neo_app.models.route_matrix import list_model_backend_routes, normalize_backend, resolve_model_backend_route

PhysicalValidationStatus = Literal[
    "validated",
    "manual_validation_required",
    "not_installed_locally",
    "gated_not_testable",
    "future_backend",
]

PHYSICAL_VALIDATION_STATUSES: tuple[str, ...] = (
    "validated",
    "manual_validation_required",
    "not_installed_locally",
    "gated_not_testable",
    "future_backend",
)

PHYSICAL_VALIDATION_STATUS_POLICY: dict[str, dict[str, Any]] = {
    "validated": {
        "can_run_manual_test": True,
        "counts_as_verified": True,
        "ui_label": "Physically validated",
        "badge": "Validated",
    },
    "manual_validation_required": {
        "can_run_manual_test": True,
        "counts_as_verified": False,
        "ui_label": "Manual validation required",
        "badge": "Needs physical test",
    },
    "not_installed_locally": {
        "can_run_manual_test": False,
        "counts_as_verified": False,
        "ui_label": "Local model not installed",
        "badge": "No local asset",
    },
    "gated_not_testable": {
        "can_run_manual_test": False,
        "counts_as_verified": False,
        "ui_label": "Route gated / not testable",
        "badge": "Gated",
    },
    "future_backend": {
        "can_run_manual_test": False,
        "counts_as_verified": False,
        "ui_label": "Future backend adapter",
        "badge": "Future backend",
    },
}

# Phase 12.33: this list intentionally records only assets the current physical
# tester reported having. It is not a global product support claim; the route
# matrix remains the source of truth for implementation support.
DEFAULT_LOCAL_TEST_ASSETS: dict[tuple[str, str], dict[str, Any]] = {
    ("sdxl", "checkpoint"): {
        "asset_status": "available_on_tester_machine",
        "notes": ["Checkpoint family is available for manual Comfy validation."],
    },
    ("flux", "gguf"): {
        "asset_status": "available_on_tester_machine",
        "notes": ["Flux GGUF txt2img/img2img/inpaint/outpaint are available on local assets after Phase M14.2 runtime validation; future machines should still record their own physical validation artifacts."],
    },
    ("flux", "diffusion_model"): {
        "asset_status": "manual_asset_required",
        "notes": ["V25.9.20 P1 records Flux 1 Safetensors / Components txt2img/img2img and internal Flux Fill inpaint/outpaint as manually testable; fill modes require flux1-fill-dev.safetensors or compatible fill model plus CLIP-L/T5 and AE/VAE assets."],
    },
    ("flux1_fill", "diffusion_model"): {
        "asset_status": "legacy_internal_alias",
        "notes": ["V25.9.20 P1 keeps flux1_fill as a saved-job compatibility alias only; new manual validation should be recorded under flux + diffusion_model + inpaint/outpaint."],
    },
    ("qwen_image", "gguf"): {
        "asset_status": "available_on_tester_machine",
        "notes": ["Qwen GGUF routes are testable with local UNet/text encoder/VAE/mmproj assets."],
    },
    ("qwen_rapid_aio", "gguf"): {
        "asset_status": "available_on_tester_machine",
        "notes": ["V25.9.20 Pass E records the user's Qwen Rapid AIO GGUF runtime-testing path as manually testable with local GGUF UNet/text encoder/VAE/mmproj assets."],
    },
    ("flux2_klein", "diffusion_model"): {
        "asset_status": "manual_asset_required",
        "notes": ["V25.9.20 P4 records Flux 2 Klein Safetensors / Components as manually testable for txt2img/img2img/edit/inpaint/outpaint; requires Klein diffusion model, Qwen3 text encoder, and flux2-vae assets."],
    },
    ("flux2_klein", "gguf"): {
        "asset_status": "available_on_tester_machine",
        "notes": ["V25.9.20 Pass O1 records Flux 2 Klein GGUF as manually testable on the active machine; img2img/edit uses Image 1 as the latent anchor while Image 2/Image 3 remain replay/reference lanes."],
    },
    ("z_image", "diffusion_model"): {
        "asset_status": "manual_asset_required",
        "notes": ["V25.9.20 P5 records ZImage Safetensors / Components as manually testable for txt2img/img2img/inpaint/outpaint; requires ZImage diffusion model, Qwen3 text encoder, and AE/VAE assets."],
    },
    ("z_image_turbo", "diffusion_model"): {
        "asset_status": "manual_asset_required",
        "notes": ["V25.9.20 P6/P8.5 records ZImage Turbo Safetensors / Components as manually testable for txt2img/img2img/inpaint/outpaint; requires z_image_turbo_bf16.safetensors or compatible Turbo diffusion model, Qwen3 text encoder, and AE/VAE assets."],
    },
    ("z_image_turbo", "gguf"): {
        "asset_status": "manual_asset_required",
        "notes": ["V25.9.20 P8.5 records ZImage Turbo GGUF as manually testable for txt2img/img2img/inpaint/outpaint; requires ZImage Turbo GGUF UNet, Qwen3/lumina2 text encoder, AE/VAE assets, and Image 1/mask/padding for image modes."],
    },
}

MANUAL_VALIDATION_CHECKLIST: tuple[str, ...] = (
    "Confirm visible UI fields match route snapshot.",
    "Confirm payload keys include only route-owned parameters.",
    "Confirm provider queue succeeds or fails with a route blocker before queue.",
    "Confirm output sidecar records route and route_snapshot.",
    "Save screenshot/output reference and known issues in the validation record.",
)


@dataclass(frozen=True)
class PhysicalValidationEntry:
    backend: str
    family: str
    loader: str
    mode: str
    route_state: str
    physical_status: PhysicalValidationStatus
    reason: str = ""
    workflow_type: str | None = None
    compiler_id: str | None = None
    required_assets: list[str] = field(default_factory=list)
    local_asset_status: str = "unknown"
    validation_source: str = "manual_tester_matrix"
    validated_at: str | None = None
    sample_output_ref: str | None = None
    known_issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def can_run_manual_test(self) -> bool:
        return bool(PHYSICAL_VALIDATION_STATUS_POLICY[self.physical_status]["can_run_manual_test"])

    @property
    def counts_as_verified(self) -> bool:
        return bool(PHYSICAL_VALIDATION_STATUS_POLICY[self.physical_status]["counts_as_verified"])

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "can_run_manual_test": self.can_run_manual_test,
            "counts_as_verified": self.counts_as_verified,
            "status_policy": PHYSICAL_VALIDATION_STATUS_POLICY[self.physical_status],
        }


def _physical_status_for_route(row: dict[str, Any], local_assets: dict[tuple[str, str], dict[str, Any]]) -> PhysicalValidationStatus:
    backend = normalize_backend(row.get("backend"))
    if backend != "comfyui":
        return "future_backend"

    state = row.get("state")
    if state not in {"available", "experimental_available"}:
        return "gated_not_testable"

    if (row.get("family"), row.get("loader")) in local_assets:
        return "manual_validation_required"

    return "not_installed_locally"


def _known_issues_for_route(row: dict[str, Any]) -> list[str]:
    family = row.get("family")
    loader = row.get("loader")
    mode = row.get("mode")
    backend = normalize_backend(row.get("backend"))

    issues: list[str] = []
    if backend == "comfyui" and family == "sdxl" and loader == "checkpoint" and mode == "inpaint":
        issues.append("Previously reported: SDXL inpaint could queue but preserve the source if mask ingestion regressed.")
    if backend == "comfyui" and family == "flux" and loader == "gguf" and mode in {"img2img", "inpaint", "outpaint"}:
        issues.append("Resolved in Phase M14/M14.2 on the active tester path: provider-owned Flux GGUF source branches compile and runtime img2img/inpaint/outpaint completed; record fresh artifacts when moving machines/backends.")
    if backend == "comfyui" and family == "flux" and loader == "diffusion_model" and mode == "img2img":
        issues.append("Pass O2 implements the Flux 1 native img2img compiler shape; physical output validation is still required per local model/encoder set.")
    if backend == "comfyui" and family == "flux" and loader == "diffusion_model" and mode in {"inpaint", "outpaint"}:
        issues.append("P1 implements the internal Flux Fill workflow shape with InpaintModelConditioning + DifferentialDiffusion under the normal Flux 1 family; validate with real flux1-fill-dev.safetensors outputs before marking verified.")
    if backend == "comfyui" and family == "flux1_fill" and loader == "diffusion_model" and mode in {"inpaint", "outpaint"}:
        issues.append("P1 legacy alias only: validate new runs under flux + diffusion_model instead of the old visible flux1_fill family.")
    if backend == "comfyui" and family == "flux2_klein" and loader == "diffusion_model" and mode in {"img2img", "edit", "inpaint", "outpaint"}:
        issues.append("P4 implements the Flux 2 Klein component compiler shape; run physical Comfy validation with local Klein diffusion model, Qwen3 text encoder, and flux2-vae before marking verified.")
    if backend == "comfyui" and family == "flux2_klein" and loader == "gguf" and mode in {"img2img", "edit"}:
        issues.append("Pass O1 validates the local compiler shape for Flux 2 Klein GGUF img2img/edit as Image-1 latent-anchor. Optional Image 2/Image 3 are not yet wired into native Flux2 multi-reference conditioning.")
    if backend == "comfyui" and family == "z_image" and loader == "diffusion_model" and mode in {"img2img", "inpaint", "outpaint"}:
        issues.append("P5 implements the ZImage component compiler shape; run physical Comfy validation with local ZImage diffusion model, Qwen3 text encoder, AE/VAE, source image, and mask/padding before marking verified.")
    if backend == "comfyui" and family == "z_image_turbo" and loader in {"diffusion_model", "gguf"} and mode in {"img2img", "inpaint", "outpaint"}:
        issues.append("P8.5 locks ZImage Turbo image modes to the selected loader's provider-owned Turbo route with family-forced low-step/low-CFG defaults; run physical Comfy validation with local Turbo model/text encoder/AE assets, source image, and mask/padding before marking verified.")
    if backend == "comfyui" and mode == "outpaint" and family in {"sdxl", "sd15", "qwen_image", "qwen_image_edit_2509"}:
        issues.append("Outpaint must be validated with the V1-parity canvas popup, drag/reposition, and padding sync.")
    return issues


def list_physical_validation_matrix(
    backend: str | None = None,
    *,
    local_assets: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the manual/physical validation matrix layered on top of route support.

    This is deliberately separate from the route matrix: a route can be
    implemented and unit-tested while still requiring a physical run with local
    assets before being marked as validated.
    """

    asset_map = dict(DEFAULT_LOCAL_TEST_ASSETS if local_assets is None else local_assets)
    rows: list[dict[str, Any]] = []
    for route in list_model_backend_routes(backend):
        asset_info = asset_map.get((route["family"], route["loader"]), {})
        status = _physical_status_for_route(route, asset_map)
        notes = [*asset_info.get("notes", [])]
        if status == "manual_validation_required":
            notes.append("Run manually on the active backend and update validation artifacts before marking validated.")
        elif status == "not_installed_locally":
            notes.append("Route is implemented, but no local test asset is declared for this tester machine.")
        elif status == "gated_not_testable":
            notes.append("Route is not selectable/compilable, so physical generation should not be attempted.")
        elif status == "future_backend":
            notes.append("Backend adapter is planned/gated; Comfy validation does not imply this backend works.")

        entry = PhysicalValidationEntry(
            backend=route["backend"],
            family=route["family"],
            loader=route["loader"],
            mode=route["mode"],
            route_state=route["state"],
            physical_status=status,
            reason=route.get("reason", ""),
            workflow_type=route.get("workflow_type"),
            compiler_id=route.get("compiler_id"),
            required_assets=list(route.get("requires") or []),
            local_asset_status=asset_info.get("asset_status", "not_declared"),
            known_issues=_known_issues_for_route(route),
            notes=notes,
        )
        rows.append(entry.as_dict())
    return rows


def resolve_physical_validation_route(
    family: str,
    loader: str,
    mode: str,
    backend: str = "comfyui",
    *,
    local_assets: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> PhysicalValidationEntry:
    route = resolve_model_backend_route(family, loader, mode, backend).as_dict()
    asset_map = dict(DEFAULT_LOCAL_TEST_ASSETS if local_assets is None else local_assets)
    status = _physical_status_for_route(route, asset_map)
    asset_info = asset_map.get((route["family"], route["loader"]), {})
    return PhysicalValidationEntry(
        backend=route["backend"],
        family=route["family"],
        loader=route["loader"],
        mode=route["mode"],
        route_state=route["state"],
        physical_status=status,
        reason=route.get("reason", ""),
        workflow_type=route.get("workflow_type"),
        compiler_id=route.get("compiler_id"),
        required_assets=list(route.get("requires") or []),
        local_asset_status=asset_info.get("asset_status", "not_declared"),
        known_issues=_known_issues_for_route(route),
        notes=list(asset_info.get("notes", [])),
    )


def physical_validation_payload(backend: str | None = None) -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "contract": "Physical validation is layered on top of route support; unit tests do not equal a manual model run.",
        "statuses": list(PHYSICAL_VALIDATION_STATUSES),
        "status_policy": PHYSICAL_VALIDATION_STATUS_POLICY,
        "manual_validation_checklist": list(MANUAL_VALIDATION_CHECKLIST),
        "local_test_assets": [
            {"family": family, "loader": loader, **info}
            for (family, loader), info in DEFAULT_LOCAL_TEST_ASSETS.items()
        ],
        "routes": list_physical_validation_matrix(backend),
    }
