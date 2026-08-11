"""Verify the uv workspace resolves every Python package to one shared core.

This is the PHASE 0 guarantee: all three apps import the *same* ``backend_core``
rather than each vendoring their own copy (taskbook §5.1). If someone later adds
an app that shadows the package, these tests fail.
"""

from __future__ import annotations

import backend_core


def test_backend_core_is_importable() -> None:
    assert backend_core.__version__


def test_every_app_resolves_the_same_backend_core() -> None:
    import aipvs_api
    import aipvs_render_worker
    import aipvs_worker

    # Importing the apps must not pull in a second, divergent copy.
    import backend_core as core_after_apps

    assert core_after_apps is backend_core
    assert aipvs_api.__version__
    assert aipvs_worker.__version__
    assert aipvs_render_worker.__version__


def test_backend_core_does_not_depend_on_any_app() -> None:
    """The dependency arrow points one way: apps -> core, never the reverse."""
    from pathlib import Path

    package_root = Path(backend_core.__file__).parent
    forbidden = ("aipvs_api", "aipvs_worker", "aipvs_render_worker")

    offenders = [
        f"{source.relative_to(package_root)}: imports {name}"
        for source in package_root.rglob("*.py")
        for name in forbidden
        if name in source.read_text(encoding="utf-8")
    ]

    assert not offenders, f"backend_core must not import app packages: {offenders}"
