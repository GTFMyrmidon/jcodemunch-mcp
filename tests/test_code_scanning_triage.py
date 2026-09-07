"""The code-scanning triage of 2026-09-07 (docs/cicd/FINDINGS.md C-16): every alert with a
security severity was either fixed here or dismissed with its reason in the alert. These are the
fixes' guards, one per alert group, each written to fail on the tree before the fix.

- py/bad-tag-filter (alerts 13, 14): the Razor and Astro `<script>`/`<style>` block regexes ended at
  a bare `</script>`, so a block closed `</script >` (valid HTML) ran on to the NEXT close tag and
  swallowed the markup between, ids included.
- py/overly-permissive-file (alert 15): the process-lock file was created 0o644; its metadata is
  read only by the same user's processes.
- py/jinja2/autoescape-false (alert 18): the munch-bench leaderboard rendered model and provider
  names into HTML unescaped.
- actions/code-injection (alerts 5, 6, 7): the speedreview composite action interpolated caller
  inputs into `run:` text.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp.parser import extractor, parse_file

REPO = Path(__file__).resolve().parent.parent

_RAZOR = (
    '@page "/x"\n'
    "<script >\nfunction alpha() { return 1; }\n</script >\n"
    '<div id="hero"></div>\n'
    "<script>\nfunction beta() { return 2; }\n</script>\n"
)


@pytest.mark.parametrize("pattern", [extractor._RAZOR_SCRIPT_RE, extractor._ASTRO_SCRIPT_RE])
def test_a_script_block_closed_with_whitespace_before_the_bracket_ends_there(pattern):
    """Two blocks, the first closed `</script >`: two matches, and the div is in neither.
    Before the fix the first match ran to the second block's close tag."""
    matches = list(pattern.finditer(_RAZOR))
    assert len(matches) == 2, [m.group(0)[:40] for m in matches]
    assert 'id="hero"' not in matches[0].group(2)
    assert "alpha" in matches[0].group(2) and "beta" in matches[1].group(2)


@pytest.mark.parametrize("pattern", [extractor._RAZOR_STYLE_RE, extractor._ASTRO_STYLE_RE])
def test_a_style_block_closed_with_whitespace_ends_there(pattern):
    src = "<style >\n.a{}\n</style >\n<p>x</p>\n<style>\n.b{}\n</style>\n"
    matches = list(pattern.finditer(src))
    assert len(matches) == 2
    assert "<p>" not in matches[0].group(2)


def test_razor_parses_both_functions_when_the_first_block_closes_with_whitespace():
    syms = parse_file(_RAZOR, "Views/A.cshtml", "razor")
    by_name = {s.name: s for s in syms}
    assert {"alpha", "beta", "hero"} <= set(by_name)
    assert by_name["alpha"].end_line < by_name["hero"].end_line < by_name["beta"].end_line


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_lock_file_is_readable_by_its_owner_only(tmp_path):
    from jcodemunch_mcp.storage.process_locks import acquire, release

    assert acquire("test", "alpha", str(tmp_path)) is True
    try:
        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert files, "acquire wrote no lock file under the storage root"
        for p in files:
            mode = stat.S_IMODE(os.stat(p).st_mode)
            assert mode & 0o077 == 0, (p, oct(mode))
    finally:
        release("test", "alpha", str(tmp_path))


def test_the_lock_file_is_opened_0o600_on_every_platform():
    """The POSIX check above cannot run on Windows; the literal is asserted at its one site so the
    mode cannot drift back on the box this project is developed on."""
    src = (REPO / "src" / "jcodemunch_mcp" / "storage" / "process_locks.py").read_text(encoding="utf-8")
    opens = re.findall(r"^.*os\.open\(.*O_CREAT.*$", src, flags=re.M)
    assert opens, "the O_EXCL create call moved; update this scan"
    for call in opens:
        assert "0o600" in call, call
        assert "0o644" not in call, call


def test_the_leaderboard_template_declares_autoescape_at_its_source():
    """Runs on every gate leg. The behavioural arm below needs jinja2, which is in the `bench`
    extra the PR gate does not install (C-16), so the flag is asserted where it is set as well."""
    src = (REPO / "munch-bench" / "munch_bench" / "leaderboard.py").read_text(encoding="utf-8")
    calls = re.findall(r"Template\((.*?)\"\"\"", src, flags=re.S)
    assert calls, "the Template() construction moved; update this scan"
    for args in calls:
        assert "autoescape=True" in args, args


def test_the_leaderboard_escapes_a_model_name_carrying_markup(tmp_path):
    pytest.importorskip("munch_bench", reason="munch-bench is importable only when the package is installed")
    pytest.importorskip("jinja2", reason="munch-bench's own dependency; the `bench` extra, not the dev group")
    from munch_bench.evaluate import BenchmarkRun, QuestionResult
    from munch_bench.leaderboard import generate_leaderboard

    hostile = '<img src=x onerror="alert(1)">'
    run = BenchmarkRun(provider="p", model=hostile, timestamp="2026-04-13T00:00:00Z", token_budget=8000)
    run.results.append(QuestionResult(
        question_id="q", repo="r", question="?", difficulty="easy", category="api",
        retrieval_precision_at_5=0.8, retrieval_precision_at_10=0.6, retrieval_recall=0.9,
        retrieval_wall_time_s=0.1, retrieval_tokens=500, symbols_returned=["s"], answer="a",
        model=hostile, provider="p", inference_wall_time_s=0.5, inference_input_tokens=1000,
        inference_output_tokens=200, inference_cost_usd=0.001, exact_match=True, llm_judge_score=0.5,
        ground_truth_answer="a", ground_truth_symbols=["s"],
    ))
    out = tmp_path / "lb.html"
    generate_leaderboard([run], str(out))
    html = out.read_text(encoding="utf-8")
    assert hostile not in html
    assert "&lt;img src=x onerror=" in html
    # the chart arrays are JSON built by the module and stay unescaped by design
    assert "const labels = [" in html


def test_the_speedreview_action_interpolates_no_input_into_shell_text():
    """`${{ inputs.* }}` and `${{ github.action_path }}` belong in `env:` (or an env var the runner
    sets), never inside a `run:` script: the caller controls the input and the shell runs the text."""
    text = (REPO / "speedreview" / "action.yml").read_text(encoding="utf-8")
    runs = re.findall(r"^\s*run:\s*(.+?)(?=^\s*- name:|^\s*\w+:\s*$|\Z)", text, flags=re.M | re.S)
    assert runs, "no run: steps found; the scan's shape is stale"
    # any expression context a caller can influence, in any spacing: `${{inputs.x}}` is valid
    # Actions syntax too, and `github.event.*` is as caller-controlled as an input
    injectable = re.compile(r"\$\{\{\s*(inputs\.|github\.action_path|github\.event\.)")
    for body in runs:
        assert not injectable.search(body), body
    # non-vacuity: the inputs are still reaching the steps, through env
    assert "JCODEMUNCH_VERSION: ${{ inputs.jcodemunch_version }}" in text
    assert "$GITHUB_ACTION_PATH" in text
