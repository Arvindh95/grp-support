"""Webhook delivery — HMAC-signed POST to caller's callback_url.

Replay protection: X-Rag-Timestamp + signature. Caller should reject if
timestamp skew > 5 minutes.

Retries: exponential backoff (1s, 5s, 25s) for HTTP 5xx + network errors.
4xx responses are NOT retried — the caller's endpoint is misconfigured and
retrying won't help.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from . import _submit_meta
from .deps import get_config
from .models import Job, WebhookEnvelope

log = logging.getLogger("rag-api.webhook")

RETRY_BACKOFFS_SEC = (1, 5, 25)


# ── SSRF guard ─────────────────────────────────────────────────────────────────

class CallbackUrlError(ValueError):
    """callback_url failed the SSRF safety checks."""


def validate_callback_url(url: str) -> None:
    """Reject SSRF-prone webhook targets. Raises CallbackUrlError.

    Enforced: HTTPS only; an optional host allowlist (WEBHOOK_ALLOWED_HOSTS);
    and every resolved IP must be public — private, loopback, link-local
    (incl. cloud metadata 169.254.169.254), reserved, multicast and
    unspecified addresses are blocked. Re-run at delivery time so a DNS
    rebind between submit and delivery is also caught.
    """
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise CallbackUrlError(f"unparseable URL: {e}") from e

    if parsed.scheme != "https":
        raise CallbackUrlError("callback_url must use https://")
    host = parsed.hostname
    if not host:
        raise CallbackUrlError("callback_url has no host")

    allow = [h.strip().lower()
             for h in (get_config().webhook_allowed_hosts or "").split(",")
             if h.strip()]
    if allow and host.lower() not in allow:
        raise CallbackUrlError(f"host {host!r} is not in the webhook allowlist")

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise CallbackUrlError(f"host does not resolve: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise CallbackUrlError(
                f"host resolves to a non-public address ({ip})")


def _resolve_secret(hint: str | None) -> str | None:
    """Look up the HMAC secret by caller-supplied hint.

    v1 only supports `WEBHOOK_DEFAULT_SECRET` (single shared secret). A real
    per-hint registry lands in W11+ (`/admin/callback-secrets`).
    """
    cfg = get_config()
    return cfg.webhook_default_secret or None


def _sign(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _envelope_from_job(job: Job) -> WebhookEnvelope:
    return WebhookEnvelope(
        job_id=job.job_id,
        status=job.status,
        delivered_at=datetime.now(timezone.utc),
        result=job.result,
        error=job.error,
        usage=job.usage,
    )


async def deliver(job: Job) -> None:
    """Best-effort webhook delivery. Logs failures; never raises."""
    meta = _submit_meta.load_submit_meta(str(job.job_id))
    url = meta.get("callback_url")
    if not url:
        return

    # Re-validate at delivery time — catches a DNS rebind since submit.
    try:
        validate_callback_url(url)
    except CallbackUrlError as e:
        log.warning('"webhook.blocked_url job=%s url=%s err=%s"',
                    job.job_id, url, e)
        return

    secret = _resolve_secret(meta.get("callback_secret_hint"))
    if not secret:
        log.warning('"webhook.no_secret job=%s url=%s"', job.job_id, url)
        return

    envelope = _envelope_from_job(job)
    body = envelope.model_dump_json(exclude_none=True).encode()
    signature = _sign(secret, body)
    ts = str(int(time.time()))

    headers = {
        "Content-Type": "application/json",
        "X-Rag-Signature": signature,
        "X-Rag-Timestamp": ts,
        "User-Agent": "grp-rag-api/0.1",
    }

    for attempt, delay in enumerate([0, *RETRY_BACKOFFS_SEC]):
        if delay:
            await asyncio.sleep(delay)
        try:
            # allow_redirects=False — a 30x must not bounce the signed
            # payload to an unvalidated (possibly internal) location.
            r = await asyncio.to_thread(
                lambda: requests.post(url, data=body, headers=headers,
                                      timeout=10, allow_redirects=False),
            )
        except requests.RequestException as e:
            log.warning('"webhook.network_error job=%s attempt=%d err=%s"',
                        job.job_id, attempt, e)
            continue
        if 200 <= r.status_code < 300:
            log.info('"webhook.delivered job=%s status=%d attempt=%d"',
                     job.job_id, r.status_code, attempt)
            return
        if 400 <= r.status_code < 500:
            log.warning('"webhook.4xx_no_retry job=%s status=%d"',
                        job.job_id, r.status_code)
            return
        log.warning('"webhook.5xx job=%s status=%d attempt=%d"',
                    job.job_id, r.status_code, attempt)

    log.error('"webhook.gave_up job=%s url=%s"', job.job_id, url)
