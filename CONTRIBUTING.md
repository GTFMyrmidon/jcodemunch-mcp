# Contributing to jCodeMunch-MCP

Thanks for your interest in contributing! A few things to know before you submit a PR.

## Contributor License Agreement

This project is dual-licensed — free for non-commercial use, with paid licenses for commercial use. To keep that model legally sound, **all contributors must sign the CLA before their PR can be merged.**

The CLA is short and plain-English: you keep your copyright, you grant the project the right to sublicense your contribution commercially, and you confirm the work is yours to submit.

**[Sign the CLA](https://cla-assistant.io/jgravelle/jcodemunch-mcp)**

CLA Assistant will prompt you automatically when you open a PR. It takes about 30 seconds.

## Commercial Licensing

If you're using jCodeMunch in a commercial context, see the [license section in the README](README.md#license-dual-use) for options.

## Getting Started

```bash
git clone https://github.com/jgravelle/jcodemunch-mcp
cd jcodemunch-mcp
pip install -e ".[test]"
pytest tests/ -q
```

## Guidelines

- Open an issue before starting large features. Saves everyone time if direction needs discussion.
- Keep PRs focused; one feature or fix per PR
- Include tests for new functionality
- Run the full test suite before submitting

## One issue, one verdict

**An issue should be a single thing that can be judged true or false and then
closed.** If your report contains several independent findings, please open
several issues, or say so plainly and we will split it at triage.

This is not a request for less detail. Detailed, adversarial, multi-part reports
are some of the most valuable things this project receives, and none of the
scope gets dropped in a split; every part keeps its own thread, its own
reproduction, and its own credit.

It is about how they close. A report with four findings closes only when the
last one is settled, so three finished fixes sit behind one unfinished
conversation and the tracker cannot tell anyone which is which. Split into four,
three close within a day and the fourth is visibly the only thing outstanding.
That is better for you as well: your finished work ships instead of waiting.

What we do at triage:

- Split a multi-finding report into one issue per finding, cross-linked, credit
  on each.
- Keep the original as the parent only if it still has its own verdict. If it is
  purely an index of the others, we close it and say so.
- Accepted design work with no start date does not stay open as an issue at all.
  It moves to [ROADMAP.md](ROADMAP.md) with its close condition verbatim and its
  author credited. Parking is not rejection, and the roadmap says so.

## A release is never blocked on an open issue

**We do not hold a release hostage to an unfinished verification, including a
verification we asked for.**

When work is done, tested, and green, it ships on schedule. If review or
independent re-verification is still outstanding, the release says so in plain
language rather than waiting:

> Verified against the reviewer's pre-registered harness at a frozen SHA. Not
> independently re-verified by its author.

That wording is deliberately weaker than a sign-off and we will not blur the two
in a changelog. When the re-verification lands, whenever it lands, it counts in
full and we announce it retroactively. Nothing expires.

Every timebox we set names its default action, because a date with no stated
consequence is a wish. "Verification by X, or Y ships with disclosure Z."

The point of this rule is that a reviewer's thoroughness should never become a
veto. If being careful can stall a release, then careful review is expensive to
accept, and that is the opposite of what we want. This way your findings are an
upgrade that can arrive at any time, and neither of us is negotiating under a
clock.

## Quality gates that run on every release

- **Schema budget** — `tests/test_schema_budget.py` fails when `tools/list` token count grows more than 5% above `benchmarks/schema_baseline.json`. If you intentionally grow the schema (new tool / longer description), regenerate the baseline in the same PR with justification:
  ```bash
  PYTHONPATH=src python benchmarks/harness/capture_schema_baseline.py
  ```
- **Retrieval-quality replay (v1.76.0+)** — `benchmarks/replay/run_replay.py` runs golden queries through `search_symbols` and reports nDCG@10 / MRR@10 / Recall@10 against the locked v1.75.0 baseline. Any aggregate metric drop > 2% fails the gate:
  ```bash
  PYTHONPATH=src python benchmarks/replay/run_replay.py \
      --fixture benchmarks/replay/fixtures/self_v1_75_0.json \
      --baseline 1.75.0 --gate 0.02
  ```
  If your change legitimately moves a metric, capture a new fixture/baseline and document the reason in the PR description.
