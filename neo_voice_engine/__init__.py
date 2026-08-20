"""Standalone Neo Voice Engine gateway/supervisor.

VO-E5A keeps Neo Studio source clean by resolving mutable Voice runtimes from
an external sibling Neo_Runtime/voice tree while preserving manifest-owned
worker isolation and the VO-E5 Chatterbox route.
"""

ENGINE_VERSION = "0.5.1"
ENGINE_PHASE = "VO-E5A"
SERVICE_ID = "neo_voice_engine"
PROTOCOL_ID = "neo.voice_engine.protocol.v1"
PROTOCOL_VERSION = "1.0"
