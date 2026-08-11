"""Object key construction and its security properties (taskbook §11)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from backend_core.storage.keys import (
    UnsupportedMediaTypeError,
    belongs_to_workspace,
    extension_for_mime_type,
    product_original_key,
    project_audio_key,
    render_output_key,
    shot_video_key,
)

WORKSPACE = UUID("11111111-1111-1111-1111-111111111111")
PRODUCT = UUID("22222222-2222-2222-2222-222222222222")
PROJECT = UUID("33333333-3333-3333-3333-333333333333")
SHOT = UUID("44444444-4444-4444-4444-444444444444")
RENDER = UUID("55555555-5555-5555-5555-555555555555")


class TestWorkspaceIsolation:
    def test_every_key_starts_with_its_workspace(self) -> None:
        keys = [
            product_original_key(WORKSPACE, PRODUCT, "jpg"),
            shot_video_key(WORKSPACE, PROJECT, SHOT),
            project_audio_key(WORKSPACE, PROJECT),
            render_output_key(WORKSPACE, PROJECT, RENDER),
        ]
        for key in keys:
            assert key.startswith(f"workspaces/{WORKSPACE}/")

    def test_belongs_to_workspace_rejects_a_foreign_key(self) -> None:
        key = product_original_key(WORKSPACE, PRODUCT, "jpg")
        assert belongs_to_workspace(key, WORKSPACE)
        assert not belongs_to_workspace(key, uuid4())

    def test_belongs_to_workspace_is_not_fooled_by_a_prefix(self) -> None:
        """A workspace id that merely *starts with* another must not match."""
        assert not belongs_to_workspace(f"workspaces/{WORKSPACE}extra/x.jpg", WORKSPACE)


class TestUntrustedInput:
    """§11: the server names the file; a user filename is never trusted."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../etc/passwd",
            "jpg/../../secret",
            "jp g",
            "jpg\x00.png",
            "",
            ".",
            "JPG!",
            "averyverylongextension",
        ],
    )
    def test_unsafe_extensions_are_rejected(self, hostile: str) -> None:
        with pytest.raises(ValueError):
            product_original_key(WORKSPACE, PRODUCT, hostile)

    def test_extension_is_normalised(self) -> None:
        key = product_original_key(WORKSPACE, PRODUCT, ".JPG")
        assert key.endswith(".jpg")

    def test_unknown_mime_type_raises_rather_than_guessing(self) -> None:
        with pytest.raises(UnsupportedMediaTypeError):
            extension_for_mime_type("application/x-msdownload")

    @pytest.mark.parametrize(
        ("mime", "expected"),
        [
            ("image/jpeg", "jpg"),
            ("image/png", "png"),
            ("image/webp", "webp"),
            ("video/mp4", "mp4"),
            ("video/quicktime", "mov"),
            ("audio/wav", "wav"),
            ("IMAGE/JPEG", "jpg"),
            ("image/jpeg; charset=binary", "jpg"),
        ],
    )
    def test_supported_mime_types_map_to_extensions(self, mime: str, expected: str) -> None:
        assert extension_for_mime_type(mime) == expected


class TestNonDestructiveRegeneration:
    """§144: regenerating a shot must never overwrite the previous take."""

    def test_repeated_shot_keys_are_unique(self) -> None:
        first = shot_video_key(WORKSPACE, PROJECT, SHOT)
        second = shot_video_key(WORKSPACE, PROJECT, SHOT)
        assert first != second

    def test_render_key_is_stable_for_a_given_render(self) -> None:
        """A render owns its output, so re-running one replaces its own file."""
        assert render_output_key(WORKSPACE, PROJECT, RENDER) == render_output_key(
            WORKSPACE, PROJECT, RENDER
        )


class TestKeyShape:
    def test_matches_the_documented_layout(self) -> None:
        key = shot_video_key(WORKSPACE, PROJECT, SHOT)
        assert key.startswith(f"workspaces/{WORKSPACE}/projects/{PROJECT}/shots/{SHOT}/")
        assert key.endswith(".mp4")
