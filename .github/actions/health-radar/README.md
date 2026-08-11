# jCodeMunch Health Radar — GitHub Action

A composite action that computes a six-axis code-health radar on the PR
branch and the base branch, diffs them, and posts the result as a
**sticky PR comment**. Suggestion-style — never blocks merges.

## What it does

1. Indexes the PR branch with `jcodemunch-mcp index .`
2. Runs `jcodemunch-mcp health . --radar-only` → PR radar JSON
3. Checks out the base branch, re-indexes, runs the same command → base
   radar JSON
4. Computes the diff via the same pure helper exposed as the
   `diff_health_radar` MCP tool
5. Renders a markdown table + axis movements + regression / improvement
   bullets
6. Finds an existing sticky comment by HTML marker — `PATCH`es it on
   re-runs, `POST`s a new one on the first run.

## Why a comment, not a status check

Status checks block merges. A heuristic that blocks merges gets the
Action disabled by the first frustrated maintainer. The radar comment
is **explanatory, not gating** — reviewers see the deltas, decide for
themselves.

## Usage

```yaml
# .github/workflows/health-radar.yml
name: Health Radar
on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  pull-requests: write   # for sticky comment
  contents: read         # for git checkout

jobs:
  radar:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          # Full history. churn_surface is measured from `git log`, so a
          # shallow checkout changes what the number means. The action
          # unshallows both sides itself, but doing it here is cheaper.
          fetch-depth: 0
      - uses: jgravelle/jcodemunch-mcp/.github/actions/health-radar@health-radar-v1.0.0
```

That's the whole setup. The action handles install, index, base/branch
toggling, and comment posting itself.

## Versioning

The action is tagged in its own `health-radar-vX.Y.Z` namespace, separate
from the `vX.Y.Z` package release tags. A package release does not imply
an action change, and this action's behaviour should not shift because
the Python package shipped a patch.

Two ways to reference it, and the tradeoff is the usual one:

```yaml
# Immutable. Never changes under you. You update deliberately.
- uses: jgravelle/jcodemunch-mcp/.github/actions/health-radar@health-radar-v1.0.1

# Floating. Tracks the newest 1.x, so fixes arrive without a pin bump,
# and so does anything else that lands.
- uses: jgravelle/jcodemunch-mcp/.github/actions/health-radar@health-radar-v1
```

`health-radar-v1` always points at the newest `health-radar-v1.Y.Z`. It is
the only tag in this namespace that moves, and choosing it is opting into
that. If you want to audit what you run, take the immutable pin.

⚠ The floating tag is a maintenance obligation, not a free convenience.
It is worth naming what it costs: it has to be moved by hand on every
action change, and nothing fails if that is forgotten. Tags are not
reliably present in a CI checkout, so a guard test would be either
skippable or flaky, and a guard nobody can see fail is one nobody should
believe. The instructions live at the top of `action.yml`, where whoever
changes the file will see them.

⚠ **`@v1.88.0` is superseded and should not be used.** It fetched the base
branch with `git fetch --depth=1`, which shortens an already complete clone
rather than merely limiting a download. `churn_surface` is
`complexity x log(1 + churn)` with churn counted by `git log --since=<N>
days ago`, so the base saw one commit, scored every file at churn <= 1, and
came back artificially healthy. Every PR was then charged for the gap.
Measured at a single commit with identical trees on both sides: shallow
82.2 (B) against full 75.5 (C), with `churn_surface` the only axis that
moved. If you pinned `@v1.88.0`, every regression verdict it posted on
`churn_surface` is suspect.

That tag is deliberately not moved. Repointing a published tag at different
code is worse than leaving a known-bad one in place, because it breaks the
one guarantee pinning offers.

## Inputs

| Input | Default | Description |
|---|---|---|
| `python-version` | `3.11` | Python version on the runner. |
| `jcodemunch-version` | `latest` | Pin a specific package version, or `latest`. |
| `base-ref` | _(PR's base branch)_ | Override the comparison ref. |
| `github-token` | `${{ github.token }}` | Token used to post/edit the comment. |

## Output shape

The sticky comment looks like:

```markdown
<!-- jcm-health-radar -->
## jCodeMunch Health Radar

🟡 **Composite:** B → C (-7.5 pts)
🔴 **Verdict:** REGRESSION on 2 axis/axes (composite -7.5)

| Axis | Baseline | PR | Δ |
|---|---:|---:|---:|
| `complexity`     | 88 | 64 | **-24.0** ↓ |
| `dead_code`      | 82 | 79 | -3.0 ↓ |
| `cycles`         | 100 | 100 | +0.0 · |
...

### Regressions
- `complexity`: raw 4.5 → 11.0
- `dead_code`: raw 4.5 → 5.7
```

## Methodology

Per-axis scoring rules and rationale: see
[`tools/health_radar.py`](https://github.com/jgravelle/jcodemunch-mcp/blob/main/src/jcodemunch_mcp/tools/health_radar.py).

The composite is the arithmetic mean of every scored axis;
`omitted_axes` lists axes whose underlying signals weren't available
(e.g. `test_gap` if `get_untested_symbols` couldn't run).

## Known caveats

- **Re-indexes on both branches** — adds runtime in CI, scaling roughly
  with codebase size. For very large repos, the baseline could be
  cached by base SHA in a follow-up release.
- **Heuristic, not coverage data** — `test_gap` is import-graph
  reachability + name matching. It catches "this function isn't
  referenced by anything in `tests/`," not runtime line coverage.
- **`coupling` axis penalises high import fan-out**, which can be
  legitimate in framework-style codebases. Treat the absolute number
  as suggestive; the *delta* is what matters at PR time.
- **`churn_surface` needs full git history on both sides.** It is
  `complexity x log(1 + churn)`, and churn is counted by
  `git log --since=<N> days ago`. A shallow checkout collapses churn to
  at most 1 per file, so whichever side is shallow scores artificially
  healthy and the *other* side reads as a regression. The action now
  unshallows both sides itself, but `fetch-depth: 0` in your checkout
  step is still the cheaper way to get there.

## Disabling

Just remove the workflow file. The action stores no state outside the
runner; no cleanup is required.
