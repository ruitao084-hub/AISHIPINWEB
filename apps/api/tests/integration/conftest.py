"""Fixtures for API integration tests.

Everything under this directory talks to a real Postgres and Redis. Unit tests
live one level up and must stay runnable with no services at all — which is
why these fixtures are scoped here rather than in the parent conftest.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from aipvs_api.app import create_app
from backend_core.cache import close_redis
from backend_core.db import dispose_engines, get_async_engine

_HERE = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything in this directory as `integration`.

    A module-level ``pytestmark`` in a conftest applies only to tests defined
    *in that conftest*, not to sibling modules — so without this hook these
    tests were collected as unit tests and passed locally purely because a
    database happened to be running. In CI's unit job, which has no services,
    they would have failed.
    """
    for item in items:
        if item.path.is_relative_to(_HERE):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncIterator[None]:
    """Reset database rows and rate-limit counters around each test.

    The counters matter as much as the rows: every test registers from the same
    client address, so without clearing them the 5-per-hour registration limit
    (which is doing exactly its job) fails the sixth test in the file. Cleared
    rather than raised — the limit protecting production should not be relaxed
    to suit a test suite. ``test_ratelimit.py`` asserts it still bites.

    The teardown also **disposes the pools this fixture opened**. Engines and
    Redis clients are cached per event loop (so a Celery worker calling
    ``asyncio.run`` per job gets a usable client), and pytest-asyncio gives
    each test a fresh loop — so every test that touches the database here
    builds a new pool. Without disposal those connections accumulate until
    Postgres refuses with "sorry, too many clients already", which is what
    happened the moment the integration suite passed roughly a hundred tests.
    The app's own lifespan disposes the pools it opened on *its* loop; this
    covers the ones the fixture opened on the test's.
    """
    await _flush_rate_limits()
    yield
    try:
        engine = get_async_engine()
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE workspace_members, workspaces, users CASCADE"))
        await _flush_rate_limits()
    finally:
        await dispose_engines()
        await close_redis()


async def _flush_rate_limits() -> None:
    from backend_core.cache import get_redis

    redis = get_redis()
    keys = [key async for key in redis.scan_iter(match="ratelimit:*")]
    if keys:
        await redis.delete(*keys)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def unique_email(prefix: str = "user") -> str:
    """An address that cannot collide with another test's."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


class ApiUser:
    """A registered user plus what is needed to act as them."""

    def __init__(self, client: TestClient, body: dict[str, object], password: str) -> None:
        self.client = client
        self.password = password
        self.access_token = str(body["access_token"])
        user = body["user"]
        assert isinstance(user, dict)
        self.id = uuid.UUID(str(user["id"]))
        self.email = str(user["email"])
        self.display_name = str(user["display_name"])

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


DEFAULT_PASSWORD = "correct-horse-battery"


def register_user(
    client: TestClient, *, prefix: str = "user", password: str = DEFAULT_PASSWORD
) -> ApiUser:
    """Register a user and return an authenticated handle."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": unique_email(prefix),
            "password": password,
            "display_name": prefix.title(),
        },
    )
    assert response.status_code == 201, response.text
    return ApiUser(client, response.json(), password)


def sole_workspace_id(user: ApiUser) -> uuid.UUID:
    """The workspace created for a user at registration."""
    response = user.client.get("/api/v1/workspaces", headers=user.auth)
    assert response.status_code == 200, response.text
    workspaces = response.json()
    assert len(workspaces) == 1
    return uuid.UUID(workspaces[0]["id"])


def add_member(owner: ApiUser, workspace_id: uuid.UUID, invitee: ApiUser, role: str) -> uuid.UUID:
    """Add ``invitee`` to ``workspace_id`` with ``role``. Returns the member id."""
    response = owner.client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=owner.auth,
        json={"email": invitee.email, "role": role},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])
