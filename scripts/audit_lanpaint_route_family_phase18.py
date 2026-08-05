from __future__ import annotations

import json
from pathlib import Path

from neo_app.image.lanpaint_family_adapter import PHASE18_STATE, PHASE20_STATE, PHASE21_STATE, PHASE22_STATE, get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_app.models.registry import get_family
from neo_app.models.route_matrix import resolve_model_backend_route
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    checks = []
    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    family = get_family("qwen_image_edit_2511")
    check("2511 first-class family", bool(family and family.supported_loaders == ["diffusion_model", "gguf"]))
    for f in ("qwen_image_edit_2509", "qwen_image_edit_2511"):
        policy = get_lanpaint_family_policy(f)
        check(f"{f} complete policy", bool(policy and policy["validation"]["ok"] and policy["conditioning_policy"]["positive"]["node_class"] == "TextEncodeQwenImageEditPlus"))
        for loader in ("diffusion_model", "gguf"):
            adapter = get_lanpaint_family_adapter(f, loader=loader, provider_id="comfyui_portable")
            check(f"{f} {loader} adapter bound", bool(adapter["binding"]["selectable"] and adapter["binding"]["graph_profile"] == "qwen_edit_crop_stitch_aura_v1"))
            profile = get_lanpaint_family_expansion_profile(f, loader=loader, provider_id="comfyui_portable")
            check(f"{f} {loader} expansion onboarded", bool(profile and profile["execution"]["state"] == "phase18_onboarded"))
            check(f"{f} {loader} LoRA matrix", route_support("comfyui_portable", f, loader, "inpaint")["state"] == "experimental_available")
    for loader in ("diffusion_model", "gguf"):
        for mode in ("txt2img", "img2img", "edit", "inpaint", "outpaint"):
            check(f"2511 {loader} {mode} route", resolve_model_backend_route("qwen_image_edit_2511", loader, mode, "comfyui").state == "available")
    registry = lanpaint_family_adapter_registry("comfyui_portable")
    check("registry phase state", registry["onboarding_state"] in {PHASE20_STATE, PHASE21_STATE, PHASE22_STATE})
    check("registry active count", len(registry["active_route_keys"]) == 28, str(len(registry["active_route_keys"])))
    check("2511 UI family marker", "qwen_image_edit_2511" in (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8"))
    check("Phase 18 records", (ROOT / "neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE18_QWEN_IMAGE_EDIT_VARIANTS_20260805.md").exists())
    result = {"schema_id": "neo.validation.lanpaint_phase18.v1", "checks": checks, "passed": sum(1 for x in checks if x["ok"]), "total": len(checks), "ok": all(x["ok"] for x in checks)}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
