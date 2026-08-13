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


# ---------------------------------------------------------------------------
# PHASE 7 — Project, Creative and Script (§10.9-§10.11, §16, §17, §105)
# ---------------------------------------------------------------------------


class ProjectStatus(StrEnum):
    """Where a video project has got to (§10.9, §105).

    Longer than the product's machine because a project *is* the pipeline: each
    state names a stage that can fail on its own and be resumed on its own.
    """

    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    CREATIVE_PLANNING = "CREATIVE_PLANNING"
    SCRIPTING = "SCRIPTING"
    STORYBOARDING = "STORYBOARDING"
    GENERATING = "GENERATING"
    COMPOSITING = "COMPOSITING"
    QC = "QC"
    READY = "READY"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


#: The pipeline in order. Used to build the transition table below and to answer
#: "where does a recovered project go back to", so the sequence is stated once.
_PROJECT_PIPELINE: tuple[ProjectStatus, ...] = (
    ProjectStatus.DRAFT,
    ProjectStatus.ANALYZING,
    ProjectStatus.CREATIVE_PLANNING,
    ProjectStatus.SCRIPTING,
    ProjectStatus.STORYBOARDING,
    ProjectStatus.GENERATING,
    ProjectStatus.COMPOSITING,
    ProjectStatus.QC,
    ProjectStatus.READY,
)

#: States a failure can strike from. §105 says FAILED is reachable from most
#: intermediate states — not from DRAFT (nothing has run yet) and not from the
#: terminal ones.
_FAILABLE: frozenset[ProjectStatus] = frozenset(_PROJECT_PIPELINE[1:-1])


def _build_project_transitions() -> dict[ProjectStatus, frozenset[ProjectStatus]]:
    """Derive the legal edges rather than hand-listing forty of them.

    Three rules, and deriving them means they cannot disagree with each other:

    1. **Forward one step**, along the pipeline.
    2. **Backward one step**, because §103 rule 4 requires the user to be able
       to go back — regenerating a script after rejecting the storyboard is the
       ordinary case, not an exception.
    3. **Into FAILED and back out to where it failed**, which is §105's
       "恢复后回原合理状态": a recovered project resumes at the stage that
       failed, not at the beginning.

    ARCHIVED is reachable from everywhere and leads nowhere, matching the
    product machine.
    """
    table: dict[ProjectStatus, set[ProjectStatus]] = {
        status: {ProjectStatus.ARCHIVED} for status in ProjectStatus
    }
    table[ProjectStatus.ARCHIVED] = set()

    for index, status in enumerate(_PROJECT_PIPELINE):
        if index + 1 < len(_PROJECT_PIPELINE):
            table[status].add(_PROJECT_PIPELINE[index + 1])
        if index > 0:
            table[status].add(_PROJECT_PIPELINE[index - 1])
        if status in _FAILABLE:
            table[status].add(ProjectStatus.FAILED)
            table[ProjectStatus.FAILED].add(status)

    # A finished project can be sent back for another pass — §103 rule 10's
    # one-click regeneration has to land somewhere.
    table[ProjectStatus.READY].add(ProjectStatus.STORYBOARDING)

    # `ANALYZING` is the one skippable stage, and it has to be. A project's
    # analysis stage means "analyse the product this project is for", and by
    # PHASE 6 that belongs to the *product*: a product already in
    # `REVIEW_REQUIRED` or `READY` has been analysed, and forcing its projects
    # through a no-op stage would mean either a second billed vision call or a
    # status the pipeline sets and immediately clears. Neither is honest.
    table[ProjectStatus.DRAFT].add(ProjectStatus.CREATIVE_PLANNING)
    table[ProjectStatus.CREATIVE_PLANNING].add(ProjectStatus.DRAFT)

    return {status: frozenset(targets) for status, targets in table.items()}


_PROJECT_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = _build_project_transitions()


def can_transition_project(current: ProjectStatus, target: ProjectStatus) -> bool:
    """Whether ``current -> target`` is a legal project transition (§105)."""
    if current is target:
        return True
    return target in _PROJECT_TRANSITIONS[current]


def allowed_project_transitions(current: ProjectStatus) -> frozenset[ProjectStatus]:
    """Every status reachable from ``current`` in one step."""
    return _PROJECT_TRANSITIONS[current]


class ProjectPurpose(StrEnum):
    """What the video is for (§10.9, §16).

    Feeds the creative prompt, so these are not cosmetic: a launch film and a
    marketplace listing want different structures from the same product.
    """

    LAUNCH = "LAUNCH"
    ECOMMERCE_LISTING = "ECOMMERCE_LISTING"
    SOCIAL_AD = "SOCIAL_AD"
    BRAND_STORY = "BRAND_STORY"
    FEATURE_HIGHLIGHT = "FEATURE_HIGHLIGHT"
    TUTORIAL = "TUTORIAL"
    OTHER = "OTHER"


class TargetPlatform(StrEnum):
    """Where the video will be published (§10.9)."""

    DOUYIN = "DOUYIN"
    XIAOHONGSHU = "XIAOHONGSHU"
    BILIBILI = "BILIBILI"
    WECHAT_CHANNELS = "WECHAT_CHANNELS"
    TAOBAO = "TAOBAO"
    TIKTOK = "TIKTOK"
    INSTAGRAM = "INSTAGRAM"
    YOUTUBE = "YOUTUBE"
    OTHER = "OTHER"


class AspectRatio(StrEnum):
    """Frame shape. Stored as an enum rather than a free string because the
    render pipeline (PHASE 13) resolves it to exact pixel dimensions, and
    "9 : 16" versus "9:16" would be two products' worth of bugs."""

    PORTRAIT_9_16 = "9:16"
    LANDSCAPE_16_9 = "16:9"
    SQUARE_1_1 = "1:1"
    VERTICAL_4_5 = "4:5"


#: Each platform's native frame. Offered as the default in the wizard; the user
#: can still choose otherwise, since one asset is often cut for several places.
PLATFORM_DEFAULT_ASPECT: dict[TargetPlatform, AspectRatio] = {
    TargetPlatform.DOUYIN: AspectRatio.PORTRAIT_9_16,
    TargetPlatform.XIAOHONGSHU: AspectRatio.VERTICAL_4_5,
    TargetPlatform.BILIBILI: AspectRatio.LANDSCAPE_16_9,
    TargetPlatform.WECHAT_CHANNELS: AspectRatio.PORTRAIT_9_16,
    TargetPlatform.TAOBAO: AspectRatio.SQUARE_1_1,
    TargetPlatform.TIKTOK: AspectRatio.PORTRAIT_9_16,
    TargetPlatform.INSTAGRAM: AspectRatio.VERTICAL_4_5,
    TargetPlatform.YOUTUBE: AspectRatio.LANDSCAPE_16_9,
    TargetPlatform.OTHER: AspectRatio.PORTRAIT_9_16,
}


class VideoStyle(StrEnum):
    """Visual register (§10.9). Reaches the prompt compiler in PHASE 8."""

    CLEAN_MINIMAL = "CLEAN_MINIMAL"
    WARM_LIFESTYLE = "WARM_LIFESTYLE"
    TECH_PREMIUM = "TECH_PREMIUM"
    BOLD_ENERGETIC = "BOLD_ENERGETIC"
    NATURAL_DOCUMENTARY = "NATURAL_DOCUMENTARY"
    LUXURY = "LUXURY"


class QualityMode(StrEnum):
    """Cost/quality tier (§10.9, §138). Drives provider and model selection in
    PHASE 19 and the credit price in PHASE 18."""

    FAST = "FAST"
    STANDARD = "STANDARD"
    HIGH = "HIGH"
    PREMIUM = "PREMIUM"


class ScriptStatus(StrEnum):
    """§10.11. A script is DRAFT until a person accepts it.

    `SUPERSEDED` rather than deletion: §17 requires history to survive an edit,
    so a new version marks the old one superseded instead of overwriting it.
    """

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


#: The script's sections, in order, exactly as §17 lists them. Held here rather
#: than in the prompt so the schema, the validator and the prompt all read the
#: same tuple — a section the model invents cannot pass validation, and one it
#: omits is visible.
SCRIPT_SECTIONS: tuple[str, ...] = (
    "opening_hook",
    "problem",
    "product_intro",
    "feature_1",
    "feature_2",
    "usage_scene",
    "proof_or_visual_support",
    "brand_ending",
    "cta",
)

#: Spoken syllables per second used to budget section wordcount against the
#: project's duration (§17 "必须根据目标时长做字数预算"). Chinese narration in
#: advertising sits around this rate; it is a budget, not a promise, and PHASE
#: 12 replaces the estimate with the TTS engine's measured duration.
SPEECH_RATE_CHARS_PER_SECOND: float = 4.5


# ---------------------------------------------------------------------------
# PHASE 8 — Storyboard, Shot, Prompt Compiler (§10.12-§10.14, §18, §19, §29)
# ---------------------------------------------------------------------------


class StoryboardStatus(StrEnum):
    """§10.12. Same shape as `ScriptStatus`, and for the same reason (§17):
    a new storyboard supersedes rather than overwrites, so "which storyboard
    did we film" keeps an answer."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class ShotType(StrEnum):
    """What a shot is doing (§10.13).

    Not decoration: PHASE 8's compiler picks reference imagery by shot type,
    and PHASE 14's QC weights the identity check differently for a `MACRO`
    than for a `LIFESTYLE` — one is mostly product, the other mostly room.
    """

    HOOK = "HOOK"
    PRODUCT_HERO = "PRODUCT_HERO"
    MACRO = "MACRO"
    ROTATION = "ROTATION"
    USAGE = "USAGE"
    MATERIAL = "MATERIAL"
    FEATURE = "FEATURE"
    EXPLODED = "EXPLODED"
    BEFORE_AFTER = "BEFORE_AFTER"
    LIFESTYLE = "LIFESTYLE"
    BRAND_ENDING = "BRAND_ENDING"
    CUSTOM = "CUSTOM"


#: Shot types where the product fills the frame, so a drifted shape or an
#: invented component is unmissable. §29's identity lock defaults on for these
#: and off for the rest — locking a wide lifestyle shot as hard as a macro
#: produces stiff, product-catalogue footage for no safety gain.
PRODUCT_DOMINANT_SHOTS: frozenset[ShotType] = frozenset(
    {
        ShotType.PRODUCT_HERO,
        ShotType.MACRO,
        ShotType.ROTATION,
        ShotType.MATERIAL,
        ShotType.FEATURE,
        ShotType.EXPLODED,
    }
)


class ShotStatus(StrEnum):
    """Where a shot sits in generation (§10.13).

    `PENDING` means "written but never sent to a provider"; `READY` means a
    generated clip has been chosen for it. The middle states become meaningful
    in PHASE 9 when the job system can actually report progress.
    """

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ReferenceRole(StrEnum):
    """Why an image is attached to a shot (§10.14).

    `IDENTITY` is the load-bearing one: those are the frames §29 says the
    generated product must match, and PHASE 14's QC compares against exactly
    this set.
    """

    IDENTITY = "IDENTITY"
    STYLE = "STYLE"
    COMPOSITION = "COMPOSITION"
    ENVIRONMENT = "ENVIRONMENT"
    OTHER = "OTHER"


class TransitionType(StrEnum):
    """How a shot enters or leaves (§10.13). Resolved to ffmpeg filters in
    PHASE 13; kept as an enum because "cross fade" and "crossfade" would be two
    products' worth of render bugs."""

    CUT = "CUT"
    FADE = "FADE"
    DISSOLVE = "DISSOLVE"
    WIPE = "WIPE"
    ZOOM = "ZOOM"
    NONE = "NONE"


#: §18's per-shot duration guidance. A shot under two seconds reads as a
#: flicker; over ten, most video models lose coherence and the clip drifts.
MIN_SHOT_SECONDS: float = 2.0
MAX_SHOT_SECONDS: float = 10.0

#: How far a storyboard's total may sit from the project's target duration.
#: §18 says the sum must be ≈ the project duration, and this is what ≈ means:
#: tight, because unlike a script's word budget this number is what the
#: renderer will actually produce.
STORYBOARD_DURATION_TOLERANCE: float = 0.10


# ---------------------------------------------------------------------------
# PHASE 9 — Job system (§10.15, §10.16, §22, §23, §24, §106)
# ---------------------------------------------------------------------------


class JobType(StrEnum):
    """What a job is for (§10.15).

    One table for every long task, because §22 routes them all through the same
    orchestrator. The type picks the queue and the worker, not the schema.
    """

    VIDEO_GENERATION = "VIDEO_GENERATION"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    TTS = "TTS"
    RENDER = "RENDER"
    QC = "QC"
    PRODUCT_ANALYSIS = "PRODUCT_ANALYSIS"


class JobStatus(StrEnum):
    """§106's job machine.

    `SUBMITTED` and `PROCESSING` are distinct on purpose: the first means a
    provider accepted the request, the second that it reports work underway.
    Losing the distinction would make "the provider never answered" and "the
    provider is slow" the same incident.
    """

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TIMEOUT = "TIMEOUT"


#: §106: terminal states never return to PROCESSING. A retry creates a new
#: attempt rather than resurrecting a finished job, which is what keeps
#: `retry_count` meaningful and stops a job being billed twice.
TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.TIMEOUT}
)

_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset({JobStatus.QUEUED, JobStatus.CANCELED, JobStatus.FAILED}),
    JobStatus.QUEUED: frozenset(
        {JobStatus.SUBMITTED, JobStatus.CANCELED, JobStatus.FAILED, JobStatus.TIMEOUT}
    ),
    JobStatus.SUBMITTED: frozenset(
        {
            JobStatus.PROCESSING,
            # A fast provider can finish before we ever poll.
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELED,
            JobStatus.TIMEOUT,
        }
    ),
    JobStatus.PROCESSING: frozenset(
        {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.TIMEOUT}
    ),
    # A retry re-queues from FAILED or TIMEOUT. Not from COMPLETED or CANCELED:
    # one is done and the other was deliberately stopped.
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.TIMEOUT: frozenset({JobStatus.QUEUED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.CANCELED: frozenset(),
}


def can_transition_job(current: JobStatus, target: JobStatus) -> bool:
    """Whether ``current -> target`` is legal (§106)."""
    if current is target:
        return True
    return target in _JOB_TRANSITIONS[current]


def allowed_job_transitions(current: JobStatus) -> frozenset[JobStatus]:
    return _JOB_TRANSITIONS[current]


def is_terminal_job(status: JobStatus) -> bool:
    return status in TERMINAL_JOB_STATUSES


class QueueName(StrEnum):
    """§25's separate queues.

    One Redis, four queues. Separate because their work is differently shaped:
    a render pins a CPU for minutes while a TTS call is mostly waiting, and one
    concurrency number for both would either starve renders or overload them.
    """

    VIDEO = "video"
    TTS = "tts"
    RENDER = "render"
    QC = "qc"
    DEFAULT = "default"


#: Which queue each job type lands on.
QUEUE_FOR_JOB_TYPE: dict[JobType, QueueName] = {
    JobType.VIDEO_GENERATION: QueueName.VIDEO,
    JobType.IMAGE_GENERATION: QueueName.VIDEO,
    JobType.TTS: QueueName.TTS,
    JobType.RENDER: QueueName.RENDER,
    JobType.QC: QueueName.QC,
    JobType.PRODUCT_ANALYSIS: QueueName.DEFAULT,
}


class FailureKind(StrEnum):
    """§24's retry taxonomy, as a value rather than a scattered `if`.

    The distinction is the whole of §24: a 429 is worth retrying and a policy
    violation is not, because retrying an identical rejected request spends
    money to receive the same refusal.
    """

    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"


#: Error codes §24 names as worth retrying.
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
        "STORAGE_ERROR",
        "JOB_TIMEOUT",
        "DOWNLOAD_FAILED",
        "NETWORK_ERROR",
    }
)

#: And the ones it names as not. Listed rather than inferred from "everything
#: else", so a new error code defaults to *not* retrying — the safer default,
#: since a wrongly-retried permanent failure costs money on every attempt.
PERMANENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "PROVIDER_REJECTED",
        "VALIDATION_ERROR",
        "ASSET_INVALID",
        "WORKSPACE_FORBIDDEN",
        "INSUFFICIENT_CREDITS",
        "POLICY_VIOLATION",
    }
)


def classify_failure(error_code: str | None) -> FailureKind:
    """Decide whether a failure is worth another attempt (§24)."""
    if error_code and error_code.upper() in RETRYABLE_ERROR_CODES:
        return FailureKind.RETRYABLE
    return FailureKind.PERMANENT


# ---------------------------------------------------------------------------
# PHASE 12-14 — TTS, subtitles, timeline, render, QC (§30-§37)
# ---------------------------------------------------------------------------


class VoiceGender(StrEnum):
    """§30's MVP requirement: Chinese and English, male and female."""

    MALE = "MALE"
    FEMALE = "FEMALE"
    NEUTRAL = "NEUTRAL"


class TrackType(StrEnum):
    """§33's timeline tracks.

    Four, and the separation is what makes the render deterministic: audio
    ducking, subtitle burn-in and clip ordering are each one track's concern,
    and a single flat list of "items" would make their interactions implicit.
    """

    VIDEO = "VIDEO"
    VOICE = "VOICE"
    BGM = "BGM"
    SUBTITLE = "SUBTITLE"


class RenderStatus(StrEnum):
    """Where one render attempt has got to (§34)."""

    PENDING = "PENDING"
    RENDERING = "RENDERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class QCCheckType(StrEnum):
    """§37's two families.

    `TECHNICAL` is decidable — a file either decodes or it does not. `VISUAL`
    is a model's opinion, and §37.2 exists because the technical checks pass
    happily on a video of the wrong product.
    """

    TECHNICAL = "TECHNICAL"
    VISUAL = "VISUAL"


class QCStatus(StrEnum):
    """The outcome of one quality check.

    `WARNING` is distinct from `FAILED` on purpose: a clip two frames short of
    its target is worth showing a person and not worth blocking a delivery on,
    and collapsing the two would force every borderline result to be either
    ignored or fatal.
    """

    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"


class LicenseType(StrEnum):
    """§32's BGM licensing.

    Recorded because §32 forbids defaulting to music of unknown provenance —
    an unlicensed track in a customer's advert is their legal problem and our
    fault, and "we did not ask" is not a defence.
    """

    USER_OWNED = "USER_OWNED"
    ROYALTY_FREE = "ROYALTY_FREE"
    LICENSED = "LICENSED"
    CREATIVE_COMMONS = "CREATIVE_COMMONS"
    UNKNOWN = "UNKNOWN"


#: §34's output baseline, as a single place the render plan and the QC checks
#: both read. Two constants that disagreed would mean rendering one format and
#: validating another.
RENDER_VIDEO_CODEC: str = "libx264"
RENDER_AUDIO_CODEC: str = "aac"
RENDER_PIXEL_FORMAT: str = "yuv420p"
RENDER_CONTAINER: str = "mp4"

#: §36's supported frame shapes, resolved to pixels. The renderer needs exact
#: dimensions, and deriving them from a ratio string at render time is how a
#: 1079-pixel-wide video gets produced and rejected by a platform.
ASPECT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
}

RENDER_FPS: int = 30


class AuditAction(StrEnum):
    """The actions §60 requires a record of.

    A closed set rather than free text, for the reason every audit trail keeps
    one: "generate" and "Generate" and "generation" are three different strings
    and one question nobody can answer six months later. Adding a member is a
    visible edit; typing a new string is not.
    """

    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    PRODUCT_CREATE = "PRODUCT_CREATE"
    PRODUCT_DELETE = "PRODUCT_DELETE"
    FACT_VERIFY = "FACT_VERIFY"
    CLAIM_VERIFY = "CLAIM_VERIFY"
    GENERATE = "GENERATE"
    JOB_CANCEL = "JOB_CANCEL"
    CREDIT_ADJUSTMENT = "CREDIT_ADJUSTMENT"
    RENDER = "RENDER"
    DOWNLOAD = "DOWNLOAD"
    ADMIN_PROVIDER_CHANGE = "ADMIN_PROVIDER_CHANGE"


class ModerationTarget(StrEnum):
    """What was screened (§61, P16-T13).

    The stage matters as much as the verdict: a prompt rejected before
    submission costs nothing, while the same content caught on an uploaded
    asset means it is already in storage and has to be removed.
    """

    UPLOAD = "UPLOAD"
    PROMPT = "PROMPT"
    SCRIPT = "SCRIPT"
    GENERATION_REQUEST = "GENERATION_REQUEST"
    RENDER_OUTPUT = "RENDER_OUTPUT"


class ModerationDecision(StrEnum):
    """The screening outcome.

    `FLAGGED` is deliberately not `BLOCKED`: most policy signals are worth a
    person's attention and not worth refusing work over, and collapsing the two
    forces every borderline case to be either ignored or fatal.
    """

    ALLOWED = "ALLOWED"
    FLAGGED = "FLAGGED"
    BLOCKED = "BLOCKED"
    ERRORED = "ERRORED"


#: Moderation outcomes that must stop the work they screened.
BLOCKING_MODERATION: frozenset[ModerationDecision] = frozenset({ModerationDecision.BLOCKED})


class BrandTone(StrEnum):
    """How a brand speaks (§58).

    §58 is explicit that a Brand Kit is not a logo uploader — it must reach
    creative, script, prompt, subtitles and the ending. Tone is the part that
    reaches the *words*, so it is a field rather than a note, and the generator
    receives it as an instruction rather than as decoration.
    """

    PROFESSIONAL = "PROFESSIONAL"
    FRIENDLY = "FRIENDLY"
    LUXURY = "LUXURY"
    PLAYFUL = "PLAYFUL"
    TECHNICAL = "TECHNICAL"
    WARM = "WARM"
    BOLD = "BOLD"


class TemplateCategory(StrEnum):
    """What a template is for (§57)."""

    ECOMMERCE = "ECOMMERCE"
    SOCIAL = "SOCIAL"
    BRAND = "BRAND"
    LAUNCH = "LAUNCH"
    TUTORIAL = "TUTORIAL"
    SEASONAL = "SEASONAL"


class LogoPosition(StrEnum):
    """Where a logo sits when one is burned in (§58, §34)."""

    TOP_LEFT = "TOP_LEFT"
    TOP_RIGHT = "TOP_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"
    NONE = "NONE"


#: A colour as `#RRGGBB`, validated before it reaches an ffmpeg filter or a CSS
#: rule. Narrow on purpose: §35 forbids user text becoming filter arguments,
#: and a hex triple is the one colour format with no metacharacters in it.
HEX_COLOR_PATTERN: str = r"^#[0-9A-Fa-f]{6}$"


class CreditTransactionType(StrEnum):
    """Why credits moved (§10.29, PHASE 18).

    Reserve, capture and release are three separate rows rather than one
    mutated balance, because §95's acceptance — no double charge, no negative
    balance, no charge on failure — is only checkable if each step left a
    record. A single `balance` column can be wrong; a ledger that disagrees
    with its own sum cannot hide.
    """

    GRANT = "GRANT"
    RESERVE = "RESERVE"
    CAPTURE = "CAPTURE"
    RELEASE = "RELEASE"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"
    EXPIRY = "EXPIRY"


#: Transaction types that move credits *out* of a workspace's balance.
DEBIT_TRANSACTIONS: frozenset[CreditTransactionType] = frozenset(
    {CreditTransactionType.CAPTURE, CreditTransactionType.EXPIRY}
)


class BatchStatus(StrEnum):
    """Where a bulk import has got to (§98, P21-T04).

    `VALIDATING` and `READY` are distinct from `RUNNING` because §98 asks for a
    validation *preview*: the rows are parsed and checked before anybody
    commits to generating dozens of videos, and a batch that went straight from
    upload to running would spend money on rows a person would have rejected.
    """

    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    #: Some items succeeded and some did not. Distinct from `FAILED` because a
    #: batch of fifty with two failures is a success with a follow-up, not a
    #: failed import, and collapsing them would hide forty-eight finished
    #: videos behind a red banner.
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class BatchItemStatus(StrEnum):
    """One row's progress (§98, P21-T06)."""

    PENDING = "PENDING"
    INVALID = "INVALID"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


#: Item states that will not change without someone acting.
TERMINAL_BATCH_ITEM: frozenset[BatchItemStatus] = frozenset(
    {
        BatchItemStatus.COMPLETED,
        BatchItemStatus.FAILED,
        BatchItemStatus.INVALID,
        BatchItemStatus.SKIPPED,
    }
)
