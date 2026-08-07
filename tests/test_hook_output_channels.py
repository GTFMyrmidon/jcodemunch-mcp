"""Regression tests for hook output *channels* (not content).

A hook can compute a perfect nudge and still be inert: on a hook that exits 0,
only ``hookSpecificOutput.additionalContext`` reaches the model. Both other
plausible-looking channels surface to the user instead —

* stderr on exit 0 → transcript / debug log
* top-level ``systemMessage`` → "unlike on every other event, where you see the
  systemMessage and Claude doesn't"

— so a steering message on either is silently discarded. That is exactly what
shipped in v1.22.5 (the fix for #241, which correctly stopped hard-blocking Read
but moved the nudge onto stderr) and stayed broken through v1.108.x: the hook
fired, computed the right text, and the model never saw a word of it.

The pre-existing tests asserted `"search_text" in err`, which *encoded* the
defect — they passed precisely because the message went nowhere useful. These
tests assert the delivery channel directly so a future refactor cannot silently
re-mute the hooks.
"""

import io
import json
import os
import sys
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import (
    _emit_additional_context,
    run_pretooluse,
    run_subagentstart,
)


def _run(func, stdin_text: str) -> tuple[int, str, str]:
    fake_in, fake_out, fake_err = (
        io.StringIO(stdin_text), io.StringIO(), io.StringIO(),
    )
    with mock.patch.object(sys, "stdin", fake_in), \
         mock.patch.object(sys, "stdout", fake_out), \
         mock.patch.object(sys, "stderr", fake_err):
        rc = func()
    return rc, fake_out.getvalue(), fake_err.getvalue()


def _read_input(path: str) -> str:
    return json.dumps({
        "session_id": "s", "hook_event_name": "PreToolUse",
        "tool_name": "Read", "tool_input": {"file_path": path},
    })


def _grep_input(pattern: str, cwd: str) -> str:
    return json.dumps({
        "session_id": "s", "hook_event_name": "PreToolUse",
        "tool_name": "Grep", "tool_input": {"pattern": pattern}, "cwd": cwd,
    })


@pytest.fixture
def big_py(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("x = 1\n" * 2000)  # comfortably over the 4KB threshold
    return f


class TestEmitAdditionalContext:
    """The shared helper's wire format."""

    def test_emits_additional_context_on_stdout(self):
        rc, out, err = _run(
            lambda: _emit_additional_context("PreToolUse", "hello model"), ""
        )
        assert rc == 0
        assert err == ""
        assert json.loads(out) == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "hello model",
            }
        }

    def test_carries_no_permission_decision(self):
        """Advisory context must not double as a deny — the call still proceeds."""
        _, out, _ = _run(lambda: _emit_additional_context("PreToolUse", "x"), "")
        assert "permissionDecision" not in json.loads(out)["hookSpecificOutput"]

    def test_stdout_is_pure_json(self):
        """Claude Code parses stdout as a single JSON object; stray text breaks it."""
        _, out, _ = _run(lambda: _emit_additional_context("PreToolUse", "x"), "")
        json.loads(out)  # would raise on leading/trailing noise


class TestReadNudgeReachesModel:
    def test_read_nudge_uses_additional_context(self, big_py):
        rc, out, err = _run(run_pretooluse, _read_input(str(big_py)))
        assert rc == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert "get_file_outline" in hso["additionalContext"]

    def test_read_nudge_leaves_stderr_empty(self, big_py):
        """The regression itself: a nudge on stderr is invisible to the model."""
        _, _, err = _run(run_pretooluse, _read_input(str(big_py)))
        assert err == ""

    def test_read_nudge_does_not_block(self, big_py):
        """Advisory must stay advisory — Read-before-Edit depends on it (#241)."""
        rc, out, _ = _run(run_pretooluse, _read_input(str(big_py)))
        assert rc == 0  # exit 2 would block AND is the only stderr route to model
        assert "permissionDecision" not in json.loads(out)["hookSpecificOutput"]


class TestGrepNudgeReachesModel:
    @pytest.fixture(autouse=True)
    def _indexed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(tmp_path)))],
        )

    def test_grep_nudge_uses_additional_context(self, tmp_path):
        rc, out, err = _run(run_pretooluse, _grep_input("foo", str(tmp_path)))
        assert rc == 0
        assert err == ""
        hso = json.loads(out)["hookSpecificOutput"]
        assert "search_text" in hso["additionalContext"]
        assert "permissionDecision" not in hso

    def test_silent_paths_stay_fully_silent(self, tmp_path, monkeypatch):
        """Gating is unchanged: outside an indexed repo, no channel is used."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks._indexed_source_roots", lambda: []
        )
        rc, out, err = _run(run_pretooluse, _grep_input("foo", str(tmp_path)))
        assert (rc, out, err) == (0, "", "")


class TestStrictModeUnaffected:
    """Strict mode already used a model-facing channel; it must keep working."""

    def test_strict_read_still_denies(self, big_py, monkeypatch, tmp_path):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(tmp_path)))],
        )
        rc, out, _ = _run(run_pretooluse, _read_input(str(big_py)))
        assert rc == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert "additionalContext" not in hso  # deny carries its own reason


class TestSubagentBriefingReachesSubagent:
    def test_briefing_uses_additional_context(self, monkeypatch):
        from jcodemunch_mcp.storage import CodeIndex

        idx = mock.MagicMock(spec=CodeIndex)
        idx.symbols = [{
            "id": "a", "name": "main", "kind": "function",
            "file": "main.py", "line": 1, "language": "python",
        }]
        idx.source_files = ["main.py"]
        idx.imports = {}
        idx.alias_map = None
        monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", type(
            "MockStore", (), {
                "__init__": lambda self, **kw: None,
                "list_repos": lambda self: [{"owner": "test", "name": "repo"}],
                "load_index": lambda self, o, n: idx,
            },
        ))

        rc, out, _ = _run(run_subagentstart, '{"hook_event_name": "SubagentStart"}')
        assert rc == 0
        if out:  # no-op when nothing is indexed in the ambient environment
            payload = json.loads(out)
            assert "systemMessage" not in payload, (
                "a briefing the subagent cannot read is pointless"
            )
            hso = payload["hookSpecificOutput"]
            assert hso["hookEventName"] == "SubagentStart"
            assert "search_symbols" in hso["additionalContext"]
