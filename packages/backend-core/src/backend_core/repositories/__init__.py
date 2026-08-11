"""Data access.

Every query against a workspace-scoped table filters by ``workspace_id`` (§9).
Repositories return domain objects rather than leaking ORM rows upward, so a
service cannot accidentally trigger lazy loading across a transaction
boundary.

Populated from PHASE 3.
"""
