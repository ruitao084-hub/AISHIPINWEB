"""product analyses

Revision ID: b0626a7c900e
Revises: 0b6ac8e2e56b
Create Date: 2026-08-12 05:55:01.491824+00:00

Adds `product_analyses` (§14, §15): one row per vision-analysis run, including
the failed ones, carrying the prompt key and version that produced it.

Reversible, and verified so: run through `upgrade -> downgrade -> upgrade`
against a real Postgres. The `analysis_status` ENUM is dropped explicitly in
`downgrade()` — `drop_table` leaves the type behind, and the second `upgrade`
then fails with "type already exists".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b0626a7c900e"
down_revision: str | None = "0b6ac8e2e56b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_analyses",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SUCCEEDED", "FAILED", name="analysis_status"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("prompt_key", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "analyzed_asset_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_fact_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_claim_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "status <> 'SUCCEEDED' OR result IS NOT NULL",
            name=op.f("ck_product_analyses_successful_analyses_have_a_result"),
        ),
        sa.CheckConstraint(
            "prompt_version > 0", name=op.f("ck_product_analyses_prompt_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_analyses_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_product_analyses_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_analyses")),
    )
    op.create_index(
        "ix_product_analyses_product_id_created_at",
        "product_analyses",
        ["product_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_product_analyses_workspace_id"), "product_analyses", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_product_analyses_workspace_id_product_id",
        "product_analyses",
        ["workspace_id", "product_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_product_analyses_workspace_id_product_id", table_name="product_analyses")
    op.drop_index(op.f("ix_product_analyses_workspace_id"), table_name="product_analyses")
    op.drop_index("ix_product_analyses_product_id_created_at", table_name="product_analyses")
    op.drop_table("product_analyses")
    # `drop_table` does not drop the ENUM the column referenced, so without
    # this the next `upgrade` fails with "type analysis_status already exists".
    op.execute(sa.text("DROP TYPE IF EXISTS analysis_status"))
