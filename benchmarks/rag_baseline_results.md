# LangChain RAG Baseline Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)  
**Embeddings:** `sentence-transformers/all-MiniLM-L6-v2` (sentence-transformers)  
**Vector store:** FAISS (faiss-cpu, in-memory)  
**Retrieval:** similarity search, k=5, top 3 used  
**Chunk sizes tested:** 512, 1024, 2048 tokens, ~10% overlap  

## expressjs/express

| Metric | Value |
|--------|-------|
| Files indexed | **185** |
| Baseline tokens (all files) | **155,960** |
| Chunks (size 512) | 442 |
| Chunks (size 1024) | 285 |
| Chunks (size 2048) | 222 |

### Chunk size: 512 tokens

*442 chunks | embed 5.4s | total build 5.63s | FAISS 1,341 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 4,880 | 155,960 | 96.9% | 32.0x | 1/3 | 0/3 |
| `middleware` | 2,089 | 155,960 | 98.7% | 74.7x | 0/3 | 0/3 |
| `error exception` | 1,403 | 155,960 | 99.1% | 111.2x | 0/3 | 0/3 |
| `request response` | 3,607 | 155,960 | 97.7% | 43.2x | 0/3 | 0/3 |
| `context bind` | 3,827 | 155,960 | 97.5% | 40.8x | 0/3 | 1/3 |
| **Average** | — | — | **98.0%** | **60.4x** | **0.2** | **0.2** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 3,077 | 1,803 | 3/3 | 3/3 | 18.3 |
| `middleware` | 1,479 | 610 | 3/3 | 2/3 | 8.4 |
| `error exception` | 1,104 | 299 | 3/3 | 1/3 | 7.5 |
| `request response` | 2,218 | 1,389 | 3/3 | 2/3 | 8.2 |
| `context bind` | 2,522 | 1,305 | 0/3 | 3/3 | 8.5 |

</details>

### Chunk size: 1024 tokens

*285 chunks | embed 3.92s | total build 4.18s | FAISS 1,093 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 9,480 | 155,960 | 93.9% | 16.5x | 0/3 | 0/3 |
| `middleware` | 5,586 | 155,960 | 96.4% | 27.9x | 0/3 | 0/3 |
| `error exception` | 1,984 | 155,960 | 98.7% | 78.6x | 0/3 | 0/3 |
| `request response` | 3,884 | 155,960 | 97.5% | 40.2x | 0/3 | 1/3 |
| `context bind` | 5,130 | 155,960 | 96.7% | 30.4x | 0/3 | 0/3 |
| **Average** | — | — | **96.6%** | **38.7x** | **0.0** | **0.2** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 5,907 | 3,573 | 3/3 | 3/3 | 13.9 |
| `middleware` | 3,923 | 1,663 | 3/3 | 2/3 | 13.4 |
| `error exception` | 1,685 | 299 | 3/3 | 1/3 | 12.9 |
| `request response` | 2,406 | 1,478 | 3/3 | 3/3 | 13.1 |
| `context bind` | 2,929 | 2,201 | 1/3 | 2/3 | 11.7 |

</details>

### Chunk size: 2048 tokens

*222 chunks | embed 3.29s | total build 3.53s | FAISS 988 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 14,220 | 155,960 | 90.9% | 11.0x | 0/3 | 2/3 |
| `middleware` | 8,622 | 155,960 | 94.5% | 18.1x | 0/3 | 0/3 |
| `error exception` | 3,157 | 155,960 | 98.0% | 49.4x | 0/3 | 0/3 |
| `request response` | 5,702 | 155,960 | 96.3% | 27.4x | 0/3 | 0/3 |
| `context bind` | 3,628 | 155,960 | 97.7% | 43.0x | 0/3 | 0/3 |
| **Average** | — | — | **95.5%** | **29.8x** | **0.0** | **0.4** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 9,572 | 4,648 | 3/3 | 3/3 | 12.9 |
| `middleware` | 6,055 | 2,567 | 3/3 | 2/3 | 12.3 |
| `error exception` | 2,858 | 299 | 3/3 | 1/3 | 11.2 |
| `request response` | 3,939 | 1,763 | 3/3 | 3/3 | 10.3 |
| `context bind` | 2,027 | 1,601 | 0/3 | 2/3 | 9.4 |

</details>

---

## fastapi/fastapi

| Metric | Value |
|--------|-------|
| Files indexed | **1,000** |
| Baseline tokens (all files) | **823,784** |
| Chunks (size 512) | 2,571 |
| Chunks (size 1024) | 1,600 |
| Chunks (size 2048) | 1,214 |

### Chunk size: 512 tokens

*2,571 chunks | embed 36.85s | total build 38.84s | FAISS 8,477 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 4,069 | 823,784 | 99.5% | 202.5x | 0/3 | 2/3 |
| `middleware` | 2,457 | 823,784 | 99.7% | 335.3x | 1/3 | 1/3 |
| `error exception` | 3,402 | 823,784 | 99.6% | 242.1x | 0/3 | 1/3 |
| `request response` | 3,462 | 823,784 | 99.6% | 238.0x | 0/3 | 3/3 |
| `context bind` | 2,826 | 823,784 | 99.7% | 291.5x | 0/3 | 2/3 |
| **Average** | — | — | **99.6%** | **261.9x** | **0.2** | **1.8** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 2,687 | 1,382 | 3/3 | 3/3 | 12.0 |
| `middleware` | 1,289 | 1,168 | 3/3 | 2/3 | 10.1 |
| `error exception` | 2,325 | 1,077 | 3/3 | 3/3 | 8.1 |
| `request response` | 2,306 | 1,156 | 3/3 | 1/3 | 8.6 |
| `context bind` | 1,674 | 1,152 | 3/3 | 2/3 | 8.1 |

</details>

### Chunk size: 1024 tokens

*1,600 chunks | embed 22.1s | total build 23.61s | FAISS 6,913 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 7,435 | 823,784 | 99.1% | 110.8x | 1/3 | 2/3 |
| `middleware` | 1,316 | 823,784 | 99.8% | 626.0x | 0/3 | 1/3 |
| `error exception` | 3,862 | 823,784 | 99.5% | 213.3x | 0/3 | 0/3 |
| `request response` | 5,631 | 823,784 | 99.3% | 146.3x | 0/3 | 2/3 |
| `context bind` | 5,829 | 823,784 | 99.3% | 141.3x | 0/3 | 2/3 |
| **Average** | — | — | **99.4%** | **247.5x** | **0.2** | **1.4** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 4,873 | 2,562 | 3/3 | 3/3 | 10.8 |
| `middleware` | 722 | 594 | 3/3 | 1/3 | 10.7 |
| `error exception` | 2,180 | 1,682 | 3/3 | 3/3 | 9.1 |
| `request response` | 3,419 | 2,212 | 3/3 | 1/3 | 9.1 |
| `context bind` | 3,997 | 1,832 | 3/3 | 3/3 | 9.1 |

</details>

### Chunk size: 2048 tokens

*1,214 chunks | embed 16.22s | total build 17.64s | FAISS 6,272 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 5,998 | 823,784 | 99.3% | 137.3x | 0/3 | 0/3 |
| `middleware` | 680 | 823,784 | 99.9% | 1211.4x | 0/3 | 0/3 |
| `error exception` | 5,415 | 823,784 | 99.3% | 152.1x | 0/3 | 0/3 |
| `request response` | 4,995 | 823,784 | 99.4% | 164.9x | 0/3 | 2/3 |
| `context bind` | 7,812 | 823,784 | 99.1% | 105.5x | 0/3 | 2/3 |
| **Average** | — | — | **99.4%** | **354.2x** | **0.0** | **0.8** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 4,113 | 1,885 | 3/3 | 1/3 | 11.4 |
| `middleware` | 587 | 93 | 3/3 | 0/3 | 9.2 |
| `error exception` | 3,816 | 1,599 | 2/3 | 3/3 | 9.0 |
| `request response` | 2,667 | 2,328 | 3/3 | 1/3 | 8.2 |
| `context bind` | 5,500 | 2,312 | 3/3 | 3/3 | 8.8 |

</details>

---

## gin-gonic/gin

| Metric | Value |
|--------|-------|
| Files indexed | **98** |
| Baseline tokens (all files) | **151,842** |
| Chunks (size 512) | 401 |
| Chunks (size 1024) | 231 |
| Chunks (size 2048) | 151 |

### Chunk size: 512 tokens

*401 chunks | embed 6.08s | total build 6.3s | FAISS 1,202 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 3,577 | 151,842 | 97.6% | 42.4x | 0/3 | 0/3 |
| `middleware` | 4,002 | 151,842 | 97.4% | 37.9x | 0/3 | 0/3 |
| `error exception` | 3,850 | 151,842 | 97.5% | 39.4x | 1/3 | 1/3 |
| `request response` | 4,306 | 151,842 | 97.2% | 35.3x | 0/3 | 1/3 |
| `context bind` | 3,714 | 151,842 | 97.6% | 40.9x | 0/3 | 0/3 |
| **Average** | — | — | **97.5%** | **39.2x** | **0.2** | **0.4** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 2,419 | 1,158 | 3/3 | 1/3 | 12.4 |
| `middleware` | 2,437 | 1,565 | 3/3 | 2/3 | 11.2 |
| `error exception` | 2,537 | 1,313 | 3/3 | 2/3 | 10.9 |
| `request response` | 2,833 | 1,473 | 3/3 | 2/3 | 10.4 |
| `context bind` | 2,367 | 1,347 | 3/3 | 3/3 | 10.7 |

</details>

### Chunk size: 1024 tokens

*231 chunks | embed 3.53s | total build 3.75s | FAISS 932 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 5,518 | 151,842 | 96.4% | 27.5x | 0/3 | 0/3 |
| `middleware` | 8,259 | 151,842 | 94.6% | 18.4x | 1/3 | 0/3 |
| `error exception` | 8,050 | 151,842 | 94.7% | 18.9x | 1/3 | 2/3 |
| `request response` | 7,633 | 151,842 | 95.0% | 19.9x | 0/3 | 2/3 |
| `context bind` | 6,739 | 151,842 | 95.6% | 22.5x | 1/3 | 0/3 |
| **Average** | — | — | **95.3%** | **21.4x** | **0.6** | **0.8** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 3,706 | 1,812 | 3/3 | 1/3 | 13.8 |
| `middleware` | 4,735 | 3,524 | 3/3 | 3/3 | 12.0 |
| `error exception` | 4,747 | 3,303 | 3/3 | 2/3 | 12.0 |
| `request response` | 4,569 | 3,064 | 3/3 | 3/3 | 12.6 |
| `context bind` | 4,062 | 2,677 | 3/3 | 2/3 | 11.8 |

</details>

### Chunk size: 2048 tokens

*151 chunks | embed 2.34s | total build 2.55s | FAISS 805 KB*

| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |
|-------|----------:|----------:|---------:|------:|:---------:|:-------:|
| `router route handler` | 16,557 | 151,842 | 89.1% | 9.2x | 0/3 | 0/3 |
| `middleware` | 9,570 | 151,842 | 93.7% | 15.9x | 0/3 | 0/3 |
| `error exception` | 12,670 | 151,842 | 91.7% | 12.0x | 0/3 | 0/3 |
| `request response` | 9,529 | 151,842 | 93.7% | 15.9x | 0/3 | 1/3 |
| `context bind` | 10,536 | 151,842 | 93.1% | 14.4x | 1/3 | 0/3 |
| **Average** | — | — | **92.3%** | **13.5x** | **0.2** | **0.2** |

<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>

| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |
|-------|-----------------:|------------------:|:-:|:-:|------:|
| `router route handler` | 10,306 | 6,251 | 3/3 | 3/3 | 12.5 |
| `middleware` | 5,544 | 4,026 | 2/3 | 2/3 | 12.6 |
| `error exception` | 7,720 | 4,950 | 3/3 | 3/3 | 10.4 |
| `request response` | 6,899 | 2,630 | 3/3 | 3/3 | 11.3 |
| `context bind` | 6,624 | 3,912 | 3/3 | 2/3 | 10.9 |

</details>

---

## Combined Comparison

Average RAG tokens per query (mean of 5 queries), compared to jCodemunch.
jCodemunch per-repo figures are read from `benchmarks/jcm_reference.json` (run 2026-07-29T11:14:04-0500, v1.108.199; grand summary: baseline 5,657,930, jMunch 25,090, 15 task-runs). Regenerate with `python benchmarks/harness/run_benchmark.py --reference`.

| Repo | Baseline | RAG-512 | RAG-1024 | RAG-2048 | jCodemunch | Best-RAG-ratio | jCodemunch-ratio | Winner |
|------|--------:|---------:|---------:|---------:|-----------:|--------------:|-----------------:|--------|
| expressjs/express | 155,960 | 3,161 | 5,213 | 7,066 | 985 | 49.3x | 158.3x | jCodemunch (3.2×) |
| fastapi/fastapi | 823,784 | 3,243 | 4,815 | 4,980 | 2,494 | 254.0x | 330.3x | jCodemunch (1.3×) |
| gin-gonic/gin | 151,842 | 3,890 | 7,240 | 11,772 | 1,540 | 39.0x | 98.6x | jCodemunch (2.5×) |

## Infrastructure Overhead

| Repo | Chunk size | Chunks | Embed time | FAISS size |
|------|:----------:|-------:|-----------:|-----------:|
| expressjs/express | 512 | 442 | 5.4s | 1,341 KB |
| expressjs/express | 1024 | 285 | 3.92s | 1,093 KB |
| expressjs/express | 2048 | 222 | 3.29s | 988 KB |
| fastapi/fastapi | 512 | 2,571 | 36.85s | 8,477 KB |
| fastapi/fastapi | 1024 | 1,600 | 22.1s | 6,913 KB |
| fastapi/fastapi | 2048 | 1,214 | 16.22s | 6,272 KB |
| gin-gonic/gin | 512 | 401 | 6.08s | 1,202 KB |
| gin-gonic/gin | 1024 | 231 | 3.53s | 932 KB |
| gin-gonic/gin | 2048 | 151 | 2.34s | 805 KB |

## Chunk Integrity Summary

Percentage of retrieved top-3 chunks that are complete code units vs. split mid-function.
(Heuristic: complete = starts with def/class/function/func and braces balance; split = brace imbalance > 2 or ends mid-indented-block.)

| Repo | Chunk size | Complete % | Split % |
|------|:----------:|-----------:|--------:|
| expressjs/express | 512 | 7% | 7% |
| expressjs/express | 1024 | 0% | 7% |
| expressjs/express | 2048 | 0% | 13% |
| fastapi/fastapi | 512 | 7% | 60% |
| fastapi/fastapi | 1024 | 7% | 47% |
| fastapi/fastapi | 2048 | 0% | 27% |
| gin-gonic/gin | 512 | 7% | 13% |
| gin-gonic/gin | 1024 | 20% | 27% |
| gin-gonic/gin | 2048 | 7% | 7% |
