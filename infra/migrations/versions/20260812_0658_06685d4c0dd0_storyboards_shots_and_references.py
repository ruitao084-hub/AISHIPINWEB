"""storyboards shots and references

Revision ID: 06685d4c0dd0
Revises: b2b83d5b4d0e
Create Date: 2026-08-12 06:58:25.410558+00:00

Adds §10.12-§10.14: `storyboards`, `shots` and `shot_references`, plus the five
ENUM types they introduce.

Reversible, and verified so: run through `upgrade -> downgrade -> upgrade`
against a real Postgres. `drop_table` leaves ENUM types behind, so each is
dropped explicitly in `downgrade()`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "06685d4c0dd0"
down_revision: str | None = "b2b83d5b4d0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storyboards",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("script_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "APPROVED", "SUPERSEDED", name="storyboard_status"),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "total_duration_seconds", sa.Float(), server_default=sa.text("0"), nullable=False
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
            name=op.f("fk_storyboards_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
            name=op.f("fk_storyboards_script_id_scripts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_storyboards_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storyboards")),
        sa.UniqueConstraint("project_id", "version", name="uq_storyboards_project_version"),
    )
    op.create_index(
        op.f("ix_storyboards_workspace_id"), "storyboards", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_storyboards_workspace_id_project_id",
        "storyboards",
        ["workspace_id", "project_id"],
        unique=False,
    )
    op.create_index(
        "uq_storyboards_approved",
        "storyboards",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )
    op.create_table(
        "shots",
        sa.Column("storyboard_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), server_default="", nullable=False),
        sa.Column(
            "shot_type",
            sa.Enum(
                "HOOK",
                "PRODUCT_HERO",
                "MACRO",
                "ROTATION",
                "USAGE",
                "MATERIAL",
                "FEATURE",
                "EXPLODED",
                "BEFORE_AFTER",
                "LIFESTYLE",
                "BRAND_ENDING",
                "CUSTOM",
                name="shot_type",
            ),
            server_default="CUSTOM",
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("visual_prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("negative_prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("camera", sa.Text(), server_default="", nullable=False),
        sa.Column("motion", sa.Text(), server_default="", nullable=False),
        sa.Column("lighting", sa.Text(), server_default="", nullable=False),
        sa.Column("composition", sa.Text(), server_default="", nullable=False),
        sa.Column("voiceover_text", sa.Text(), server_default="", nullable=False),
        sa.Column("subtitle_text", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "transition_in",
            sa.Enum("CUT", "FADE", "DISSOLVE", "WIPE", "ZOOM", "NONE", name="transition_type"),
            server_default="CUT",
            nullable=False,
        ),
        sa.Column(
            "transition_out",
            sa.Enum("CUT", "FADE", "DISSOLVE", "WIPE", "ZOOM", "NONE", name="transition_type"),
            server_default="CUT",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "QUEUED", "GENERATING", "READY", "FAILED", "SKIPPED", name="shot_status"
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("selected_generation_job_id", sa.UUID(), nullable=True),
        sa.Column("identity_lock", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            "duration_seconds >= 0.5 AND duration_seconds <= 30",
            name=op.f("ck_shots_duration_is_within_range"),
        ),
        sa.CheckConstraint("sequence_no > 0", name=op.f("ck_shots_sequence_starts_at_one")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_shots_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["storyboard_id"],
            ["storyboards.id"],
            name=op.f("fk_shots_storyboard_id_storyboards"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_shots_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shots")),
        sa.UniqueConstraint("storyboard_id", "sequence_no", name="uq_shots_storyboard_sequence"),
    )
    op.create_index(op.f("ix_shots_workspace_id"), "shots", ["workspace_id"], unique=False)
    op.create_index(
        "ix_shots_workspace_id_project_id", "shots", ["workspace_id", "project_id"], unique=False
    )
    op.create_index(
        "ix_shots_workspace_id_storyboard_id",
        "shots",
        ["workspace_id", "storyboard_id"],
        unique=False,
    )
    op.create_table(
        "shot_references",
        sa.Column("shot_id", sa.UUID(), nullable=False),
        sa.Column("media_asset_id", sa.UUID(), nullable=False),
        sa.Column(
            "reference_role",
            sa.Enum(
                "IDENTITY", "STYLE", "COMPOSITION", "ENVIRONMENT", "OTHER", name="reference_role"
            ),
            server_default="IDENTITY",
            nullable=False,
        ),
        sa.Column("weight", sa.Float(), nullable=True),
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
            "weight IS NULL OR (weight > 0 AND weight <= 1)",
            name=op.f("ck_shot_references_weight_is_a_fraction"),
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_shot_references_media_asset_id_media_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shot_id"],
            ["shots.id"],
            name=op.f("fk_shot_references_shot_id_shots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_shot_references_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shot_references")),
        sa.UniqueConstraint("shot_id", "media_asset_id", name="uq_shot_references_asset"),
    )
    op.create_index(
        op.f("ix_shot_references_workspace_id"), "shot_references", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_shot_references_workspace_id_shot_id",
        "shot_references",
        ["workspace_id", "shot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shot_references_workspace_id_shot_id", table_name="shot_references")
    op.drop_index(op.f("ix_shot_references_workspace_id"), table_name="shot_references")
    op.drop_table("shot_references")
    op.drop_index("ix_shots_workspace_id_storyboard_id", table_name="shots")
    op.drop_index("ix_shots_workspace_id_project_id", table_name="shots")
    op.drop_index(op.f("ix_shots_workspace_id"), table_name="shots")
    op.drop_table("shots")
    op.drop_index(
        "uq_storyboards_approved",
        table_name="storyboards",
        postgresql_where=sa.text("status = 'APPROVED'"),
    )
    op.drop_index("ix_storyboards_workspace_id_project_id", table_name="storyboards")
    op.drop_index(op.f("ix_storyboards_workspace_id"), table_name="storyboards")
    op.drop_table("storyboards")
    # `drop_table` does not remove the ENUM types the columns referenced.
    for enum_name in (
        "storyboard_status",
        "shot_type",
        "shot_status",
        "transition_type",
        "reference_role",
    ):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
