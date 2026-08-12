"""ORM models: identity, media, products, and video projects.

Covers taskbook §10.1-§10.3 (identity and tenancy), §10.17 (media),
§10.5-§10.8 (products and the Truth Layer) and §10.9-§10.11 (projects,
creative plans and scripts).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend_core.db.base import (
    BaseEntity,
    SoftDeleteMixin,
    WorkspaceEntity,
    workspace_scoped_index,
)
from backend_core.domain.enums import (
    AnalysisStatus,
    AspectRatio,
    AssetSourceType,
    AssetType,
    ClaimRiskLevel,
    ClaimStatus,
    ClaimType,
    FactSourceType,
    FactType,
    PlanCode,
    ProductAssetRole,
    ProductStatus,
    ProjectPurpose,
    ProjectStatus,
    QualityMode,
    ScriptStatus,
    TargetPlatform,
    UploadStatus,
    UserStatus,
    VerificationStatus,
    VideoStyle,
    WorkspaceRole,
    WorkspaceStatus,
)


def _pg_enum(enum_type: type[Any], name: str) -> Enum:
    """A Postgres ENUM storing member *values* rather than Python names.

    ``native_enum`` creates a real database type, so an invalid status cannot
    be written even by a hand-run SQL statement — §9 asks for status fields to
    be constrained by the database, not only by the application.
    """
    return Enum(
        enum_type,
        name=name,
        native_enum=True,
        values_callable=lambda members: [member.value for member in members],
    )


class User(BaseEntity):
    """A person with login credentials (§10.1).

    Not workspace-scoped: one user belongs to many workspaces through
    :class:`WorkspaceMember`.
    """

    __tablename__ = "users"

    # Stored lowercased and unique. Case-sensitive emails would let
    # "User@x.com" and "user@x.com" register as two accounts for one mailbox,
    # which is an account-takeover vector during password reset.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    # Argon2 output. Never the password, and never reversible.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    status: Mapped[UserStatus] = mapped_column(
        _pg_enum(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
        index=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        CheckConstraint("length(email) >= 3", name="email_min_length"),
    )

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    def __repr__(self) -> str:
        # Deliberately no email: reprs reach logs and tracebacks, and an email
        # is personal data (§62).
        return f"User(id={self.id!r}, status={self.status.value!r})"


class Workspace(BaseEntity):
    """A tenant boundary (§10.2).

    Every piece of content in the system hangs off exactly one workspace; it is
    the unit of isolation, billing and quota.
    """

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # RESTRICT, not CASCADE: deleting a user must not silently destroy a
    # workspace their colleagues are working in. Ownership is transferred
    # first, which forces the decision to be explicit.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    plan_code: Mapped[PlanCode] = mapped_column(
        _pg_enum(PlanCode, "plan_code"),
        nullable=False,
        default=PlanCode.FREE,
        server_default=PlanCode.FREE.value,
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        _pg_enum(WorkspaceStatus, "workspace_status"),
        nullable=False,
        default=WorkspaceStatus.ACTIVE,
        server_default=WorkspaceStatus.ACTIVE.value,
        index=True,
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_user_id], lazy="raise")
    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("slug = lower(slug)", name="slug_is_lowercase"),
        CheckConstraint("length(slug) >= 3", name="slug_min_length"),
    )

    @property
    def is_active(self) -> bool:
        return self.status is WorkspaceStatus.ACTIVE

    def __repr__(self) -> str:
        return f"Workspace(id={self.id!r}, slug={self.slug!r})"


class WorkspaceMember(BaseEntity):
    """A user's role within a workspace (§10.3).

    This row *is* the authorisation record: every permission check resolves to
    finding it and reading its role.
    """

    __tablename__ = "workspace_members"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        _pg_enum(WorkspaceRole, "workspace_role"),
        nullable=False,
        default=WorkspaceRole.VIEWER,
        server_default=WorkspaceRole.VIEWER.value,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="members", lazy="raise")
    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise")

    __table_args__ = (
        # §164 requires this at the database level. Enforcing it only in
        # application code loses to a race between two concurrent invites,
        # leaving one user with two roles and an ambiguous permission answer.
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
        # Permission lookups are "this user, in this workspace" on every single
        # request, so the index leads with user_id.
        Index("ix_workspace_members_user_id_workspace_id", "user_id", "workspace_id"),
    )

    def __repr__(self) -> str:
        return (
            f"WorkspaceMember(workspace_id={self.workspace_id!r}, "
            f"user_id={self.user_id!r}, role={self.role.value!r})"
        )


class MediaAsset(WorkspaceEntity, SoftDeleteMixin):
    """One object in storage, with everything known about it (§10.17).

    This is the only table that may point at binary content: §9 forbids storing
    video or image bytes in Postgres, so what lives here is a *reference* —
    bucket plus key — alongside the metadata the product needs to reason about
    the file without fetching it.

    Every path that produces media converges on this row: a browser upload
    (§12), a provider result re-hosted by a worker (§27), a render output
    (§10.23) and a derived thumbnail all become ``MediaAsset`` records that
    differ only by :class:`AssetSourceType`. Downstream code therefore never
    needs to know where a file came from in order to serve, download or
    garbage-collect it.
    """

    __tablename__ = "media_assets"

    asset_type: Mapped[AssetType] = mapped_column(
        _pg_enum(AssetType, "asset_type"),
        nullable=False,
    )
    source_type: Mapped[AssetSourceType] = mapped_column(
        _pg_enum(AssetSourceType, "asset_source_type"),
        nullable=False,
    )

    # Not in §10.17's column list, but §12's handshake demands it: the row is
    # created when the presigned URL is issued, which is *before* any bytes
    # exist. Without a status, a PENDING row is indistinguishable from a
    # complete one and the API would happily serve a key that 404s.
    upload_status: Mapped[UploadStatus] = mapped_column(
        _pg_enum(UploadStatus, "upload_status"),
        nullable=False,
        default=UploadStatus.PENDING,
        server_default=UploadStatus.PENDING.value,
    )

    # Recorded per row rather than read from settings at access time. Buckets
    # get migrated and regions get split; an asset written last year must still
    # resolve to the bucket it was actually written to (§11).
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Display only. The key is server-generated (§11) precisely so this string
    # is never used to address anything.
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)

    # BigInteger: a 4K master render passes the 2 GiB signed-int ceiling.
    # Nullable because it is unknown until the upload is confirmed.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Milliseconds, not seconds: cut points and audio sync are specified in ms
    # throughout the timeline model, and a float duration would accumulate
    # rounding error across a multi-shot concatenation.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Double precision rather than a 3-decimal numeric, because real footage is
    # 30000/1001 and storing 29.970 would drift over a long render.
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)

    codec: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # SHA-256 hex. Used for integrity after a worker re-hosts a provider file
    # (§27) and for detecting a re-upload of the same bytes.
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Named ``asset_metadata`` in Python because ``metadata`` is taken by
    # SQLAlchemy's declarative machinery; the column keeps the §10.17 name.
    asset_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        postgresql.JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        # One row per object. Two assets sharing a key would make deletion
        # unsound: removing one would break the other.
        UniqueConstraint("bucket", "object_key", name="uq_media_asset_object"),
        # §61 — a key must live under its own workspace prefix. Enforced in
        # code by `belongs_to_workspace`, and again here so a bug in a service
        # cannot write a cross-tenant key at all.
        #
        # `starts_with` rather than `LIKE '...%'`: a literal `%` in a constraint
        # string is escaped to `%%` on its way through SQLAlchemy and lands in
        # the stored definition that way. Harmless in LIKE, but confusing to
        # read back — and this predicate needs no wildcard in the first place.
        CheckConstraint(
            "starts_with(object_key, 'workspaces/' || workspace_id::text || '/')",
            name="object_key_is_workspace_scoped",
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint("fps IS NULL OR fps > 0", name="fps_positive"),
        # A READY asset is one the server has confirmed; serving code reads
        # these columns without null checks, so the database guarantees them.
        CheckConstraint(
            "upload_status <> 'READY' OR size_bytes IS NOT NULL",
            name="ready_assets_have_size",
        ),
        # "Show me this workspace's images" is the library view's only query.
        workspace_scoped_index("media_assets", "asset_type"),
        # §163's orphan collector scans for uploads that were presigned and
        # then abandoned. Partial, so it stays tiny — PENDING is a transient
        # state and the healthy steady-state row count is near zero.
        Index(
            "ix_media_assets_pending_created_at",
            "created_at",
            postgresql_where=text("upload_status = 'PENDING'"),
        ),
    )

    @property
    def is_ready(self) -> bool:
        return self.upload_status is UploadStatus.READY and self.deleted_at is None

    def __repr__(self) -> str:
        return (
            f"MediaAsset(id={self.id!r}, type={self.asset_type.value!r}, "
            f"status={self.upload_status.value!r})"
        )


class Product(WorkspaceEntity, SoftDeleteMixin):
    """A thing being advertised (§10.5).

    The root of the Truth Layer. Everything a generated video may assert about
    this product hangs off it as a :class:`ProductFact` or :class:`ProductClaim`,
    and §13's whole point is that those are separate from the product's own
    free-text description — which is marketing copy, not evidence.
    """

    __tablename__ = "products"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    brand_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ProductStatus] = mapped_column(
        _pg_enum(ProductStatus, "product_status"),
        nullable=False,
        default=ProductStatus.DRAFT,
        server_default=ProductStatus.DRAFT.value,
    )

    # Written by the analyser in PHASE 6. Explicitly *not* a source of truth:
    # it is a readable summary, and anything load-bearing must exist as a
    # verified fact (§13).
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # §14's `visual_dna`: tone, palette, recommended backgrounds and camera
    # styles. Aesthetic direction rather than factual assertion, which is why
    # it lives here as JSON rather than going through fact verification.
    visual_dna: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    assets: Mapped[list[ProductAsset]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    facts: Mapped[list[ProductFact]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    claims: Mapped[list[ProductClaim]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(trim(category)) > 0", name="category_not_blank"),
        # A SKU identifies one product within a workspace. Partial, so the many
        # products without a SKU do not collide with each other on NULL.
        Index(
            "uq_products_workspace_id_sku",
            "workspace_id",
            "sku",
            unique=True,
            postgresql_where=text("sku IS NOT NULL AND deleted_at IS NULL"),
        ),
        workspace_scoped_index("products", "status"),
    )

    def __repr__(self) -> str:
        return f"Product(id={self.id!r}, name={self.name!r}, status={self.status.value!r})"


class ProductAsset(WorkspaceEntity):
    """A media asset attached to a product, with the role it plays (§10.6).

    A join row rather than a foreign key on `media_assets`, because one image
    can legitimately serve two products (a shared packaging shot) and because
    the *role* belongs to the relationship, not to the file.
    """

    __tablename__ = "product_assets"

    product_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT, not CASCADE: deleting a media asset that a product depends on
    # should fail loudly rather than silently strip the product's imagery.
    # Detaching is an explicit action (§112).
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )

    asset_role: Mapped[ProductAssetRole] = mapped_column(
        _pg_enum(ProductAssetRole, "product_asset_role"),
        nullable=False,
        default=ProductAssetRole.OTHER,
        server_default=ProductAssetRole.OTHER.value,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    product: Mapped[Product] = relationship(back_populates="assets", lazy="raise")
    media_asset: Mapped[MediaAsset] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint("product_id", "media_asset_id", name="uq_product_asset"),
        # At most one primary image per product, enforced by the database.
        # Doing this in application code loses to two concurrent "set primary"
        # requests, and a product with two primaries has no defined hero shot.
        Index(
            "uq_product_assets_primary",
            "product_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("ix_product_assets_product_id_sort_order", "product_id", "sort_order"),
    )

    def __repr__(self) -> str:
        return f"ProductAsset(product_id={self.product_id!r}, role={self.asset_role.value!r})"


class ProductFact(WorkspaceEntity):
    """Something asserted about a product, and how much it is trusted (§10.7).

    The load-bearing table of the Truth Layer. A fact's
    :class:`VerificationStatus` decides whether it may back a claim, and
    therefore whether a generated script may rely on it (§13, §109).

    Facts are never hard-deleted through the normal path: a wrong one is
    `REJECTED` so the analyser is not re-asked the same question and the
    review history survives.
    """

    __tablename__ = "product_facts"

    product_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )

    fact_type: Mapped[FactType] = mapped_column(_pg_enum(FactType, "fact_type"), nullable=False)

    #: Short machine-ish name, e.g. ``noise_level`` or ``filter_type``.
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The human-readable value. Always present, even when `value_json` is too.
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Structured form when there is one — a number with a unit, a range, a
    #: list. Kept alongside rather than instead of the text, because the text
    #: is what a script quotes and the structure is what a filter queries.
    value_json: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB, nullable=True)

    source_type: Mapped[FactSourceType] = mapped_column(
        _pg_enum(FactSourceType, "fact_source_type"), nullable=False
    )
    #: The image or document this came from, when it came from one.
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )

    verification_status: Mapped[VerificationStatus] = mapped_column(
        _pg_enum(VerificationStatus, "verification_status"),
        nullable=False,
        default=VerificationStatus.AI_INFERRED,
        server_default=VerificationStatus.AI_INFERRED.value,
    )
    # SET NULL rather than RESTRICT: a departing employee's account should not
    # pin every fact they ever confirmed, and `verified_at` still records that
    # verification happened.
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    product: Mapped[Product] = relationship(back_populates="facts", lazy="raise")

    __table_args__ = (
        CheckConstraint("length(trim(key)) > 0", name="key_not_blank"),
        CheckConstraint("length(trim(value_text)) > 0", name="value_not_blank"),
        # The database refuses a VERIFIED fact with no timestamp. Without this,
        # one missed assignment on one code path silently produces a fact that
        # claims to be confirmed with no record of when — and §13's guarantee
        # is only as good as its weakest write.
        CheckConstraint(
            "verification_status <> 'VERIFIED' OR verified_at IS NOT NULL",
            name="verified_facts_have_a_timestamp",
        ),
        workspace_scoped_index("product_facts", "product_id"),
        Index("ix_product_facts_product_id_status", "product_id", "verification_status"),
    )

    @property
    def is_verified(self) -> bool:
        return self.verification_status is VerificationStatus.VERIFIED

    def __repr__(self) -> str:
        return (
            f"ProductFact(id={self.id!r}, key={self.key!r}, "
            f"status={self.verification_status.value!r})"
        )


class ProductClaim(WorkspaceEntity):
    """A marketing statement, and whether it may be used (§10.8).

    §109 is the rule this table exists to enforce: a script generator calls
    for `VERIFIED` claims and gets nothing else. A claim that asserts anything
    checkable must cite the verified facts that support it, which is what
    `source_fact_ids` holds.
    """

    __tablename__ = "product_claims"

    product_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )

    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[ClaimType] = mapped_column(_pg_enum(ClaimType, "claim_type"), nullable=False)

    # A JSONB array of fact ids rather than a join table, because §10.8
    # specifies it and because the list is read as a whole every time — a
    # claim's evidence is never queried piecewise.
    source_fact_ids: Mapped[list[str]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    status: Mapped[ClaimStatus] = mapped_column(
        _pg_enum(ClaimStatus, "claim_status"),
        nullable=False,
        default=ClaimStatus.SUGGESTED,
        server_default=ClaimStatus.SUGGESTED.value,
    )
    risk_level: Mapped[ClaimRiskLevel] = mapped_column(
        _pg_enum(ClaimRiskLevel, "claim_risk_level"),
        nullable=False,
        default=ClaimRiskLevel.MEDIUM,
        server_default=ClaimRiskLevel.MEDIUM.value,
    )

    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    product: Mapped[Product] = relationship(back_populates="claims", lazy="raise")

    __table_args__ = (
        CheckConstraint("length(trim(claim_text)) > 0", name="claim_not_blank"),
        CheckConstraint("jsonb_typeof(source_fact_ids) = 'array'", name="source_fact_ids_is_array"),
        CheckConstraint(
            "status <> 'VERIFIED' OR verified_at IS NOT NULL",
            name="verified_claims_have_a_timestamp",
        ),
        workspace_scoped_index("product_claims", "product_id"),
        Index("ix_product_claims_product_id_status", "product_id", "status"),
    )

    @property
    def is_verified(self) -> bool:
        return self.status is ClaimStatus.VERIFIED

    def __repr__(self) -> str:
        return (
            f"ProductClaim(id={self.id!r}, type={self.claim_type.value!r}, "
            f"status={self.status.value!r})"
        )


class ProductAnalysis(WorkspaceEntity):
    """One run of the vision analyser against a product's imagery (§14, §15).

    Not in §10's table list, and added deliberately. §15 requires the prompt
    key and version to be recorded on *every* call, and §10.16's
    `provider_jobs` cannot serve: it hangs off `generation_job_id`, which is a
    video-generation concept from PHASE 9. Without a row here, "which prompt
    produced this claim?" has no answer — and that question is the whole point
    of versioning prompts.

    Failed runs are recorded too. A call that was refused or timed out is the
    one most likely to need explaining later.
    """

    __tablename__ = "product_analyses"

    product_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[AnalysisStatus] = mapped_column(
        _pg_enum(AnalysisStatus, "analysis_status"),
        nullable=False,
        default=AnalysisStatus.PENDING,
        server_default=AnalysisStatus.PENDING.value,
    )

    #: Adapter name, e.g. ``mock``. Not the vendor model id — that lives in
    #: `model` and can differ from what was asked for (§20).
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_key: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: The validated `ProductIntelligence`. Stored whole so a reviewer can see
    #: what the model actually said, including the fields it declined to fill.
    result: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB, nullable=True)

    #: Which product images were analysed. A later re-run against different
    #: imagery should be distinguishable from a re-run against the same set.
    analyzed_asset_ids: Mapped[list[str]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    # Cost metadata §20 requires a provider to report, so PHASE 18's credits
    # bill against measurements rather than an estimate invented later.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: How many facts and claims this run produced, so the review queue can be
    #: sized without re-reading the result blob.
    created_fact_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_claim_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    product: Mapped[Product] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("prompt_version > 0", name="prompt_version_positive"),
        CheckConstraint(
            "status <> 'SUCCEEDED' OR result IS NOT NULL",
            name="successful_analyses_have_a_result",
        ),
        workspace_scoped_index("product_analyses", "product_id"),
        Index("ix_product_analyses_product_id_created_at", "product_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"ProductAnalysis(id={self.id!r}, provider={self.provider!r}, "
            f"status={self.status.value!r})"
        )


# ---------------------------------------------------------------------------
# PHASE 7 — Project, Creative Plan, Script (§10.9-§10.11)
# ---------------------------------------------------------------------------


class Project(WorkspaceEntity, SoftDeleteMixin):
    """One video being made from one product (§10.9).

    The brief and the pipeline state in one row. Every field above `status` is
    an input the creative and script engines read (§16), which is why they are
    columns rather than a settings blob: "generate me a 30-second 9:16 Douyin
    ad" has to be answerable by a query, not by parsing JSON.
    """

    __tablename__ = "projects"

    product_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: PHASE 17. Declared now because the creative engine (§16) lists Brand Kit
    #: among its inputs, and adding the column later means a second migration
    #: over a table that will by then have rows.
    brand_kit_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[ProjectPurpose] = mapped_column(
        _pg_enum(ProjectPurpose, "project_purpose"),
        nullable=False,
        default=ProjectPurpose.SOCIAL_AD,
        server_default=ProjectPurpose.SOCIAL_AD.value,
    )
    target_platform: Mapped[TargetPlatform] = mapped_column(
        _pg_enum(TargetPlatform, "target_platform"),
        nullable=False,
        default=TargetPlatform.DOUYIN,
        server_default=TargetPlatform.DOUYIN.value,
    )
    target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: BCP-47. The language every generated word must be written in (§128).
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="zh-CN", server_default="zh-CN"
    )
    aspect_ratio: Mapped[AspectRatio] = mapped_column(
        _pg_enum(AspectRatio, "aspect_ratio"),
        nullable=False,
        default=AspectRatio.PORTRAIT_9_16,
        server_default=AspectRatio.PORTRAIT_9_16.value,
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default=text("30")
    )
    style: Mapped[VideoStyle] = mapped_column(
        _pg_enum(VideoStyle, "video_style"),
        nullable=False,
        default=VideoStyle.CLEAN_MINIMAL,
        server_default=VideoStyle.CLEAN_MINIMAL.value,
    )
    quality_mode: Mapped[QualityMode] = mapped_column(
        _pg_enum(QualityMode, "quality_mode"),
        nullable=False,
        default=QualityMode.STANDARD,
        server_default=QualityMode.STANDARD.value,
    )

    status: Mapped[ProjectStatus] = mapped_column(
        _pg_enum(ProjectStatus, "project_status"),
        nullable=False,
        default=ProjectStatus.DRAFT,
        server_default=ProjectStatus.DRAFT.value,
    )
    #: Why the project failed, kept for the resume path (§105). Cleared when a
    #: recovery transition moves it back onto the pipeline.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    product: Mapped[Product] = relationship(lazy="raise")

    __table_args__ = (
        # §12's ceiling, enforced in the database as well as the settings layer:
        # a 40-minute "short video" is a render bill nobody authorised.
        CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 600",
            name="duration_is_within_range",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="failed_projects_explain_themselves",
        ),
        workspace_scoped_index("projects", "status"),
        workspace_scoped_index("projects", "product_id"),
    )

    def __repr__(self) -> str:
        return f"Project(id={self.id!r}, name={self.name!r}, status={self.status.value!r})"


class CreativePlan(WorkspaceEntity):
    """One of the three creative directions offered for a project (§10.10, §16).

    Immutable once written. Regenerating produces a new `version` rather than
    editing these — the user's choice was made against specific wording, and
    rewriting it under them would make "which plan did they pick?" unanswerable.
    """

    __tablename__ = "creative_plans"

    project_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Generation round. All three plans from one call share a version.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    core_message: Mapped[str] = mapped_column(Text, nullable=False)
    narrative_structure: Mapped[str] = mapped_column(Text, nullable=False)
    visual_direction: Mapped[str] = mapped_column(Text, nullable=False)
    camera_direction: Mapped[str] = mapped_column(Text, nullable=False)
    music_direction: Mapped[str] = mapped_column(Text, nullable=False)
    ending_cta: Mapped[str] = mapped_column(Text, nullable=False)
    #: §16's `risk_notes`. Where the model flags a direction that leans on
    #: something unverified — read by a human before selection, not enforced.
    risk_notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    recommended_style: Mapped[VideoStyle | None] = mapped_column(
        _pg_enum(VideoStyle, "video_style"), nullable=True
    )

    selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: §15's record: provider, model, prompt key and version for this call.
    model_info: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        # At most one selected plan per project. §16 requires a choice before
        # scripting, and two simultaneous choices is not a state the script
        # engine could act on.
        Index(
            "uq_creative_plans_selected",
            "project_id",
            unique=True,
            postgresql_where=text("selected"),
        ),
        workspace_scoped_index("creative_plans", "project_id"),
        Index("ix_creative_plans_project_id_version", "project_id", "version"),
    )

    def __repr__(self) -> str:
        return f"CreativePlan(id={self.id!r}, title={self.title!r}, selected={self.selected!r})"


class Script(WorkspaceEntity):
    """A versioned script for a project (§10.11, §17).

    Versions are immutable and never overwritten: §17 says an edit produces a
    new version and history survives. `SUPERSEDED` marks the ones an edit
    replaced, so "what did we approve last Tuesday" stays answerable.

    `sourced_claim_ids` is the audit trail P7-T09 exists to produce. It records
    which verified claims were loaded when this text was generated, so a claim
    later withdrawn can be traced to every script that leaned on it.
    """

    __tablename__ = "scripts"

    project_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    creative_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("creative_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The structured sections of §17, validated against `ScriptDocument`.
    content_json: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB, nullable=False)
    #: The same script as flat narration, for reading and for the TTS engine.
    plain_text: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[ScriptStatus] = mapped_column(
        _pg_enum(ScriptStatus, "script_status"),
        nullable=False,
        default=ScriptStatus.DRAFT,
        server_default=ScriptStatus.DRAFT.value,
    )

    #: Which VERIFIED claims were in scope when this was written (P7-T09).
    sourced_claim_ids: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: Estimated narration length from the character budget (§17). An estimate,
    #: replaced by the TTS engine's measurement in PHASE 12.
    estimated_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_info: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_scripts_project_version"),
        # One approved script at a time: PHASE 8 asks "which script do I turn
        # into a storyboard", and that must have one answer.
        Index(
            "uq_scripts_approved",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'APPROVED'"),
        ),
        workspace_scoped_index("scripts", "project_id"),
    )

    def __repr__(self) -> str:
        return f"Script(id={self.id!r}, version={self.version!r}, status={self.status.value!r})"
