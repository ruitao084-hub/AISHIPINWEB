"""Shared request/response conventions for ``/api/v1``.

Lives apart from `router` so resource modules can import it without the import
cycle that pulling from the module which imports *them* would create.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiRequest(BaseModel):
    """Base for every request body: unknown fields are an error, not noise.

    Pydantic's default is to ignore what it does not recognise, and for a
    request body that is a bad default in two different ways.

    A client that PATCHes `duration_secondss` gets a 200 and no change, and
    believes it worked. Worse, it is how a read-only field becomes writable by
    accident: PHASE 8's shot editor deliberately offers no `visual_prompt`
    field, because §19 forbids handing a video model a sentence a user typed —
    and silently ignoring one sent anyway would make that prohibition look
    enforced while teaching callers it is optional.

    Rejecting is also kinder. "unexpected field: visual_prompt" is a fixable
    message; silence is not.
    """

    model_config = ConfigDict(extra="forbid")


__all__ = ["ApiRequest"]
