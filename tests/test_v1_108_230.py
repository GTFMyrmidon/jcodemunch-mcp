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
        # Was "unweighted_vote_of_3" in .230; .231 stopped giving a vote to
        # signals that do not discriminate.
        assert d["confidence_basis"] == "informative_signals_over_3"
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
        # .230 defined uninformative as "rate is exactly 0.0 or 1.0", a
        # placeholder written before there was a measurement. .231 replaced it
        # with the degeneracy cutoff, which is the definition that decides votes.
        cut = d["degeneracy_cutoff"]
        for s, rate in d["fire_rate"].items():
            degenerate = rate >= cut or rate <= 1.0 - cut
            assert (s in d["uninformative"]) == degenerate

    @pytest.mark.parametrize("files", [_NO_ENTRY_POINT, _WITH_ENTRY_POINT])
    def test_confidence_counts_only_the_signals_that_voted(self, tmp_path, files):
        """⚠ SUPERSEDED BY v1.108.231.

        In .230 this asserted `confidence == len(signals) / 3.0` — that an
        instrument release changes no verdict. That was the correct contract for
        .230 and it held. .231 then deliberately stopped giving a vote to signals
        that do not discriminate, so the invariant is now stated against the
        signals that were actually counted. The denominator is still 3; that part
        never moved, and it is what makes the ceiling fall rather than a lone
        surviving signal being rescaled to 1.0.
        """
        repo, store = _build(tmp_path, files)
        r = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        for d in r["dead_symbols"]:
            counted = d.get("counted_signals", d["signals"])
            assert d["confidence"] == round(len(counted) / 3.0, 2)
            assert set(counted) <= set(d["signals"])
