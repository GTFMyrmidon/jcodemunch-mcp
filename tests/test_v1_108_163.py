"""v1.108.163 — Audit WS-8 (V12): license-key transport moves out of the URL.

The key must never ride a query string (it lands in server/proxy access logs).
validate.php checks travel as a POST form body; starter-pack downloads carry the
key in the X-JCM-License header.

v1.108.198 removed both one-shot legacy fallbacks — validate.php and the packs
API have been POST/header-aware since 2026-07-23, verified live on all three
transports. The tests that pinned the fallback predicates are replaced below by
tests that the key NEVER reaches a URL and that no legacy retry is attempted,
which is the invariant those predicates existed to protect.
"""
from __future__ import annotations

import sys
import types

import pytest

import jcodemunch_mcp.org.license as lic
from jcodemunch_mcp.cli import install_pack


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeResponse:
    def __init__(self, body=None, *, content_type="application/json", content=b""):
        self._body = body
        self.headers = {"content-type": content_type}
        self.content = content

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHTTPX(types.ModuleType):
    """Stands in for httpx: records every call, serves scripted responses."""

    class HTTPError(Exception):
        pass

    def __init__(self, post_responses=(), get_responses=()):
        super().__init__("httpx")
        self.post_calls = []
        self.get_calls = []
        self._post = list(post_responses)
        self._get = list(get_responses)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if not self._post:
            raise self.HTTPError("unscripted POST")
        return self._post.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self._get:
            raise self.HTTPError("unscripted GET")
        return self._get.pop(0)


@pytest.fixture()
def fake_httpx(monkeypatch):
    def install(post_responses=(), get_responses=()):
        fake = FakeHTTPX(post_responses, get_responses)
        monkeypatch.setitem(sys.modules, "httpx", fake)
        return fake
    return install


# --------------------------------------------------------------------------- #
# validate.php client (_check_server)
# --------------------------------------------------------------------------- #

def test_check_server_posts_key_in_body_not_url(fake_httpx):
    fake = fake_httpx(post_responses=[FakeResponse({"valid": True, "tier": "studio"})])
    out = lic._check_server("SECRETKEY001")
    assert out == {"valid": True, "tier": "studio", "error": None}
    assert len(fake.post_calls) == 1 and not fake.get_calls
    url, kwargs = fake.post_calls[0]
    assert "SECRETKEY001" not in url
    assert "params" not in kwargs
    assert kwargs["data"] == {"product": lic.PRODUCT, "license": "SECRETKEY001"}


def test_check_server_real_rejection_never_falls_back_to_get(fake_httpx):
    fake = fake_httpx(post_responses=[
        FakeResponse({"valid": False, "error": "License key not found for this product."}),
    ])
    out = lic._check_server("BOGUSKEY0001")
    assert out["valid"] is False
    assert not fake.get_calls


def test_check_server_reports_the_missing_parameter_answer_verbatim(fake_httpx):
    """v1.108.198: this signature used to be rescued by a legacy GET. It is now
    reported as what it is. A live POST-aware backend only says this when no key
    was sent, so rescuing it would hide a caller bug rather than a deploy gap."""
    fake = fake_httpx(
        post_responses=[FakeResponse({"valid": False, "error": "Missing license parameter."})],
    )
    out = lic._check_server("SECRETKEY001")
    assert out == {"valid": False, "tier": None, "error": "Missing license parameter."}
    assert len(fake.post_calls) == 1 and fake.get_calls == []


def test_check_server_unreachable_returns_none(fake_httpx):
    fake_httpx()  # no scripted responses → POST raises
    assert lic._check_server("SECRETKEY001") is None


@pytest.mark.parametrize("body", [
    {"valid": False, "error": "License key not found for this product."},
    {"valid": False, "error": "Missing license parameter."},
    {"valid": True, "tier": "studio"},
])
def test_check_server_never_retries_over_get(fake_httpx, body):
    """v1.108.198: the legacy GET fallback is gone, so NO answer triggers a
    second request. "Missing license parameter." is included deliberately — it
    is the exact signature that used to trigger the retry, and a live backend
    only emits it when no key was sent at all."""
    fx = fake_httpx(post_responses=[FakeResponse(body)])
    lic._check_server("SECRETKEY001")
    assert len(fx.post_calls) == 1
    assert fx.get_calls == [], "a legacy GET retry survived the removal"


def test_check_server_keeps_the_key_out_of_the_url(fake_httpx):
    """The whole point of V12: a key in a query string lands in access logs."""
    fx = fake_httpx(post_responses=[FakeResponse({"valid": True, "tier": "studio"})])
    lic._check_server("SECRETKEY001")
    url, kwargs = fx.post_calls[0]
    assert "SECRETKEY001" not in url
    assert kwargs["data"]["license"] == "SECRETKEY001"
    assert "params" not in kwargs


# --------------------------------------------------------------------------- #
# starter-pack download (install_pack)
# --------------------------------------------------------------------------- #

def _run_install(monkeypatch, tmp_path, fake, key="SECRETKEY001"):
    monkeypatch.setattr(install_pack, "httpx", fake)
    return install_pack._install_pack("fastapi", license_key=key, base_path=tmp_path)


def test_install_pack_key_rides_header_not_url(monkeypatch, tmp_path):
    fake = FakeHTTPX(get_responses=[
        FakeResponse({"error": "Invalid or expired license."}),
    ])
    rc = _run_install(monkeypatch, tmp_path, fake)
    assert rc == 1
    assert len(fake.get_calls) == 1
    url, kwargs = fake.get_calls[0]
    assert "SECRETKEY001" not in url
    assert kwargs["headers"] == {"X-JCM-License": "SECRETKEY001"}


def test_install_pack_no_key_sends_no_header(monkeypatch, tmp_path):
    fake = FakeHTTPX(get_responses=[
        FakeResponse({"error": "This pack requires a jCodeMunch license."}),
    ])
    rc = _run_install(monkeypatch, tmp_path, fake, key=None)
    assert rc == 1
    _, kwargs = fake.get_calls[0]
    assert kwargs["headers"] is None


def test_install_pack_never_puts_the_key_in_a_url(monkeypatch, tmp_path):
    """v1.108.198: the no-license 403 no longer triggers a query-string retry.

    One request, key in the header, nothing in the URL. The removed retry was
    the ONLY path that could put a license key in a server access log, which is
    the finding (V12) this whole module exists for."""
    fake = FakeHTTPX(get_responses=[
        FakeResponse({"error": "This pack requires a jCodeMunch license."}),
    ])
    rc = _run_install(monkeypatch, tmp_path, fake)
    assert rc == 1
    assert len(fake.get_calls) == 1
    url, kwargs = fake.get_calls[0]
    assert "license=" not in url
    assert kwargs["headers"]["X-JCM-License"] == "SECRETKEY001"


def test_install_pack_real_rejection_never_retries(monkeypatch, tmp_path):
    fake = FakeHTTPX(get_responses=[
        FakeResponse({"error": "Invalid or expired license."}),
    ])
    rc = _run_install(monkeypatch, tmp_path, fake)
    assert rc == 1
    assert len(fake.get_calls) == 1


def test_install_pack_predicate_is_gone():
    """v1.108.198: the pre-header fallback predicate is removed, not renamed.

    Pinned by name because a resurrected helper is how a removed transitional
    path quietly comes back."""
    assert not hasattr(install_pack, "_looks_like_missing_license_response")
    assert not hasattr(lic, "_needs_legacy_get_fallback")
