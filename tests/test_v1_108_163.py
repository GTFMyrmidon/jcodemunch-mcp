"""v1.108.163 — Audit WS-8 (V12): license-key transport moves out of the URL.

The key must never ride a query string (it lands in server/proxy access logs).
validate.php checks travel as a POST form body; starter-pack downloads carry the
key in the X-JCM-License header. Each has a one-shot legacy fallback keyed to
the EXACT error signature a pre-deploy backend produces, so ship order can't
strand a paying customer — and a genuine key rejection never retries.
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


def test_check_server_legacy_backend_triggers_one_get_fallback(fake_httpx):
    # A pre-POST validate.php reads only $_GET → answers the POST with its
    # missing-parameter 400. That exact signature retries once over GET.
    fake = fake_httpx(
        post_responses=[FakeResponse({"valid": False, "error": "Missing license parameter."})],
        get_responses=[FakeResponse({"valid": True, "tier": "platform"})],
    )
    out = lic._check_server("SECRETKEY001")
    assert out == {"valid": True, "tier": "platform", "error": None}
    assert len(fake.post_calls) == 1 and len(fake.get_calls) == 1
    _, kwargs = fake.get_calls[0]
    assert kwargs["params"]["license"] == "SECRETKEY001"


def test_check_server_unreachable_returns_none(fake_httpx):
    fake_httpx()  # no scripted responses → POST raises
    assert lic._check_server("SECRETKEY001") is None


@pytest.mark.parametrize("answer,expected", [
    (None, False),
    ({"valid": True, "tier": "studio", "error": None}, False),
    ({"valid": False, "tier": None, "error": "License is revoked."}, False),
    ({"valid": False, "tier": None, "error": "Missing license parameter."}, True),
    ({"valid": False, "tier": None, "error": None}, False),
])
def test_needs_legacy_get_fallback(answer, expected):
    assert lic._needs_legacy_get_fallback(answer) is expected


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


def test_install_pack_legacy_backend_retries_once_with_query_key(monkeypatch, tmp_path):
    # Pre-header backend ignores X-JCM-License → answers the licensed pack
    # with its no-license 403. Exactly one legacy retry, key in the query.
    fake = FakeHTTPX(get_responses=[
        FakeResponse({"error": "This pack requires a jCodeMunch license."}),
        FakeResponse({"error": "Invalid or expired license."}),
    ])
    rc = _run_install(monkeypatch, tmp_path, fake)
    assert rc == 1
    assert len(fake.get_calls) == 2
    first_url, _ = fake.get_calls[0]
    retry_url, retry_kwargs = fake.get_calls[1]
    assert "license=" not in first_url
    assert "license=SECRETKEY001" in retry_url
    assert "headers" not in retry_kwargs or retry_kwargs.get("headers") is None


def test_install_pack_real_rejection_never_retries(monkeypatch, tmp_path):
    fake = FakeHTTPX(get_responses=[
        FakeResponse({"error": "Invalid or expired license."}),
    ])
    rc = _run_install(monkeypatch, tmp_path, fake)
    assert rc == 1
    assert len(fake.get_calls) == 1


@pytest.mark.parametrize("body,ctype,expected", [
    ({"error": "This pack requires a jCodeMunch license."}, "application/json", True),
    ({"error": "Invalid or expired license."}, "application/json", False),
    ({"error": "This pack requires a jCodeMunch license."}, "application/zip", False),
    (None, "application/json", False),
    ({}, "application/json", False),
])
def test_looks_like_missing_license_response(body, ctype, expected):
    resp = FakeResponse(body, content_type=ctype)
    assert install_pack._looks_like_missing_license_response(resp) is expected
