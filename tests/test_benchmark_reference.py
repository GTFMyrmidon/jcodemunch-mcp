"""Recurrence guard: the comparison harnesses must not carry their own jCodeMunch numbers.

`benchmarks/harness/run_rag_baseline.py` and `run_odysseus_compare.py` measure the
*other* system live and then divide by jCodeMunch's per-query cost. For most of
their life that divisor was a hand-typed constant captured on 2026-03-28, so every
published ratio had a fresh numerator over a stale denominator and drifted on its
own as the benchmark repos moved. When the constants were finally re-measured on
2026-07-29 all three had moved against us (express 924 -> 985, fastapi 1,834 ->
2,494, gin 1,124 -> 1,540) and one published row flipped its winner.

Worse, a repo absent from the constants dict fell back to `_jmunch_avg_tokens_for_repo`,
which allocated jCodeMunch's cost *proportionally to repo size*. That number was
never measured, and its premise is the opposite of what this project claims about
retrieval cost, so the estimate was biased in a direction no reader could see.

Both are now structural: the jCodeMunch side comes from `benchmarks/jcm_reference.json`,
written by `run_benchmark.py --reference`, and an uncovered repo renders
"not measured". These tests pin the invariant rather than the instances, and they
assert the estimator absent BY NAME — a resurrected helper is how a removed path
comes back.
"""

import ast
import json
import pathlib

import pytest

BENCH = pathlib.Path(__file__).resolve().parents[1] / "benchmarks"
HARNESS = BENCH / "harness"
REFERENCE = BENCH / "jcm_reference.json"

# Harnesses that compare another system against jCodeMunch and must therefore
# source the jCodeMunch side from the reference artifact.
COMPARISON_HARNESSES = ("run_rag_baseline.py", "run_odysseus_compare.py")

# Removed for cause. Named here so a reintroduction fails loudly instead of
# quietly restoring an unmeasured, size-proportional estimate.
BANNED_NAMES = {"_jmunch_avg_tokens_for_repo"}

REFERENCE_SCHEMA = "jcm-benchmark-reference/v2"
CANONICAL_REPOS = {"expressjs/express", "fastapi/fastapi", "gin-gonic/gin"}
TASKS = BENCH / "tasks.json"


def _tree(name: str) -> ast.Module:
    return ast.parse((HARNESS / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The artifact
# ---------------------------------------------------------------------------

def test_reference_artifact_is_committed_and_well_formed():
    assert REFERENCE.exists(), (
        "benchmarks/jcm_reference.json is missing. The comparison harnesses read it "
        "instead of hardcoding jCodeMunch's numbers; regenerate with "
        "`python benchmarks/harness/run_benchmark.py --reference`."
    )
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert data["schema"] == REFERENCE_SCHEMA
    assert data["tokenizer"] == "cl100k_base"
    assert CANONICAL_REPOS <= set(data["repos"]), (
        "The reference artifact must cover every canonical benchmark repo — a repo "
        "missing from it reads as 'not measured' in every comparison table."
    )


@pytest.mark.parametrize("repo", sorted(CANONICAL_REPOS))
def test_reference_entry_records_the_index_state_it_was_measured_against(repo):
    """Without the corpus fingerprint a cross-run comparison cannot be detected."""
    entry = json.loads(REFERENCE.read_text(encoding="utf-8"))["repos"][repo]
    for field in ("avg_tokens_per_query", "baseline_tokens", "file_count", "queries"):
        assert entry.get(field), f"{repo}: reference entry is missing {field}"
    assert entry["avg_tokens_per_query"] == round(
        entry["jmunch_total_tokens"] / entry["queries"]
    ), f"{repo}: the published average does not match its own measured total"


# ---------------------------------------------------------------------------
# The corpus behind the numbers (v1.108.222)
#
# A published number has to be able to answer two questions: which upstream
# tree, and was all of it indexed. Neither was recorded before v2 of the
# artifact. The fastapi index behind every number published through v1.108.221
# held 1,000 of 1,182 eligible files, silently truncated by a file cap, with an
# empty coverage record. What it dropped turned out to be worth zero tokens —
# but that was established by measuring it, not by reading the artifact.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("repo", sorted(CANONICAL_REPOS))
def test_every_benchmark_repo_pins_an_upstream_commit(repo):
    """`index_repo` has no ref parameter, so an unpinned corpus is unreproducible."""
    corpus = json.loads(TASKS.read_text(encoding="utf-8"))
    entry = next((r for r in corpus["repos"] if r["id"] == repo), None)
    assert entry is not None, f"{repo} is missing from tasks.json"
    sha = entry.get("sha", "")
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (
        f"{repo}: tasks.json must pin a full 40-hex commit SHA, got {sha!r}. "
        "Without it a re-run measures whatever the default branch points at today."
    )


@pytest.mark.parametrize("repo", sorted(CANONICAL_REPOS))
def test_published_numbers_were_measured_against_the_pinned_tree(repo):
    entry = json.loads(REFERENCE.read_text(encoding="utf-8"))["repos"][repo]
    corpus = json.loads(TASKS.read_text(encoding="utf-8"))
    pinned = next(r["sha"] for r in corpus["repos"] if r["id"] == repo)
    state = entry.get("corpus")
    assert isinstance(state, dict) and state, (
        f"{repo}: the reference entry carries no corpus block. Regenerate with "
        "`python benchmarks/harness/run_benchmark.py --reference`."
    )
    assert state.get("git_head") == pinned, (
        f"{repo}: published numbers were measured against "
        f"{state.get('git_head')!r} but tasks.json pins {pinned!r}. One of the "
        "two is wrong; a number measured on a different tree is not comparable."
    )
    assert state.get("pin") == "verified"


@pytest.mark.parametrize("repo", sorted(CANONICAL_REPOS))
def test_published_numbers_come_from_a_complete_corpus(repo):
    """`"unknown"` must fail here. It is the state fastapi was in for months."""
    state = json.loads(REFERENCE.read_text(encoding="utf-8"))["repos"][repo]["corpus"]
    assert state.get("complete") is True, (
        f"{repo}: corpus completeness is {state.get('complete')!r}. An index with "
        "no coverage record has not been found complete — it has not been "
        "measured, and a truncated corpus publishes a baseline for a tree that "
        "was never fully read."
    )


def test_a_published_artifact_is_never_provisional():
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert not data.get("provisional"), (
        "The committed artifact was written with --allow-unpinned: "
        f"{data.get('caveats')}. Fix the corpus, do not commit the override."
    )


def test_the_harness_refuses_to_publish_an_unpinnable_corpus(rb):
    """The gate, exercised rather than assumed."""
    verified = {"git_head": "a" * 40, "pinned_sha": "a" * 40,
                "pin": "verified", "complete": True}
    assert rb.corpus_objection(verified) is None

    assert "no SHA pinned" in rb.corpus_objection({**verified, "pin": "unpinned"})
    assert "!=" in rb.corpus_objection(
        {**verified, "pin": "mismatch", "git_head": "b" * 40}
    )
    assert "cannot be checked" in rb.corpus_objection({**verified, "pin": "unknown"})
    unknown = rb.corpus_objection({**verified, "complete": "unknown"})
    assert unknown and "'unknown'" in unknown, (
        "an index with no coverage record must be rejected as unknown, not "
        "silently accepted as complete"
    )
    assert rb.corpus_objection({**verified, "complete": False})


def test_corpus_state_reads_an_absent_coverage_record_as_unknown(rb):
    class _Ix:
        git_head = "c" * 40
        coverage = {}

    state = rb.corpus_state(_Ix(), "some/repo", pins={"some/repo": "c" * 40})
    assert state["pin"] == "verified"
    assert state["complete"] == "unknown", (
        "Collapsing a missing coverage record to True is the false-absence shape "
        "this project keeps closing; collapsing it to False invents a defect."
    )
    assert rb.corpus_objection(state)


def test_reproducing_doc_exists_and_names_every_pin():
    doc = (BENCH / "REPRODUCING.md").read_text(encoding="utf-8")
    corpus = json.loads(TASKS.read_text(encoding="utf-8"))
    for entry in corpus["repos"]:
        assert entry["sha"] in doc, (
            f"REPRODUCING.md does not name the pinned SHA for {entry['id']}. "
            "A reproduction recipe that omits the commit reproduces nothing."
        )


def test_provenance_artifact_matches_the_reference_run():
    """The third mirror of this run, and the one that broke when it was missed.

    `benchmarks/provenance/measured.json` backs every basis="measured" entry in
    the provenance registry. tests/test_provenance.py already checks it against
    the METHODOLOGY prose; nothing checked it against the run that produced the
    numbers, so re-measuring updated the prose and left the artifact behind.
    `run_benchmark.py --reference` now rewrites the block, and this pins it.
    """
    grand = json.loads(REFERENCE.read_text(encoding="utf-8"))["grand"]
    measured = json.loads(
        (BENCH / "provenance" / "measured.json").read_text(encoding="utf-8")
    )["token_reduction"]
    assert measured["baseline_tokens"] == grand["baseline_tokens"]
    assert measured["jcodemunch_tokens"] == grand["jmunch_tokens"]
    assert measured["task_runs"] == grand["task_runs"]
    assert measured["average_pct"] == round(
        (1 - grand["jmunch_tokens"] / grand["baseline_tokens"]) * 100, 1
    )


def test_grand_summary_is_derived_from_the_per_repo_rows():
    """A hand-edited grand total is how the artifact starts lying about itself."""
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    rows = data["repos"].values()
    assert data["grand"]["jmunch_tokens"] == sum(r["jmunch_total_tokens"] for r in rows)
    assert data["grand"]["task_runs"] == sum(r["queries"] for r in rows)


# ---------------------------------------------------------------------------
# The harnesses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("harness", COMPARISON_HARNESSES)
def test_no_hardcoded_jcodemunch_constants(harness):
    """A jCodeMunch number typed into a harness is stale the next time a repo moves."""
    offenders = [
        target.id
        for node in ast.walk(_tree(harness))
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name) and target.id.startswith("JCODEMUNCH_")
    ]
    assert not offenders, (
        f"{harness} defines {offenders}. jCodeMunch figures belong in "
        "benchmarks/jcm_reference.json, written by the same run that measures them."
    )


@pytest.mark.parametrize("harness", COMPARISON_HARNESSES)
def test_removed_estimator_stays_removed(harness):
    """Absent BY NAME, so the size-proportional estimate cannot quietly return."""
    defined = {
        node.name
        for node in ast.walk(_tree(harness))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not (defined & BANNED_NAMES), (
        f"{harness} reintroduces {sorted(defined & BANNED_NAMES)}. A repo outside the "
        "reference artifact has no measured jCodeMunch cost and must render "
        "'not measured' rather than an estimate."
    )


@pytest.mark.parametrize("harness", COMPARISON_HARNESSES)
def test_harness_reads_the_reference_artifact(harness):
    """The positive half: banning constants means nothing if nothing loads the artifact."""
    source = (HARNESS / harness).read_text(encoding="utf-8")
    assert "reference_entry" in source, (
        f"{harness} no longer consults the reference artifact, so its jCodeMunch "
        "column is coming from somewhere this guard cannot see."
    )


# ---------------------------------------------------------------------------
# The loader's own behaviour
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rb():
    """run_benchmark imports tiktoken and the jcodemunch package; skip if absent.

    The structural guards above deliberately do NOT depend on this — they are
    plain file reads so they run everywhere.
    """
    pytest.importorskip("tiktoken")
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "_bench_run_benchmark", HARNESS / "run_benchmark.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_reference_refuses_a_foreign_schema(rb, tmp_path):
    """An unrecognised artifact must read as absent, never as usable data."""
    bad = tmp_path / "ref.json"
    bad.write_text(json.dumps({"schema": "something-else/v9", "repos": {}}), encoding="utf-8")
    assert rb.load_reference(bad) is None
    assert rb.load_reference(tmp_path / "does-not-exist.json") is None


def test_reference_entry_is_none_for_an_uncovered_repo(rb):
    reference = rb.load_reference(REFERENCE)
    assert reference is not None
    assert rb.reference_entry(reference, "expressjs/express")
    assert rb.reference_entry(reference, "nobody/unmeasured") is None
    assert rb.reference_entry(None, "expressjs/express") is None


def test_reference_drift_names_the_corpus_that_moved(rb):
    entry = {"file_count": 185, "baseline_tokens": 155_960}
    assert rb.reference_drift(entry, 185, 155_960) is None
    drift = rb.reference_drift(entry, 172, 143_355)
    assert drift and "185 -> 172 files" in drift and "baseline" in drift
    # No entry means no comparison at all — not a silent "no drift".
    assert rb.reference_drift(None, 1, 1) is None


# --- The README is the fourth artifact mirroring one benchmark run ---------- #
# CLAUDE.md names four: results.md, METHODOLOGY.md, README.md, and
# benchmarks/provenance/measured.json. test_provenance.py gates three of them.
# The README had no gate, and on 2026-08-02 its header advertised "run
# 2026-07-23" while its own benchmark section 200 lines below said "run
# 2026-07-29, v1.108.199" and jcm_reference.json agreed with the latter. The
# file contradicted itself in public, on PyPI, for the reader most likely to
# check. Re-syncing three artifacts and missing the fourth is the same defect
# in a different costume; the fix is a gate on the fourth, not another
# checklist line.

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"


def _reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def test_readme_benchmark_run_date_matches_the_reference():
    """Every `run YYYY-MM-DD` the README quotes must be the reference's capture
    date. A second date is a second claim, and only one run happened."""
    import re

    ref_date = _reference()["captured_at"][:10]
    quoted = set(re.findall(r"run (\d{4}-\d{2}-\d{2})", README.read_text(encoding="utf-8")))
    assert quoted, "README quotes no benchmark run date; the provenance was dropped"
    assert quoted == {ref_date}, (
        f"README quotes run date(s) {sorted(quoted)} but jcm_reference.json was "
        f"captured {ref_date}. Re-run `run_benchmark.py --reference` or fix the README; "
        f"two dates in one file means at least one is wrong."
    )


def test_readme_benchmark_version_matches_the_reference():
    """The README pins the version the benchmark ran under. It must be the one
    the artifact records, or the number describes a build nobody measured."""
    ref = _reference()
    version = ref["jcodemunch_version"]
    text = README.read_text(encoding="utf-8")
    assert f"v{version}" in text, (
        f"README does not name v{version}, the version jcm_reference.json was "
        f"measured under (captured {ref['captured_at'][:10]})."
    )
