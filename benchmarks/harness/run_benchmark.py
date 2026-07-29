#!/usr/bin/env python3
"""jcodemunch-mcp benchmark harness.

Measures real (tiktoken cl100k_base) token counts for the jcodemunch
retrieval workflow vs an "open every file" baseline on identical tasks.

Usage:
    python benchmarks/harness/run_benchmark.py [owner/repo ...]
    python benchmarks/harness/run_benchmark.py --out results.md

If no repos are given, runs against all three canonical benchmark repos:
    expressjs/express  |  fastapi/fastapi  |  gin-gonic/gin

Those repos must already be indexed (jcodemunch index_repo owner/repo).

Methodology
-----------
Baseline:   concatenate all raw source files stored in the index, count tokens.
            This is the minimum tokens a "read everything first" agent would use.

jMunch:     for each task query —
              1. call search_symbols (max_results=5)     → count JSON response tokens
              2. call get_symbol for the top 3 hits      → count each source snippet
            Total = search response + 3 × symbol source.

Tokenizer:  tiktoken cl100k_base (GPT-4 / Claude family ballpark; consistent
            across runs regardless of model).

Output:     per-task rows + per-repo summary + grand summary table (markdown).

Reference artifact
------------------
`--reference` writes `benchmarks/jcm_reference.json`: the machine-readable
jCodeMunch side of every comparison harness in this directory. The comparison
harnesses (`run_rag_baseline.py`, `run_odysseus_compare.py`) READ that file
instead of carrying hand-typed constants, and refuse to print a ratio for a
repo the artifact does not cover.

Each repo entry records the index state it was measured against (file count,
baseline tokens). A comparison harness that measures a different index state
labels the row cross-run rather than dividing a fresh number by a stale one.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap: add src/ to path so we can import jcodemunch_mcp directly
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    import tiktoken
except ImportError:
    sys.exit("tiktoken not found — run: pip install tiktoken")

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.search_symbols import search_symbols
from jcodemunch_mcp.tools.get_symbol import get_symbol_source as get_symbol

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task corpus — loaded from benchmarks/tasks.json if present, else hardcoded
# ---------------------------------------------------------------------------

_CORPUS_PATH = _REPO_ROOT / "benchmarks" / "tasks.json"

def _load_corpus():
    if _CORPUS_PATH.exists():
        import json as _json
        corpus = _json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
        repos = [r["id"] for r in corpus.get("repos", [])]
        tasks = [t["query"] for t in corpus.get("tasks", [])]
        return repos, tasks
    # Fallback hardcoded values (kept in sync with tasks.json)
    return (
        ["expressjs/express", "fastapi/fastapi", "gin-gonic/gin"],
        ["router route handler", "middleware", "error exception", "request response", "context bind"],
    )

DEFAULT_REPOS, TASKS = _load_corpus()

SEARCH_MAX_RESULTS = 5
SYMBOLS_FETCHED = 3        # get_symbol calls per query in the jMunch workflow

TOKENIZER = "cl100k_base"  # tiktoken encoding name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_enc = tiktoken.get_encoding(TOKENIZER)


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _serialize(obj) -> str:
    """Stable JSON serialization of a tool response."""
    return json.dumps(obj, separators=(",", ":"), default=str)


def _parse_repo(repo_str: str) -> tuple[str, str]:
    """Split 'owner/repo' → (owner, repo). Handles 'local/name' too."""
    parts = repo_str.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "local", parts[0]


# ---------------------------------------------------------------------------
# Baseline measurement
# ---------------------------------------------------------------------------

def measure_baseline(store: IndexStore, owner: str, name: str) -> dict:
    """Count tokens across ALL raw source files stored in the index."""
    content_dir = store._content_dir(owner, name)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"Not indexed: {owner}/{name}"}

    total_tokens = 0
    file_count = 0
    for rel_path in index.source_files:
        abs_path = content_dir / Path(rel_path.replace("/", "\\") if sys.platform == "win32" else rel_path)
        # Try both path separator forms
        if not abs_path.exists():
            abs_path = content_dir / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                # Store-backed fallback (the real reader; the old
                # get_file_content_text name never existed → dead fallback).
                content = store.get_file_content(owner, name, rel_path, index) or ""
            except Exception:
                content = ""
        total_tokens += count_tokens(content)
        file_count += 1

    return {"tokens": total_tokens, "files": file_count}


# ---------------------------------------------------------------------------
# jMunch workflow measurement
# ---------------------------------------------------------------------------

def measure_jmunch(repo_str: str, query: str) -> dict:
    """
    jMunch workflow for one query:
      1. search_symbols → serialize response → count tokens
      2. get_symbol for top N hits → count tokens
    """
    # 1. Search
    t0 = time.perf_counter()
    # v1.70.0: pin detail_level="standard" so benchmark numbers stay comparable
    # to runs before the auto-default flip (baseline methodology preserved).
    search_result = search_symbols(repo=repo_str, query=query, max_results=SEARCH_MAX_RESULTS, detail_level="standard")
    search_ms = (time.perf_counter() - t0) * 1000

    search_text = _serialize(search_result)
    search_tokens = count_tokens(search_text)

    # Extract symbol IDs from results
    symbols = search_result.get("results") or search_result.get("symbols") or []
    symbol_ids = [s.get("id") or s.get("symbol_id") for s in symbols if s.get("id") or s.get("symbol_id")]
    symbol_ids = symbol_ids[:SYMBOLS_FETCHED]

    # 2. Get symbol sources
    fetch_tokens = 0
    for sid in symbol_ids:
        sym_result = get_symbol(repo=repo_str, symbol_id=sid)
        fetch_tokens += count_tokens(_serialize(sym_result))

    total = search_tokens + fetch_tokens
    return {
        "tokens": total,
        "search_tokens": search_tokens,
        "fetch_tokens": fetch_tokens,
        "hits_fetched": len(symbol_ids),
        "search_ms": round(search_ms, 1),
    }


# ---------------------------------------------------------------------------
# Per-repo benchmark
# ---------------------------------------------------------------------------

def benchmark_repo(repo_str: str) -> dict:
    store = IndexStore()
    owner, name = _parse_repo(repo_str)

    # Resolve: might be stored as local/name with hash suffix
    all_repos = store.list_repos()
    matched = None
    for r in all_repos:
        if r["repo"] == repo_str:
            matched = r
            break
    # Fallback: try display_name match
    if matched is None:
        for r in all_repos:
            display = r.get("display_name", "")
            if display and f"{owner}/{display}" == repo_str:
                matched = r
                break

    if matched is None:
        return {"error": f"Not indexed: {repo_str}. Run: jcodemunch index_repo {repo_str}"}

    actual_repo = matched["repo"]
    owner2, name2 = _parse_repo(actual_repo)

    # Baseline (compute once for the repo)
    baseline = measure_baseline(store, owner2, name2)
    if "error" in baseline:
        return baseline

    baseline_tokens = baseline["tokens"]
    file_count = baseline["files"]
    symbol_count = matched.get("symbol_count", 0)

    task_rows = []
    for query in TASKS:
        jm = measure_jmunch(actual_repo, query)
        if "error" in jm:
            task_rows.append({"query": query, "error": jm["error"]})
            continue
        reduction_pct = (1 - jm["tokens"] / baseline_tokens) * 100 if baseline_tokens > 0 else 0
        ratio = baseline_tokens / jm["tokens"] if jm["tokens"] > 0 else float("inf")
        task_rows.append({
            "query": query,
            "baseline_tokens": baseline_tokens,
            "jmunch_tokens": jm["tokens"],
            "reduction_pct": round(reduction_pct, 1),
            "ratio": round(ratio, 1),
            "search_tokens": jm["search_tokens"],
            "fetch_tokens": jm["fetch_tokens"],
            "hits_fetched": jm["hits_fetched"],
            "search_ms": jm["search_ms"],
        })

    return {
        "repo": repo_str,
        "display": matched.get("display_name", name2),
        "file_count": file_count,
        "symbol_count": symbol_count,
        "baseline_tokens": baseline_tokens,
        "tasks": task_rows,
    }


# ---------------------------------------------------------------------------
# Reference artifact — the jCodeMunch side of every comparison harness
# ---------------------------------------------------------------------------

REFERENCE_SCHEMA = "jcm-benchmark-reference/v1"
REFERENCE_PATH = _REPO_ROOT / "benchmarks" / "jcm_reference.json"


def _jcm_version() -> str:
    """Version of the tree being measured, not whatever happens to be installed.

    The harness runs against `src/` via the path bootstrap above, so importlib
    metadata can name a different build (or nothing at all) — read pyproject
    first and keep the installed distribution as the fallback.
    """
    try:
        import re

        text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        from importlib.metadata import version

        return version("jcodemunch-mcp")
    except Exception:
        return "unknown"


def build_reference(results: list[dict]) -> dict:
    """Machine-readable jCodeMunch measurements for the comparison harnesses.

    Every number here is measured by this run. Nothing is estimated, and a repo
    that errored is omitted rather than carried forward from an older artifact —
    a comparison harness must be able to tell "not measured" from "measured low".
    """
    repos: dict[str, dict] = {}
    grand_baseline = grand_jmunch = task_runs = 0

    for res in results:
        if "error" in res:
            continue
        valid = [t for t in res["tasks"] if "error" not in t]
        if not valid:
            continue
        jmunch_total = sum(t["jmunch_tokens"] for t in valid)
        repos[res["repo"]] = {
            "avg_tokens_per_query": round(jmunch_total / len(valid)),
            "jmunch_total_tokens": jmunch_total,
            "queries": len(valid),
            # Index state this was measured against. A comparison harness that
            # measures different values is looking at a different corpus.
            "baseline_tokens": res["baseline_tokens"],
            "file_count": res["file_count"],
            "symbol_count": res.get("symbol_count", 0),
        }
        grand_baseline += sum(t["baseline_tokens"] for t in valid)
        grand_jmunch += jmunch_total
        task_runs += len(valid)

    return {
        "schema": REFERENCE_SCHEMA,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "jcodemunch_version": _jcm_version(),
        "tokenizer": TOKENIZER,
        "workflow": f"search_symbols(max_results={SEARCH_MAX_RESULTS}) + get_symbol_source x{SYMBOLS_FETCHED}",
        "baseline_definition": "all indexed source files concatenated",
        "grand": {
            "baseline_tokens": grand_baseline,
            "jmunch_tokens": grand_jmunch,
            "task_runs": task_runs,
        },
        "repos": repos,
    }


MEASURED_JSON_PATH = _REPO_ROOT / "benchmarks" / "provenance" / "measured.json"


def sync_measured_artifact(reference: dict, path: Path = MEASURED_JSON_PATH) -> bool:
    """Rewrite the `token_reduction` block of the provenance artifact from this run.

    `benchmarks/provenance/measured.json` backs every `basis="measured"` entry in
    the provenance registry, and its own header says never to hand-edit a number
    without re-running the cited benchmark. It was hand-edited anyway, because
    nothing connected it to the run — so a re-measure updated METHODOLOGY.md and
    left this file asserting the previous run's totals, and the CI drift guard
    (tests/test_provenance.py) failed.

    Only `token_reduction` is touched. Every other block is backed by a different
    artifact and a different run; rewriting them here would be the same mistake
    pointed the other way.
    """
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if "token_reduction" not in artifact:
        return False

    grand = reference["grand"]
    baseline, jmunch = grand["baseline_tokens"], grand["jmunch_tokens"]
    block = artifact["token_reduction"]
    block["average_pct"] = round((1 - jmunch / baseline) * 100, 1) if baseline else 0.0
    block["task_runs"] = grand["task_runs"]
    block["baseline_tokens"] = baseline
    block["jcodemunch_tokens"] = jmunch
    block["tokenizer"] = reference["tokenizer"]
    block["run_date"] = reference["captured_at"][:10]
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return True


def load_reference(path: Path = REFERENCE_PATH) -> Optional[dict]:
    """Read the committed reference artifact, or None when it is absent/unusable.

    None is an honest answer and callers must render it as "not measured".
    Never substitute an estimate — that is the defect this artifact exists to
    close.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("schema") != REFERENCE_SCHEMA:
        return None
    if not isinstance(data.get("repos"), dict):
        return None
    return data


def reference_entry(reference: Optional[dict], repo: str) -> Optional[dict]:
    """The reference row for `repo`, or None when this run cannot be compared."""
    if not reference:
        return None
    entry = reference["repos"].get(repo)
    if not isinstance(entry, dict) or not entry.get("avg_tokens_per_query"):
        return None
    return entry


def reference_drift(entry: Optional[dict], file_count: int, baseline_tokens: int) -> Optional[str]:
    """Describe how this run's corpus differs from the reference measurement.

    Returns None when the two agree. A non-None result means the comparison is
    cross-run: the caller must say so rather than divide one against the other
    silently.
    """
    if not entry:
        return None
    deltas = []
    ref_files = entry.get("file_count")
    ref_baseline = entry.get("baseline_tokens")
    if ref_files and ref_files != file_count:
        deltas.append(f"{ref_files} -> {file_count} files")
    if ref_baseline and ref_baseline != baseline_tokens:
        deltas.append(f"baseline {ref_baseline:,} -> {baseline_tokens:,} tokens")
    return "; ".join(deltas) or None


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(results: list[dict], tokenizer: str) -> str:
    lines = []
    lines.append("# jcodemunch-mcp -- Token Efficiency Benchmark")
    lines.append("")
    lines.append(f"**Tokenizer:** `{tokenizer}` (tiktoken)  ")
    lines.append(f"**Workflow:** `search_symbols` (top {SEARCH_MAX_RESULTS}) + `get_symbol` x {SYMBOLS_FETCHED}  ")
    lines.append(f"**Baseline:** all source files concatenated (minimum for \"open every file\" agent)  ")
    lines.append("")

    grand_baseline = 0
    grand_jmunch = 0
    grand_tasks = 0

    for res in results:
        if "error" in res:
            lines.append(f"## {res.get('repo', '?')} — ERROR")
            lines.append(f"> {res['error']}")
            lines.append("")
            continue

        repo = res["repo"]
        lines.append(f"## {repo}")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Files indexed | **{res['file_count']:,}** |")
        lines.append(f"| Symbols extracted | **{res['symbol_count']:,}** |")
        lines.append(f"| Baseline tokens (all files) | **{res['baseline_tokens']:,}** |")
        lines.append("")

        lines.append("| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |")
        lines.append("|-------|---------------------:|-------------------:|----------:|------:|")

        repo_jmunch_sum = 0
        valid_tasks = [t for t in res["tasks"] if "error" not in t]
        for t in valid_tasks:
            lines.append(
                f"| `{t['query']}` "
                f"| {t['baseline_tokens']:,} "
                f"| {t['jmunch_tokens']:,} "
                f"| **{t['reduction_pct']}%** "
                f"| {t['ratio']}x |"
            )
            repo_jmunch_sum += t["jmunch_tokens"]
            grand_jmunch += t["jmunch_tokens"]
            grand_baseline += t["baseline_tokens"]
            grand_tasks += 1

        if valid_tasks:
            avg_reduction = sum(t["reduction_pct"] for t in valid_tasks) / len(valid_tasks)
            avg_ratio = sum(t["ratio"] for t in valid_tasks) / len(valid_tasks)
            lines.append(
                f"| **Average** | — | — "
                f"| **{avg_reduction:.1f}%** "
                f"| **{avg_ratio:.1f}x** |"
            )
        lines.append("")

        # Detail table
        lines.append("<details><summary>Query detail (search + fetch tokens, latency)</summary>")
        lines.append("")
        lines.append("| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |")
        lines.append("|-------|-----------------:|------------------:|------------------:|---------------:|")
        for t in valid_tasks:
            lines.append(
                f"| `{t['query']}` "
                f"| {t['search_tokens']:,} "
                f"| {t['fetch_tokens']:,} "
                f"| {t['hits_fetched']} "
                f"| {t['search_ms']} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Grand summary
    if grand_tasks > 0:
        grand_reduction = (1 - grand_jmunch / grand_baseline) * 100
        grand_ratio = grand_baseline / grand_jmunch
        lines.append("---")
        lines.append("")
        lines.append("## Grand Summary")
        lines.append("")
        lines.append("| | Tokens |")
        lines.append("|--|-------:|")
        lines.append(f"| Baseline total ({grand_tasks} task-runs) | {grand_baseline:,} |")
        lines.append(f"| jMunch total | {grand_jmunch:,} |")
        lines.append(f"| **Reduction** | **{grand_reduction:.1f}%** |")
        lines.append(f"| **Ratio** | **{grand_ratio:.1f}x** |")
        lines.append("")
        lines.append(
            f"> Measured with tiktoken `{tokenizer}`. "
            "Baseline = all indexed source files. "
            f"jMunch = search_symbols (top {SEARCH_MAX_RESULTS}) + "
            f"get_symbol x {SYMBOLS_FETCHED} per query."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repos", nargs="*", help="owner/repo to benchmark (default: all 3 canonical repos)")
    parser.add_argument("--out", metavar="FILE", help="write markdown results to FILE")
    parser.add_argument("--json", metavar="FILE", dest="json_out", help="write raw JSON results to FILE")
    parser.add_argument(
        "--reference",
        nargs="?",
        const=str(REFERENCE_PATH),
        metavar="FILE",
        help=(
            "write the machine-readable reference artifact the comparison harnesses read "
            f"(default: {REFERENCE_PATH.relative_to(_REPO_ROOT).as_posix()})"
        ),
    )
    args = parser.parse_args()

    repos = args.repos or DEFAULT_REPOS

    print(f"jcodemunch-mcp benchmark harness  |  tokenizer: {TOKENIZER}", flush=True)
    print(f"Repos: {', '.join(repos)}", flush=True)
    print(f"Tasks: {len(TASKS)} queries × {len(repos)} repos = {len(TASKS) * len(repos)} measurements", flush=True)
    print()

    results = []
    for repo in repos:
        print(f"  benchmarking {repo} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        res = benchmark_repo(repo)
        elapsed = time.perf_counter() - t0
        if "error" in res:
            print(f"ERROR: {res['error']}")
        else:
            valid = [t for t in res["tasks"] if "error" not in t]
            avg_r = sum(t["reduction_pct"] for t in valid) / len(valid) if valid else 0
            print(f"done ({elapsed:.1f}s)  avg reduction {avg_r:.1f}%")
        results.append(res)

    print()
    md = render_markdown(results, TOKENIZER)
    print(md)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\nResults written to: {args.out}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"JSON written to: {args.json_out}")

    if args.reference:
        ref_path = Path(args.reference)
        if len(repos) < len(DEFAULT_REPOS):
            # Writing a partial run over the artifact would silently drop repos
            # the comparison harnesses still need, and a missing repo reads as
            # "not measured" forever after.
            print(
                f"\nRefusing to write {ref_path}: benchmarked {len(repos)} repo(s), "
                f"artifact covers {len(DEFAULT_REPOS)}. Re-run without a repo filter.",
                file=sys.stderr,
            )
            return 1
        reference = build_reference(results)
        ref_path.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
        print(f"Reference artifact written to: {ref_path}")
        if sync_measured_artifact(reference):
            print(f"Provenance artifact updated: {MEASURED_JSON_PATH}")
            print(
                "  Re-sync the prose mirrors of this run "
                "(benchmarks/results.md, benchmarks/METHODOLOGY.md, README.md) "
                "or tests/test_provenance.py will fail."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
