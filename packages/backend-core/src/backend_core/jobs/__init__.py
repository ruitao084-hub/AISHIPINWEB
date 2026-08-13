"""Job execution: the worker half of §22's orchestrator.

Kept in `backend_core` rather than in the worker app so the API and the worker
agree on one state machine. §5.1 puts the Celery entrypoint in `apps/worker`;
everything it calls lives here.
"""

from backend_core.jobs.ingestion import ingest_provider_media
from backend_core.jobs.runner import VideoJobRunner

__all__ = ["VideoJobRunner", "ingest_provider_media"]
