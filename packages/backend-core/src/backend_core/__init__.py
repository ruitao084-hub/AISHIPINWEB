"""Shared backend core for AI Product Video Studio.

Taskbook §5.1 forbids ``apps/api``, ``apps/worker`` and ``apps/render-worker``
from each carrying their own copy of the domain, provider and repository code.
They all import it from here instead; only the HTTP controllers, the Celery
entrypoint and the FFmpeg worker entrypoint stay inside their own app.

The module tree this package is required to host is::

    domain/          schemas/       repositories/   services/
    providers/       prompts/       storage/        jobs/
    security/        observability/

Those land in P2-T09, which is the task that formally establishes this package.
PHASE 0 only wires it into the uv workspace so every app resolves one shared
importable core from the very first commit.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
