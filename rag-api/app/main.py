"""FastAPI entrypoint for the RAG-API service."""
from __future__ import annotations

import logging
import os
import pathlib
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .errors import error_envelope
from .models import ErrorCode
from .routes import jobs as jobs_routes
from .routes import meta as meta_routes
from .routes import rfs as rfs_routes


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format='{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":%(message)s}',
    )


_setup_logging()

app = FastAPI(
    title="GRP RAG-API",
    version="0.1.0",
    docs_url=None,        # replaced by a self-hosted Swagger UI below
    redoc_url=None,
    openapi_url="/openapi.json",
)

# Self-hosted Swagger UI assets. The site CSP is `script-src 'self'`, so the
# default CDN-loaded Swagger page renders blank — serve the bundle locally.
_STATIC_DIR = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/docs", include_in_schema=False)
async def swagger_ui(request: Request):
    root = request.scope.get("root_path", "").rstrip("/")
    return get_swagger_ui_html(
        openapi_url=f"{root}{app.openapi_url}",
        title="GRP RAG-API — API reference",
        swagger_js_url=f"{root}/static/swagger-ui-bundle.js",
        swagger_css_url=f"{root}/static/swagger-ui.css",
    )


# ── Request ID ─────────────────────────────────────────────────────────────────

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


# ── Error handlers — always return the ErrorEnvelope shape ─────────────────────

_HTTP_CODE_TO_API_CODE = {
    400: ErrorCode.bad_request,
    401: ErrorCode.unauthorized,
    403: ErrorCode.forbidden,
    404: ErrorCode.not_found,
    409: ErrorCode.conflict,
    413: ErrorCode.payload_too_large,
    429: ErrorCode.rate_limited,
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code = _HTTP_CODE_TO_API_CODE.get(exc.status_code, ErrorCode.internal)
    message = exc.detail if isinstance(exc.detail, str) else (
        exc.detail.get("message") if isinstance(exc.detail, dict) else "Error"
    )
    details = {}
    if isinstance(exc.detail, dict) and exc.detail.get("details"):
        details = {"details": exc.detail["details"]}
    body = error_envelope(code, message or "Error", **details)
    body["error"]["request_id"] = getattr(request.state, "request_id", None)
    return JSONResponse(status_code=exc.status_code, content=body,
                        headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = error_envelope(ErrorCode.bad_request, "Validation failed",
                          errors=exc.errors())
    body["error"]["request_id"] = getattr(request.state, "request_id", None)
    return JSONResponse(status_code=400, content=body)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.getLogger("rag-api").exception('"unhandled exception"')
    body = error_envelope(ErrorCode.internal, "Internal server error")
    body["error"]["request_id"] = getattr(request.state, "request_id", None)
    return JSONResponse(status_code=500, content=body)


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(rfs_routes.router, tags=["rfs"])
app.include_router(jobs_routes.router, tags=["jobs"])
app.include_router(meta_routes.router, tags=["meta"])


# ── Worker launch (single-process v1) ──────────────────────────────────────────

@app.on_event("startup")
async def _maybe_start_worker():
    """If `RAG_RUN_WORKER` is set, start the in-process worker pool.

    Tests and the (future) dedicated worker process set this differently:
      RAG_RUN_WORKER=1      — start worker tasks in this uvicorn process
      RAG_RUN_WORKER=0      — API-only (tests use this so a fakeredis blpop
                              doesn't drain test jobs before assertions).
    """
    if os.environ.get("RAG_RUN_WORKER", "1") != "1":
        return
    from .worker import start_worker_pool
    await start_worker_pool()
