"""Attachment handling — decode, validate, build Claude-native content blocks.

The RFS may carry inline files (base64). Claude processes PDFs, images, and
plain text NATIVELY — no OCR, no conversion on our side. Word/Excel are not
Claude-ingestible; the caller must convert (Word -> PDF, Excel -> CSV) before
submitting, and we reject them with an explanatory error.

Each attachment becomes two things:
  - a content block (image / document / text) handed to the Analyst and
    Verifier so Claude sees the actual file;
  - a citable pseudo-chunk (chunk_id `attachment::<filename>`) so the Analyst
    can cite it and the Formatter can resolve it like any retrieved chunk.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Sequence

from .models import Attachment
from .retrieval import RetrievedChunk


IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PDF_TYPE = "application/pdf"
TEXT_TYPES = {"text/plain", "text/csv", "text/markdown"}
ALLOWED_TYPES = IMAGE_TYPES | {PDF_TYPE} | TEXT_TYPES

# Common rejected types -> what the caller should do instead.
_CONVERT_HINT = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "Word .docx is not supported — convert it to PDF and resubmit.",
    "application/msword":
        "Word .doc is not supported — convert it to PDF and resubmit.",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        "Excel .xlsx is not supported — convert each sheet to CSV and resubmit.",
    "application/vnd.ms-excel":
        "Excel .xls is not supported — convert each sheet to CSV and resubmit.",
}


class AttachmentError(ValueError):
    """Raised on an unsupported type, bad base64, or an over-cap total size."""


@dataclass
class DecodedAttachment:
    filename: str
    content_type: str
    data: bytes


def decode_and_validate(
    attachments: Sequence[Attachment],
    *,
    max_total_bytes: int,
) -> list[DecodedAttachment]:
    """Decode every attachment's base64, enforce the type allow-list and the
    total-size cap. Raises AttachmentError on the first problem."""
    out: list[DecodedAttachment] = []
    total = 0
    for att in attachments:
        ct = (att.content_type or "").split(";")[0].strip().lower()
        if ct not in ALLOWED_TYPES:
            hint = _CONVERT_HINT.get(ct)
            raise AttachmentError(
                f"{att.filename}: unsupported content_type {ct!r}. "
                + (hint or "Allowed: PDF, PNG/JPEG/GIF/WebP images, "
                           "plain text / CSV / Markdown.")
            )
        if not att.content_b64:
            raise AttachmentError(
                f"{att.filename}: content_b64 is required (inline file content)")
        try:
            data = base64.b64decode(att.content_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise AttachmentError(f"{att.filename}: invalid base64 ({e})") from e
        if not data:
            raise AttachmentError(f"{att.filename}: decoded content is empty")
        total += len(data)
        if total > max_total_bytes:
            raise AttachmentError(
                f"total attachment size exceeds the {max_total_bytes}-byte limit")
        out.append(DecodedAttachment(filename=att.filename,
                                     content_type=ct, data=data))
    return out


def build_content_blocks(decoded: Sequence[DecodedAttachment]) -> list[dict[str, Any]]:
    """Native Claude content blocks. Images -> image block, PDF -> document
    block, text/CSV/Markdown -> inline text block. No OCR anywhere."""
    blocks: list[dict[str, Any]] = []
    for d in decoded:
        if d.content_type in IMAGE_TYPES:
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": d.content_type,
                    "data": base64.b64encode(d.data).decode("ascii"),
                },
            })
        elif d.content_type == PDF_TYPE:
            blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": PDF_TYPE,
                    "data": base64.b64encode(d.data).decode("ascii"),
                },
                "title": d.filename,
            })
        else:  # text / csv / markdown
            text = d.data.decode("utf-8", errors="replace")
            blocks.append({
                "type": "text",
                "text": f"=== Attachment: {d.filename} ===\n{text}",
            })
    return blocks


def build_pseudo_chunks(decoded: Sequence[DecodedAttachment]) -> list[RetrievedChunk]:
    """One citable pseudo-chunk per attachment. The Analyst cites it as
    `attachment::<filename>`; the real file content is supplied separately as
    a content block."""
    chunks: list[RetrievedChunk] = []
    for d in decoded:
        chunks.append(RetrievedChunk(
            chunk_id=f"attachment::{d.filename}",
            index="attachments",
            kind="attachment",
            locator={"filename": d.filename, "content_type": d.content_type},
            text=(f"[Attachment '{d.filename}' ({d.content_type}) — the full "
                  f"file content is provided to you as an attached block]"),
            score=1.0,
        ))
    return chunks
