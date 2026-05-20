"""HTTP error helpers — every error response is an ErrorEnvelope."""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .models import ErrorBody, ErrorCode, ErrorEnvelope


def error_envelope(code: ErrorCode, message: str, **details) -> dict:
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message,
                                         details=details or None))
    return body.model_dump(mode="json", exclude_none=True)


def error_response(status_code: int, code: ErrorCode, message: str,
                   headers: dict | None = None, **details) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_envelope(code, message, **details),
        headers=headers,
    )


def raise_error(status_code: int, code: ErrorCode, message: str, **details):
    raise HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": message, "details": details or None},
    )
