"""Comfy provider workflow compiler modules.

Phase 12 keeps provider graph wiring behind family+loader+mode compile routes.
Comfy-specific node names stay inside these compiler modules and must not leak
into Image surface contracts.
"""

from neo_app.providers.compile_router import CompileRoute, select_comfy_compile_route

from neo_app.providers.comfy_workflows.checkpoint_sd import (
    SDCheckpointDefaults,
    resolve_sd_checkpoint_defaults,
    sd_checkpoint_workflow_type,
)
from neo_app.providers.comfy_workflows.flux_native import (
    FLUX_NATIVE_DEFAULTS,
    FluxNativeDefaults,
    compile_flux_native_txt2img,
)
from neo_app.providers.comfy_workflows.flux_gguf import (
    FLUX_GGUF_DEFAULTS,
    FluxGGUFDefaults,
    compile_flux_gguf_txt2img,
)

__all__ = [
    "CompileRoute",
    "select_comfy_compile_route",
    "SDCheckpointDefaults",
    "resolve_sd_checkpoint_defaults",
    "sd_checkpoint_workflow_type",
    "FLUX_NATIVE_DEFAULTS",
    "FluxNativeDefaults",
    "compile_flux_native_txt2img",
    "FLUX_GGUF_DEFAULTS",
    "FluxGGUFDefaults",
    "compile_flux_gguf_txt2img",
]

# Phase 12.12: qwen_gguf compiler is provider-local and routed via compile_router.
