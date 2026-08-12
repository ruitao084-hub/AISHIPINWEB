"""projects creative plans and scripts

Revision ID: b2b83d5b4d0e
Revises: b0626a7c900e
Create Date: 2026-08-12 06:36:11.542772+00:00

Adds §10.9-§10.11: `projects`, `creative_plans` and `scripts`, plus the five
ENUM types they introduce.

Reversible, and verified so: run through `upgrade -> downgrade -> upgrade`
against a real Postgres. `drop_table` leaves ENUM types behind, so all five are
dropped explicitly in `downgrade()` — otherwise the second `upgrade` fails with
"type already exists".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2b83d5b4d0e"
down_revision: str | None = "b0626a7c900e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("brand_kit_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "LAUNCH",
                "ECOMMERCE_LISTING",
                "SOCIAL_AD",
                "BRAND_STORY",
                "FEATURE_HIGHLIGHT",
                "TUTORIAL",
                "OTHER",
                name="project_purpose",
            ),
            server_default="SOCIAL_AD",
            nullable=False,
        ),
        sa.Column(
            "target_platform",
            sa.Enum(
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
            ),
            server_default="DOUYIN",
            nullable=False,
        ),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=16), server_default="zh-CN", nullable=False),
        sa.Column(
            "aspect_ratio",
            sa.Enum("9:16", "16:9", "1:1", "4:5", name="aspect_ratio"),
            server_default="9:16",
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), server_default=sa.text("30"), nullable=False),
        sa.Column(
            "style",
            sa.Enum(
                "CLEAN_MINIMAL",
                "WARM_LIFESTYLE",
                "TECH_PREMIUM",
                "BOLD_ENERGETIC",
                "NATURAL_DOCUMENTARY",
                "LUXURY",
                name="video_style",
            ),
            server_default="CLEAN_MINIMAL",
            nullable=False,
        ),
        sa.Column(
            "quality_mode",
            sa.Enum("FAST", "STANDARD", "HIGH", "PREMIUM", name="quality_mode"),
            server_default="STANDARD",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "ANALYZING",
                "CREATIVE_PLANNING",
                "SCRIPTING",
                "STORYBOARDING",
                "GENERATING",
                "COMPOSITING",
                "QC",
                "READY",
                "FAILED",
                "ARCHIVED",
                name="project_status",
            ),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
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
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name=op.f("ck_projects_failed_projects_explain_themselves"),
        ),
        sa.CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 600",
            name=op.f("ck_projects_duration_is_within_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_projects_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_projects_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_projects_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(op.f("ix_projects_deleted_at"), "projects", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_projects_workspace_id"), "projects", ["workspace_id"], unique=False)
    op.create_index(
        "ix_projects_workspace_id_product_id",
        "projects",
        ["workspace_id", "product_id"],
        unique=False,
    )
    op.create_index(
        "ix_projects_workspace_id_status", "projects", ["workspace_id", "status"], unique=False
    )
    op.create_table(
        "creative_plans",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("hook", sa.Text(), nullable=False),
        sa.Column("core_message", sa.Text(), nullable=False),
        sa.Column("narrative_structure", sa.Text(), nullable=False),
        sa.Column("visual_direction", sa.Text(), nullable=False),
        sa.Column("camera_direction", sa.Text(), nullable=False),
        sa.Column("music_direction", sa.Text(), nullable=False),
        sa.Column("ending_cta", sa.Text(), nullable=False),
        sa.Column("risk_notes", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "recommended_style",
            sa.Enum(
                "CLEAN_MINIMAL",
                "WARM_LIFESTYLE",
                "TECH_PREMIUM",
                "BOLD_ENERGETIC",
                "NATURAL_DOCUMENTARY",
                "LUXURY",
                name="video_style",
            ),
            nullable=True,
        ),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            name=op.f("fk_creative_plans_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_creative_plans_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_creative_plans")),
    )
    op.create_index(
        "ix_creative_plans_project_id_version",
        "creative_plans",
        ["project_id", "version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_creative_plans_workspace_id"), "creative_plans", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_creative_plans_workspace_id_project_id",
        "creative_plans",
        ["workspace_id", "project_id"],
        unique=False,
    )
    op.create_index(
        "uq_creative_plans_selected",
        "creative_plans",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("selected"),
    )
    op.create_table(
        "scripts",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("creative_plan_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "APPROVED", "SUPERSEDED", name="script_status"),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "sourced_claim_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("estimated_duration_seconds", sa.Float(), nullable=True),
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
            ["creative_plan_id"],
            ["creative_plans.id"],
            name=op.f("fk_scripts_creative_plan_id_creative_plans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_scripts_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_scripts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scripts")),
        sa.UniqueConstraint("project_id", "version", name="uq_scripts_project_version"),
    )
    op.create_index(op.f("ix_scripts_workspace_id"), "scripts", ["workspace_id"], unique=False)
    op.create_index(
        "ix_scripts_workspace_id_project_id",
        "scripts",
        ["workspace_id", "project_id"],
        unique=False,
    )
    op.create_index(
        "uq_scripts_approved",
        "scripts",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_scripts_approved", table_name="scripts", postgresql_where=sa.text("status = 'APPROVED'")
    )
    op.drop_index("ix_scripts_workspace_id_project_id", table_name="scripts")
    op.drop_index(op.f("ix_scripts_workspace_id"), table_name="scripts")
    op.drop_table("scripts")
    op.drop_index(
        "uq_creative_plans_selected",
        table_name="creative_plans",
        postgresql_where=sa.text("selected"),
    )
    op.drop_index("ix_creative_plans_workspace_id_project_id", table_name="creative_plans")
    op.drop_index(op.f("ix_creative_plans_workspace_id"), table_name="creative_plans")
    op.drop_index("ix_creative_plans_project_id_version", table_name="creative_plans")
    op.drop_table("creative_plans")
    op.drop_index("ix_projects_workspace_id_status", table_name="projects")
    op.drop_index("ix_projects_workspace_id_product_id", table_name="projects")
    op.drop_index(op.f("ix_projects_workspace_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_deleted_at"), table_name="projects")
    op.drop_table("projects")
    # `drop_table` does not remove the ENUM types the columns referenced.
    for enum_name in (
        "project_status",
        "project_purpose",
        "target_platform",
        "aspect_ratio",
        "video_style",
        "quality_mode",
        "script_status",
    ):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
