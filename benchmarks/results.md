# jcodemunch-mcp -- Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)  
**Workflow:** `search_symbols` (top 5) + `get_symbol` x 3  
**Baseline:** all source files concatenated (minimum for "open every file" agent)  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **185** |
| Symbols extracted | **200** |
| Baseline tokens (all files) | **155,960** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 155,960 | 1,120 | **99.3%** | 139.2x |
| `middleware` | 155,960 | 1,244 | **99.2%** | 125.4x |
| `error exception` | 155,960 | 1,139 | **99.3%** | 136.9x |
| `request response` | 155,960 | 1,169 | **99.3%** | 133.4x |
| `context bind` | 155,960 | 252 | **99.8%** | 618.9x |
| **Average** | — | — | **99.4%** | **230.8x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 421 | 699 | 3 | 30.3 |
| `middleware` | 359 | 885 | 3 | 1.7 |
| `error exception` | 468 | 671 | 3 | 16.5 |
| `request response` | 467 | 702 | 3 | 2.0 |
| `context bind` | 252 | 0 | 0 | 5.9 |

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
| `router route handler` | 605 | 1,003 | 3 | 747.4 |
| `middleware` | 556 | 1,281 | 3 | 1.5 |
| `error exception` | 514 | 717 | 3 | 1.9 |
| `request response` | 548 | 4,141 | 3 | 18.1 |
| `context bind` | 556 | 2,547 | 3 | 1.8 |

</details>

## gin-gonic/gin

| Metric | Value |
|--------|-------|
| Files indexed | **98** |
| Symbols extracted | **1,179** |
| Baseline tokens (all files) | **151,842** |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 151,842 | 1,548 | **99.0%** | 98.1x |
| `middleware` | 151,842 | 1,783 | **98.8%** | 85.2x |
| `error exception` | 151,842 | 1,113 | **99.3%** | 136.4x |
| `request response` | 151,842 | 1,544 | **99.0%** | 98.3x |
| `context bind` | 151,842 | 1,710 | **98.9%** | 88.8x |
| **Average** | — | — | **99.0%** | **101.4x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 534 | 1,014 | 3 | 111.0 |
| `middleware` | 441 | 1,342 | 3 | 20.2 |
| `error exception` | 468 | 645 | 3 | 1.7 |
| `request response` | 684 | 860 | 3 | 1.8 |
| `context bind` | 493 | 1,217 | 3 | 19.3 |

</details>

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline total (15 task-runs) | 5,657,930 |
| jMunch total | 25,090 |
| **Reduction** | **99.6%** |
| **Ratio** | **225.5x** |

> Measured with tiktoken `cl100k_base`. Baseline = all indexed source files. jMunch = search_symbols (top 5) + get_symbol x 3 per query.