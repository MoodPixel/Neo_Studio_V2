from __future__ import annotations

VOICE_BACKEND_CONTRACT_ENDPOINTS = [
    "GET /api/voice/health",
    "GET /api/voice/capabilities",
    "GET /api/voice/models",
    "GET /api/voice/voices",
    "POST /api/voice/preview",
    "POST /api/voice/render",
    "POST /api/voice/reference/upload",
    "POST /api/voice/reference/analyze",
    "GET /api/voice/reference/history",
    "GET /api/voice/jobs/{job_id}",
    "POST /api/voice/jobs/{job_id}/cancel",
    "POST /api/voice/jobs/{job_id}/retry_chunk",
    "GET /api/voice/history",
    "GET /api/voice/output-file",
]
