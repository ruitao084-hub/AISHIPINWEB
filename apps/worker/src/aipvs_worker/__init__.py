"""AI Product Video Studio async job worker.

Owns the Celery entrypoint only (taskbook §5.1); all job logic lives in
``backend_core.jobs`` so the API and this worker agree on one state machine.

Queues consumed, kept separate so a slow render cannot starve shot generation
(§25): ``video_generation``, ``tts``, ``qc``.

The Celery application and task registrations land in P9-T04. PHASE 0 only
establishes the package inside the uv workspace so lint, typing and tests cover
it from the first commit.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
