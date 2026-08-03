# jcodemunch-mcp -- Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)  
**Workflow:** `search_symbols` (top 5) + `get_symbol` x 3  
**Baseline A (read-all):** all source files concatenated  
**Baseline B (grep-top-3):** `rg -l` the query terms, then open the top 3 files whole  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **182** |
| Symbols extracted | **200** |
| Baseline tokens (all files) | **154,272** |
| Upstream commit | `1faf228935aa` (pinned) |
| Corpus complete | yes |

| Query | Read-all&nbsp;tokens | Grep-top-3&nbsp;tokens | jMunch&nbsp;tokens | Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |
|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|
| `router route handler` | 154,272 | 9,890 | 1,135 | 135.9x | **8.71x** |
| `middleware` | 154,272 | 11,777 | 1,259 | 122.5x | **9.35x** |
| `error exception` | 154,272 | 18,961 | 1,155 | 133.6x | **16.42x** |
| `request response` | 154,272 | 21,620 | 1,186 | 130.1x | **18.23x** |
| `context bind` | 154,272 | 16,371 | 299 | 516.0x | **54.75x** |
| **Average** | — | — | — | 207.6x | **21.5x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 424 | 711 | 3 | 93.3 |
| `middleware` | 362 | 897 | 3 | 5.7 |
| `error exception` | 472 | 683 | 3 | 39.1 |
| `request response` | 474 | 712 | 3 | 5.5 |
| `context bind` | 299 | 0 | 0 | 59.3 |

</details>

## fastapi/fastapi

| Metric | Value |
|--------|-------|
| Files indexed | **1,182** |
| Symbols extracted | **6,841** |
| Baseline tokens (all files) | **823,784** |
| Upstream commit | `a64dfbbd21a4` (pinned) |
| Corpus complete | yes |

| Query | Read-all&nbsp;tokens | Grep-top-3&nbsp;tokens | jMunch&nbsp;tokens | Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |
|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|
| `router route handler` | 823,784 | 97,495 | 1,657 | 497.2x | **58.84x** |
| `middleware` | 823,784 | 36,575 | 1,963 | 419.7x | **18.63x** |
| `error exception` | 823,784 | 100,987 | 1,198 | 687.6x | **84.3x** |
| `request response` | 823,784 | 130,461 | 5,078 | 162.2x | **25.69x** |
| `context bind` | 823,784 | 60,963 | 1,150 | 716.3x | **53.01x** |
| **Average** | — | — | — | 496.6x | **48.1x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 610 | 1,047 | 3 | 917.2 |
| `middleware` | 569 | 1,394 | 3 | 4.2 |
| `error exception` | 525 | 673 | 3 | 4.4 |
| `request response` | 539 | 4,539 | 3 | 20.6 |
| `context bind` | 526 | 624 | 3 | 4.6 |

</details>

## gin-gonic/gin

| Metric | Value |
|--------|-------|
| Files indexed | **98** |
| Symbols extracted | **1,179** |
| Baseline tokens (all files) | **151,842** |
| Upstream commit | `75ccf94d605a` (pinned) |
| Corpus complete | yes |

| Query | Read-all&nbsp;tokens | Grep-top-3&nbsp;tokens | jMunch&nbsp;tokens | Ratio&nbsp;vs&nbsp;read-all | **Ratio&nbsp;vs&nbsp;grep** |
|-------|---------------------:|----------------------:|-------------------:|--------------------------:|--------------------------:|
| `router route handler` | 151,842 | 18,789 | 1,563 | 97.1x | **12.02x** |
| `middleware` | 151,842 | 13,190 | 1,798 | 84.5x | **7.34x** |
| `error exception` | 151,842 | 44,021 | 1,129 | 134.5x | **38.99x** |
| `request response` | 151,842 | 39,929 | 1,568 | 96.8x | **25.46x** |
| `context bind` | 151,842 | 43,946 | 1,667 | 91.1x | **26.36x** |
| **Average** | — | — | — | 100.8x | **22.0x** |

<details><summary>Query detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |
|-------|-----------------:|------------------:|------------------:|---------------:|
| `router route handler` | 537 | 1,026 | 3 | 157.8 |
| `middleware` | 444 | 1,354 | 3 | 22.6 |
| `error exception` | 472 | 657 | 3 | 4.1 |
| `request response` | 687 | 881 | 3 | 4.4 |
| `context bind` | 496 | 1,171 | 3 | 28.4 |

</details>

---

## Grand Summary

| | Tokens |
|--|-------:|
| Baseline A total, read-all (15 task-runs) | 5,649,490 |
| Baseline B total, grep-top-3 | 664,975 |
| jMunch total | 23,805 |
| Reduction vs read-all | 99.6% |
| Ratio vs read-all | 237.3x |
| **Reduction vs grep-top-3** | **96.4%** |
| **Ratio vs grep-top-3** | **27.9x** |

> **Baseline B is the number to quote.** Read-all is a ceiling nobody pays: it assumes an agent opens every file in the repository before acting. Grep-then-read is what a competent agent without this tool actually does, and it is 11.8% of the read-all figure — so measuring against read-all overstates the advantage by about 8x.

> Measured with tiktoken `cl100k_base`. Read-all = every indexed source file. Grep-top-3 = `rg -l` the query terms, then open the top 3 matching files whole. jMunch = search_symbols (top 5) + get_symbol x 3 per query. Both baselines are measured in THIS run against THIS corpus.