"""v1.108.207 — eager native embedding import (jdatamunch-mcp#3, same defect here).

The first `import onnxruntime` / `import sentence_transformers` used to happen
inside the `asyncio.to_thread` worker that `call_tool` dispatches `embed_repo`
and `check_embedding_drift` into. On Windows that loads native DLLs off the
main thread while the main thread services the transport, and deadlocks on the
loader lock — the call never returns. Reproduced here with a standalone stdio
`ClientSession` probe before the fix; the import now happens on the main thread
in `main()`, before any event loop starts.

Note the trigger is `local_onnx`, the ZERO-CONFIG default provider, so this
needed no env var to bite.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.tools import embed_repo
from jcodemunch_mcp.tools.embed_repo import warm_up_embedding_backend

ONNX_INSTALLED = importlib.util.find_spec("onnxruntime") is not None
ST_INSTALLED = importlib.util.find_spec("sentence_transformers") is not None


@pytest.fixture
def fake_backends(monkeypatch):
    """Make the native backend imports succeed without the real libraries.

    CI installs neither onnxruntime nor sentence-transformers (torch is ~2 GB),
    so gating the success path on them would leave the point of this fix
    unverified everywhere it actually runs. Stubs stand in where the real
    library is absent; `test_warms_the_real_*` below keep the non-vacuity check
    on machines that have them.
    """
    import sys
    import types

    for name, installed in (
        ("onnxruntime", ONNX_INSTALLED),
        ("sentence_transformers", ST_INSTALLED),
    ):
        if not installed:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    yield


# ── warm_up_embedding_backend ──────────────────────────────────────────────


class TestWarmUp:
    def test_no_provider_configured_is_a_noop(self, monkeypatch):
        monkeypatch.delenv("JCODEMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(embed_repo, "_detect_provider", lambda: None)
        assert warm_up_embedding_backend() is None

    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_network_providers_stay_lazy(self, monkeypatch, provider):
        """They load no native code, so there is nothing to pay startup for."""
        monkeypatch.delenv("JCODEMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(embed_repo, "_detect_provider", lambda: (provider, "m"))
        assert warm_up_embedding_backend() is None

    def test_opt_out_env_disables_it(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_EAGER_EMBED_IMPORT", "0")
        monkeypatch.setattr(embed_repo, "_detect_provider", lambda: ("local_onnx", "m"))
        assert warm_up_embedding_backend() is None

    def test_warms_local_onnx(self, monkeypatch, fake_backends):
        import sys

        monkeypatch.delenv("JCODEMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(embed_repo, "_detect_provider", lambda: ("local_onnx", "m"))
        assert warm_up_embedding_backend() == "local_onnx"
        assert "onnxruntime" in sys.modules

    def test_warms_sentence_transformers(self, monkeypatch, fake_backends):
        import sys

        monkeypatch.delenv("JCODEMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(
            embed_repo, "_detect_provider", lambda: ("sentence_transformers", "m")
        )
        assert warm_up_embedding_backend() == "sentence_transformers"
        assert "sentence_transformers" in sys.modules

    @pytest.mark.skipif(not ONNX_INSTALLED, reason="onnxruntime not installed")
    def test_warms_the_real_onnxruntime(self, monkeypatch):
        """Non-vacuity for the stubbed pair above, on a machine that has it."""
        import sys

        monkeypatch.delenv("JCODEMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(embed_repo, "_detect_provider", lambda: ("local_onnx", "m"))
        assert warm_up_embedding_backend() == "local_onnx"
        assert "onnxruntime" in sys.modules

    def test_detection_failure_does_not_stop_startup(self, monkeypatch):
        def boom():
            raise RuntimeError("simulated")

        monkeypatch.delenv("JCODEMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(embed_repo, "_detect_provider", boom)
        assert warm_up_embedding_backend() is None

    def test_broken_backend_does_not_stop_startup(self, monkeypatch):
        import builtins
        import sys

        real_import = builtins.__import__

        def boom(name, *args, **kwargs):
            if name == "onnxruntime":
                raise ImportError("simulated broken install")
            return real_import(name, *args, **kwargs)

        monkeypatch.delenv("JCODEMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(embed_repo, "_detect_provider", lambda: ("local_onnx", "m"))
        monkeypatch.setattr(builtins, "__import__", boom)
        monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
        assert warm_up_embedding_backend() is None


# ── Wiring: it has to run BEFORE any event loop ────────────────────────────


class TestMainWiring:
    def _main_body(self) -> ast.FunctionDef:
        tree = ast.parse(Path(inspect.getsourcefile(server)).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return node
        pytest.fail("server.main not found")

    def _lines(self, fn: ast.FunctionDef, attr: str) -> list[int]:
        out = []
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Name) and f.id == attr:
                out.append(n.lineno)
            elif isinstance(f, ast.Attribute) and f.attr == attr:
                out.append(n.lineno)
        return out

    def test_main_warms_the_backend_up(self):
        assert self._lines(self._main_body(), "warm_up_embedding_backend")

    def test_warm_up_precedes_every_server_launch(self):
        """After the loop starts the import is back on a worker thread — the bug.

        Scoped to the serve runners: `main` also has `asyncio.run` calls for the
        watch / index subcommands, which are separate branches that never reach
        the serve dispatch.
        """
        fn = self._main_body()
        warm = self._lines(fn, "warm_up_embedding_backend")
        runners = [
            "run_stdio_server",
            "run_sse_server",
            "run_streamable_http_server",
            "_run_server_with_watcher",
        ]
        launches = [ln for r in runners for ln in self._lines(fn, r)]
        assert warm, "main must warm the embedding backend up"
        assert len(launches) >= 4, "expected every transport dispatch to be found"
        assert max(warm) < min(launches)
