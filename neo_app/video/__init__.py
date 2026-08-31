# Video surface contract helpers live in neo_app.video.output_paths.
#
# Phase 5 installs the MiniMax H3 compiler-side Video LoRA integration during
# package initialization. The integration is idempotent and keeps the legacy H3
# compiler as graph authority while adding the universal compiler-owned LoRA
# patch-profile/runtime bridge.
from neo_app.video.minimax_h3_lora_integration import install_minimax_h3_lora_integration

install_minimax_h3_lora_integration()
