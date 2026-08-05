from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE15_STATE, PHASE16_STATE, PHASE17_STATE, PHASE18_STATE, get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.providers.compile_router import select_comfy_compile_route
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support
from tests.test_lanpaint_route_family_phase15_sd_onboarding import (
    PHASE14_ACTIVE,
    PHASE15_ACTIVE,
    PHASE15_ROUTES,
    _assets,
    _capabilities,
    _classes,
    _compile,
    _job,
)

SCHEMA_ID = "neo.validation.lanpaint_route_family_phase15.v1"
DATE = "2026-08-05"

PHASE16_ACTIVE = {
    "flux:diffusion_model:inpaint:lanpaint",
    "flux:gguf:inpaint:lanpaint",
}

PHASE17_ACTIVE = {
    "flux2_dev:diffusion_model:inpaint:lanpaint",
    "flux2_dev:gguf:inpaint:lanpaint",
    "flux2_klein:diffusion_model:inpaint:lanpaint",
    "flux2_klein:gguf:inpaint:lanpaint",
}

PHASE18_ACTIVE = {
    "qwen_image_edit_2509:diffusion_model:inpaint:lanpaint",
    "qwen_image_edit_2509:gguf:inpaint:lanpaint",
    "qwen_image_edit_2511:diffusion_model:inpaint:lanpaint",
    "qwen_image_edit_2511:gguf:inpaint:lanpaint",
}


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def build_report() -> dict[str, Any]:
    registry = lanpaint_family_adapter_registry("comfyui_portable")
    adapters = {item["identity"]["route_key"]: item for item in registry["adapters"]}
    compiled = {(family, loader): _compile(family, loader) for family, loader in sorted(PHASE15_ROUTES)}
    classes = {key: _classes(value) for key, value in compiled.items()}

    previous_fingerprints = {
        "krea2_turbo:diffusion_model:inpaint:lanpaint": "a612a352cdd274ba92ff729258710c47ad54b67a7b351037715b079993f173f5",
        "krea2_turbo:gguf:inpaint:lanpaint": "3a776817ecec7c1b2f9bd2cf1ca498f728ecbda1fdecf39477b02d5224fb51a3",
        "qwen_image:diffusion_model:inpaint:lanpaint": "8973b4959ad180c2fec89283107e54dac716a0b6037fb65fb213ff2e0e4292cc",
        "qwen_image:gguf:inpaint:lanpaint": "6fc313f0e63fcb0ad75a49c151f408330a2ad3b260dde1ffe3282b2475c12335",
        "z_image:diffusion_model:inpaint:lanpaint": "fae299eddbe4e93a29a771403aafa46a365e6344740c1abd0472412bbf3703db",
        "z_image:gguf:inpaint:lanpaint": "a19000001da12db7be15c43262b5ad72c0693506d3b274898cab09faa103768e",
        "z_image_turbo:diffusion_model:inpaint:lanpaint": "db5aa5eb63546eafed429c43405876a260755632d99dba2e3247c9e897a0567b",
        "z_image_turbo:gguf:inpaint:lanpaint": "a669a8f058de6163d9877a8c02ac262a4b3331eea3b8575c2178abc8fc9a3c29",
    }

    sdxl = compiled[("sdxl", "checkpoint")]
    sd15 = compiled[("sd15", "checkpoint")]
    sd35_native = compiled[("sd35", "diffusion_model")]
    sd35_gguf = compiled[("sd35", "gguf")]

    plain_sd35 = _compile("sd35", "gguf", capabilities=_capabilities("sd35", "gguf", include_lora=False))
    lora_sd35 = _compile("sd35", "gguf", explicit_lora=True)
    lora_profile = lora_sd35.backend_payload["actual_params"]["_neo_lora_patch_profile"]

    capability_ready = {
        route: evaluate_lanpaint_route_capabilities(
            _capabilities(*route),
            provider_id="comfyui_portable",
            family=route[0],
            loader=route[1],
            selected_assets=_assets(*route),
        )
        for route in PHASE15_ROUTES
    }
    capability_blocked = evaluate_lanpaint_route_capabilities(
        _capabilities("sd35", "gguf", remove="TripleCLIPLoaderGGUF"),
        provider_id="comfyui_portable",
        family="sd35",
        loader="gguf",
        selected_assets=_assets("sd35", "gguf"),
    )

    sd15_gguf_adapter = get_lanpaint_family_adapter("sd15", loader="gguf", provider_id="comfyui_portable")
    sd15_gguf_profile = get_lanpaint_family_expansion_profile("sd15", loader="gguf", provider_id="comfyui_portable")
    sd15_gguf_route = select_comfy_compile_route(_job("sd15", "gguf"))

    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    public_sources = [
        ROOT / "neo_app/image/lanpaint_family_adapter.py",
        ROOT / "neo_app/image/lanpaint_family_policies.py",
        ROOT / "neo_app/image/lanpaint_capabilities.py",
        ROOT / "neo_app/providers/comfy_workflows/lanpaint_sd.py",
        ROOT / "neo_app/static/js/neo.js",
    ]
    path_pattern = re.compile(r"(?:/(?:home|Users|mnt)/|[A-Za-z]:\\(?:Users|Documents and Settings)\\)")
    path_hits = [path.relative_to(ROOT).as_posix() for path in public_sources if path_pattern.search(path.read_text(encoding="utf-8"))]

    checks = [
        _check(
            "exact_active_route_set",
            registry["onboarding_state"] == PHASE18_STATE
            and len(registry["adapters"]) == 28
            and set(registry["active_route_keys"]) == PHASE14_ACTIVE | PHASE15_ACTIVE | PHASE16_ACTIVE | PHASE17_ACTIVE | PHASE18_ACTIVE,
            "Phase 15 adds exactly four SD routes; the current registry may also include the exact later Flux.1 and Flux.2 slices.",
        ),
        _check(
            "phase15_bindings_are_exact",
            {item["identity"]["route_key"] for item in registry["adapters"] if item["binding"].get("new_route_activated_by_phase15")} == PHASE15_ACTIVE,
            "Only SDXL checkpoint, SD 1.5 checkpoint, and SD 3.5 safetensors/GGUF are newly bound.",
        ),
        _check(
            "previous_adapter_fingerprints_stable",
            all(adapters[key]["adapter_fingerprint"] == value for key, value in previous_fingerprints.items()),
            "All eight Phase 14 adapter fingerprints remain byte-stable for replay compatibility.",
        ),
        _check(
            "exact_routes_compile_experimental",
            all(item.compile_status == "compiled" and item.backend_payload["actual_params"]["lanpaint_capability_report"]["status"] == "experimental_available" for item in compiled.values()),
            "All four exact SD routes compile and remain experimental pending physical validation.",
        ),
        _check(
            "checkpoint_graph_parity",
            classes[("sdxl", "checkpoint")] == classes[("sd15", "checkpoint")]
            and classes[("sdxl", "checkpoint")].count("CheckpointLoaderSimple") == 1
            and sdxl.backend_payload["actual_params"]["steps"] != sd15.backend_payload["actual_params"]["steps"],
            "SDXL and SD 1.5 share checkpoint topology while retaining family-owned defaults.",
        ),
        _check(
            "sd35_loader_parity",
            "UNETLoader" in classes[("sd35", "diffusion_model")]
            and "TripleCLIPLoader" in classes[("sd35", "diffusion_model")]
            and "UnetLoaderGGUF" in classes[("sd35", "gguf")]
            and "TripleCLIPLoaderGGUF" in classes[("sd35", "gguf")],
            "SD 3.5 safetensors and GGUF use exact model and triple-encoder loader branches.",
        ),
        _check(
            "sd35_sampling_is_family_native",
            all("ModelSamplingSD3" in classes[key] and "ModelSamplingAuraFlow" not in classes[key] and "DifferentialDiffusionAdvanced" not in classes[key] for key in (("sd35", "diffusion_model"), ("sd35", "gguf"))),
            "SD 3.5 uses ModelSamplingSD3 and does not inherit Qwen/Z AuraFlow or Krea Differential Diffusion.",
        ),
        _check(
            "sd15_gguf_is_honestly_blocked",
            sd15_gguf_adapter["binding"]["selectable"] is False
            and sd15_gguf_profile["onboarding"]["state"] == "blocked_loader_ecosystem"
            and sd15_gguf_route.status in {"implementation_target", "unsupported"},
            "SD 1.5 GGUF is an explicit non-selectable ecosystem blocker, not a fake executable route.",
        ),
        _check(
            "capability_gate_is_exact",
            all(item["status"] == "experimental_available" for item in capability_ready.values())
            and capability_blocked["status"] == "blocked_missing_nodes"
            and any("TripleCLIPLoaderGGUF" in item["message"] for item in capability_blocked["blockers"]),
            "Capability detection validates exact SD loaders, triple encoders, sampling transforms, and assets.",
        ),
        _check(
            "plain_lanpaint_has_no_lora_dependency",
            plain_sd35.compile_status == "compiled"
            and not any(node["class_type"].startswith("LoraLoader") for node in plain_sd35.backend_payload["prompt"].values())
            and plain_sd35.backend_payload["lanpaint_route_capabilities"]["lora"]["requested"] is False,
            "Plain SD LanPaint compiles without any LoRA loader or graph mutation.",
        ),
        _check(
            "explicit_lora_anchors_are_exact",
            lora_profile["compatibility_route_key"] == "sd35:gguf:inpaint"
            and lora_profile["workflow_route_key"] == "sd35:gguf:inpaint:lanpaint"
            and lora_profile["loader_node_class"] == "LoraLoader"
            and lora_profile["requires_model"] is True
            and lora_profile["requires_clip"] is True,
            "An explicit SD 3.5 LoRA request uses model+CLIP compatibility while retaining LanPaint graph lineage.",
        ),
        _check(
            "lora_engine_independence",
            all(
                route_support("comfyui", family, loader, "inpaint", engine="native")["compatibility_route_key"]
                == route_support("comfyui", family, loader, "inpaint", engine="lanpaint")["compatibility_route_key"]
                == f"{family}:{loader}:inpaint"
                for family, loader in PHASE15_ROUTES
            ),
            "Native Inpaint and LanPaint share the same SD family/loader LoRA compatibility key.",
        ),
        _check(
            "replay_lineage_is_adapter_bound",
            all(
                item.backend_payload["actual_params"]["lanpaint_replay"]["family_adapter"]["adapter_fingerprint"]
                == item.backend_payload["actual_params"]["lanpaint_family_adapter_fingerprint"]
                for item in compiled.values()
            ),
            "Replay records the exact SD adapter identity and fingerprint.",
        ),
        _check(
            "frontend_and_cache_are_current",
            all(marker in js for marker in ("sdxl", "sd15", "sd35", "ModelSamplingSD3", "sd3_crop_stitch_v1", "lanpaint_sd_family_phase15_20260805"))
            and "phase15=lanpaint_sd_family_20260805" in index,
            "Frontend route policy, diagnostics, graph labels, and cache revision include the SD onboarding slice.",
        ),
        _check("no_personal_paths", not path_hits, "Phase 15 public source contains no personal or machine-specific absolute paths."),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "phase": 15,
        "title": "LanPaint SD family onboarding",
        "status": "passed" if not failed else "failed",
        "routes": sorted(PHASE15_ACTIVE),
        "adapter_registry_fingerprint": registry["registry_fingerprint"],
        "path_hits": path_hits,
        "physical_validation": {
            "status": "not_run",
            "reason": "The packaging environment does not host a live target ComfyUI profile with SDXL, SD 1.5, and SD 3.5 assets.",
            "required_next": "Run each activated SD route with LoRA off/on, verify dimensions and stitching, and replay the persisted result before promotion.",
        },
        "checks": checks,
        "summary": {
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "total": len(checks),
            "failed_ids": [item["id"] for item in failed],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Phase 15 LanPaint SD family onboarding.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(report, indent=2 if (args.pretty or args.output) else None, ensure_ascii=False, sort_keys=not (args.pretty or args.output)) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
