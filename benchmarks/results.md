# jcodemunch-mcp -- Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)  
**Workflow:** `search_symbols` (top 5) + `get_symbol` x 3  
**Baseline:** all source files concatenated (minimum for "open every file" agent)  
**Run:** 2026-07-23, jcodemunch-mcp v1.108.163 (repos re-indexed same day)  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **172** |
| Symbols extracted | **182** |
| Baseline tokens (all files) | **143,355** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 143,355 | 1,342 | **99.1%** | 106.8x |
| `middleware` | 143,355 | 1,284 | **99.1%** | 111.6x |
| `error exception` | 143,355 | 1,167 | **99.2%** | 122.8x |
| `request response` | 143,355 | 1,193 | **99.2%** | 120.2x |
| `context bind` | 143,355 | 209 | **99.9%** | 685.9x |
| **Average** | — | — | **99.3%** | **229.5x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 543 | 799 | 3 | 33.8 |
| `middleware` | 495 | 789 | 3 | 1.2 |
| `error exception` | 493 | 674 | 3 | 17.3 |
| `request response` | 527 | 666 | 3 | 2.3 |
| `context bind` | 209 | 0 | 0 | 5.6 |

</details>

## fastapi/fastapi

| Metric | Value |
|--------|-------|
| Files indexed | **1,000** |
| Symbols extracted | **6,722** |
| Baseline tokens (all files) | **823,784** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 823,784 | 1,608 | **99.8%** | 512.3x |
| `middleware` | 823,784 | 1,837 | **99.8%** | 448.4x |
| `error exception` | 823,784 | 1,231 | **99.9%** | 669.2x |
| `request response` | 823,784 | 4,689 | **99.4%** | 175.7x |
| `context bind` | 823,784 | 3,103 | **99.6%** | 265.5x |
| **Average** | — | — | **99.7%** | **414.2x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 605 | 1,003 | 3 | 776.0 |
| `middleware` | 556 | 1,281 | 3 | 1.5 |
| `error exception` | 514 | 717 | 3 | 1.8 |
| `request response` | 548 | 4,141 | 3 | 16.8 |
| `context bind` | 556 | 2,547 | 3 | 1.9 |

</details>

## gin-gonic/gin

| Metric | Value |
|--------|-------|
| Files indexed | **109** |
| Symbols extracted | **1,502** |
| Baseline tokens (all files) | **192,800** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 192,800 | 1,568 | **99.2%** | 123.0x |
| `middleware` | 192,800 | 1,726 | **99.1%** | 111.7x |
| `error exception` | 192,800 | 1,099 | **99.4%** | 175.4x |
| `request response` | 192,800 | 1,530 | **99.2%** | 126.0x |
| `context bind` | 192,800 | 1,634 | **99.2%** | 118.0x |
| **Average** | — | — | **99.2%** | **130.8x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 522 | 1,046 | 3 | 155.2 |
| `middleware` | 441 | 1,285 | 3 | 15.4 |
| `error exception` | 464 | 635 | 3 | 1.8 |
| `request response` | 671 | 859 | 3 | 1.7 |
| `context bind` | 490 | 1,144 | 3 | 18.1 |

</details>

## Real-world A/B test: naming audit task (2026-03-18)

50-iteration test by @Mharbulous comparing JCodeMunch vs native tools (Grep/Glob/Read) on a real Vue 3 + Firebase production codebase. Full report: [ab-test-naming-audit-2026-03-18.md](ab-test-naming-audit-2026-03-18.md)

| Metric | Native | JCodeMunch | Delta |
|--------|--------|------------|-------|
| Success rate | 72% | 80% | +8 pp |
| Timeout rate | 40% | 32% | −8 pp |
| Mean cost/iteration | $0.783 | $0.738 | −5.7% |
| Mean cache creation | 104,135 | 93,178 | −10.5% |

Tool-layer savings (isolated from fixed overhead): **15–25%**

---

## Real-world A/B test: dead code detection task (2026-03-18)

50-iteration test by @Mharbulous comparing JCodeMunch vs native tools on the same Vue 3 + Firebase codebase. Designed to isolate pure tool-layer cost with no subagent overhead. Full report: [ab-test-dead-code-2026-03-18.md](ab-test-dead-code-2026-03-18.md)

| Metric | Native | JCodeMunch | Delta |
|--------|--------|------------|-------|
| Success rate | 96% | 92% | −4 pp |
| Mean cost/iteration | $0.4474 | $0.3560 | −20.0% |
| Mean total tokens | 449,356 | 289,275 | −36% |
| Mean duration (s) | 129 | 117 | −9% |
| File-level F1 (dead files) | 95.8% | 95.7% | equivalent |
| File-level F1 (alive files) | 100.0% | 69.6% | gap |
| Export-level F1 | 93.3% | 64.1% | gap |

**Confirmed tool-layer savings: 20%** (statistically significant, Wilcoxon p=0.0074). Dead file detection is equivalent. Accuracy gaps identified on alive-file classification and export-level analysis; three root causes found and addressed (see report).

Raw data: https://gist.github.com/Mharbulous/bb097396fa92ef1d34d03a72b56b2c61

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline total (15 task-runs) | 5,799,695 |
| jMunch total | 25,220 |
| **Reduction** | **99.6%** |
| **Ratio** | **230.0x** |

> Measured with tiktoken `cl100k_base`. Baseline = all indexed source files. jMunch = search_symbols (top 5) + get_symbol x 3 per query.
