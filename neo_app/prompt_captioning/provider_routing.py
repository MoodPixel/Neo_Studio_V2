from __future__ import annotations

from typing import Any


def provider_execution_gate(profile: dict[str, Any] | None) -> tuple[bool, str]:
    """Return whether Prompt/Caption execution is wired for the selected provider."""
    if not profile:
        return False, "No backend profile is configured."
    provider_id = str(profile.get("provider_id") or "").strip()
    if provider_id == "comfy_llamacpp":
        runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
        readiness = runtime.get("readiness") if isinstance(runtime.get("readiness"), dict) else {}
        discovery = runtime.get("backend_capabilities") if isinstance(runtime.get("backend_capabilities"), dict) else {}
        if readiness:
            if not readiness.get("prompt_ready"):
                blocker = readiness.get("prompt_blocker") if isinstance(readiness.get("prompt_blocker"), dict) else {}
                detail = str(blocker.get("detail") or "ComfyUI LLM/VLM Prompt readiness is incomplete.")
                action = str(blocker.get("next_action") or "Run Connect/Test and review backend readiness.")
                return False, f"{detail} Next: {action}"
            return True, ""
        if not runtime.get("reachable"):
            return False, "Start ComfyUI and run Connect/Test so Neo can discover the llama.cpp/VLM nodes and models."
        if not discovery.get("node_pack_ready"):
            return False, "ComfyUI is connected, but llama_cpp_model_loader / llama_cpp_instruct_adv were not detected. Install or update ComfyUI-llama-cpp_vlm and restart ComfyUI."
        if not discovery.get("text_ready"):
            return False, "ComfyUI llama.cpp nodes are detected, but no usable main LLM/VLM model is exposed by the loader."
        if not discovery.get("text_execution_ready"):
            return False, "ComfyUI llama.cpp is detected, but NeoPromptCaptionTextOutput is missing. Copy Neo Studio's bundled neo_prompt_captioning folder into ComfyUI/custom_nodes, restart ComfyUI, then Connect/Test again."
        return True, ""
    return True, ""
