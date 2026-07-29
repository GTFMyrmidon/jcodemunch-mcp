"""A bare cloud API key must never select a paid embedding provider.

Suite-parity check for a defect found in jdocmunch, NOT a port of its fix.

jdoc's `get_provider_name()` auto-selected OpenAI from an ambient
`OPENAI_API_KEY`, so `index_local`'s default `use_embeddings="auto"` began
billing a remote account and shipping the indexed corpus off the machine. It
needed a new opt-in gate (`JDOCMUNCH_ALLOW_PAID_EMBEDDINGS`).

⚠ jcodemunch does NOT have that defect, and adding the same gate here would be
an unreachable guard — the exact mistake recorded when jcm's own unreachable
`vocabulary_gap` identity check was removed. `_detect_provider` already requires
TWO signals for a cloud provider: the API key AND an explicit `*_EMBED_MODEL`.
Naming the model is the opt-in. The bundled local ONNX encoder also wins at
priority 0, so the common path never reaches a cloud branch at all.

So what is ported is the DEFECT CHECK, not the remedy: these tests pin the
property jdoc lost, so it cannot be refactored away here without failing loudly.
If someone ever "simplifies" `_detect_provider` to key off the API key alone,
this is what catches it.
"""

from __future__ import annotations

from unittest import mock

import pytest

from jcodemunch_mcp.tools.embed_repo import _detect_provider


_ENV = (
    "GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL",
    "OPENAI_API_KEY", "OPENAI_EMBED_MODEL",
    "JCODEMUNCH_EMBED_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def no_local_onnx():
    """Priority 0 is the bundled local encoder and it wins whenever present.

    These tests are about the CLOUD branches, so the local encoder is forced
    unavailable — otherwise every case below returns local_onnx and the
    assertions would pass without ever exercising the code they describe.
    """
    with mock.patch(
        "jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=False
    ):
        yield


@pytest.mark.parametrize(
    "key_var,model_var,name",
    [
        ("OPENAI_API_KEY", "OPENAI_EMBED_MODEL", "openai"),
        ("GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL", "gemini"),
    ],
)
def test_bare_cloud_key_alone_selects_nothing(clean_env, no_local_onnx, key_var, model_var, name):
    """The property jdoc lost. A key with no model must not reach a cloud provider."""
    clean_env.setenv(key_var, "not-a-real-key")
    assert _detect_provider() is None, (
        f"a bare {key_var} selected {name}: that bills a remote account and sends "
        f"indexed source off the machine. {model_var} is the opt-in and must stay required."
    )


@pytest.mark.parametrize(
    "key_var,model_var,name,model",
    [
        ("OPENAI_API_KEY", "OPENAI_EMBED_MODEL", "openai", "text-embedding-3-small"),
        ("GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL", "gemini", "text-embedding-004"),
    ],
)
def test_naming_the_model_opts_in(clean_env, no_local_onnx, key_var, model_var, name, model):
    """Non-vacuity for the test above: the provider is gated, not broken."""
    clean_env.setenv(key_var, "not-a-real-key")
    clean_env.setenv(model_var, model)
    assert _detect_provider() == (name, model)


def test_local_onnx_outranks_any_cloud_key(clean_env):
    """Priority 0 keeps the default path entirely on-machine even when a fully
    configured cloud provider is present."""
    clean_env.setenv("OPENAI_API_KEY", "not-a-real-key")
    clean_env.setenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    with mock.patch(
        "jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available", return_value=True
    ), mock.patch(
        "jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=True
    ):
        provider, _ = _detect_provider()
    assert provider == "local_onnx"
