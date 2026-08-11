"""Development entrypoint: ``python -m aipvs_api.main``.

Production runs the ASGI app through a process manager instead — see
``infra/docker/Dockerfile.api`` (P1) and docs/operations/.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Run the API with the local development server."""
    uvicorn.run(
        "aipvs_api.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
