"""Domain layer — entities, enums and state machines.

The rules that stay true regardless of how data is stored or exposed: the
Product (§104), Project (§105) and Job (§106) state machines, the role model
(§40), and the verification states that make the Truth Layer work (§13).

State transitions are validated here, never by assigning a string to a status
column — §105 is explicit that arbitrary status writes are forbidden.

Importing this package registers every model with the shared metadata, which
is what lets Alembic autogeneration see the full schema.
"""

from backend_core.domain.enums import (
    FACT_BACKED_CLAIM_TYPES,
    ROLE_PERMISSIONS,
    AnalysisStatus,
    AssetSourceType,
    AssetType,
    ClaimRiskLevel,
    ClaimStatus,
    ClaimType,
    FactSourceType,
    FactType,
    Permission,
    PlanCode,
    ProductAssetRole,
    ProductStatus,
    UploadStatus,
    UserStatus,
    VerificationStatus,
    WorkspaceRole,
    WorkspaceStatus,
    allowed_product_transitions,
    can_transition_product,
    default_risk_level,
    permissions_for,
    requires_fact_backing,
    role_has_permission,
    role_rank,
)
from backend_core.domain.models import (
    MediaAsset,
    Product,
    ProductAnalysis,
    ProductAsset,
    ProductClaim,
    ProductFact,
    User,
    Workspace,
    WorkspaceMember,
)

__all__ = [
    "FACT_BACKED_CLAIM_TYPES",
    "ROLE_PERMISSIONS",
    "AnalysisStatus",
    "AssetSourceType",
    "AssetType",
    "ClaimRiskLevel",
    "ClaimStatus",
    "ClaimType",
    "FactSourceType",
    "FactType",
    "MediaAsset",
    "Permission",
    "PlanCode",
    "Product",
    "ProductAnalysis",
    "ProductAsset",
    "ProductAssetRole",
    "ProductClaim",
    "ProductFact",
    "ProductStatus",
    "UploadStatus",
    "User",
    "UserStatus",
    "VerificationStatus",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
    "WorkspaceStatus",
    "allowed_product_transitions",
    "can_transition_product",
    "default_risk_level",
    "permissions_for",
    "requires_fact_backing",
    "role_has_permission",
    "role_rank",
]
