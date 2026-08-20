"""Neo Studio Prompt & Captioning ComfyUI bridge nodes.

Copy this folder into ``ComfyUI/custom_nodes/neo_prompt_captioning`` and restart
ComfyUI. The bridge owns Neo's stable image/text handoff and also provides a
Generic MTMD fallback for multimodal GGUF + mmproj pairs that do not have a
compatible dedicated handler in the third-party llama.cpp Comfy extension.

Nodes:
- ``NeoPromptCaptionImageInput`` decodes Neo image data into a Comfy IMAGE.
- ``NeoPromptCaptionTextOutput`` owns the stable text result read from history.
- ``NeoGenericMTMDModelLoader`` loads template-driven multimodal GGUF/mmproj.
- ``NeoGenericMTMDInstruct`` performs the Generic MTMD image+text completion.
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
