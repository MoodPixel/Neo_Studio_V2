from __future__ import annotations

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.providers.base import BaseProvider
from neo_app.providers.schema import CompiledJob, NeoJob


class MockProvider(BaseProvider):
    """Safe placeholder provider used until real backend adapters are implemented."""

    def compile_job(self, job: NeoJob) -> CompiledJob:
        validation = self.validate_job(job)
        payload = {
            "neo_job": model_to_dict(job),
            "validation": model_to_dict(validation),
            "note": "Mock compiled payload. No backend-specific request is sent in Phase 6.",
        }
        return CompiledJob(provider_id=self.manifest.provider_id, backend_payload=payload)
