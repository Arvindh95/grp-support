"""Pydantic models. Mirror openapi.yaml — that file is the source of truth.

Keep this thin: validation only, no business logic.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl


# ── Enums ──────────────────────────────────────────────────────────────────────

class Priority(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"


class ActionType(str, Enum):
    Lodge = "Lodge"
    Assign = "Assign"
    Note = "Note"
    Close = "Close"
    Attach = "Attach"
    Escalate = "Escalate"
    Edit = "Edit"


class CitationSource(str, Enum):
    manual = "manual"
    rfs_ticket = "rfs_ticket"
    code_script = "code_script"
    attachment = "attachment"


class AgentName(str, Enum):
    classifier = "classifier"
    retrieval_planner = "retrieval_planner"
    analyst = "analyst"
    verifier = "verifier"
    formatter = "formatter"


class AgentStepStatus(str, Enum):
    ok = "ok"
    short_circuit = "short_circuit"
    retried = "retried"
    failed = "failed"


class ErrorCode(str, Enum):
    bad_request = "bad_request"
    unauthorized = "unauthorized"
    forbidden = "forbidden"
    not_found = "not_found"
    conflict = "conflict"
    payload_too_large = "payload_too_large"
    rate_limited = "rate_limited"
    idempotency_conflict = "idempotency_conflict"
    budget_exhausted = "budget_exhausted"
    upstream_unavailable = "upstream_unavailable"
    internal = "internal"


class VerifierFlagKind(str, Enum):
    unsupported_claim = "unsupported_claim"
    weak_citation = "weak_citation"
    low_confidence = "low_confidence"
    retrieval_gap = "retrieval_gap"


# ── RFS payload ────────────────────────────────────────────────────────────────

class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    content_type: str
    # sha256/bytes are optional metadata (relevant for the url-fetch case).
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    bytes: int | None = Field(default=None, ge=0)
    url: HttpUrl | None = None
    # Inline file content, base64-encoded. The supported delivery mode in v1.
    content_b64: str | None = None


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ActionType
    timestamp: datetime
    userid: str | None = None
    note: str | None = None


class RFS(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lodge_id: str
    referno: str | None = None
    branch_id: str | None = None
    timestamp: datetime | None = None
    serviceid: str | None = None
    clientid: str | None = None
    projectid: str | None = None
    probtypeid: str | None = None
    probareaid: str | None = None
    relatedarea: str | None = None
    priority: int | None = Field(default=None, ge=0, le=9)
    notes: str = Field(min_length=1, max_length=16000)
    contactname: str | None = None
    contactemail: EmailStr | None = None
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    actions: list[Action] = Field(default_factory=list)


# ── Submit ─────────────────────────────────────────────────────────────────────

class RFSAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rfs: RFS
    callback_url: HttpUrl | None = None
    callback_secret_hint: str | None = Field(default=None, min_length=8, max_length=64)
    priority: Priority = Priority.normal
    client_metadata: dict[str, Any] = Field(default_factory=dict)


class JobAccepted(BaseModel):
    job_id: UUID
    status: JobStatus = JobStatus.queued
    poll_url: str
    estimated_seconds: int | None = None


# ── Analysis output ────────────────────────────────────────────────────────────

class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: CitationSource
    locator: dict[str, Any]
    snippet: str = Field(max_length=400)
    score: float | None = None


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int
    detail: str = Field(max_length=600)
    source_refs: list[str] = Field(default_factory=list)


class RelatedRFS(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lodge_id: str
    score: float
    snippet: str = Field(max_length=240)


class VerifierFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: VerifierFlagKind
    detail: str


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(max_length=600)
    likely_cause: str | None = Field(default=None, max_length=1000)
    recommended_actions: list[RecommendedAction] = Field(min_length=1, max_length=10)
    citations: list[Citation] = Field(default_factory=list)
    related_rfs: list[RelatedRFS] = Field(default_factory=list)
    verifier_flags: list[VerifierFlag] = Field(default_factory=list)


# ── Job + trace ────────────────────────────────────────────────────────────────

class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: AgentName
    model: str
    status: AgentStepStatus
    duration_ms: int
    input_tokens: int | None = None
    input_cache_read_tokens: int | None = None
    output_tokens: int | None = None
    note: str | None = None


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = 0
    input_cache_read_tokens: int = 0
    input_cache_write_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_rm: float = 0.0


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: ErrorBody


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    rfs_lodge_id: str | None = None
    priority: Priority = Priority.normal
    client_metadata: dict[str, Any] = Field(default_factory=dict)
    result: Analysis | None = None
    error: ErrorBody | None = None
    agent_trace: list[AgentStep] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


# ── Webhook ────────────────────────────────────────────────────────────────────

class WebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    status: JobStatus
    delivered_at: datetime
    result: Analysis | None = None
    error: ErrorBody | None = None
    usage: Usage | None = None


# ── Readiness ──────────────────────────────────────────────────────────────────

class ReadinessDeps(BaseModel):
    elasticsearch: bool = False
    ollama: bool = False
    anthropic: bool = False
    redis: bool = False


class Readiness(BaseModel):
    status: str   # ready | degraded | down
    deps: ReadinessDeps
