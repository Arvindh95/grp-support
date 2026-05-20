"""callback_url SSRF guard."""
from __future__ import annotations

import pytest

from app.webhook import CallbackUrlError, validate_callback_url


def test_rejects_non_https():
    with pytest.raises(CallbackUrlError, match="https"):
        validate_callback_url("http://example.com/hook")


def test_rejects_loopback_ip():
    with pytest.raises(CallbackUrlError):
        validate_callback_url("https://127.0.0.1/hook")


def test_rejects_loopback_hostname():
    with pytest.raises(CallbackUrlError):
        validate_callback_url("https://localhost/hook")


def test_rejects_private_ip():
    with pytest.raises(CallbackUrlError):
        validate_callback_url("https://10.1.2.3/hook")


def test_rejects_private_ip_192():
    with pytest.raises(CallbackUrlError):
        validate_callback_url("https://192.168.0.10:8443/hook")


def test_rejects_cloud_metadata_ip():
    # 169.254.169.254 — the canonical SSRF target — is link-local.
    with pytest.raises(CallbackUrlError):
        validate_callback_url("https://169.254.169.254/latest/meta-data/")


def test_rejects_missing_host():
    with pytest.raises(CallbackUrlError):
        validate_callback_url("https:///nohost")


def test_accepts_public_https(monkeypatch):
    # Pin DNS so the test does not depend on real network resolution.
    monkeypatch.setattr(
        "app.webhook.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    validate_callback_url("https://hooks.example.com/grp")  # must not raise
