"""brand kits and templates

Revision ID: 814e072a2d98
Revises: e4403eb7d4d1
Create Date: 2026-08-13 01:32:45.277567+00:00

Every migration must be reversible or document why it is not (taskbook §73).
Fill in ``downgrade()`` — an empty one is only acceptable with a comment
explaining the irreversible operation and the recovery plan.

CHECK BEFORE COMMITTING:

* If this migration adds a column with ``sa.Enum``, autogenerate created a
  Postgres ENUM type but ``drop_table`` will NOT remove it. Add an explicit
  ``op.execute(sa.text("DROP TYPE IF EXISTS <name>"))`` to ``downgrade()``,
  or the next ``upgrade`` fails with "type already exists".
* Run ``upgrade -> downgrade -> upgrade`` against a real database. A downgrade
  that has never been executed is not known to work.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "814e072a2d98"
down_revision: str | None = "e4403eb7d4d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `postgresql.ENUM(..., create_type=False)` for the five enums that already exist from earlier
    # migrations (`aspect_ratio`, `video_style`, `project_purpose`,
    # `target_platform`, `transition_type`). Autogenerate does not know a type
    # is already there, and `sa.Enum` inside `create_table` issues CREATE TYPE
    # unconditionally — which fails with "type already exists". Note this must
    # be `postgresql.ENUM`: the generic `sa.Enum` accepts `create_type` as a
    # keyword and ignores it, so the wrong one fails in exactly the same way.
    op.create_table(
        "brand_kits",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("logo_asset_id", sa.UUID(), nullable=True),
        sa.Column(
            "logo_position",
            sa.Enum(
                "TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT", "NONE", name="logo_position"
            ),
            server_default="BOTTOM_RIGHT",
            nullable=False,
        ),
        sa.Column("primary_color", sa.String(length=7), nullable=True),
        sa.Column("secondary_color", sa.String(length=7), nullable=True),
        sa.Column("subtitle_color", sa.String(length=7), nullable=True),
        sa.Column("font_family", sa.String(length=120), nullable=True),
        sa.Column(
            "tone",
            sa.Enum(
                "PROFESSIONAL",
                "FRIENDLY",
                "LUXURY",
                "PLAYFUL",
                "TECHNICAL",
                "WARM",
                "BOLD",
                name="brand_tone",
            ),
            server_default="PROFESSIONAL",
            nullable=False,
        ),
        sa.Column(
            "required_phrases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "banned_phrases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("ending_line", sa.Text(), nullable=True),
        sa.Column("ending_cta", sa.String(length=200), nullable=True),
        sa.Column("visual_guidelines", sa.Text(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["logo_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_brand_kits_logo_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_brand_kits_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_kits")),
    )
    op.create_index(op.f("ix_brand_kits_deleted_at"), "brand_kits", ["deleted_at"], unique=False)
    op.create_index(
        op.f("ix_brand_kits_workspace_id"), "brand_kits", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_brand_kits_workspace_id_name", "brand_kits", ["workspace_id", "name"], unique=False
    )
    op.create_index(
        "uq_brand_kits_one_default",
        "brand_kits",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )
    op.create_table(
        "templates",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "ECOMMERCE",
                "SOCIAL",
                "BRAND",
                "LAUNCH",
                "TUTORIAL",
                "SEASONAL",
                name="template_category",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("preview_asset_id", sa.UUID(), nullable=True),
        sa.Column("is_preset", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "aspect_ratio",
            postgresql.ENUM("9:16", "16:9", "1:1", "4:5", name="aspect_ratio", create_type=False),
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "style",
            postgresql.ENUM(
                "CLEAN_MINIMAL",
                "WARM_LIFESTYLE",
                "TECH_PREMIUM",
                "BOLD_ENERGETIC",
                "NATURAL_DOCUMENTARY",
                "LUXURY",
                name="video_style",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            postgresql.ENUM(
                "LAUNCH",
                "ECOMMERCE_LISTING",
                "SOCIAL_AD",
                "BRAND_STORY",
                "FEATURE_HIGHLIGHT",
                "TUTORIAL",
                "OTHER",
                name="project_purpose",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "target_platform",
            postgresql.ENUM(
                "DOUYIN",
                "XIAOHONGSHU",
                "BILIBILI",
                "WECHAT_CHANNELS",
                "TAOBAO",
                "TIKTOK",
                "INSTAGRAM",
                "YOUTUBE",
                "OTHER",
                name="target_platform",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "storyboard_blueprint",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "prompt_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "subtitle_style",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "transition_style",
            postgresql.ENUM(
                "CUT",
                "FADE",
                "DISSOLVE",
                "WIPE",
                "ZOOM",
                "NONE",
                name="transition_type",
                create_type=False,
            ),
            server_default="CUT",
            nullable=False,
        ),
        sa.Column(
            "music_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("ending_style", sa.Text(), nullable=True),
        sa.Column("usage_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 600",
            name=op.f("ck_templates_duration_range"),
        ),
        sa.ForeignKeyConstraint(
            ["preview_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_templates_preview_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_templates_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_templates")),
    )
    op.create_index(op.f("ix_templates_deleted_at"), "templates", ["deleted_at"], unique=False)
    op.create_index(
        "ix_templates_preset_category", "templates", ["is_preset", "category"], unique=False
    )
    op.create_index(op.f("ix_templates_workspace_id"), "templates", ["workspace_id"], unique=False)
    op.create_index(
        "ix_templates_workspace_id_category",
        "templates",
        ["workspace_id", "category"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_templates_workspace_id_category", table_name="templates")
    op.drop_index(op.f("ix_templates_workspace_id"), table_name="templates")
    op.drop_index("ix_templates_preset_category", table_name="templates")
    op.drop_index(op.f("ix_templates_deleted_at"), table_name="templates")
    op.drop_table("templates")
    op.drop_index(
        "uq_brand_kits_one_default",
        table_name="brand_kits",
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )
    op.drop_index("ix_brand_kits_workspace_id_name", table_name="brand_kits")
    op.drop_index(op.f("ix_brand_kits_workspace_id"), table_name="brand_kits")
    op.drop_index(op.f("ix_brand_kits_deleted_at"), table_name="brand_kits")
    op.drop_table("brand_kits")

    # `drop_table` leaves the ENUM types behind, so the next `upgrade` would
    # fail with "type already exists". Only the types this migration created —
    # `aspect_ratio`, `video_style`, `project_purpose`, `target_platform` and
    # `transition_type` are shared with existing tables and must survive.
    op.execute(sa.text("DROP TYPE IF EXISTS template_category"))
    op.execute(sa.text("DROP TYPE IF EXISTS brand_tone"))
    op.execute(sa.text("DROP TYPE IF EXISTS logo_position"))
