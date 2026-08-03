"""v1.108.230 — signal diagnostics for get_dead_code_v2 (#408, instrument only).

This release adds a measurement and changes no verdict. The confidence
arithmetic is still ``len(signals) / 3.0``; the point of shipping the instrument
alone is that the weighting change which follows can be read against a measured
before rather than argued from first principles.

⚠ The verdict-invariance test below is the load-bearing one. If a later change
to the scorer makes it fail, that change does not belong in an instrument
release.
"""

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2, _SIGNAL_NAMES


def _build(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    return r["repo"], str(store)


_NO_ENTRY_POINT = {
    "lib.py": (
        "def alpha(x):\n    return beta(x)\n\n"
        "def beta(x):\n    return x\n\n"
        "def orphan(x):\n    return x - 1\n"
    ),
    "helper.py": "from lib import alpha\n\ndef driver():\n    return alpha(1)\n",
}

_WITH_ENTRY_POINT = dict(_NO_ENTRY_POINT, **{
    "main.py": "from lib import alpha\n\nif __name__ == '__main__':\n    print(alpha(2))\n",
})


class TestSignalDiagnostics:
    def test_diagnostics_present_and_well_formed(self, tmp_path):
        repo, store = _build(tmp_path, _WITH_ENTRY_POINT)
        d = get_dead_code_v2(repo=repo, storage_path=store)["_meta"]["signal_diagnostics"]
        assert d["analysed"] > 0
        assert d["confidence_basis"] == "unweighted_vote_of_3"
        assert set(d["fire_rate"]) == set(_SIGNAL_NAMES)
        for rate in d["fire_rate"].values():
            assert 0.0 <= rate <= 1.0

    def test_fire_rate_does_not_move_with_min_confidence(self, tmp_path):
        """An instrument whose reading depends on the threshold it informs is useless."""
        repo, store = _build(tmp_path, _WITH_ENTRY_POINT)
        lo = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        hi = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=1.0)
        lo_d = lo["_meta"]["signal_diagnostics"]
        hi_d = hi["_meta"]["signal_diagnostics"]
        assert lo_d["analysed"] == hi_d["analysed"]
        assert lo_d["fire_rate"] == hi_d["fire_rate"]
        assert lo_d["cofire_rate"] == hi_d["cofire_rate"]
        # ...while the returned set very much does move.
        assert len(lo["dead_symbols"]) > len(hi["dead_symbols"])

    def test_degraded_reported_when_no_entry_point(self, tmp_path):
        repo, store = _build(tmp_path, _NO_ENTRY_POINT)
        r = get_dead_code_v2(repo=repo, storage_path=store)
        d = r["_meta"]["signal_diagnostics"]
        assert d["entry_points_detected"] == 0
        assert d["degraded"] == {"unreachable_file": "no_entry_points_detected"}
        assert "framework_warning" in r
        # The machine-readable form must agree with the prose, which is the whole
        # complaint in #408: Signal 1 accuses every symbol here.
        assert d["fire_rate"]["unreachable_file"] == 1.0
        assert "unreachable_file" in d["uninformative"]

    def test_uninformative_flags_a_saturated_signal(self, tmp_path):
        """⚠ A signal can be degenerate WITH entry points detected.

        Measured across 31 indexed repos: `unreachable_file` fires on 100% of
        symbols in 6 of them, but only 3 of those have zero entry points. So
        `entry_points_detected > 0` is not evidence the signal discriminates,
        and `uninformative` must be derived from the measured rate rather than
        from the entry-point count.
        """
        repo, store = _build(tmp_path, _WITH_ENTRY_POINT)
        d = get_dead_code_v2(repo=repo, storage_path=store)["_meta"]["signal_diagnostics"]
        for s, rate in d["fire_rate"].items():
            assert (s in d["uninformative"]) == (rate in (0.0, 1.0))

    @pytest.mark.parametrize("files", [_NO_ENTRY_POINT, _WITH_ENTRY_POINT])
    def test_verdicts_unchanged_by_the_instrument(self, tmp_path, files):
        """The arithmetic is still an unweighted vote of three. Nothing moved."""
        repo, store = _build(tmp_path, files)
        r = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        for d in r["dead_symbols"]:
            assert d["confidence"] == round(len(d["signals"]) / 3.0, 2), (
                "an instrument release must not change a confidence value"
            )
