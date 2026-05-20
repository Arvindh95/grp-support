"""POST /rfs/analyze — submit an RFS for async analysis."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse

from ..auth import require_api_key
from ..errors import error_envelope
from ..models import (
    ErrorCode,
    JobAccepted,
    JobStatus,
    RFSAnalyzeRequest,
)
from ..deps import get_config
from .. import idempotency, queue
from ..rate_limit import enforce as rate_limit


router = APIRouter()


@router.post("/rfs/analyze", status_code=status.HTTP_202_ACCEPTED)
def submit_rfs(
    payload: RFSAnalyzeRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _principal: dict = Depends(require_api_key),
    _rl: None = Depends(rate_limit),
):
    cfg = get_config()

    # Body size guard (FastAPI doesn't enforce a body cap by default).
    cl = request.headers.get("content-length")
    if cl and int(cl) > cfg.max_body_bytes:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content=error_envelope(
                ErrorCode.payload_too_large,
                f"Body exceeds {cfg.max_body_bytes} bytes",
            ),
        )

    body_dict = payload.model_dump(mode="json")

    # Idempotency
    if idempotency_key:
        existing, conflict = idempotency.check(idempotency_key, body_dict)
        if conflict:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=error_envelope(
                    ErrorCode.idempotency_conflict,
                    "Idempotency-Key reused with different payload",
                    original_job_id=existing.job_id,
                ),
            )
        if existing:
            response.headers["Location"] = f"/jobs/{existing.job_id}"
            accepted = JobAccepted(
                job_id=existing.job_id,
                status=JobStatus.queued,
                poll_url=f"/jobs/{existing.job_id}",
            )
            return accepted.model_dump(mode="json")

    job = queue.create_job(
        rfs_lodge_id=payload.rfs.lodge_id,
        priority=payload.priority,
        client_metadata=payload.client_metadata,
    )

    if idempotency_key:
        idempotency.remember(idempotency_key, body_dict, str(job.job_id))

    # Persist the RFS + callback config in a private sibling key. None of this
    # leaks via GET /jobs/{id}; only the worker reads it.
    from .._submit_meta import save_submit_meta
    save_submit_meta(
        str(job.job_id),
        callback_url=str(payload.callback_url) if payload.callback_url else None,
        callback_secret_hint=payload.callback_secret_hint,
        rfs=body_dict["rfs"],
    )

    accepted = JobAccepted(
        job_id=job.job_id,
        status=JobStatus.queued,
        poll_url=f"/jobs/{job.job_id}",
        estimated_seconds=queue.estimate_seconds(payload.priority),
    )
    response.headers["Location"] = f"/jobs/{job.job_id}"
    return accepted.model_dump(mode="json")
