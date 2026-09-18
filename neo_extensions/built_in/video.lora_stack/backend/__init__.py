"""Universal Video LoRA Stack payload foundation.

Phase 2 defines portable payload contracts and normalization helpers only.
It intentionally does not inspect routes, mutate workflows, bind ComfyUI node
inputs, or apply LoRAs to MiniMax, WAN, or LTX compilers.
"""

EXTENSION_ID = "video.lora_stack"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "assets"
PAYLOAD_SCHEMA_VERSION = "neo.video.lora_stack.payload.v1"
