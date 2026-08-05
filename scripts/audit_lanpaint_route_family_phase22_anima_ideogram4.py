from __future__ import annotations

import json
import re
import runpy
from pathlib import Path

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE22_STATE, get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_app.models.route_matrix import resolve_model_backend_route
from neo_app.providers.comfy_workflows.lanpaint import LANPAINT_OBJECT_INFO_NODE_CLASSES
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support

ROOT = Path(__file__).resolve().parents[1]
HELPERS = runpy.run_path(str(ROOT / "tests/test_lanpaint_route_family_phase22_anima_ideogram4.py"))
ASSETS = HELPERS["ASSETS"]
_compile_native = HELPERS["_compile_native"]
_compile_lanpaint = HELPERS["_compile_lanpaint"]
_caps = HELPERS["_caps"]


def main() -> int:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    registry = lanpaint_family_adapter_registry("comfyui_portable")
    phase22_routes = {f"{family}:{loader}:inpaint:lanpaint" for family in ("anima", "ideogram4") for loader in ("diffusion_model", "gguf")}
    check("registry Phase 22 state", registry.get("onboarding_state") == PHASE22_STATE, str(registry.get("onboarding_state")))
    check("four Phase 22 adapters onboarded", set(registry.get("phase22_onboarded_route_keys", [])) == phase22_routes)
    check("all Phase 22 routes active", phase22_routes <= set(registry.get("active_route_keys", [])))

    manifest = json.loads((ROOT / "neo_app/models/model_family_manifest.json").read_text(encoding="utf-8"))
    families = {item.get("family_id"): item for item in manifest.get("families", [])}
    check("Anima family registered", "anima" in families)
    check("Ideogram 4 family registered", "ideogram4" in families)
    check("Anima img2img experimental", families.get("anima", {}).get("loader_mode_support", {}).get("gguf", {}).get("img2img") == "experimental_available")
    check("Ideogram img2img held", families.get("ideogram4", {}).get("loader_mode_support", {}).get("gguf", {}).get("img2img") == "held_unverified")

    required_discovery = {"EmptyLatentImage", "KSampler", "EmptyFlux2LatentImage", "Ideogram4Scheduler", "DualModelGuider", "SamplerCustomAdvanced", "LanPaint_SamplerCustomAdvanced"}
    check("provider discovery includes Phase 22 nodes", required_discovery <= set(LANPAINT_OBJECT_INFO_NODE_CLASSES))

    for family in ("anima", "ideogram4"):
        for loader in ("diffusion_model", "gguf"):
            key = f"{family}:{loader}"
            policy = get_lanpaint_family_policy(family, loader=loader, provider_id="comfyui_portable")
            adapter = get_lanpaint_family_adapter(family, loader=loader, provider_id="comfyui_portable")
            expansion = get_lanpaint_family_expansion_profile(family, loader=loader, provider_id="comfyui_portable")
            check(f"{key} policy complete", bool(policy and policy.get("validation", {}).get("ok")))
            check(f"{key} adapter bound", bool(adapter and adapter.get("validation", {}).get("ok") and adapter.get("binding", {}).get("selectable")))
            check(f"{key} expansion onboarded", bool(expansion and expansion.get("onboarding", {}).get("state") == "onboarded_phase22"))

            selected = {"model": ASSETS[family][loader]["model"], "text_encoder": ASSETS[family][loader]["clip"], "vae": ASSETS[family][loader]["vae"]}
            report = evaluate_lanpaint_route_capabilities(_caps(family, loader), provider_id="comfyui_portable", family=family, loader=loader, selected_assets=selected)
            check(f"{key} capability available", report.get("status") == "experimental_available", str(report.get("status")))

            compiled = _compile_lanpaint(family, loader)
            classes = [node.get("class_type") for node in compiled.backend_payload.get("prompt", {}).values()]
            actual = compiled.backend_payload.get("actual_params", {})
            replay = actual.get("lanpaint_replay", {})
            if family == "anima":
                topology = "LanPaint_KSampler" in classes and "LanPaint_SamplerCustomAdvanced" not in classes
                lora = actual.get("_neo_lora_patch_profile", {}).get("strategy") == "lora_loader_model_only_consumer_rewire"
                replay_ok = replay.get("route", {}).get("sampler_contract") == "basic" and not replay.get("route", {}).get("dual_model_required")
            else:
                topology = (
                    "LanPaint_SamplerCustomAdvanced" in classes
                    and "LanPaint_KSampler" not in classes
                    and "Ideogram4Scheduler" in classes
                    and "DualModelGuider" in classes
                    and classes.count("UNETLoader" if loader == "diffusion_model" else "UnetLoaderGGUF") == 2
                )
                lora = actual.get("_neo_lora_patch_profile", {}).get("strategy") == "none"
                replay_ok = replay.get("route", {}).get("sampler_contract") == "custom_advanced" and replay.get("route", {}).get("dual_model_required") is True
            check(f"{key} LanPaint topology", compiled.compile_status == "compiled" and topology)
            check(f"{key} LoRA boundary", lora)
            check(f"{key} replay sampler identity", replay_ok)

    for loader in ("diffusion_model", "gguf"):
        anima_txt = _compile_native("anima", loader, "txt2img")
        anima_img = _compile_native("anima", loader, "img2img")
        txt_classes = [node.get("class_type") for node in anima_txt.backend_payload.get("prompt", {}).values()]
        img_classes = [node.get("class_type") for node in anima_img.backend_payload.get("prompt", {}).values()]
        check(f"Anima {loader} txt2img", anima_txt.compile_status == "compiled" and "EmptyLatentImage" in txt_classes and "VAEEncode" not in txt_classes)
        check(f"Anima {loader} img2img", anima_img.compile_status == "compiled" and "LoadImage" in img_classes and "VAEEncode" in img_classes)

        ideo = _compile_native("ideogram4", loader, "txt2img")
        classes = [node.get("class_type") for node in ideo.backend_payload.get("prompt", {}).values()]
        check(f"Ideogram {loader} txt2img paired advanced", ideo.compile_status == "compiled" and "Ideogram4Scheduler" in classes and "DualModelGuider" in classes and "SamplerCustomAdvanced" in classes)
        held = resolve_model_backend_route("ideogram4", loader, "img2img", "comfyui")
        check(f"Ideogram {loader} img2img held", not held.selectable)

        native = route_support("comfyui_portable", "anima", loader, "inpaint", engine="native")
        lanpaint = route_support("comfyui_portable", "anima", loader, "inpaint", engine="lanpaint")
        check(f"Anima {loader} LoRA engine-independent", native.get("compatibility_route_key") == lanpaint.get("compatibility_route_key") == f"anima:{loader}:inpaint")
        ideogram_lora = route_support("comfyui_portable", "ideogram4", loader, "inpaint", engine="lanpaint")
        check(f"Ideogram {loader} LoRA fail-closed", ideogram_lora.get("route_state") == "planned_gated" and ideogram_lora.get("graph_patch") == "none")

    blocked = evaluate_lanpaint_route_capabilities(
        _caps("ideogram4", "gguf", remove_node="LanPaint_SamplerCustomAdvanced"),
        provider_id="comfyui_portable",
        family="ideogram4",
        loader="gguf",
        selected_assets={"model": ASSETS["ideogram4"]["gguf"]["model"], "text_encoder": ASSETS["ideogram4"]["gguf"]["clip"], "vae": ASSETS["ideogram4"]["gguf"]["vae"]},
    )
    check("missing custom advanced node fails closed", blocked.get("status") == "blocked_missing_nodes")

    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    check("frontend Phase 22 cache revision", "lanpaint_anima_ideogram4_phase22_20260805" in js and "phase22=lanpaint_anima_ideogram4_20260805" in index)
    check("frontend family labels", "Anima Base v1" in js and "Ideogram 4 Dual-Model GGUF Runtime" in js)

    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (
            ROOT / "neo_app/providers/comfy_workflows/phase22_families.py",
            ROOT / "neo_app/providers/comfy_workflows/lanpaint_phase22.py",
            ROOT / "neo_app/image/lanpaint_family_policies.py",
            ROOT / "neo_app/image/lanpaint_family_adapter.py",
        )
    )
    windows_absolute = re.compile(r"(?<![A-Za-z0-9+.\-])\b[A-Za-z]:[\\/]")
    named_posix_home = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^/\s]+/")
    check("no personal absolute paths", not windows_absolute.search(source_text) and not named_posix_home.search(source_text))
    check("Phase 22 provider record", (ROOT / "neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE22_ANIMA_IDEOGRAM4_20260805.md").exists())
    check("Phase 22 validation record", (ROOT / "neo_system_records/09_VALIDATION/LANPAINT_ROUTE_FAMILY_PHASE22_ANIMA_IDEOGRAM4_20260805.md").exists())

    result = {
        "schema_id": "neo.validation.lanpaint_phase22_anima_ideogram4.v1",
        "phase_state": PHASE22_STATE,
        "checks": checks,
        "passed": sum(1 for item in checks if item["ok"]),
        "total": len(checks),
        "ok": all(item["ok"] for item in checks),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
