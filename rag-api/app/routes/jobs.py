"""GET /jobs/{id}, POST /jobs/{id}/cancel."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from ..auth import require_api_key
from ..errors import error_envelope
from ..models import ErrorCode, JobStatus
from .. import queue


router = APIRouter()


@router.get("/jobs/{job_id}")
def get_job_route(job_id: str, _principal: dict = Depends(require_api_key)):
    job = queue.get_job(job_id)
    if job is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=error_envelope(ErrorCode.not_found, f"Job {job_id} not found"),
        )
    return job.model_dump(mode="json", exclude_none=True)


@router.post("/jobs/{job_id}/cancel")
def cancel_job_route(job_id: str, principal: dict = Depends(require_api_key)):
    # Admin-only — matches the openapi contract.
    if principal.get("role") != "admin":
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=error_envelope(ErrorCode.forbidden, "Admin only"),
        )

    job = queue.get_job(job_id)
    if job is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=error_envelope(ErrorCode.not_found, f"Job {job_id} not found"),
        )

    terminal = {JobStatus.succeeded, JobStatus.failed,
                JobStatus.cancelled, JobStatus.expired}
    if job.status in terminal:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=error_envelope(ErrorCode.conflict,
                                   f"Job already {job.status.value}"),
        )

    queue.mark_cancel(job_id)
    job = queue.update_job(job_id, status=JobStatus.cancelled.value)
    return job.model_dump(mode="json", exclude_none=True)
