"""Running an imported batch (§98, PHASE 21).

The dispatcher and the per-item worker, split because they fail differently.

**The dispatcher** claims work up to the batch's concurrency bound and returns.
It does not loop: a task that held a worker slot for a five-hundred-row batch
would block the default queue for an hour, and a crash mid-loop would lose the
run. It re-enqueues itself instead, which makes the batch's progress a sequence
of short, restartable steps.

**The item worker** turns one row into a product and a project. Only those two:
the storyboard, generation and render are the ordinary pipeline's, and
duplicating them here would give batch imports a second implementation of rules
that could drift from the first. A batch's job is to get fifty products to the
starting line, not to reimplement the race.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.db import get_async_sessionmaker
from backend_core.domain.enums import (
    AspectRatio,
    BatchItemStatus,
    ProjectPurpose,
    TargetPlatform,
    VideoStyle,
)
from backend_core.domain.models import Batch, BatchItem, Template, User
from backend_core.errors import AppError, ValidationError
from backend_core.observability import get_logger
from backend_core.services.batch import BatchService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """What one dispatch pass did."""

    batch_id: uuid.UUID
    started: tuple[uuid.UUID, ...] = ()
    #: Rows still waiting. The dispatcher re-enqueues itself while this is
    #: non-zero, which is what turns a long batch into short steps.
    remaining: int = 0


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    item_id: uuid.UUID
    status: BatchItemStatus
    product_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    error: str = ""


@dataclass(slots=True)
class _Brief:
    """The project brief a row produces, after defaults are applied."""

    duration_seconds: int = 15
    aspect_ratio: AspectRatio = AspectRatio.PORTRAIT_9_16
    style: VideoStyle = VideoStyle.CLEAN_MINIMAL
    purpose: ProjectPurpose = ProjectPurpose.SOCIAL_AD
    platform: TargetPlatform = TargetPlatform.DOUYIN
    language: str = "zh-CN"
    audience: str = ""
    extras: dict[str, str] = field(default_factory=dict)


async def dispatch(
    workspace_id: uuid.UUID, batch_id: uuid.UUID, *, settings: Settings | None = None
) -> DispatchOutcome:
    """Claim the next tranche of a batch (§98, P21-T05, P21-T08)."""
    resolved = settings or get_settings()

    async with get_async_sessionmaker()() as session:
        service = BatchService(session, settings=resolved)
        claimed = await service.claim_next(workspace_id=workspace_id, batch_id=batch_id)
        progress = await service.progress(workspace_id=workspace_id, batch_id=batch_id)
        await session.commit()

    return DispatchOutcome(
        batch_id=batch_id,
        started=tuple(item.id for item in claimed),
        # Only what is genuinely still waiting. Counting running items here
        # would keep the dispatcher re-enqueueing forever on the last row.
        remaining=max(0, progress.pending - len(claimed)),
    )


async def run_item(
    workspace_id: uuid.UUID, item_id: uuid.UUID, *, settings: Settings | None = None
) -> ItemOutcome:
    """Turn one row into a product and a project (§98, P21-T05).

    Every failure is recorded on the item rather than raised out of the task.
    A batch of fifty must not stop at row 34: the row is marked `FAILED`, the
    dispatcher carries on, and P21-T07's retry picks it up later.
    """
    resolved = settings or get_settings()

    async with get_async_sessionmaker()() as session:
        item = await session.get(BatchItem, item_id)
        if item is None or item.workspace_id != workspace_id:
            logger.warning("batch_item_missing", extra={"item_id": str(item_id)})
            return ItemOutcome(item_id=item_id, status=BatchItemStatus.FAILED)

        service = BatchService(session, settings=resolved)
        await service.mark_running(item)
        await session.commit()

    async with get_async_sessionmaker()() as session:
        item = await session.get(BatchItem, item_id)
        if item is None:  # pragma: no cover — just loaded above
            return ItemOutcome(item_id=item_id, status=BatchItemStatus.FAILED)

        service = BatchService(session, settings=resolved)
        try:
            product = await service.materialise(item)
            project = await _create_project(session, item, product.id)
            await service.mark_completed(item, product_id=product.id, project_id=project)
            await session.commit()
        except AppError as exc:
            await session.rollback()
            return await _fail(workspace_id, item_id, exc.code.value, str(exc), resolved)
        except Exception as exc:
            await session.rollback()
            logger.exception("batch_item_failed", extra={"item_id": str(item_id)})
            return await _fail(workspace_id, item_id, "INTERNAL_ERROR", str(exc), resolved)

        logger.info(
            "batch_item_completed",
            extra={
                "item_id": str(item_id),
                "product_id": str(product.id),
                "project_id": str(project),
            },
        )
        return ItemOutcome(
            item_id=item_id,
            status=BatchItemStatus.COMPLETED,
            product_id=product.id,
            project_id=project,
        )


async def _fail(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    code: str,
    message: str,
    settings: Settings,
) -> ItemOutcome:
    """Record a failure in its own transaction.

    Its own, because the one that failed has been rolled back — writing the
    failure into it would be rolled back too, leaving the item stuck in
    `RUNNING` for the reaper to find.
    """
    async with get_async_sessionmaker()() as session:
        item = await session.get(BatchItem, item_id)
        if item is not None and item.workspace_id == workspace_id:
            await BatchService(session, settings=settings).mark_failed(
                item, error_code=code, error_message=message
            )
            await session.commit()
    return ItemOutcome(item_id=item_id, status=BatchItemStatus.FAILED, error=message)


async def _create_project(
    session: AsyncSession, item: BatchItem, product_id: uuid.UUID
) -> uuid.UUID:
    """Create the project for a row, inheriting the batch's template.

    The template supplies the brief — duration, ratio, style, purpose — and the
    row overrides individual fields where it names them. That order matters: a
    batch exists so fifty SKUs share one creative direction, and a row that
    silently ignored the template would defeat the point.
    """
    from backend_core.services.projects import ProjectService

    batch = await session.get(Batch, item.batch_id)
    if batch is None:  # pragma: no cover — the item's FK guarantees one
        raise ValidationError("That batch no longer exists.")

    creator = await session.get(User, batch.created_by) if batch.created_by else None
    if creator is None:
        # `ProjectService.create` records who started a project, and a batch
        # whose creator was deleted cannot answer that. Failing the row is
        # better than attributing fifty projects to nobody.
        raise ValidationError(
            "The user who created this batch no longer exists.",
            details={"batch_id": str(batch.id)},
        )

    template = (
        await session.get(Template, batch.template_id) if batch.template_id is not None else None
    )
    brief = _brief_from(item.source_row, template)

    project = await ProjectService(session).create(
        workspace_id=item.workspace_id,
        user=creator,
        product_id=product_id,
        name=str(item.source_row.get("name", "Untitled"))[:200],
        purpose=brief.purpose,
        target_platform=brief.platform,
        aspect_ratio=brief.aspect_ratio,
        duration_seconds=brief.duration_seconds,
        style=brief.style,
        language=brief.language,
        target_audience=brief.audience or None,
    )

    # Set after creation rather than passed in: §58's brand kit is a property
    # of the project, but `create` predates PHASE 17 and widening its signature
    # for one caller would push the batch's concern into every other one.
    project.brand_kit_id = batch.brand_kit_id
    await session.flush()
    return project.id


def _brief_from(row: dict[str, object], template: Template | None) -> _Brief:
    """Template defaults, overridden by whatever the row names."""
    brief = _Brief()

    if template is not None:
        brief.duration_seconds = template.duration_seconds
        brief.aspect_ratio = template.aspect_ratio
        brief.style = template.style
        brief.purpose = template.purpose
        brief.platform = template.target_platform

    # Validated at import (P21-T03), so a row reaching here should parse. The
    # fallbacks are belt and braces: a retry can replay a row imported under an
    # older rule set, and a template default is a better answer than a crash.
    duration = _parse_int(str(row.get("duration_seconds", "")).strip())
    if duration is not None:
        brief.duration_seconds = duration

    ratio = _parse_ratio(str(row.get("aspect_ratio", "")).strip())
    if ratio is not None:
        brief.aspect_ratio = ratio

    language = str(row.get("language", "")).strip()
    if language:
        brief.language = language

    audience = str(row.get("target_audience", "")).strip()
    if audience:
        brief.audience = audience

    return brief


def _parse_int(text: str) -> int | None:
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        logger.info("batch_row_bad_duration", extra={"value": text[:32]})
        return None


def _parse_ratio(text: str) -> AspectRatio | None:
    if not text:
        return None
    try:
        return AspectRatio(text)
    except ValueError:
        logger.info("batch_row_bad_ratio", extra={"value": text[:32]})
        return None


__all__ = ["DispatchOutcome", "ItemOutcome", "dispatch", "run_item"]
