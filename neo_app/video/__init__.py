# Video surface contract helpers live in neo_app.video.output_paths.
#
# Video LoRA integrations are installed during package initialization. Each
# adapter is idempotent and keeps the route compiler as graph authority while
# adding the universal compiler-owned LoRA patch-profile/runtime bridge.
from neo_app.video.minimax_h3_lora_integration import install_minimax_h3_lora_integration
from neo_app.video.ltx_lora_integration import install_ltx_lora_integration
from neo_app.video.wan_lora_integration import install_wan_lora_integration
from neo_app.video.wan_lora_payload_context import install_wan_lora_payload_context_guard

install_minimax_h3_lora_integration()
install_ltx_lora_integration()
install_wan_lora_integration()
install_wan_lora_payload_context_guard()
