"""Pydantic models for the API and AI boundaries.

Two distinct jobs:

* HTTP request/response shapes, which become the OpenAPI contract (§5.2).
* Schemas that validate *AI* output (§107). A model returning malformed JSON
  must never reach the database — parse, validate, retry, then fall back.

Populated from PHASE 3.
"""
