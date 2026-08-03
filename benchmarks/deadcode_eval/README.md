# Dead-code false-positive evaluation

A **re-runnable, deterministic, agent-free** measurement of how often
`get_dead_code_v2` calls provably-live code dead.

## Why this exists

The only labeled ground truth we had for dead-code detection was
[`../ab-test-dead-code-2026-03-18.md`](../ab-test-dead-code-2026-03-18.md)
(@Mharbulous, [#130](https://github.com/jgravelle/jcodemunch-mcp/issues/130)).
It is a good measurement and it cannot be re-run: it is an agent-in-the-loop A/B
over 50 iterations against a private Vue codebase, it costs real money, and it
was taken against a jcm five months older than today's.

That left the accuracy of every subsequent change to the detector unfalsifiable,
which is how
[#408](https://github.com/jgravelle/jcodemunch-mcp/issues/408) came to ship with
"directionally supported and not verified" as the honest summary of its evidence.

## The oracle: coverage is one-sided, and that is the point

**Anything the test suite executes is alive.** Not "probably alive", not "alive
by heuristic" — it ran. That makes coverage an unimpeachable oracle for one of
the two classes, with no second heuristic grading the first.

So this harness measures exactly one thing:

> Of the symbols `get_dead_code_v2` reports as dead, how many did we watch
> execute?

Every such symbol is a **definite** false positive. No judgement call, no
name-matching, no sampling.

⚠⚠ **It deliberately does NOT measure recall, and the absence of coverage is
NOT evidence of deadness.** A symbol with zero hits may be perfectly alive and
merely untested — that is the common case in most repositories. Reporting a
"recall" from this data would be inventing the other half of a confusion matrix
out of an oracle that cannot see it. If you want recall, you need a different
instrument, and it will not be free.

The one-sidedness is a feature here rather than a limitation, because the
March A/B says our deficit is specifically on the alive class: dead-file
detection was already at parity with native tooling (95.7% vs 95.8% F1) while
alive classification sat at 69.6% against a native 100%.

## Running it

```bash
# 1. Collect the oracle (slow: runs the whole suite)
PYTHONPATH=src python -m pytest tests/ -q \
    --cov=src/jcodemunch_mcp --cov-report=json:benchmarks/deadcode_eval/coverage.json

# 2. Measure (fast, no agent, no network, no cost)
PYTHONPATH=src python benchmarks/deadcode_eval/run_eval.py \
    --coverage benchmarks/deadcode_eval/coverage.json \
    --repo . \
    --compare 0.90 0.95 1.0
```

`--compare` evaluates several `degeneracy_cutoff` values **in the same run,
against the same oracle**, so the before and after are never measured against
different corpora or different index states.

## Reading the output

| column | meaning |
| --- | --- |
| `flagged` | symbols reported dead at this cutoff |
| `proven_alive` | symbols in the oracle (executed at least once) |
| `false_pos` | flagged AND executed — **definite** errors |
| `fp_rate` | `false_pos / flagged`; the headline |
| `alive_caught` | `false_pos / proven_alive`; share of provably-live code accused |

⚠ `fp_rate` is a **lower bound on the error rate**, not an estimate of it. A
flagged symbol that is alive but untested is invisible to this oracle and is
counted as if it were correct. The true false-positive rate is at least this
high and probably higher.

⚠ A cutoff that returns nothing scores a vacuous `fp_rate` of 0. Read `flagged`
alongside it or a tool that always returns the empty set looks perfect.
