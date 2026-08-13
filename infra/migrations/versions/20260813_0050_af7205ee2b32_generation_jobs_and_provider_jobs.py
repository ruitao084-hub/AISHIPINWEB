"""generation jobs and provider jobs

Revision ID: af7205ee2b32
Revises: 06685d4c0dd0
Create Date: 2026-08-13 00:50:04.084680+00:00

Adds §10.15 and §10.16: `generation_jobs` and `provider_jobs`.

The unique index on `(workspace_id, idempotency_key)` is §23's guarantee — a
repeated request returns the original job rather than creating and billing a
second one, and the constraint is what closes the race a read-then-insert
cannot.

Reversible; both ENUM types are dropped explicitly in `downgrade()`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "af7205ee2b32"
down_revision: str | None = "06685d4c0dd0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("shot_id", sa.UUID(), nullable=True),
        sa.Column(
            "job_type",
            sa.Enum(
                "VIDEO_GENERATION",
                "IMAGE_GENERATION",
                "TTS",
                "RENDER",
                "QC",
                "PRODUCT_ANALYSIS",
                name="job_type",
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED",
                "QUEUED",
                "SUBMITTED",
                "PROCESSING",
                "COMPLETED",
                "FAILED",
                "CANCELED",
                "TIMEOUT",
                name="job_status",
            ),
            server_default="CREATED",
            nullable=False,
        ),
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column(
            "input_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("estimated_cost", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("actual_cost", sa.Float(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_asset_id", sa.UUID(), nullable=True),
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
            "status <> 'FAILED' OR error_code IS NOT NULL",
            name=op.f("ck_generation_jobs_failed_jobs_explain_themselves"),
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name=op.f("ck_generation_jobs_progress_is_a_percentage"),
        ),
        sa.CheckConstraint(
            "retry_count >= 0", name=op.f("ck_generation_jobs_retry_count_is_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_generation_jobs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["result_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_generation_jobs_result_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["shot_id"],
            ["shots.id"],
            name=op.f("fk_generation_jobs_shot_id_shots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_generation_jobs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_jobs")),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_generation_jobs_idempotency"
        ),
    )
    op.create_index(
        "ix_generation_jobs_active_started_at",
        "generation_jobs",
        ["started_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('QUEUED', 'SUBMITTED', 'PROCESSING')"),
    )
    op.create_index(
        "ix_generation_jobs_project_id_created_at",
        "generation_jobs",
        ["project_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_generation_jobs_shot_id", "generation_jobs", ["shot_id"], unique=False)
    op.create_index(
        op.f("ix_generation_jobs_workspace_id"), "generation_jobs", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_generation_jobs_workspace_id_job_type",
        "generation_jobs",
        ["workspace_id", "job_type"],
        unique=False,
    )
    op.create_index(
        "ix_generation_jobs_workspace_id_status",
        "generation_jobs",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_table(
        "provider_jobs",
        sa.Column("generation_job_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_job_id", sa.String(length=200), nullable=True),
        sa.Column("provider_status", sa.String(length=64), nullable=True),
        sa.Column(
            "request_payload_redacted",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "response_payload_redacted", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
            ["generation_job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_provider_jobs_generation_job_id_generation_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_jobs")),
    )
    op.create_index(
        "ix_provider_jobs_generation_job_id", "provider_jobs", ["generation_job_id"], unique=False
    )
    op.create_index(
        "ix_provider_jobs_provider_job_id",
        "provider_jobs",
        ["provider", "provider_job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provider_jobs_provider_job_id", table_name="provider_jobs")
    op.drop_index("ix_provider_jobs_generation_job_id", table_name="provider_jobs")
    op.drop_table("provider_jobs")
    op.drop_index("ix_generation_jobs_workspace_id_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_workspace_id_job_type", table_name="generation_jobs")
    op.drop_index(op.f("ix_generation_jobs_workspace_id"), table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_shot_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_project_id_created_at", table_name="generation_jobs")
    op.drop_index(
        "ix_generation_jobs_active_started_at",
        table_name="generation_jobs",
        postgresql_where=sa.text("status IN ('QUEUED', 'SUBMITTED', 'PROCESSING')"),
    )
    op.drop_table("generation_jobs")
    for enum_name in ("job_status", "job_type"):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
