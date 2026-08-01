"""v1.108.209 — per-file freshness gets an `unknown` bucket.

`FreshnessProbe.classify` used to answer `fresh` on every path where the
comparison could not be made at all. That is the same defect `repo_freshness`
grew its four-state split to fix in v1.108.180 (#377 item 4), one level down:
an index whose freshness was never established claimed current-snapshot
equivalence.

The population it mislabels cannot self-diagnose — a `.db`-only starter pack,
an index built on another machine, a source root that has since moved — which
is precisely when a caller most needs to be told the answer is unverified.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval.freshness import FreshnessProbe


def _ns(seconds: float) -> int:
    return int(seconds * 1e9)


class TestUnmeasurableIsUnknown:
    """Every path that cannot make the comparison must say so."""

    def test_no_source_root(self):
        probe = FreshnessProbe(source_root=None, indexed_at="", index_sha=None)
        assert probe.classify("foo.py") == "unknown"

    def test_source_root_does_not_exist(self):
        # The classic starter-pack / moved-checkout shape: the index is intact,
        # the tree it describes is not reachable from here.
        probe = FreshnessProbe(
            source_root="/nonexistent/root",
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
        )
        assert probe.classify("foo.py") == "unknown"

    def test_file_absent_from_tree(self, tmp_path):
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
        )
        assert probe.classify("never_written.py") == "unknown"

    def test_empty_file_path(self, tmp_path):
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
        )
        assert probe.classify("") == "unknown"

    def test_no_baseline_to_compare_against(self, tmp_path):
        """File stats fine, but the index recorded nothing to compare it to."""
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="",  # unparseable → no index-wide timestamp
            index_sha="same",
            current_sha="same",
            file_mtimes=None,  # and no per-file mtime either
        )
        assert probe.classify("a.py") == "unknown"


class TestMeasurableAnswersUnchanged:
    """The measured paths must be byte-for-byte what they were."""

    def test_unedited_is_still_fresh(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
            file_mtimes={"a.py": _ns(f.stat().st_mtime)},
        )
        assert probe.classify("a.py") == "fresh"

    def test_edited_is_still_edited(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
            file_mtimes={"a.py": _ns(f.stat().st_mtime - 3600)},
        )
        assert probe.classify("a.py") == "edited_uncommitted"

    def test_stale_repo_still_wins(self, tmp_path):
        """A stale index is a measured answer and outranks per-file unknown."""
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
        )
        assert probe.classify("never_written.py") == "stale_index"

    def test_indexed_at_fallback_still_works(self, tmp_path):
        """No per-file mtimes, but a parseable indexed_at is a real baseline."""
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2020-01-01T00:00:00Z",  # long before the file
            index_sha="same",
            current_sha="same",
            file_mtimes=None,
        )
        assert probe.classify("a.py") == "edited_uncommitted"


class TestSummaryReportsUnknown:
    def test_unknown_is_counted_not_dropped(self, tmp_path):
        """A bucket missing from the summary reads as zero — silent undercount."""
        f = tmp_path / "ok.py"
        f.write_text("x = 1\n")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
            file_mtimes={"ok.py": _ns(f.stat().st_mtime)},
        )
        entries = [{"file": "ok.py"}, {"file": "gone.py"}, {"file": "also_gone.py"}]
        probe.annotate(entries)
        summary = probe.summary(entries)
        assert summary["fresh"] == 1
        assert summary["unknown"] == 2
        # The counts must account for every entry, or the block is misleading.
        counted = sum(
            summary[k]
            for k in ("fresh", "edited_uncommitted", "stale_index", "unknown")
        )
        assert counted == len(entries)

    def test_unannotated_entry_counts_unknown_not_fresh(self):
        probe = FreshnessProbe(source_root=None, indexed_at="", index_sha=None)
        summary = probe.summary([{"file": "a.py"}, {"file": "b.py"}])
        assert summary["unknown"] == 2
        assert summary["fresh"] == 0

    def test_summary_always_carries_the_unknown_key(self, tmp_path):
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="same",
            current_sha="same",
        )
        assert "unknown" in probe.summary([])


class TestServePathSurfacesUnknown:
    """The whole point is that a real tool response carries the signal."""

    def test_get_symbol_source_reports_unknown_when_root_is_gone(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.get_symbol import get_symbol_source

        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"
        store.mkdir()
        (src / "auth.py").write_text("def authenticate():\n    return True\n")
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True

        # Simulate the shipped-index / moved-checkout case: the content cache
        # (and therefore the served bytes) survives, the working tree does not.
        (src / "auth.py").unlink()
        src.rmdir()

        out = get_symbol_source(
            repo=r["repo"],
            symbol_id="auth.py::authenticate#function",
            storage_path=str(store),
        )
        # The body still serves from the content cache — that is by design and
        # is why we cannot mis-slice the way an on-the-fly slicer can.
        assert "def authenticate" in out["source"]
        # ...but it must not claim to be verified against the current tree.
        assert out["_freshness"] == "unknown"
        assert out["_meta"]["freshness"]["unknown"] == 1
        assert out["_meta"]["freshness"]["fresh"] == 0
