"""AI Product Video Studio FFmpeg render worker.

Separated from ``aipvs_worker`` because rendering has a different resource
profile (CPU- and disk-bound, minutes-long) and must not be deployed onto
serverless functions that cap execution time (taskbook §71).

Non-negotiable rules for everything added here:

* The ``Timeline`` is the only source of shot order — never re-derive or guess
  it at render time (§33).
* FFmpeg is invoked with an argument array and never ``shell=True``; all paths
  are server-generated (§35).
* Every render gets an isolated temp dir cleaned up in a ``finally`` (§117).

The render pipeline lands in P13-T05. PHASE 0 only establishes the package.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
