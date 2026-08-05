from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.models.route_matrix import resolve_model_backend_route
from neo_app.providers.comfy_provider import ComfyProvider
from neo_app.providers.compile_router import select_comfy_compile_route
from neo_app.providers.comfy_workflows.qwen_gguf import compile_qwen_gguf_txt2img
from neo_app.providers.schema import NeoJob, ProviderManifest, ProviderValidationResult

SCHEMA_ID = "neo.image.lanpaint_route_family_phase0_audit.v1"
ROUTE_FAMILY_ID = "image.inpaint.lanpaint"
DATE = "2026-08-03"

CORE_SAMPLE_NODE_ROLES = {
    "LoadImage": "source_image",
    "CropByMask": "crop_context",
    "ImageResizeKJv2": "processing_resize",
    "GrowMaskWithBlur": "mask_refinement",
    "VAEEncode": "latent_encode",
    "SetLatentNoiseMask": "latent_noise_mask",
    "DifferentialDiffusionAdvanced": "optional_family_model_transform",
    "LanPaint_KSampler": "lanpaint_sampler",
    "VAEDecode": "latent_decode",
    "ImageCompositeMasked": "stitch_composite",
}

AUTHORING_ONLY_SAMPLE_NODES = {
    "Note",
    "PreviewImage",
    "Image Comparer (rgthree)",
    "MaskPreview+",
    "SAM3Segment",
    "Switch mask [Crystools]",
    "CR Upscale Image",
    "Reroute",
}

FAMILY_CANDIDATES = [
    "krea2",
    "krea2_turbo",
    "qwen_image",
    "qwen_rapid_aio",
    "qwen_image_edit_2509",
    "z_image",
    "z_image_turbo",
]


def _manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id="comfyui_portable",
        display_name="ComfyUI Portable",
        provider_type="local",
        surfaces=["image"],
        status="configured",
        supported_modes=["txt2img", "img2img", "inpaint", "outpaint", "edit"],
        supported_families=FAMILY_CANDIDATES,
        supported_loaders=["checkpoint", "checkpoint_aio", "diffusion_model", "gguf"],
    )


def _krea2_job(loader: str) -> NeoJob:
    model = "krea2_turbo-Q5_K_M.gguf" if loader == "gguf" else "krea2_turbo_fp8_scaled.safetensors"
    params: dict[str, Any] = {
        "source_image": "source.png",
        "mask_image": "mask.png",
        "qwen3vl_text_encoder": "qwen3vl_4b_fp8_scaled.safetensors",
        "text_encoder_1": "qwen3vl_4b_fp8_scaled.safetensors",
        "vae": "qwen_image_vae.safetensors",
        "steps": 8,
        "cfg": 1.0,
        "seed": 1,
        "sampler": "euler",
        "scheduler": "simple",
        "denoise": 1.0,
    }
    params["gguf_model" if loader == "gguf" else "diffusion_model"] = model
    return NeoJob(
        surface="image",
        subtab="inpaint",
        mode="inpaint",
        provider_id="comfyui_portable",
        family="krea2_turbo",
        loader=loader,
        model=model,
        prompt="replace only the masked region",
        negative_prompt="",
        params=params,
    )


def _qwen_gguf_job() -> NeoJob:
    params = {
        "gguf_unet": "Qwen-Rapid-AIO-NSFW-v19_Q3_K.gguf",
        "qwen_text_encoder": "Qwen2.5-VL-7B-Instruct-abliterated.Q4_K_M.gguf",
        "qwen_mmproj": "Qwen2.5-VL-7B-Instruct-abliterated.mmproj-f16.gguf",
        "vae": "pig_qwen_image_vae_fp32-f16.gguf",
        "source_image": "source.png",
        "comfy_source_image_name": "source.png",
        "mask_image": "mask.png",
        "comfy_mask_image_name": "mask.png",
        "width": 896,
        "height": 1344,
        "sampler": "sa_solver",
        "scheduler": "beta",
    }
    return NeoJob(
        provider_id="comfyui_portable",
        surface="image",
        subtab="inpaint",
        family="qwen_image",
        loader="gguf",
        mode="inpaint",
        model=params["gguf_unet"],
        prompt="change only the masked shirt",
        negative_prompt="",
        params=params,
    )


def _classes(compiled: Any) -> list[str]:
    return sorted(
        {
            str(node.get("class_type"))
            for node in compiled.backend_payload.get("prompt", {}).values()
            if isinstance(node, dict) and node.get("class_type")
        }
    )


def _read_lora_states() -> dict[str, str]:
    path = ROOT / "neo_extensions/built_in/image.lora_stack/extension_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    states = payload.get("route_states") or {}
    keys = [
        "comfyui:krea2_turbo:gguf:inpaint",
        "comfyui_portable:krea2_turbo:gguf:inpaint",
        "comfyui:krea2:diffusion_model:inpaint",
        "comfyui:qwen_image:gguf:inpaint",
        "comfyui:z_image:gguf:inpaint",
    ]
    return {key: str(states.get(key) or "missing") for key in keys}


def _sample_inventory(workflow_path: Path | None) -> dict[str, Any]:
    if workflow_path is None:
        return {
            "provided": False,
            "note": "Run with --workflow to validate the submitted LanPaint sample workflow.",
        }
    payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    nodes = [node for node in payload.get("nodes", []) if isinstance(node, dict)]
    node_types = [str(node.get("type") or "") for node in nodes]
    counts: dict[str, int] = {}
    for node_type in node_types:
        counts[node_type] = counts.get(node_type, 0) + 1
    key_widgets: dict[str, list[Any]] = {}
    for node in nodes:
        node_type = str(node.get("type") or "")
        if node_type in {
            "UNETLoader",
            "CLIPLoader",
            "VAELoader",
            "CropByMask",
            "ImageResizeKJv2",
            "GrowMaskWithBlur",
            "DifferentialDiffusionAdvanced",
            "LanPaint_KSampler",
        }:
            key_widgets.setdefault(node_type, list(node.get("widgets_values") or []))

    text = workflow_path.read_text(encoding="utf-8")
    absolute_paths = sorted(
        set(
            re.findall(
                r"(?:[A-Za-z]:\\\\[^\"\r\n]+|/(?:home|Users|mnt|opt|srv)/[^\"\r\n]+)",
                text,
            )
        )
    )
    return {
        "provided": True,
        "filename": workflow_path.name,
        "node_count": len(nodes),
        "link_count": len(payload.get("links", [])),
        "group_count": len(payload.get("groups", [])),
        "node_type_counts": dict(sorted(counts.items())),
        "core_role_nodes": {node: CORE_SAMPLE_NODE_ROLES[node] for node in sorted(CORE_SAMPLE_NODE_ROLES) if node in counts},
        "authoring_only_nodes": sorted(node for node in AUTHORING_ONLY_SAMPLE_NODES if node in counts),
        "key_widgets": key_widgets,
        "absolute_paths": absolute_paths,
    }


def build_report(workflow_path: Path | None = None) -> dict[str, Any]:
    provider = ComfyProvider(_manifest())
    krea_graphs: dict[str, Any] = {}
    for loader in ("diffusion_model", "gguf"):
        job = _krea2_job(loader)
        route = select_comfy_compile_route(job)
        compiled = provider.compile_job(job)
        krea_graphs[loader] = {
            "route_status": route.status,
            "compiler_id": route.compiler_id,
            "workflow_type": route.workflow_type,
            "compile_status": compiled.compile_status,
            "node_classes": _classes(compiled),
        }

    qwen_job = _qwen_gguf_job()
    qwen_route = select_comfy_compile_route(qwen_job)
    qwen_compiled = compile_qwen_gguf_txt2img(
        provider_id="comfyui_portable",
        base_url="http://127.0.0.1:8188",
        job=qwen_job,
        validation=ProviderValidationResult(provider_id="comfyui_portable", ok=True),
        route=qwen_route,
        capabilities={},
        backend_capabilities={},
    )
    qwen_classes = _classes(qwen_compiled)

    route_rows: list[dict[str, Any]] = []
    for family in FAMILY_CANDIDATES:
        for loader in ("diffusion_model", "gguf"):
            row = resolve_model_backend_route(family, loader, "inpaint", "comfyui")
            route_rows.append(
                {
                    "family": family,
                    "loader": loader,
                    "state": row.state,
                    "compiler_id": row.compiler_id,
                    "selectable": row.selectable,
                }
            )

    lora_states = _read_lora_states()
    sample = _sample_inventory(workflow_path)
    product_files = list((ROOT / "neo_app").rglob("*.py")) + list((ROOT / "neo_extensions/built_in").rglob("extension_manifest.json"))
    contract_only_allowlist = {
        "neo_app/image/lanpaint_route_contract.py",
        "neo_app/image/lanpaint_workflow_abstraction.py",
        "neo_app/image/lanpaint_family_policies.py",
        "neo_app/image/lanpaint_family_expansion.py",
        "neo_app/providers/comfy_workflows/lanpaint.py",
    }
    contract_occurrences: list[str] = []
    route_family_occurrences: list[str] = []
    for path in product_files:
        try:
            if ROUTE_FAMILY_ID not in path.read_text(encoding="utf-8"):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in contract_only_allowlist:
                contract_occurrences.append(relative)
            else:
                route_family_occurrences.append(relative)
        except UnicodeDecodeError:
            continue

    contract_occurrences.sort()
    route_family_occurrences.sort()

    checks = [
        {
            "id": "route_family_activation_is_scoped_after_phase0",
            "passed": set(route_family_occurrences).issubset({"neo_app/image/lanpaint_family_adapter.py", "neo_app/models/route_matrix.py", "neo_app/providers/compile_router.py"}),
            "detail": "The Phase 0 baseline remains valid: later activation is scoped to the explicit adapter, route-matrix, and compile-router contracts while native routes stay unchanged.",
        },
        {
            "id": "krea2_native_current_path_locked",
            "passed": "KSampler" in krea_graphs["diffusion_model"]["node_classes"] and "LanPaint_KSampler" not in krea_graphs["diffusion_model"]["node_classes"],
            "detail": "Current Krea2 native inpaint remains the existing DifferentialDiffusion + normal KSampler route.",
        },
        {
            "id": "krea2_gguf_current_path_locked",
            "passed": "KSampler" in krea_graphs["gguf"]["node_classes"] and "LanPaint_KSampler" not in krea_graphs["gguf"]["node_classes"],
            "detail": "Current Krea2 GGUF inpaint remains the existing DifferentialDiffusion + normal KSampler route.",
        },
        {
            "id": "krea2_lora_inpaint_later_activation_is_engine_independent",
            "passed": lora_states["comfyui_portable:krea2_turbo:gguf:inpaint"] == "experimental_available",
            "detail": "The Phase 0 base graph remains unchanged while Phase 12 separately enables engine-independent Krea2 inpaint LoRA compatibility through compiler-owned anchors.",
        },
        {
            "id": "qwen_non_lanpaint_lock_preserved",
            "passed": "KSampler" in qwen_classes and "LanPaint_KSampler" not in qwen_classes,
            "detail": "Existing Qwen GGUF inpaint remains on its locked non-LanPaint path in Phase 0.",
        },
        {
            "id": "sample_contains_lanpaint_core" if sample.get("provided") else "sample_optional",
            "passed": (
                all(node in sample.get("node_type_counts", {}) for node in ("LanPaint_KSampler", "SetLatentNoiseMask", "VAEEncode", "VAEDecode", "ImageCompositeMasked"))
                if sample.get("provided")
                else True
            ),
            "detail": "Submitted sample contains the expected LanPaint latent and stitch stages." if sample.get("provided") else "No sample workflow was supplied to this audit invocation.",
        },
        {
            "id": "sample_public_path_hygiene" if sample.get("provided") else "sample_path_check_optional",
            "passed": not sample.get("absolute_paths", []),
            "detail": "Submitted sample contains no personal absolute paths." if sample.get("provided") else "No sample workflow was supplied to this audit invocation.",
        },
    ]

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "route_family_frame": {
            "route_family_id": ROUTE_FAMILY_ID,
            "engine": "lanpaint",
            "operation": "inpaint",
            "provider_scope": ["comfyui", "comfyui_portable"],
            "behavior_enabled_in_phase0": False,
            "selection_dimensions": ["provider", "family", "loader", "mode", "engine", "variant"],
            "first_implementation_target": {
                "family": "krea2_turbo",
                "loader": "gguf",
                "mode": "inpaint",
                "engine": "lanpaint",
            },
            "future_family_candidates": FAMILY_CANDIDATES,
        },
        "current_krea2_inpaint_graphs": krea_graphs,
        "current_qwen_gguf_inpaint": {
            "route_status": qwen_route.status,
            "compiler_id": qwen_route.compiler_id,
            "compile_status": qwen_compiled.compile_status,
            "node_classes": qwen_classes,
            "lock": "non_lanpaint_until_explicit_family_overlay_and_physical_validation",
        },
        "current_inpaint_route_matrix": route_rows,
        "current_lora_route_states": lora_states,
        "sample_workflow_inventory": sample,
        "contract_only_route_family_occurrences": contract_occurrences,
        "production_route_family_occurrences": route_family_occurrences,
        "checks": checks,
        "summary": {
            "passed": sum(1 for item in checks if item["passed"]),
            "failed": len(failed),
            "total": len(checks),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the Phase 0 LanPaint route-family baseline without enabling execution.")
    parser.add_argument("--workflow", type=Path, help="Optional submitted LanPaint workflow JSON to inventory.")
    parser.add_argument("--json-out", type=Path, help="Optional path for the machine-readable audit report.")
    args = parser.parse_args()

    report = build_report(args.workflow)
    output = json.dumps(report, indent=2, ensure_ascii=False)
    print(output)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(output + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
