"""Bulk SKU import endpoints (§98, PHASE 21).

Two things about this surface are deliberate.

**Validation is a separate endpoint from import.** `POST /batches/preview`
parses a file and reports every row's verdict without writing anything. §98
asks for a validation preview, and a preview that had already created rows
would not be one — it would be an import with a report attached.

**The file arrives as multipart, not as a JSON string.** A 500-row CSV
base64-encoded into a JSON body is a third larger and unreadable in a request
log; `UploadFile` streams and the size bound is checked against real bytes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import CurrentUser, OriginDep, SessionDep, require_permission
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import BatchItemStatus, BatchStatus, Permission
from backend_core.domain.models import Batch, BatchItem
from backend_core.errors import ValidationError
from backend_core.services.batch import (
    MAX_ROWS,
    MAX_SOURCE_BYTES,
    REQUIRED_COLUMNS,
    BatchService,
    ImportPreview,
    parse_upload,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/batches", tags=["batches"])


class RowPreview(BaseModel):
    row_number: int = Field(description="The line in the source file, header included.")
    values: dict[str, str]
    errors: list[str]
    valid: bool


class PreviewResponse(BaseModel):
    """What an import would do, before it does it (P21-T03)."""

    total_rows: int
    valid_rows: int
    invalid_rows: int
    missing_columns: list[str] = Field(
        description="Required columns absent from the file. Fatal for the whole import."
    )
    unknown_columns: list[str] = Field(
        description=(
            "Columns the importer does not understand. Reported rather than "
            "ignored — a misspelled header is silently dropped data otherwise."
        )
    )
    required_columns: list[str]
    max_rows: int
    rows: list[RowPreview]

    @classmethod
    def of(cls, preview: ImportPreview) -> PreviewResponse:
        return cls(
            total_rows=len(preview.rows),
            valid_rows=len(preview.valid_rows),
            invalid_rows=len(preview.invalid_rows),
            missing_columns=list(preview.missing_columns),
            unknown_columns=list(preview.unknown_columns),
            required_columns=list(REQUIRED_COLUMNS),
            max_rows=MAX_ROWS,
            rows=[
                RowPreview(
                    row_number=row.row_number,
                    values=dict(row.values),
                    errors=list(row.errors),
                    valid=row.valid,
                )
                for row in preview.rows
            ],
        )


class BatchResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: BatchStatus
    template_id: uuid.UUID | None
    brand_kit_id: uuid.UUID | None
    source_filename: str | None
    source_format: str
    total_items: int
    completed_items: int
    failed_items: int
    max_concurrency: int
    error_message: str | None
    created_at: datetime

    @classmethod
    def of(cls, batch: Batch) -> BatchResponse:
        return cls(
            id=batch.id,
            name=batch.name,
            status=batch.status,
            template_id=batch.template_id,
            brand_kit_id=batch.brand_kit_id,
            source_filename=batch.source_filename,
            source_format=batch.source_format,
            total_items=batch.total_items,
            completed_items=batch.completed_items,
            failed_items=batch.failed_items,
            max_concurrency=batch.max_concurrency,
            error_message=batch.error_message,
            created_at=batch.created_at,
        )


class BatchItemResponse(BaseModel):
    id: uuid.UUID
    row_number: int
    status: BatchItemStatus
    source_row: dict[str, Any]
    validation_errors: list[str]
    product_id: uuid.UUID | None
    project_id: uuid.UUID | None
    attempts: int
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, item: BatchItem) -> BatchItemResponse:
        return cls(
            id=item.id,
            row_number=item.row_number,
            status=item.status,
            source_row=dict(item.source_row),
            validation_errors=list(item.validation_errors),
            product_id=item.product_id,
            project_id=item.project_id,
            attempts=item.attempts,
            error_code=item.error_code,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )


class BatchProgressResponse(BaseModel):
    total: int
    pending: int
    running: int
    completed: int
    failed: int
    invalid: int
    finished: bool
    by_status: dict[str, int]


class CreatedBatchResponse(BaseModel):
    batch: BatchResponse
    preview: PreviewResponse


class StartBatchRequest(ApiRequest):
    """Nothing configurable yet; `max_concurrency` is set at import."""

    confirm: bool = True


async def _read_upload(file: UploadFile) -> bytes:
    """Read the whole file, refusing one that is too large.

    Checked against real bytes rather than `Content-Length`, which a client
    controls and can understate.
    """
    data = await file.read()
    if len(data) > MAX_SOURCE_BYTES:
        raise ValidationError(
            "That file is too large to import.",
            details={"bytes": len(data), "limit": MAX_SOURCE_BYTES},
        )
    if not data:
        raise ValidationError("That file is empty.")
    return data


@router.post(
    "/preview",
    response_model=PreviewResponse,
    summary="Validate a spreadsheet without importing it",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def preview_import(
    workspace_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="A .csv or .xlsx file.")],
) -> PreviewResponse:
    """§98's validation preview (P21-T03).

    Writes nothing. Every row's verdict is returned so a user can fix their
    spreadsheet before committing to generating dozens of videos.
    """
    _ = workspace_id
    data = await _read_upload(file)
    return PreviewResponse.of(parse_upload(data, file.filename or "upload.csv"))


@router.post(
    "",
    response_model=CreatedBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a spreadsheet as a batch",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def create_batch(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=1, max_length=200)],
    template_id: Annotated[uuid.UUID | None, Form()] = None,
    brand_kit_id: Annotated[uuid.UUID | None, Form()] = None,
    max_concurrency: Annotated[int, Form(ge=1, le=20)] = 3,
) -> CreatedBatchResponse:
    """Create the batch and its items (P21-T04).

    Invalid rows are stored as `INVALID` items rather than dropped, so the
    count a user sees matches the file they uploaded.
    """
    data = await _read_upload(file)
    batch, preview = await BatchService(session).create_from_upload(
        workspace_id=workspace_id,
        name=name,
        data=data,
        filename=file.filename or "upload.csv",
        template_id=template_id,
        brand_kit_id=brand_kit_id,
        created_by=user.id,
        max_concurrency=max_concurrency,
    )
    return CreatedBatchResponse(batch=BatchResponse.of(batch), preview=PreviewResponse.of(preview))


@router.get(
    "",
    response_model=list[BatchResponse],
    summary="List batches",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_batches(
    workspace_id: uuid.UUID, session: SessionDep, limit: int = 50
) -> list[BatchResponse]:
    batches = await BatchService(session).list_batches(
        workspace_id=workspace_id, limit=min(max(limit, 1), 200)
    )
    return [BatchResponse.of(batch) for batch in batches]


@router.get(
    "/{batch_id}",
    response_model=BatchResponse,
    summary="Get a batch",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def get_batch(
    workspace_id: uuid.UUID, batch_id: uuid.UUID, session: SessionDep
) -> BatchResponse:
    batch = await BatchService(session).get(workspace_id=workspace_id, batch_id=batch_id)
    return BatchResponse.of(batch)


@router.get(
    "/{batch_id}/items",
    response_model=list[BatchItemResponse],
    summary="Per-row status",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_items(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    session: SessionDep,
    item_status: BatchItemStatus | None = None,
) -> list[BatchItemResponse]:
    """§98's per-item status (P21-T06). `row_number` is the source line."""
    items = await BatchService(session).items(
        workspace_id=workspace_id, batch_id=batch_id, status=item_status
    )
    return [BatchItemResponse.of(item) for item in items]


@router.get(
    "/{batch_id}/progress",
    response_model=BatchProgressResponse,
    summary="Batch progress",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def batch_progress(
    workspace_id: uuid.UUID, batch_id: uuid.UUID, session: SessionDep
) -> BatchProgressResponse:
    progress = await BatchService(session).progress(workspace_id=workspace_id, batch_id=batch_id)
    return BatchProgressResponse(
        total=progress.total,
        pending=progress.pending,
        running=progress.running,
        completed=progress.completed,
        failed=progress.failed,
        invalid=progress.invalid,
        finished=progress.finished,
        by_status=dict(progress.by_status),
    )


@router.post(
    "/{batch_id}/start",
    response_model=BatchProgressResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start processing a batch",
    dependencies=[require_permission(Permission.GENERATION_RUN)],
)
async def start_batch(
    workspace_id: uuid.UUID,
    batch_id: uuid.UUID,
    payload: StartBatchRequest,
    session: SessionDep,
    origin: OriginDep,
) -> BatchProgressResponse:
    """Queue the first tranche (P21-T05).

    Only up to `max_concurrency` items are claimed here; the worker claims the
    next as each finishes. Queuing all five hundred at once would fill every
    queue with one tenant's work, which is exactly what P21-T08 forbids.
    """
    _ = payload, origin
    service = BatchService(session)
    await service.claim_next(workspace_id=workspace_id, batch_id=batch_id)
    await session.commit()

    from aipvs_worker.celery_app import dispatch_batch

    dispatch_batch.delay(str(workspace_id), str(batch_id))

    progress = await service.progress(workspace_id=workspace_id, batch_id=batch_id)
    return BatchProgressResponse(
        total=progress.total,
        pending=progress.pending,
        running=progress.running,
        completed=progress.completed,
        failed=progress.failed,
        invalid=progress.invalid,
        finished=progress.finished,
        by_status=dict(progress.by_status),
    )


@router.post(
    "/{batch_id}/retry",
    response_model=BatchProgressResponse,
    summary="Retry failed rows",
    dependencies=[require_permission(Permission.GENERATION_RUN)],
)
async def retry_batch(
    workspace_id: uuid.UUID, batch_id: uuid.UUID, session: SessionDep
) -> BatchProgressResponse:
    """P21-T07. Retries `FAILED` rows only.

    `INVALID` rows are not retried: their row is wrong, and running it again
    produces the same wrong row. Those need a corrected file.
    """
    service = BatchService(session)
    await service.retry_failed(workspace_id=workspace_id, batch_id=batch_id)
    await session.commit()

    from aipvs_worker.celery_app import dispatch_batch

    dispatch_batch.delay(str(workspace_id), str(batch_id))

    progress = await service.progress(workspace_id=workspace_id, batch_id=batch_id)
    return BatchProgressResponse(
        total=progress.total,
        pending=progress.pending,
        running=progress.running,
        completed=progress.completed,
        failed=progress.failed,
        invalid=progress.invalid,
        finished=progress.finished,
        by_status=dict(progress.by_status),
    )


@router.post(
    "/{batch_id}/cancel",
    response_model=BatchResponse,
    summary="Cancel a batch",
    dependencies=[require_permission(Permission.GENERATION_RUN)],
)
async def cancel_batch(
    workspace_id: uuid.UUID, batch_id: uuid.UUID, session: SessionDep
) -> BatchResponse:
    """Stop a batch. Rows already running are left to finish — interrupting a
    generation mid-flight wastes what has already been paid for."""
    batch = await BatchService(session).cancel(workspace_id=workspace_id, batch_id=batch_id)
    return BatchResponse.of(batch)


__all__: list[str] = ["router"]
