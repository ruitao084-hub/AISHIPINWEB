"""Domain enumerations.

These are database constraints as much as Python types (§9: "status fields use
an Enum or a database constraint"). Every value here is persisted, so renaming
one is a migration, not a refactor.
"""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    """Lifecycle of a user account (§10.1)."""

    ACTIVE = "ACTIVE"
    #: Set by an administrator. Blocks login but preserves all data.
    SUSPENDED = "SUSPENDED"
    #: Soft-deleted. Retained so audit history and authored content survive.
    DELETED = "DELETED"


class WorkspaceStatus(StrEnum):
    """Lifecycle of a workspace (§10.2)."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class PlanCode(StrEnum):
    """Subscription tier (§125).

    Recorded from the start even though billing lands in PHASE 18, because a
    workspace created before plans exist still needs a defensible default.
    """

    FREE = "FREE"
    PRO = "PRO"
    BUSINESS = "BUSINESS"
    ENTERPRISE = "ENTERPRISE"


class AssetType(StrEnum):
    """What kind of media an asset holds (§10.17)."""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    SUBTITLE = "SUBTITLE"
    DOCUMENT = "DOCUMENT"
    THUMBNAIL = "THUMBNAIL"


class AssetSourceType(StrEnum):
    """Where an asset came from (§10.17).

    This is provenance, and it matters beyond bookkeeping: a `USER_UPLOAD` is
    untrusted input that must be validated, while `AI_GENERATED` content was
    fetched from a provider and re-hosted by a worker (§27). Retention policy
    (§113) and the orphan collector (§163) both branch on it.
    """

    USER_UPLOAD = "USER_UPLOAD"
    AI_GENERATED = "AI_GENERATED"
    RENDERED = "RENDERED"
    DERIVED = "DERIVED"


class UploadStatus(StrEnum):
    """Where an asset sits in the two-phase upload handshake (§12).

    A presigned URL is handed out before any bytes exist, so a row is created
    in `PENDING` and only becomes `READY` once the server has confirmed the
    object is actually in storage and passed validation. Anything left
    `PENDING` is an abandoned upload for the GC to reclaim (§163).
    """

    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class ProductStatus(StrEnum):
    """Where a product sits in its lifecycle (§104).

    Transitions are validated by :func:`can_transition_product`; §105 forbids
    writing a status by assignment, and the same rule is applied here.
    """

    DRAFT = "DRAFT"
    #: Enough imagery attached to be analysed.
    ASSETS_READY = "ASSETS_READY"
    ANALYZING = "ANALYZING"
    #: AI produced inferences that a human has not yet confirmed (§13).
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    #: Verified facts and claims exist; safe to generate from.
    READY = "READY"
    ARCHIVED = "ARCHIVED"


#: Legal product transitions (§104). Anything absent is refused.
_PRODUCT_TRANSITIONS: dict[ProductStatus, frozenset[ProductStatus]] = {
    ProductStatus.DRAFT: frozenset({ProductStatus.ASSETS_READY, ProductStatus.ARCHIVED}),
    ProductStatus.ASSETS_READY: frozenset(
        {
            ProductStatus.ANALYZING,
            # Removing the last image drops a product back to DRAFT.
            ProductStatus.DRAFT,
            # A product whose facts were all entered by hand never needs the
            # analyser; §13 treats user-provided data as the strongest source.
            ProductStatus.READY,
            ProductStatus.ARCHIVED,
        }
    ),
    ProductStatus.ANALYZING: frozenset(
        {
            ProductStatus.REVIEW_REQUIRED,
            # Analysis failing must not strand the product mid-state (§24).
            ProductStatus.ASSETS_READY,
            ProductStatus.ARCHIVED,
        }
    ),
    ProductStatus.REVIEW_REQUIRED: frozenset(
        {
            ProductStatus.READY,
            # Re-analysis. §103 rule 10 wants one-click regeneration and rule 4
            # wants the user to be able to go back; a reviewer who adds a
            # clearer photograph and re-runs the analyser is the ordinary case.
            # Its absence here was an oversight rather than a policy: READY
            # already allows this, and it would be strange for a *finished*
            # product to be re-analysable while one still under review was not.
            ProductStatus.ANALYZING,
            ProductStatus.ARCHIVED,
        }
    ),
    ProductStatus.READY: frozenset(
        {
            # Editing facts can invalidate claims and send a product back for
            # review — see `demote_dependent_claims` in the truth service.
            ProductStatus.REVIEW_REQUIRED,
            ProductStatus.ANALYZING,
            ProductStatus.ARCHIVED,
        }
    ),
    # Terminal by design: restoring an archived product is an explicit,
    # separate operation rather than a status write.
    ProductStatus.ARCHIVED: frozenset(),
}


def can_transition_product(current: ProductStatus, target: ProductStatus) -> bool:
    """Whether ``current -> target`` is a legal product transition (§104)."""
    if current is target:
        return True
    return target in _PRODUCT_TRANSITIONS[current]


def allowed_product_transitions(current: ProductStatus) -> frozenset[ProductStatus]:
    """Every status reachable from ``current`` in one step."""
    return _PRODUCT_TRANSITIONS[current]


class AnalysisStatus(StrEnum):
    """Outcome of one product analysis run (§14, P6-T06).

    Recorded even when it fails: §15 requires the prompt key and version of
    every call, and a failed call is exactly the one someone will want to
    explain later.
    """

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ProductAssetRole(StrEnum):
    """What a product image shows (§10.6).

    The role is not decoration: PHASE 8's prompt compiler picks reference
    imagery by role, so "the front of the product" has to be answerable
    without a human looking at the picture.
    """

    FRONT = "FRONT"
    SIDE = "SIDE"
    BACK = "BACK"
    ANGLE_45 = "ANGLE_45"
    PACKAGING = "PACKAGING"
    LOGO = "LOGO"
    DETAIL = "DETAIL"
    MATERIAL = "MATERIAL"
    SCENE = "SCENE"
    STRUCTURE = "STRUCTURE"
    OTHER = "OTHER"


class FactType(StrEnum):
    """What kind of thing a product fact asserts (§10.7).

    The taskbook names ``fact_type`` without enumerating it. These values are
    chosen so that the *risky* categories are separable: §13's forbidden
    example ("除甲醛率 99.9%") is a `PERFORMANCE` fact, and being able to
    identify that class by type is what lets the claim rules treat it more
    strictly than, say, a colour.
    """

    #: A measurable physical specification: dimensions, weight, capacity.
    SPEC = "SPEC"
    MATERIAL = "MATERIAL"
    #: A functional capability — what the product does.
    FEATURE = "FEATURE"
    #: A quantified outcome. The category §13 exists to protect.
    PERFORMANCE = "PERFORMANCE"
    #: A standard, test result or certificate the product holds.
    CERTIFICATION = "CERTIFICATION"
    INGREDIENT = "INGREDIENT"
    COMPATIBILITY = "COMPATIBILITY"
    WARRANTY = "WARRANTY"
    PRICING = "PRICING"
    APPEARANCE = "APPEARANCE"
    OTHER = "OTHER"


class FactSourceType(StrEnum):
    """Where a fact came from (§10.7).

    Distinct from :class:`VerificationStatus`, which is how much it is
    trusted. A fact can be `AI_VISION` in origin and `VERIFIED` in status —
    that is precisely the review workflow §13 describes — but the origin is
    never overwritten, so "a human confirmed something the AI guessed" stays
    distinguishable from "a human typed it in".
    """

    USER_INPUT = "USER_INPUT"
    #: Inferred from product imagery by the vision provider (PHASE 6).
    AI_VISION = "AI_VISION"
    #: Extracted from text — a description or spec sheet — by an LLM.
    AI_TEXT = "AI_TEXT"
    #: Read from a document the user supplied as evidence.
    DOCUMENT = "DOCUMENT"
    IMPORT = "IMPORT"


class VerificationStatus(StrEnum):
    """How much a fact may be trusted (§10.7, §13).

    This is the Truth Layer. Only `VERIFIED` facts may back a claim, and only
    `VERIFIED` claims may reach a script (§109). Everything the AI produces
    starts at `AI_INFERRED` and can only be promoted by a person.
    """

    #: The AI's guess. Never usable as a fact on its own.
    AI_INFERRED = "AI_INFERRED"
    #: Typed in by a user but not yet explicitly confirmed.
    USER_PROVIDED = "USER_PROVIDED"
    #: Confirmed by a person, with `verified_by_user_id` and `verified_at` set.
    VERIFIED = "VERIFIED"
    #: Explicitly wrong. Kept rather than deleted so the AI is not re-asked
    #: the same question and the audit trail survives.
    REJECTED = "REJECTED"


class ClaimType(StrEnum):
    """What a marketing claim asserts (§10.8).

    Not enumerated by the taskbook. The split is by *what would have to be
    true* for the claim to be honest, because that is what decides whether it
    needs a verified fact behind it (§13, §109).
    """

    #: "Filters impurities from the air." Asserts a capability.
    FUNCTIONAL = "FUNCTIONAL"
    #: "Removes 99.9% of formaldehyde." Asserts a number.
    PERFORMANCE = "PERFORMANCE"
    #: "Quieter than the leading brand." Asserts something about a competitor.
    COMPARATIVE = "COMPARATIVE"
    #: "CE certified." Asserts a credential.
    CERTIFICATION = "CERTIFICATION"
    #: "Safe for infants." Asserts absence of harm.
    SAFETY = "SAFETY"
    #: "Brings calm to your morning." Asserts nothing checkable.
    EMOTIONAL = "EMOTIONAL"


#: Claim types that assert something about the product and therefore cannot be
#: verified without at least one `VERIFIED` fact behind them (§13).
#:
#: `EMOTIONAL` is the sole exception, and deliberately so: "brings calm to your
#: morning" makes no factual assertion, so demanding evidence for it would be
#: theatre. Everything else — including `FUNCTIONAL`, per §13's own example
#: where "helps filter impurities" is allowed only *if that function has been
#: confirmed as a fact* — requires substantiation.
FACT_BACKED_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.FUNCTIONAL,
        ClaimType.PERFORMANCE,
        ClaimType.COMPARATIVE,
        ClaimType.CERTIFICATION,
        ClaimType.SAFETY,
    }
)


class ClaimStatus(StrEnum):
    """Whether a claim may be used (§10.8).

    §109 is unambiguous: only `VERIFIED` claims reach a script.
    """

    #: Proposed — by the AI, or by a user who has not confirmed it yet.
    SUGGESTED = "SUGGESTED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ClaimRiskLevel(StrEnum):
    """How much substantiation a claim needs before it is safe to broadcast.

    Recorded per claim so the review UI can order the queue by consequence:
    an unverified `HIGH` claim about infant safety deserves attention before a
    `LOW` one about colour.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


#: Default risk for each claim type, used when a caller does not set one.
#: Quantified, comparative, safety and certification claims are the ones that
#: attract regulatory attention, so they default high rather than low.
_DEFAULT_CLAIM_RISK: dict[ClaimType, ClaimRiskLevel] = {
    ClaimType.EMOTIONAL: ClaimRiskLevel.LOW,
    ClaimType.FUNCTIONAL: ClaimRiskLevel.MEDIUM,
    ClaimType.PERFORMANCE: ClaimRiskLevel.HIGH,
    ClaimType.COMPARATIVE: ClaimRiskLevel.HIGH,
    ClaimType.CERTIFICATION: ClaimRiskLevel.HIGH,
    ClaimType.SAFETY: ClaimRiskLevel.HIGH,
}


def default_risk_level(claim_type: ClaimType) -> ClaimRiskLevel:
    """The risk level a claim of this type carries unless overridden."""
    return _DEFAULT_CLAIM_RISK[claim_type]


def requires_fact_backing(claim_type: ClaimType) -> bool:
    """Whether verifying this claim requires a `VERIFIED` supporting fact."""
    return claim_type in FACT_BACKED_CLAIM_TYPES


class WorkspaceRole(StrEnum):
    """Membership role (§40).

    Ordered from most to least privileged; :func:`role_rank` relies on that
    ordering for "at least this role" comparisons.
    """

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"


class Permission(StrEnum):
    """A single capability, checked at the point of use.

    Handlers ask for a *permission*, never a role. Roles change shape as the
    product grows — a scattered ``if role == ADMIN`` becomes wrong silently,
    whereas adding a permission to the matrix below is one visible edit.
    """

    # Workspace
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_UPDATE = "workspace:update"
    WORKSPACE_DELETE = "workspace:delete"

    # Membership
    MEMBER_READ = "member:read"
    MEMBER_INVITE = "member:invite"
    MEMBER_UPDATE_ROLE = "member:update_role"
    MEMBER_REMOVE = "member:remove"

    # Billing (§40 — OWNER only)
    BILLING_MANAGE = "billing:manage"

    # Content
    PRODUCT_READ = "product:read"
    PRODUCT_WRITE = "product:write"
    PRODUCT_DELETE = "product:delete"
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_DELETE = "project:delete"
    TEMPLATE_WRITE = "template:write"

    # Generation — costs money, so it is not implied by write access
    GENERATION_RUN = "generation:run"

    # Assets
    ASSET_UPLOAD = "asset:upload"
    ASSET_DOWNLOAD = "asset:download"


_VIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.WORKSPACE_READ,
        Permission.MEMBER_READ,
        Permission.PRODUCT_READ,
        Permission.PROJECT_READ,
        # §40 leaves download "open according to policy"; granting read-only
        # users their own finished videos is the useful default, and the
        # per-workspace policy switch lands with billing plans.
        Permission.ASSET_DOWNLOAD,
    }
)

_EDITOR_PERMISSIONS: frozenset[Permission] = _VIEWER_PERMISSIONS | {
    Permission.PRODUCT_WRITE,
    Permission.PROJECT_WRITE,
    Permission.GENERATION_RUN,
    # Separate from PRODUCT_WRITE because an upload consumes storage the
    # workspace pays for, and because media outlives the product it was
    # uploaded against — the quota question is about the asset, not the owner.
    Permission.ASSET_UPLOAD,
}

_ADMIN_PERMISSIONS: frozenset[Permission] = _EDITOR_PERMISSIONS | {
    Permission.PRODUCT_DELETE,
    Permission.PROJECT_DELETE,
    Permission.TEMPLATE_WRITE,
    Permission.WORKSPACE_UPDATE,
    # "Partial member management" (§40): an admin may invite and remove, but
    # may not change roles — otherwise an admin could promote themselves to
    # owner, which is a privilege-escalation path rather than delegation.
    Permission.MEMBER_INVITE,
    Permission.MEMBER_REMOVE,
}

_OWNER_PERMISSIONS: frozenset[Permission] = _ADMIN_PERMISSIONS | {
    Permission.WORKSPACE_DELETE,
    Permission.MEMBER_UPDATE_ROLE,
    Permission.BILLING_MANAGE,
}

#: The authorisation matrix. One table, so the whole policy is auditable at a
#: glance rather than reconstructed from conditionals across the codebase.
ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[Permission]] = {
    WorkspaceRole.OWNER: _OWNER_PERMISSIONS,
    WorkspaceRole.ADMIN: _ADMIN_PERMISSIONS,
    WorkspaceRole.EDITOR: _EDITOR_PERMISSIONS,
    WorkspaceRole.VIEWER: _VIEWER_PERMISSIONS,
}

_ROLE_ORDER: tuple[WorkspaceRole, ...] = (
    WorkspaceRole.OWNER,
    WorkspaceRole.ADMIN,
    WorkspaceRole.EDITOR,
    WorkspaceRole.VIEWER,
)


def role_rank(role: WorkspaceRole) -> int:
    """Seniority, 0 being most privileged."""
    return _ROLE_ORDER.index(role)


def role_has_permission(role: WorkspaceRole, permission: Permission) -> bool:
    """Whether ``role`` grants ``permission``."""
    return permission in ROLE_PERMISSIONS[role]


def permissions_for(role: WorkspaceRole) -> frozenset[Permission]:
    """Everything ``role`` may do."""
    return ROLE_PERMISSIONS[role]
