"""POST /rfs/analyze — submit an RFS for async analysis.

Two intake formats on the same path:
  - application/json     — RFSAnalyzeRequest body, attachments inline as base64.
  - multipart/form-data  — a text field `payload` with the RFS JSON plus native
                           file parts (`files`). Lets a browser / Postman attach
                           files directly; each part is folded into rfs.attachments.
Everything downstream (idempotency, queue, pipeline) is identical.
"""
from __future__ import annotations

import base64
import json
import uuid

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

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


class _BadRequest(Exception):
    """Carries an error envelope for a malformed submission."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _validate_request(data: object) -> RFSAnalyzeRequest:
    try:
        return RFSAnalyzeRequest.model_validate(data)
    except ValidationError as e:
        raise _BadRequest(f"invalid request: {e.errors(include_url=False)[:3]}")


async def _from_multipart(request: Request) -> RFSAnalyzeRequest:
    """Build the request from multipart/form-data: a `payload` text field
    (the RFS JSON) plus zero or more `files` parts that become attachments."""
    form = await request.form()
    raw = form.get("payload")
    if raw is None or not isinstance(raw, str):
        raise _BadRequest(
            "multipart request needs a text field 'payload' holding the RFS JSON")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise _BadRequest("the 'payload' field is not valid JSON")
    if not isinstance(data, dict):
        raise _BadRequest("the 'payload' field must be a JSON object")

    uploads = [f for f in form.getlist("files") if hasattr(f, "read")]
    if uploads:
        atts = []
        for f in uploads:
            content = await f.read()
            atts.append({
                "filename": getattr(f, "filename", None) or "file",
                "content_type": getattr(f, "content_type", None)
                or "application/octet-stream",
                "content_b64": base64.b64encode(content).decode("ascii"),
            })
        rfs = data.get("rfs")
        if not isinstance(rfs, dict):
            raise _BadRequest("the 'payload' JSON must contain an 'rfs' object")
        rfs["attachments"] = list(rfs.get("attachments") or []) + atts
    return _validate_request(data)


async def _build_request(request: Request) -> RFSAnalyzeRequest:
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype == "multipart/form-data":
        return await _from_multipart(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _BadRequest("request body is not valid JSON")
    return _validate_request(body)


@router.post("/rfs/analyze", status_code=status.HTTP_202_ACCEPTED)
async def submit_rfs(
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: dict = Depends(require_api_key),
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

    try:
        payload = await _build_request(request)
    except _BadRequest as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=error_envelope(ErrorCode.bad_request, e.message),
        )

    # Validate attachments up front — the caller gets a fast, explicit 400
    # (e.g. "convert Word to PDF") instead of a deep worker failure.
    if payload.rfs.attachments:
        from ..attachments import AttachmentError, decode_and_validate
        try:
            decode_and_validate(payload.rfs.attachments,
                                max_total_bytes=cfg.max_attachment_bytes)
        except AttachmentError as e:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=error_envelope(ErrorCode.bad_request, str(e)),
            )

    # Reject SSRF-prone webhook targets before the job is ever queued.
    if payload.callback_url:
        from ..webhook import CallbackUrlError, validate_callback_url
        try:
            validate_callback_url(str(payload.callback_url))
        except CallbackUrlError as e:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=error_envelope(ErrorCode.bad_request,
                                       f"callback_url rejected: {e}"),
            )

    body_dict = payload.model_dump(mode="json")

    # Pre-generate the job id so the idempotency claim is atomic: the id is
    # fixed in the claim record before the job exists, so two concurrent
    # requests with the same key can never both enqueue a job.
    new_job_id = uuid.uuid4()

    if idempotency_key:
        decision = idempotency.begin(idempotency_key, body_dict, str(new_job_id))
        if decision.action == "conflict":
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=error_envelope(
                    ErrorCode.idempotency_conflict,
                    "Idempotency-Key reused with different payload",
                    original_job_id=decision.job_id,
                ),
            )
        if decision.action == "replay":
            response.headers["Location"] = f"/jobs/{decision.job_id}"
            return JobAccepted(
                job_id=decision.job_id,
                status=JobStatus.queued,
                poll_url=f"/jobs/{decision.job_id}",
            ).model_dump(mode="json")
        # action == "proceed" — this request owns the claim.

    # Persist the RFS + callback config in a private sibling key. None of this
    # leaks via GET /jobs/{id}; only the worker reads it.
    from .._submit_meta import save_submit_meta
    try:
        job = queue.create_job(
            job_id=new_job_id,
            rfs_lodge_id=payload.rfs.lodge_id,
            priority=payload.priority,
            client_metadata=payload.client_metadata,
        )
        save_submit_meta(
            str(job.job_id),
            callback_url=str(payload.callback_url) if payload.callback_url else None,
            callback_secret_hint=payload.callback_secret_hint,
            owner_key_id=principal.get("key_id"),
            rfs=body_dict["rfs"],
        )
    except Exception:
        # Creation failed after we claimed the key — release it so a retry
        # with the same Idempotency-Key is not permanently wedged.
        if idempotency_key:
            idempotency.release(idempotency_key)
        raise

    accepted = JobAccepted(
        job_id=job.job_id,
        status=JobStatus.queued,
        poll_url=f"/jobs/{job.job_id}",
        estimated_seconds=queue.estimate_seconds(payload.priority),
    )
    response.headers["Location"] = f"/jobs/{job.job_id}"
    return accepted.model_dump(mode="json")
