"""Export the FastAPI OpenAPI document to disk (taskbook §5.2, P2-T08).

The document is the contract between the API and the web app. Writing it to a
tracked file makes contract changes reviewable in a diff rather than invisible,
and lets CI detect when the checked-in TypeScript client has drifted from what
the API actually serves.

Usage::

    uv run python infra/scripts/export_openapi.py [output-path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_OUTPUT = Path("packages/shared-types/openapi.json")


def export(output: Path) -> Path:
    """Write the OpenAPI document, returning the path written."""
    # Imported late so a missing dependency reports as an import error from the
    # script rather than at module scope of a tool that may not need the app.
    from aipvs_api.app import create_app

    schema = create_app().openapi()

    output.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys keeps the diff meaningful: without it, dict ordering changes
    # produce noisy diffs that hide real contract changes. The trailing newline
    # keeps Prettier and git happy.
    output.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    written = export(output)
    print(f"OpenAPI schema written to {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
