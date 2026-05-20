"""GET /jobs/{id}, POST /jobs/{id}/cancel."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from ..auth import require_api_key
from ..errors import error_envelope
from ..models import ErrorCode, JobStatus
from .. import _submit_meta, queue


router = APIRouter()


def _job_not_found(job_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=error_envelope(ErrorCode.not_found, f"Job {job_id} not found"),
    )


def _caller_may_access(job_id: str, principal: dict) -> bool:
    """A job is visible to the API key that created it, or to any admin key.
    Jobs created before ownership tracking (no owner_key_id) stay readable."""
    if principal.get("role") == "admin":
        return True
    owner = _submit_meta.load_submit_meta(job_id).get("owner_key_id")
    if not owner:
        return True
    return owner == principal.get("key_id")


@router.get("/jobs/{job_id}")
def get_job_route(job_id: str, principal: dict = Depends(require_api_key)):
    job = queue.get_job(job_id)
    if job is None:
        return _job_not_found(job_id)
    # Cross-client isolation: a non-owner is told the job does not exist
    # rather than 403 — a leaked UUID must not even confirm a job's existence.
    if not _caller_may_access(job_id, principal):
        return _job_not_found(job_id)
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
