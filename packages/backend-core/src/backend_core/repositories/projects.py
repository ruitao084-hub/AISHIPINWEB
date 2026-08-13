"""Data access for projects, creative plans and scripts (§10.9-§10.11).

Every query is workspace-scoped in its `WHERE` clause. §60 makes tenancy a
query-level concern rather than a check the caller is trusted to have done —
a repository method that took only an id would be one forgotten guard away
from serving another tenant's project.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import ProjectStatus, ScriptStatus
from backend_core.domain.models import CreativePlan, Project, Script


class ProjectRepository:
    """Reads and writes for the project aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- projects -----------------------------------------------------------

    async def get(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project | None:
        result = await self._session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
                Project.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        product_id: uuid.UUID | None = None,
        status: ProjectStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        query = select(Project).where(
            Project.workspace_id == workspace_id, Project.deleted_at.is_(None)
        )
        if product_id is not None:
            query = query.where(Project.product_id == product_id)
        if status is not None:
            query = query.where(Project.status == status)
        result = await self._session.execute(
            query.order_by(Project.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def count_for_product(self, workspace_id: uuid.UUID, product_id: uuid.UUID) -> int:
        """How many live projects use this product.

        Used before archiving a product: the FK is `RESTRICT`, so this answers
        "why can't I archive it" with a number instead of a constraint error.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Project)
            .where(
                Project.workspace_id == workspace_id,
                Project.product_id == product_id,
                Project.deleted_at.is_(None),
                Project.status != ProjectStatus.ARCHIVED,
            )
        )
        return int(result.scalar_one())

    # -- creative plans -----------------------------------------------------

    async def list_plans(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, *, version: int | None = None
    ) -> list[CreativePlan]:
        query = select(CreativePlan).where(
            CreativePlan.workspace_id == workspace_id,
            CreativePlan.project_id == project_id,
        )
        if version is not None:
            query = query.where(CreativePlan.version == version)
        result = await self._session.execute(
            query.order_by(CreativePlan.version.desc(), CreativePlan.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_plan(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, plan_id: uuid.UUID
    ) -> CreativePlan | None:
        result = await self._session.execute(
            select(CreativePlan).where(
                CreativePlan.id == plan_id,
                CreativePlan.project_id == project_id,
                CreativePlan.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def selected_plan(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> CreativePlan | None:
        result = await self._session.execute(
            select(CreativePlan).where(
                CreativePlan.workspace_id == workspace_id,
                CreativePlan.project_id == project_id,
                CreativePlan.selected.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def clear_plan_selection(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """Deselect whatever was selected.

        Run before selecting a new plan, because a partial unique index allows
        exactly one — so this is not tidiness, it is what makes the next insert
        legal.
        """
        await self._session.execute(
            update(CreativePlan)
            .where(
                CreativePlan.workspace_id == workspace_id,
                CreativePlan.project_id == project_id,
                CreativePlan.selected.is_(True),
            )
            .values(selected=False)
        )

    async def next_plan_version(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(CreativePlan.version), 0)).where(
                CreativePlan.workspace_id == workspace_id,
                CreativePlan.project_id == project_id,
            )
        )
        return int(result.scalar_one()) + 1

    # -- scripts ------------------------------------------------------------

    async def list_scripts(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> list[Script]:
        result = await self._session.execute(
            select(Script)
            .where(
                Script.workspace_id == workspace_id,
                Script.project_id == project_id,
            )
            .order_by(Script.version.desc())
        )
        return list(result.scalars().all())

    async def get_script(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, script_id: uuid.UUID
    ) -> Script | None:
        result = await self._session.execute(
            select(Script).where(
                Script.id == script_id,
                Script.project_id == project_id,
                Script.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def latest_script(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Script | None:
        result = await self._session.execute(
            select(Script)
            .where(
                Script.workspace_id == workspace_id,
                Script.project_id == project_id,
            )
            .order_by(Script.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def approved_script(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> Script | None:
        """The one script PHASE 8 will turn into a storyboard."""
        result = await self._session.execute(
            select(Script).where(
                Script.workspace_id == workspace_id,
                Script.project_id == project_id,
                Script.status == ScriptStatus.APPROVED,
            )
        )
        return result.scalar_one_or_none()

    async def next_script_version(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(Script.version), 0)).where(
                Script.workspace_id == workspace_id,
                Script.project_id == project_id,
            )
        )
        return int(result.scalar_one()) + 1

    async def supersede_scripts(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, *, keep: uuid.UUID
    ) -> None:
        """Mark every other script superseded (§17).

        Superseded, never deleted: §17 requires history to survive an edit, and
        "what did we approve last week" has to keep an answer.
        """
        await self._session.execute(
            update(Script)
            .where(
                Script.workspace_id == workspace_id,
                Script.project_id == project_id,
                Script.id != keep,
                Script.status != ScriptStatus.SUPERSEDED,
            )
            .values(status=ScriptStatus.SUPERSEDED)
        )
