# jcodemunch-mcp -- Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)  
**Workflow:** `search_symbols` (top 5) + `get_symbol` x 3  
**Baseline:** all source files concatenated (minimum for "open every file" agent)  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **182** |
| Symbols extracted | **200** |
| Baseline tokens (all files) | **154,272** |
| Upstream commit | `1faf228935aa` (pinned) |
| Corpus complete | yes |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 154,272 | 1,135 | **99.3%** | 135.9x |
| `middleware` | 154,272 | 1,259 | **99.2%** | 122.5x |
| `error exception` | 154,272 | 1,155 | **99.3%** | 133.6x |
| `request response` | 154,272 | 1,186 | **99.2%** | 130.1x |
| `context bind` | 154,272 | 299 | **99.8%** | 516.0x |
| **Average** | — | — | **99.4%** | **207.6x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 424 | 711 | 3 | 49.5 |
| `middleware` | 362 | 897 | 3 | 4.4 |
| `error exception` | 472 | 683 | 3 | 18.6 |
| `request response` | 474 | 712 | 3 | 5.6 |
| `context bind` | 299 | 0 | 0 | 57.0 |

</details>

## fastapi/fastapi

| Metric | Value |
|--------|-------|
| Files indexed | **1,182** |
| Symbols extracted | **6,841** |
| Baseline tokens (all files) | **823,784** |
| Upstream commit | `a64dfbbd21a4` (pinned) |
| Corpus complete | yes |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 823,784 | 1,657 | **99.8%** | 497.2x |
| `middleware` | 823,784 | 1,963 | **99.8%** | 419.7x |
| `error exception` | 823,784 | 1,198 | **99.9%** | 687.6x |
| `request response` | 823,784 | 5,078 | **99.4%** | 162.2x |
| `context bind` | 823,784 | 1,150 | **99.9%** | 716.3x |
| **Average** | — | — | **99.8%** | **496.6x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 610 | 1,047 | 3 | 873.6 |
| `middleware` | 569 | 1,394 | 3 | 3.3 |
| `error exception` | 525 | 673 | 3 | 3.6 |
| `request response` | 539 | 4,539 | 3 | 18.2 |
| `context bind` | 526 | 624 | 3 | 3.6 |

</details>

## gin-gonic/gin

| Metric | Value |
|--------|-------|
| Files indexed | **98** |
| Symbols extracted | **1,179** |
| Baseline tokens (all files) | **151,842** |
| Upstream commit | `75ccf94d605a` (pinned) |
| Corpus complete | yes |

| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |
|-------|---------------------:|-------------------:|----------:|------:|
| `router route handler` | 151,842 | 1,563 | **99.0%** | 97.1x |
| `middleware` | 151,842 | 1,798 | **98.8%** | 84.5x |
| `error exception` | 151,842 | 1,129 | **99.3%** | 134.5x |
| `request response` | 151,842 | 1,568 | **99.0%** | 96.8x |
| `context bind` | 151,842 | 1,667 | **98.9%** | 91.1x |
| **Average** | — | — | **99.0%** | **100.8x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 537 | 1,026 | 3 | 152.4 |
| `middleware` | 444 | 1,354 | 3 | 17.1 |
| `error exception` | 472 | 657 | 3 | 3.4 |
| `request response` | 687 | 881 | 3 | 3.2 |
| `context bind` | 496 | 1,171 | 3 | 16.2 |

</details>

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline total (15 task-runs) | 5,649,490 |
| jMunch total | 23,805 |
| **Reduction** | **99.6%** |
| **Ratio** | **237.3x** |

> Measured with tiktoken `cl100k_base`. Baseline = all indexed source files. jMunch = search_symbols (top 5) + get_symbol x 3 per query.