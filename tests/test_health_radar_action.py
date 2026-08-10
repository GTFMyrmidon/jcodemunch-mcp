"""Tests for the health-radar GitHub Action (v1.88.0).

The Python piece (``render_comment.py``) is unit-testable directly.

⚠ The shell + YAML steps cannot be *executed* here, but their text can be
asserted on, and that distinction cost us. This file used to open by saying
those steps "can only be exercised by running the Action in a real CI
environment", and under that assumption ``git fetch origin "$BASE" --depth=1``
sat in the base-checkout step unread. `--depth=1` does not merely limit a
download: against an already complete clone it SHORTENS the repo, and churn is
counted by ``git log --since=<N> days ago``. The base therefore scored every
file at churn <= 1 and came back artificially healthy, so PRs were charged for
a regression measured against a one-commit history. Verified on this repo at a
single commit: identical tree, shallow side composite 82.2 (B), full side 75.5
(C), and ``churn_surface`` the only axis that moved. See TestBaseFetchDepth.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

# Load the renderer by file path — it lives under .github/actions/, not
# the importable package tree.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RENDERER_PATH = _REPO_ROOT / ".github" / "actions" / "health-radar" / "render_comment.py"


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_comment", _RENDERER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_comment"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def renderer():
    return _load_renderer()


def _radar(grade="C", composite=70.0, axes=None) -> dict:
    if axes is None:
        axes = {
            "complexity":    {"score": 70.0, "raw": 6.0},
            "dead_code":     {"score": 80.0, "raw": 5.0},
            "cycles":        {"score": 100.0, "raw": 0},
            "coupling":      {"score": 50.0, "raw_unstable": 25, "raw_total_files": 100},
            "test_gap":      {"score": 60.0, "raw": 40.0},
            "churn_surface": {"score": 60.0, "raw": 200.0},
        }
    return {
        "axes": axes,
        "composite": composite,
        "grade": grade,
        "omitted_axes": [],
    }


class TestRender:
    def test_marker_on_first_line(self, renderer):
        out = renderer.render(_radar(), _radar())
        assert out.splitlines()[0] == "<!-- jcm-health-radar -->"

    def test_no_change_verdict(self, renderer):
        out = renderer.render(_radar(), _radar())
        assert "no meaningful change" in out

    def test_regression_summary(self, renderer):
        baseline = _radar(grade="B", composite=85.0)
        current_axes = dict(baseline["axes"])
        current_axes["complexity"] = {"score": 50.0, "raw": 12.0}
        current = {
            "axes": current_axes,
            "composite": 70.0,
            "grade": "C",
            "omitted_axes": [],
        }
        out = renderer.render(baseline, current)
        assert "B → C" in out
        assert "-15.0" in out
        assert "complexity" in out
        # Regressions section present
        assert "### Regressions" in out

    def test_improvement_summary(self, renderer):
        baseline_axes = {
            "complexity":    {"score": 30.0, "raw": 15.0},
            "dead_code":     {"score": 80.0, "raw": 5.0},
            "cycles":        {"score": 100.0, "raw": 0},
            "coupling":      {"score": 50.0, "raw_unstable": 25, "raw_total_files": 100},
            "test_gap":      {"score": 60.0, "raw": 40.0},
            "churn_surface": {"score": 60.0, "raw": 200.0},
        }
        baseline = {"axes": baseline_axes, "composite": 63.3, "grade": "D", "omitted_axes": []}
        current_axes = dict(baseline_axes)
        current_axes["complexity"] = {"score": 90.0, "raw": 4.0}
        current = {"axes": current_axes, "composite": 73.3, "grade": "C", "omitted_axes": []}
        out = renderer.render(baseline, current)
        assert "D → C" in out
        assert "+10.0" in out or "+10" in out
        assert "### Improvements" in out

    def test_axis_table_renders_all_rows(self, renderer):
        out = renderer.render(_radar(), _radar())
        for axis in ("complexity", "dead_code", "cycles", "coupling", "test_gap", "churn_surface"):
            assert f"`{axis}`" in out

    def test_version_appears_in_footer(self, renderer):
        out = renderer.render(_radar(), _radar(), version="1.88.0")
        assert "1.88.0" in out


class TestLoadRadar:
    def test_accepts_full_health_response(self, renderer, tmp_path: Path):
        full_response = {
            "summary": "...",
            "avg_complexity": 5.0,
            "radar": _radar(),
        }
        path = tmp_path / "h.json"
        path.write_text(json.dumps(full_response), encoding="utf-8")
        radar = renderer._load_radar(path)
        assert "axes" in radar
        assert "complexity" in radar["axes"]

    def test_accepts_radar_only_payload(self, renderer, tmp_path: Path):
        path = tmp_path / "r.json"
        path.write_text(json.dumps(_radar()), encoding="utf-8")
        radar = renderer._load_radar(path)
        assert "axes" in radar

    def test_rejects_unrelated_json(self, renderer, tmp_path: Path):
        path = tmp_path / "x.json"
        path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        with pytest.raises(ValueError):
            renderer._load_radar(path)


_ACTION_PATH = _REPO_ROOT / ".github" / "actions" / "health-radar" / "action.yml"
_CALLER_PATH = _REPO_ROOT / ".github" / "workflows" / "health-radar.yml"


def _action_steps() -> list[dict]:
    data = yaml.safe_load(_ACTION_PATH.read_text(encoding="utf-8"))
    return data["runs"]["steps"]


def _step(name_fragment: str) -> dict:
    for step in _action_steps():
        if name_fragment.lower() in (step.get("name") or "").lower():
            return step
    raise AssertionError(
        f"no step matching {name_fragment!r} in action.yml — "
        "if the step was renamed, update this guard rather than deleting it"
    )


class TestBaseFetchDepth:
    """The base and PR sides must see the same git history depth.

    A depth-limited fetch on either side silently rewrites what
    ``churn_surface`` measures, and the resulting verdict is posted publicly
    on contributor PRs. These assertions are on step *text*, which is weaker
    than executing the Action, but it is exactly the check that was missing.
    """

    def test_base_fetch_does_not_limit_depth(self):
        run = _step("Compute radar on base branch")["run"]
        # Comment lines are excluded deliberately: the step documents the
        # hazard by naming `--depth=1`, and a guard that cannot tell an
        # explanation from an instruction would push that explanation out.
        offenders = [
            line.strip()
            for line in run.splitlines()
            if not line.strip().startswith("#")
            and "git fetch" in line
            and "--depth" in line
        ]
        assert not offenders, (
            "the base fetch must not limit history depth; a depth-limited "
            "fetch shortens an already complete clone and collapses churn "
            f"to <= 1 per file. Offending line(s): {offenders}"
        )

    def test_base_fetch_unshallows_when_shallow(self):
        run = _step("Compute radar on base branch")["run"]
        assert "--unshallow" in run, (
            "the base fetch must deepen a shallow clone; without it a caller "
            "checking out at the default fetch-depth measures churn against "
            "one commit"
        )

    def test_pr_side_is_unshallowed_too(self):
        run = _step("Ensure full history on the PR side")["run"]
        assert "--unshallow" in run, (
            "the PR side needs the same treatment as the base, or a caller "
            "using actions/checkout's default fetch-depth gets the identical "
            "asymmetry in the other direction"
        )

    def test_our_caller_checks_out_full_history(self):
        data = yaml.safe_load(_CALLER_PATH.read_text(encoding="utf-8"))
        checkout = next(
            s for s in data["jobs"]["radar"]["steps"]
            if "actions/checkout" in (s.get("uses") or "")
        )
        assert checkout["with"]["fetch-depth"] == 0, (
            "health-radar.yml must check out full history; the action can "
            "recover from a shallow checkout but should not have to"
        )


class TestArrowsAndSigns:
    def test_signed_positive(self, renderer):
        assert renderer._signed(2.5) == "+2.5"

    def test_signed_negative(self, renderer):
        assert renderer._signed(-4.0) == "-4.0"

    def test_signed_none(self, renderer):
        assert renderer._signed(None) == "—"

    def test_arrow_thresholds(self, renderer):
        assert renderer._arrow(5.0) == "↑"
        assert renderer._arrow(-5.0) == "↓"
        assert renderer._arrow(2.0) == "·"
        assert renderer._arrow(-2.0) == "·"
