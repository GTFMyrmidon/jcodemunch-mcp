"""Empty-store nudge (jcm#375 correspondence, suggestion C).

A user ran the suite for months with jdatamunch holding zero datasets and
jdocmunch holding three documents, and only discovered it by going looking.
Their summary of the class: every tool answers confidently regardless of how
little it holds, so an agent cannot tell "I searched everything and it is not
there" from "I have almost nothing indexed". Their words on the fix: "we would
have fed both tools months ago."

An empty listing is the one case where that ambiguity is trivially removable,
because zero indexed repositories is a fact, not an inference.

The nudge is TOP-LEVEL rather than in `_meta` on purpose: the sibling servers
strip `_meta` by default, so a hint placed there would be deleted before the
agent saw it. Same key names across all three servers.
"""

from __future__ import annotations

from jcodemunch_mcp.tools.list_repos import list_repos


def test_empty_store_says_so(tmp_path):
    result = list_repos(storage_path=str(tmp_path))
    assert result["count"] == 0
    assert result["repos"] == []
    assert result["empty"] is True
    assert "hint" in result


def test_the_hint_names_the_command_that_fixes_it(tmp_path):
    """A nudge that only states the problem makes the agent guess the remedy."""
    hint = list_repos(storage_path=str(tmp_path))["hint"]
    assert "index_folder" in hint
    # And says WHY it matters, so the agent doesn't read an empty search as
    # evidence of absence.
    assert "empty" in hint.lower()


def test_populated_store_stays_silent(small_index):
    """No key, no token, no behavior change once anything is indexed."""
    result = list_repos(storage_path=small_index["store"])
    assert result["count"] > 0
    assert "empty" not in result
    assert "hint" not in result


def test_existing_shape_is_preserved(tmp_path):
    """Additive only: count/repos/_meta must survive untouched."""
    result = list_repos(storage_path=str(tmp_path))
    assert set(("count", "repos", "_meta")).issubset(result.keys())
    assert "timing_ms" in result["_meta"]
