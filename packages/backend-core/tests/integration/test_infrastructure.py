"""PHASE 1 acceptance: every backing service is genuinely reachable.

Taskbook §78 requires automated proof that the database connects, Redis
round-trips, MinIO stores and retrieves an object, and the API reports health.
These tests talk to real services — they are excluded from the default unit run
and executed by ``make test-integration`` and the CI integration job.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from backend_core.cache import get_redis, ping_redis
from backend_core.db import get_async_session, ping_database
from backend_core.storage import (
    ObjectNotFoundError,
    get_storage,
    ping_storage,
    product_original_key,
)

pytestmark = pytest.mark.integration


class TestDatabase:
    """P1-T04 — SQLAlchemy engine and connection pool."""

    async def test_database_answers_a_query(self) -> None:
        assert await ping_database() is True

    async def test_session_executes_sql(self) -> None:
        async with get_async_session() as session:
            result = await session.execute(text("SELECT 42 AS answer"))
            assert result.scalar_one() == 42

    async def test_session_rolls_back_on_error(self) -> None:
        """A failed block must not leave a transaction open (§0.1 rule 14)."""
        with pytest.raises(RuntimeError):
            async with get_async_session() as session:
                await session.execute(text("SELECT 1"))
                raise RuntimeError("boom")

        # The pool is still usable afterwards.
        assert await ping_database() is True

    async def test_pool_serves_concurrent_connections(self) -> None:
        import asyncio

        async def query() -> int:
            async with get_async_session() as session:
                result = await session.execute(text("SELECT 1"))
                return int(result.scalar_one())

        results = await asyncio.gather(*(query() for _ in range(10)))
        assert results == [1] * 10


class TestRedis:
    """P1-T06 — Redis client."""

    async def test_redis_answers_ping(self) -> None:
        assert await ping_redis() is True

    async def test_set_and_get_round_trip(self) -> None:
        redis = get_redis()
        key = f"aipvs:test:{uuid.uuid4()}"
        try:
            await redis.set(key, "hello", ex=60)
            assert await redis.get(key) == "hello"
        finally:
            await redis.delete(key)

        assert await redis.get(key) is None

    async def test_atomic_increment(self) -> None:
        """Rate limiting (§123) and quotas depend on atomic counters."""
        redis = get_redis()
        key = f"aipvs:test:counter:{uuid.uuid4()}"
        try:
            values = [await redis.incr(key) for _ in range(5)]
            assert values == [1, 2, 3, 4, 5]
        finally:
            await redis.delete(key)

    async def test_setnx_provides_a_lock_primitive(self) -> None:
        """§119 requires that two workers cannot claim the same job."""
        redis = get_redis()
        key = f"aipvs:test:lock:{uuid.uuid4()}"
        try:
            assert await redis.set(key, "worker-a", nx=True, ex=30) is True
            assert await redis.set(key, "worker-b", nx=True, ex=30) is None
            assert await redis.get(key) == "worker-a"
        finally:
            await redis.delete(key)


class TestObjectStorage:
    """P1-T07 — S3-compatible storage."""

    @pytest.fixture(autouse=True)
    def _bucket(self) -> None:
        get_storage().ensure_bucket()

    def test_storage_is_reachable(self) -> None:
        assert ping_storage() is True

    def test_put_and_get_round_trip(self) -> None:
        storage = get_storage()
        key = product_original_key(uuid.uuid4(), uuid.uuid4(), "png")
        payload = b"\x89PNG\r\n\x1a\n test bytes"
        try:
            metadata = storage.put_bytes(key, payload, "image/png")
            assert metadata.size_bytes == len(payload)
            assert storage.get_bytes(key) == payload
            assert storage.exists(key) is True

            head = storage.head(key)
            assert head.size_bytes == len(payload)
            assert head.content_type == "image/png"
        finally:
            storage.delete(key)

    def test_file_upload_and_download(self, tmp_path: Path) -> None:
        """Large media streams to disk rather than through memory (§116)."""
        storage = get_storage()
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video payload" * 1024)
        key = product_original_key(uuid.uuid4(), uuid.uuid4(), "mp4")

        try:
            storage.upload_file(source, key, "video/mp4")
            destination = tmp_path / "nested" / "downloaded.mp4"
            storage.download_file(key, destination)
            assert destination.read_bytes() == source.read_bytes()
        finally:
            storage.delete(key)

    def test_missing_object_raises_object_not_found(self) -> None:
        storage = get_storage()
        key = product_original_key(uuid.uuid4(), uuid.uuid4(), "png")
        assert storage.exists(key) is False
        with pytest.raises(ObjectNotFoundError):
            storage.get_bytes(key)

    def test_delete_is_idempotent(self) -> None:
        storage = get_storage()
        key = product_original_key(uuid.uuid4(), uuid.uuid4(), "png")
        storage.delete(key)
        storage.delete(key)

    def test_presigned_urls_are_generated(self) -> None:
        storage = get_storage()
        key = product_original_key(uuid.uuid4(), uuid.uuid4(), "jpg")

        upload_url = storage.presigned_upload_url(key, "image/jpeg")
        download_url = storage.presigned_download_url(key)

        for url in (upload_url, download_url):
            assert url.startswith("http")
            assert "X-Amz-Signature" in url
            assert "X-Amz-Expires" in url


class TestMigrations:
    """§169 — the migration chain must apply cleanly to an empty database."""

    def test_alembic_upgrade_head_succeeds(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )
