"""Project lifecycle (§10.9, §105, P7-T01/T02).

The state machine is the substance here. §105 forbids writing a status by
assignment, so every change goes through :meth:`ProjectService.transition`,
which consults the derived transition table and refuses anything not on it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import (
    AspectRatio,
    ProductStatus,
    ProjectPurpose,
    ProjectStatus,
    QualityMode,
    TargetPlatform,
    VideoStyle,
    allowed_project_transitions,
    can_transition_project,
)
from backend_core.domain.models import Project, User
from backend_core.errors import AppError, ErrorCode, NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.repositories.projects import ProjectRepository
from backend_core.services.products import ProductService

logger = get_logger(__name__)


class InvalidProjectTransitionError(AppError):
    """Refused because §105's state machine does not allow it."""

    code = ErrorCode.PROJECT_INVALID_STATE
    http_status = 409
    default_message = "That project status change is not allowed."


class ProjectService:
    """Creates projects and moves them along the pipeline."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ProjectRepository(session)
        self._products = ProductService(session)

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        user: User,
        product_id: uuid.UUID,
        name: str,
        purpose: ProjectPurpose,
        target_platform: TargetPlatform,
        aspect_ratio: AspectRatio,
        duration_seconds: int,
        style: VideoStyle,
        quality_mode: QualityMode = QualityMode.STANDARD,
        target_audience: str | None = None,
        language: str = "zh-CN",
    ) -> Project:
        """Start a project against a product.

        The product must exist in this workspace and must not be archived. An
        archived product is one somebody decided to stop selling, and building
        a new advert for it is almost certainly a mistake rather than an
        intention.

        Note what is *not* required: the product does not have to be `READY`.
        A team can draft the brief while the facts are still being verified —
        §16's inputs are only needed when plans are generated, and that is
        where the check belongs.
        """
        # Authorisation is the route's job, via `require_permission` — the
        # pattern PHASE 3 established and PHASE 5 followed. Duplicating it here
        # would mean two places to keep in step with the permission matrix.
        product = await self._products.get(workspace_id=workspace_id, product_id=product_id)
        if product.status is ProductStatus.ARCHIVED:
            raise ValidationError(
                "This product is archived. Restore it before starting a new project.",
                details={"product_id": str(product_id)},
            )

        project = Project(
            workspace_id=workspace_id,
            product_id=product_id,
            name=name.strip(),
            purpose=purpose,
            target_platform=target_platform,
            target_audience=(target_audience or "").strip() or None,
            language=language,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            style=style,
            quality_mode=quality_mode,
            status=ProjectStatus.DRAFT,
            created_by=user.id,
        )
        self._session.add(project)
        await self._session.flush()

        logger.info(
            "project_created",
            extra={
                "project_id": str(project.id),
                "product_id": str(product_id),
                "duration_seconds": duration_seconds,
                "target_platform": target_platform.value,
            },
        )
        return project

    async def get(self, *, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = await self._repo.get(workspace_id, project_id)
        if project is None:
            # 404, not 403: §60 says a cross-tenant read must not confirm that
            # the id exists somewhere else.
            raise NotFoundError("Project not found.", details={"project_id": str(project_id)})
        return project

    async def list_projects(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID | None = None,
        status: ProjectStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        return await self._repo.list_for_workspace(
            workspace_id, product_id=product_id, status=status, limit=limit, offset=offset
        )

    async def update(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str | None = None,
        target_audience: str | None = None,
        duration_seconds: int | None = None,
        aspect_ratio: AspectRatio | None = None,
        style: VideoStyle | None = None,
        quality_mode: QualityMode | None = None,
    ) -> Project:
        """Edit the brief.

        Only while the project is still ahead of generation. Changing the
        duration after shots have been produced would silently invalidate work
        that has already been paid for, so the refusal is deliberate — the user
        can go back a stage first, which §105 allows.
        """
        project = await self.get(workspace_id=workspace_id, project_id=project_id)

        if project.status not in _BRIEF_EDITABLE:
            raise InvalidProjectTransitionError(
                "The brief cannot be changed once generation has started. "
                "Move the project back a stage first.",
                details={"status": project.status.value},
            )

        if name is not None:
            project.name = name.strip()
        if target_audience is not None:
            project.target_audience = target_audience.strip() or None
        if duration_seconds is not None:
            project.duration_seconds = duration_seconds
        if aspect_ratio is not None:
            project.aspect_ratio = aspect_ratio
        if style is not None:
            project.style = style
        if quality_mode is not None:
            project.quality_mode = quality_mode

        await self._session.flush()
        return project

    async def transition(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        target: ProjectStatus,
        failure_reason: str | None = None,
    ) -> Project:
        """Move a project along §105's machine, or refuse.

        `failure_reason` is required for `FAILED` and cleared on the way out.
        §103 rule 3 says a failure must explain itself, and a CHECK constraint
        backs this up in case anything ever writes the column directly.
        """
        project = await self.get(workspace_id=workspace_id, project_id=project_id)

        if not can_transition_project(project.status, target):
            raise InvalidProjectTransitionError(
                f"A project cannot go from {project.status.value} to {target.value}.",
                details={
                    "current": project.status.value,
                    "requested": target.value,
                    "allowed": sorted(
                        status.value for status in allowed_project_transitions(project.status)
                    ),
                },
            )

        if target is ProjectStatus.FAILED:
            if not failure_reason:
                raise ValidationError("A failed project must record why it failed.")
            project.failure_reason = failure_reason[:2000]
        else:
            project.failure_reason = None

        previous = project.status
        project.status = target
        await self._session.flush()

        logger.info(
            "project_transitioned",
            extra={
                "project_id": str(project_id),
                "from": previous.value,
                "to": target.value,
            },
        )
        return project

    async def archive(self, *, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        return await self.transition(
            workspace_id=workspace_id, project_id=project_id, target=ProjectStatus.ARCHIVED
        )


#: Stages where the brief is still a description of work not yet done. Beyond
#: `STORYBOARDING`, shots exist and changing the duration would orphan them.
_BRIEF_EDITABLE: frozenset[ProjectStatus] = frozenset(
    {
        ProjectStatus.DRAFT,
        ProjectStatus.ANALYZING,
        ProjectStatus.CREATIVE_PLANNING,
        ProjectStatus.SCRIPTING,
    }
)


__all__ = ["InvalidProjectTransitionError", "ProjectService"]
