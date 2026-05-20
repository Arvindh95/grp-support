"""Attachment decode / validate / content-block / pseudo-chunk tests."""
from __future__ import annotations

import base64

import pytest

from app.attachments import (
    AttachmentError,
    build_content_blocks,
    build_pseudo_chunks,
    decode_and_validate,
)
from app.models import Attachment


def _att(filename, content_type, raw: bytes | None, *, b64=None):
    return Attachment(
        filename=filename,
        content_type=content_type,
        content_b64=b64 if b64 is not None
        else (base64.b64encode(raw).decode() if raw is not None else None),
    )


# ── decode_and_validate ───────────────────────────────────────────────────────

def test_accepts_pdf_image_text():
    atts = [
        _att("a.pdf", "application/pdf", b"%PDF-1.4 fake"),
        _att("b.png", "image/png", b"\x89PNG fake"),
        _att("c.csv", "text/csv", b"col1,col2\n1,2\n"),
    ]
    decoded = decode_and_validate(atts, max_total_bytes=1_000_000)
    assert [d.filename for d in decoded] == ["a.pdf", "b.png", "c.csv"]
    assert decoded[2].data == b"col1,col2\n1,2\n"


def test_content_type_with_charset_suffix_is_normalized():
    atts = [_att("c.txt", "text/plain; charset=utf-8", b"hello")]
    decoded = decode_and_validate(atts, max_total_bytes=1000)
    assert decoded[0].content_type == "text/plain"


def test_rejects_docx_with_convert_hint():
    atts = [_att(
        "spec.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"PK fake docx")]
    with pytest.raises(AttachmentError, match="convert it to PDF"):
        decode_and_validate(atts, max_total_bytes=1_000_000)


def test_rejects_xlsx_with_convert_hint():
    atts = [_att(
        "data.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        b"PK fake xlsx")]
    with pytest.raises(AttachmentError, match="convert each sheet to CSV"):
        decode_and_validate(atts, max_total_bytes=1_000_000)


def test_rejects_unknown_type():
    atts = [_att("x.bin", "application/octet-stream", b"\x00\x01")]
    with pytest.raises(AttachmentError, match="unsupported content_type"):
        decode_and_validate(atts, max_total_bytes=1_000_000)


def test_rejects_missing_content_b64():
    atts = [_att("a.pdf", "application/pdf", None)]
    with pytest.raises(AttachmentError, match="content_b64 is required"):
        decode_and_validate(atts, max_total_bytes=1_000_000)


def test_rejects_bad_base64():
    atts = [_att("a.pdf", "application/pdf", None, b64="not!valid!base64!!")]
    with pytest.raises(AttachmentError, match="invalid base64"):
        decode_and_validate(atts, max_total_bytes=1_000_000)


def test_rejects_empty_decoded():
    atts = [_att("a.pdf", "application/pdf", b"")]
    with pytest.raises(AttachmentError, match="empty"):
        decode_and_validate(atts, max_total_bytes=1_000_000)


def test_rejects_over_total_cap():
    atts = [
        _att("a.pdf", "application/pdf", b"x" * 600),
        _att("b.pdf", "application/pdf", b"y" * 600),
    ]
    with pytest.raises(AttachmentError, match="exceeds the 1000-byte limit"):
        decode_and_validate(atts, max_total_bytes=1000)


# ── build_content_blocks ──────────────────────────────────────────────────────

def test_blocks_image_is_base64_image_block():
    decoded = decode_and_validate(
        [_att("b.png", "image/png", b"\x89PNG data")], max_total_bytes=10_000)
    block = build_content_blocks(decoded)[0]
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert base64.b64decode(block["source"]["data"]) == b"\x89PNG data"


def test_blocks_pdf_is_document_block_with_title():
    decoded = decode_and_validate(
        [_att("manual.pdf", "application/pdf", b"%PDF data")],
        max_total_bytes=10_000)
    block = build_content_blocks(decoded)[0]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["title"] == "manual.pdf"


def test_blocks_text_is_inline_text_block():
    decoded = decode_and_validate(
        [_att("d.csv", "text/csv", b"a,b\n1,2\n")], max_total_bytes=10_000)
    block = build_content_blocks(decoded)[0]
    assert block["type"] == "text"
    assert "=== Attachment: d.csv ===" in block["text"]
    assert "a,b" in block["text"]


# ── build_pseudo_chunks ───────────────────────────────────────────────────────

def test_pseudo_chunks_are_citable():
    decoded = decode_and_validate(
        [_att("report.pdf", "application/pdf", b"%PDF data")],
        max_total_bytes=10_000)
    chunks = build_pseudo_chunks(decoded)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.chunk_id == "attachment::report.pdf"
    assert c.kind == "attachment"
    assert c.index == "attachments"
    assert c.locator["filename"] == "report.pdf"
    assert c.score == 1.0
