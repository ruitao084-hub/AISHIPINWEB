"""PHASE 4 acceptance: the two-phase upload actually works (§12).

These tests do what a browser does — call `presign`, PUT the bytes to the URL
they were handed, then call `complete` — against a real object store. Nothing
here mocks storage, because the parts most likely to be wrong are precisely the
ones a mock would paper over: whether the signature covers the content type,
whether a ranged read returns what the signature check expects, and whether the
object is really there when `complete` looks for it.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import httpx2
import pytest
from conftest import ApiUser, add_member, register_user, sole_workspace_id
from fastapi.testclient import TestClient
from PIL import Image

from backend_core.storage import get_storage

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "media"


@pytest.fixture(autouse=True)
def _bucket() -> None:
    get_storage().ensure_bucket()


def png_bytes(width: int = 120, height: int = 80) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def presign(
    user: ApiUser,
    workspace_id: uuid.UUID,
    *,
    filename: str,
    mime_type: str,
    size_bytes: int,
) -> httpx2.Response:
    return user.client.post(
        f"/api/v1/workspaces/{workspace_id}/uploads/presign",
        headers=user.auth,
        json={"filename": filename, "mime_type": mime_type, "size_bytes": size_bytes},
    )


def put_to_storage(presigned: dict[str, object], body: bytes) -> httpx2.Response:
    """Upload exactly the way a browser would: straight to the signed URL.

    Deliberately *not* through the TestClient — the whole point of §12 is that
    these bytes never touch the API.
    """
    url = str(presigned["upload_url"])
    headers = presigned["headers"]
    assert isinstance(headers, dict)
    return httpx2.put(url, content=body, headers={str(k): str(v) for k, v in headers.items()})


def complete(user: ApiUser, workspace_id: uuid.UUID, asset_id: str) -> httpx2.Response:
    return user.client.post(
        f"/api/v1/workspaces/{workspace_id}/uploads/{asset_id}/complete", headers=user.auth
    )


def upload(
    user: ApiUser,
    workspace_id: uuid.UUID,
    body: bytes,
    *,
    filename: str,
    mime_type: str,
) -> dict[str, object]:
    """The full happy path, for tests that need an existing asset."""
    response = presign(
        user, workspace_id, filename=filename, mime_type=mime_type, size_bytes=len(body)
    )
    assert response.status_code == 201, response.text
    presigned = response.json()

    put = put_to_storage(presigned, body)
    assert put.status_code in (200, 204), put.text

    finished = complete(user, workspace_id, str(presigned["asset"]["id"]))
    assert finished.status_code == 200, finished.text
    result: dict[str, object] = finished.json()
    return result


class TestAcceptance:
    """P4 验收: "浏览器上传图片到 MinIO。数据库创建 MediaAsset。\""""

    def test_browser_uploads_an_image_and_an_asset_row_appears(self, client: TestClient) -> None:
        user = register_user(client, prefix="uploader")
        workspace_id = sole_workspace_id(user)

        asset = upload(
            user, workspace_id, png_bytes(320, 240), filename="hero.png", mime_type="image/png"
        )

        assert asset["upload_status"] == "READY"
        assert asset["asset_type"] == "IMAGE"
        assert asset["width"] == 320
        assert asset["height"] == 240
        assert asset["size_bytes"] is not None and int(str(asset["size_bytes"])) > 0
        assert asset["original_filename"] == "hero.png"
        # §12 requires a file hash; images are hashed while their bytes are in
        # hand for dimension probing, so it costs no extra transfer.
        assert isinstance(asset["checksum"], str) and len(str(asset["checksum"])) == 64

    def test_the_asset_is_listed_and_downloadable(self, client: TestClient) -> None:
        user = register_user(client, prefix="lister")
        workspace_id = sole_workspace_id(user)
        body = png_bytes()
        asset = upload(user, workspace_id, body, filename="a.png", mime_type="image/png")

        listing = user.client.get(f"/api/v1/workspaces/{workspace_id}/assets", headers=user.auth)
        assert listing.status_code == 200
        assert [entry["id"] for entry in listing.json()] == [asset["id"]]

        detail = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/assets/{asset['id']}", headers=user.auth
        )
        assert detail.status_code == 200
        fetched = httpx2.get(detail.json()["download_url"])
        assert fetched.status_code == 200
        assert fetched.content == body


class TestPresign:
    def test_the_api_never_receives_the_bytes(self, client: TestClient) -> None:
        """§116 — the signed URL points at storage, not at this service."""
        user = register_user(client, prefix="direct")
        workspace_id = sole_workspace_id(user)

        response = presign(
            user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=1024
        )
        assert response.status_code == 201
        body = response.json()
        assert "/api/v1/" not in str(body["upload_url"])
        assert body["method"] == "PUT"
        assert body["headers"]["Content-Type"] == "image/png"

    def test_a_new_asset_starts_pending(self, client: TestClient) -> None:
        user = register_user(client, prefix="pending")
        workspace_id = sole_workspace_id(user)

        response = presign(
            user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=1024
        )
        assert response.json()["asset"]["upload_status"] == "PENDING"

    def test_pending_assets_are_not_listed(self, client: TestClient) -> None:
        """An abandoned upload is bookkeeping, not library content."""
        user = register_user(client, prefix="hidden")
        workspace_id = sole_workspace_id(user)
        presign(user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=1024)

        listing = user.client.get(f"/api/v1/workspaces/{workspace_id}/assets", headers=user.auth)
        assert listing.json() == []

    def test_storage_internals_are_not_exposed(self, client: TestClient) -> None:
        user = register_user(client, prefix="opaque")
        workspace_id = sole_workspace_id(user)
        asset = presign(
            user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=1024
        ).json()["asset"]

        assert "object_key" not in asset
        assert "bucket" not in asset

    def test_rejects_an_unsupported_type(self, client: TestClient) -> None:
        user = register_user(client, prefix="badtype")
        workspace_id = sole_workspace_id(user)

        response = presign(
            user, workspace_id, filename="x.svg", mime_type="image/svg+xml", size_bytes=1024
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ASSET_INVALID"

    def test_rejects_an_oversized_image_before_any_transfer(self, client: TestClient) -> None:
        user = register_user(client, prefix="toobig")
        workspace_id = sole_workspace_id(user)

        response = presign(
            user,
            workspace_id,
            filename="huge.png",
            mime_type="image/png",
            size_bytes=999 * 1024 * 1024,
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"

    def test_filename_is_stripped_of_path_components(self, client: TestClient) -> None:
        """§11 — the name is display-only, but it is still rendered."""
        user = register_user(client, prefix="traversal")
        workspace_id = sole_workspace_id(user)

        asset = presign(
            user,
            workspace_id,
            filename="../../../etc/passwd.png",
            mime_type="image/png",
            size_bytes=1024,
        ).json()["asset"]
        assert asset["original_filename"] == "passwd.png"

    def test_config_endpoint_matches_what_presign_accepts(self, client: TestClient) -> None:
        user = register_user(client, prefix="config")
        workspace_id = sole_workspace_id(user)

        config = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/uploads/config", headers=user.auth
        )
        assert config.status_code == 200
        assert set(config.json()["mime_types"]) == {
            "image/jpeg",
            "image/png",
            "image/webp",
            "video/mp4",
            "video/quicktime",
        }


class TestCompleteRejectsBadContent:
    def test_rejects_html_uploaded_as_an_image(self, client: TestClient) -> None:
        """The stored-XSS case: the signature is honoured, the content is not."""
        user = register_user(client, prefix="xss")
        workspace_id = sole_workspace_id(user)
        payload = b"<html><script>alert(1)</script></html>"

        presigned = presign(
            user, workspace_id, filename="x.png", mime_type="image/png", size_bytes=len(payload)
        ).json()
        assert put_to_storage(presigned, payload).status_code in (200, 204)

        response = complete(user, workspace_id, presigned["asset"]["id"])
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ASSET_INVALID"

    def test_a_rejected_upload_is_removed_from_storage(self, client: TestClient) -> None:
        """A file proven invalid is not left costing money (§163)."""
        user = register_user(client, prefix="cleanup")
        workspace_id = sole_workspace_id(user)
        payload = b"<html>not an image</html>"

        presigned = presign(
            user, workspace_id, filename="x.png", mime_type="image/png", size_bytes=len(payload)
        ).json()
        put_to_storage(presigned, payload)
        assert complete(user, workspace_id, presigned["asset"]["id"]).status_code == 400

        detail = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/assets/{presigned['asset']['id']}",
            headers=user.auth,
        )
        assert detail.json()["upload_status"] == "FAILED"
        assert not get_storage().exists(_object_key_of(presigned))

    def test_rejects_completing_an_upload_that_never_happened(self, client: TestClient) -> None:
        user = register_user(client, prefix="ghost")
        workspace_id = sole_workspace_id(user)
        presigned = presign(
            user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=1024
        ).json()

        response = complete(user, workspace_id, presigned["asset"]["id"])
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ASSET_INVALID"

    def test_rejects_an_image_over_the_pixel_budget(self, client: TestClient) -> None:
        """The decompression bomb: small on the wire, enormous once decoded."""
        user = register_user(client, prefix="bomb")
        workspace_id = sole_workspace_id(user)
        body = png_bytes(20000, 20000)  # 400 megapixels, well under 20 MB encoded
        assert len(body) < 20 * 1024 * 1024

        presigned = presign(
            user, workspace_id, filename="bomb.png", mime_type="image/png", size_bytes=len(body)
        ).json()
        put_to_storage(presigned, body)

        response = complete(user, workspace_id, presigned["asset"]["id"])
        assert response.status_code == 400

    def test_a_failed_upload_cannot_be_retried_in_place(self, client: TestClient) -> None:
        user = register_user(client, prefix="retry")
        workspace_id = sole_workspace_id(user)
        presigned = presign(
            user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=64
        ).json()
        put_to_storage(presigned, b"<html>bad</html>")
        assert complete(user, workspace_id, presigned["asset"]["id"]).status_code == 400

        again = complete(user, workspace_id, presigned["asset"]["id"])
        assert again.status_code == 400


class TestIdempotency:
    def test_completing_twice_returns_the_same_asset(self, client: TestClient) -> None:
        """§67 — a retry after a dropped response must not be an error."""
        user = register_user(client, prefix="idem")
        workspace_id = sole_workspace_id(user)
        body = png_bytes()

        presigned = presign(
            user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=len(body)
        ).json()
        put_to_storage(presigned, body)

        first = complete(user, workspace_id, presigned["asset"]["id"])
        second = complete(user, workspace_id, presigned["asset"]["id"])

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()


class TestIsolationAndPermissions:
    def test_another_workspace_cannot_complete_your_upload(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        owner_workspace = sole_workspace_id(owner)
        outsider = register_user(client, prefix="outsider")
        outsider_workspace = sole_workspace_id(outsider)

        presigned = presign(
            owner, owner_workspace, filename="a.png", mime_type="image/png", size_bytes=64
        ).json()
        asset_id = presigned["asset"]["id"]

        # Same asset id, wrong workspace in the path, and a caller who is not a
        # member of the right one: 404 either way, never a 403 that would
        # confirm the asset exists.
        assert complete(outsider, owner_workspace, asset_id).status_code == 404
        assert complete(outsider, outsider_workspace, asset_id).status_code == 404

    def test_a_viewer_cannot_upload(self, client: TestClient) -> None:
        owner = register_user(client, prefix="wsowner")
        workspace_id = sole_workspace_id(owner)
        viewer = register_user(client, prefix="viewer")
        add_member(owner, workspace_id, viewer, "VIEWER")

        response = presign(
            viewer, workspace_id, filename="a.png", mime_type="image/png", size_bytes=64
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"

    def test_an_editor_can_upload(self, client: TestClient) -> None:
        owner = register_user(client, prefix="wsowner2")
        workspace_id = sole_workspace_id(owner)
        editor = register_user(client, prefix="editor")
        add_member(owner, workspace_id, editor, "EDITOR")

        asset = upload(editor, workspace_id, png_bytes(), filename="a.png", mime_type="image/png")
        assert asset["upload_status"] == "READY"

    def test_a_viewer_may_still_read_the_library(self, client: TestClient) -> None:
        owner = register_user(client, prefix="wsowner3")
        workspace_id = sole_workspace_id(owner)
        viewer = register_user(client, prefix="viewer2")
        add_member(owner, workspace_id, viewer, "VIEWER")
        upload(owner, workspace_id, png_bytes(), filename="a.png", mime_type="image/png")

        listing = viewer.client.get(
            f"/api/v1/workspaces/{workspace_id}/assets", headers=viewer.auth
        )
        assert listing.status_code == 200
        assert len(listing.json()) == 1

    def test_uploads_are_filed_under_their_own_workspace(self, client: TestClient) -> None:
        """§61 — the key prefix is the tenant boundary, enforced by a CHECK."""
        user = register_user(client, prefix="scoped")
        workspace_id = sole_workspace_id(user)
        presigned = presign(
            user, workspace_id, filename="a.png", mime_type="image/png", size_bytes=64
        ).json()

        assert _object_key_of(presigned).startswith(f"workspaces/{workspace_id}/uploads/")


class TestVideo:
    """P4-T06 wired into the flow: a real container, probed over a signed URL."""

    def test_uploading_a_video_records_its_duration_and_frame_rate(
        self, client: TestClient
    ) -> None:
        user = register_user(client, prefix="video")
        workspace_id = sole_workspace_id(user)
        body = (FIXTURES / "tiny.mp4").read_bytes()

        asset = upload(user, workspace_id, body, filename="clip.mp4", mime_type="video/mp4")

        assert asset["asset_type"] == "VIDEO"
        assert asset["duration_ms"] == 1000
        assert asset["width"] == 64
        assert asset["height"] == 64
        assert asset["fps"] == 24.0
        assert asset["codec"] == "h264"
        # Hashing a video would mean streaming the whole object through the
        # API, which is what the presigned flow exists to avoid. Deferred to
        # the ingest worker in PHASE 9 — asserted so the gap stays visible.
        assert asset["checksum"] is None

    def test_rejects_a_video_longer_than_the_limit(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend_core.config import get_settings

        user = register_user(client, prefix="longvideo")
        workspace_id = sole_workspace_id(user)
        body = (FIXTURES / "tiny.mp4").read_bytes()

        # The fixture is one second; lowering the ceiling below it exercises the
        # limit without committing a long file to the repo. `monkeypatch`
        # restores the cached settings object even if an assertion fails.
        monkeypatch.setattr(get_settings(), "max_upload_video_duration_seconds", 0)

        presigned = presign(
            user,
            workspace_id,
            filename="clip.mp4",
            mime_type="video/mp4",
            size_bytes=len(body),
        ).json()
        put_to_storage(presigned, body)
        response = complete(user, workspace_id, presigned["asset"]["id"])

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ASSET_INVALID"

    def test_rejects_an_image_uploaded_as_a_video(self, client: TestClient) -> None:
        user = register_user(client, prefix="notvideo")
        workspace_id = sole_workspace_id(user)
        body = png_bytes()

        presigned = presign(
            user, workspace_id, filename="x.mp4", mime_type="video/mp4", size_bytes=len(body)
        ).json()
        put_to_storage(presigned, body)

        assert complete(user, workspace_id, presigned["asset"]["id"]).status_code == 400


def _object_key_of(presigned: dict[str, object]) -> str:
    """Recover the storage key from the signed URL.

    The API deliberately does not return it (it is infrastructure), so the test
    reads it back out of the URL rather than adding a field to the response
    purely so that tests can assert on it.
    """
    from urllib.parse import unquote, urlparse

    path = urlparse(str(presigned["upload_url"])).path
    marker = "workspaces/"
    index = path.index(marker)
    return unquote(path[index:])
