"""voiceover subtitles audio renders and qc

Revision ID: 67700e4bbb26
Revises: af7205ee2b32
Create Date: 2026-08-13 00:58:54.971866+00:00

Adds PHASE 12-14: `voiceover_tracks`, `subtitle_tracks`, `audio_tracks`,
`renders` and `quality_checks` (§30-§37).

`audio_tracks` carries §32's licence columns, with a CHECK that a track cleared
for commercial use names where that clearance came from — an unlicensed track
in a customer's advert is their legal exposure and our fault.

Reversible; every ENUM type is dropped explicitly in `downgrade()`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "67700e4bbb26"
down_revision: str | None = "af7205ee2b32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audio_tracks",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("media_asset_id", sa.UUID(), nullable=False),
        sa.Column(
            "license_type",
            sa.Enum(
                "USER_OWNED",
                "ROYALTY_FREE",
                "LICENSED",
                "CREATIVE_COMMONS",
                "UNKNOWN",
                name="license_type",
            ),
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column("license_source", sa.Text(), nullable=True),
        sa.Column(
            "allowed_commercial_use", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "attribution_required", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("is_preset", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "allowed_commercial_use = false OR license_source IS NOT NULL",
            name=op.f("ck_audio_tracks_commercial_tracks_cite_their_licence"),
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_audio_tracks_media_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audio_tracks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audio_tracks")),
    )
    op.create_index(
        op.f("ix_audio_tracks_workspace_id"), "audio_tracks", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_audio_tracks_workspace_id_is_preset",
        "audio_tracks",
        ["workspace_id", "is_preset"],
        unique=False,
    )
    op.create_table(
        "subtitle_tracks",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("language", sa.String(length=16), server_default="zh-CN", nullable=False),
        sa.Column(
            "cues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["media_assets.id"],
            name=op.f("fk_subtitle_tracks_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_subtitle_tracks_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_subtitle_tracks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subtitle_tracks")),
    )
    op.create_index(
        op.f("ix_subtitle_tracks_workspace_id"), "subtitle_tracks", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_subtitle_tracks_workspace_id_project_id",
        "subtitle_tracks",
        ["workspace_id", "project_id"],
        unique=False,
    )
    op.create_table(
        "voiceover_tracks",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("script_id", sa.UUID(), nullable=True),
        sa.Column("language", sa.String(length=16), server_default="zh-CN", nullable=False),
        sa.Column("voice", sa.String(length=120), server_default="", nullable=False),
        sa.Column("provider", sa.String(length=64), server_default="mock", nullable=False),
        sa.Column("audio_asset_id", sa.UUID(), nullable=True),
        sa.Column("total_duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "segments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audio_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_voiceover_tracks_audio_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_voiceover_tracks_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
            name=op.f("fk_voiceover_tracks_script_id_scripts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_voiceover_tracks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voiceover_tracks")),
    )
    op.create_index(
        op.f("ix_voiceover_tracks_workspace_id"), "voiceover_tracks", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_voiceover_tracks_workspace_id_project_id",
        "voiceover_tracks",
        ["workspace_id", "project_id"],
        unique=False,
    )
    op.create_table(
        "renders",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("storyboard_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RENDERING", "COMPLETED", "FAILED", name="render_status"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("timeline_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_asset_id", sa.UUID(), nullable=True),
        sa.Column("thumbnail_asset_id", sa.UUID(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' OR output_asset_id IS NOT NULL",
            name=op.f("ck_renders_completed_renders_have_output"),
        ),
        sa.ForeignKeyConstraint(
            ["output_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_renders_output_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_renders_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["storyboard_id"],
            ["storyboards.id"],
            name=op.f("fk_renders_storyboard_id_storyboards"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["thumbnail_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_renders_thumbnail_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_renders_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_renders")),
        sa.UniqueConstraint("project_id", "version", name="uq_renders_project_version"),
    )
    op.create_index(op.f("ix_renders_workspace_id"), "renders", ["workspace_id"], unique=False)
    op.create_index(
        "ix_renders_workspace_id_project_id",
        "renders",
        ["workspace_id", "project_id"],
        unique=False,
    )
    op.create_table(
        "quality_checks",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("render_id", sa.UUID(), nullable=True),
        sa.Column("shot_id", sa.UUID(), nullable=True),
        sa.Column(
            "check_type", sa.Enum("TECHNICAL", "VISUAL", name="qc_check_type"), nullable=False
        ),
        sa.Column(
            "status", sa.Enum("PASSED", "WARNING", "FAILED", name="qc_status"), nullable=False
        ),
        sa.Column(
            "findings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "model_info",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_quality_checks_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["render_id"],
            ["renders.id"],
            name=op.f("fk_quality_checks_render_id_renders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["shot_id"],
            ["shots.id"],
            name=op.f("fk_quality_checks_shot_id_shots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_quality_checks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_checks")),
    )
    op.create_index("ix_quality_checks_render_id", "quality_checks", ["render_id"], unique=False)
    op.create_index(
        op.f("ix_quality_checks_workspace_id"), "quality_checks", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_quality_checks_workspace_id_project_id",
        "quality_checks",
        ["workspace_id", "project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_quality_checks_workspace_id_project_id", table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_workspace_id"), table_name="quality_checks")
    op.drop_index("ix_quality_checks_render_id", table_name="quality_checks")
    op.drop_table("quality_checks")
    op.drop_index("ix_renders_workspace_id_project_id", table_name="renders")
    op.drop_index(op.f("ix_renders_workspace_id"), table_name="renders")
    op.drop_table("renders")
    op.drop_index("ix_voiceover_tracks_workspace_id_project_id", table_name="voiceover_tracks")
    op.drop_index(op.f("ix_voiceover_tracks_workspace_id"), table_name="voiceover_tracks")
    op.drop_table("voiceover_tracks")
    op.drop_index("ix_subtitle_tracks_workspace_id_project_id", table_name="subtitle_tracks")
    op.drop_index(op.f("ix_subtitle_tracks_workspace_id"), table_name="subtitle_tracks")
    op.drop_table("subtitle_tracks")
    op.drop_index("ix_audio_tracks_workspace_id_is_preset", table_name="audio_tracks")
    op.drop_index(op.f("ix_audio_tracks_workspace_id"), table_name="audio_tracks")
    op.drop_table("audio_tracks")
    for enum_name in (
        "license_type",
        "render_status",
        "qc_check_type",
        "qc_status",
    ):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
