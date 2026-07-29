# Odysseus rag_server vs jCodeMunch -- retrieval-layer benchmark

**What this is:** for identical code-navigation queries, the tokens each
retrieval layer injects into the model's context. Complementary framing --
jCodeMunch runs *inside* Odysseus over SSE; this shows the delta of routing
code retrieval through it instead of the built-in `rag_server`.

**Odysseus pipeline reproduced from source** (`src/rag_vector.py`): embeddings `sentence-transformers/all-MiniLM-L6-v2`; chunking `_split_into_chunks` (char/sentence, size=1000 chars, overlap=200); retrieval `search(k=5)`.

**jCodeMunch figures** are read from `benchmarks/jcm_reference.json`, written by
`run_benchmark.py --reference` (`measure_jmunch`) against the same IndexStore
content -- both sides read byte-identical source. A repo the artifact does not
cover gets no jCodeMunch number; nothing here is estimated.

**Read the two axes together.** Token count alone is a trap: Odysseus's
rag_server returns fixed ~1000-char fragments, so on repos with large
symbols it can inject *fewer* tokens than jCodeMunch -- but those fragments
are frequently cut mid-definition. jCodeMunch returns *complete* symbols by
construction. So compare `tokens/query` next to `complete chunks` (of 5):
RAG cheapness that comes with split chunks is truncated context, not a win.

**Caveats:** FAISS here vs ChromaDB in Odysseus (immaterial to token count);
pure-vector ranking here vs Odysseus's 0.7/0.3 hybrid (can reorder top-k, not
its token cost -- relevance reported separately). Not a live agent-loop run.

| Repo | Files | Odysseus rag tokens/q | jCodeMunch tokens/q | Token delta | Odysseus complete/5 | Odysseus split/5 | Odysseus terms-hit/5 |
|------|------:|----------------------:|--------------------:|------------:|:-------------------:|:----------------:|:--------------------:|
| expressjs/express | 185 | 1,187 | 985 | jcm **1.2x leaner** | 0.0 | 0.6 | 3.8 |
| fastapi/fastapi | 1,000 | 538 | 2,494 | RAG 4.6x leaner* | 0.4 | 3.6 | 5.0 |
| gin-gonic/gin | 98 | 1,378 | 1,540 | RAG 1.1x leaner* | 1.6 | 0.6 | 4.8 |

\* *Where RAG shows fewer tokens, check its complete/5 and split/5: the saving comes from truncated ~1000-char fragments, while jCodeMunch's tokens are whole symbols. Cheaper context that is cut mid-function is not cheaper to reason over.*

## Per-query detail

### expressjs/express

*817 Odysseus chunks | 9.71s embed*

| Query | Odysseus rag tokens | Complete/5 | Split/5 | Terms-hit/5 | Query ms |
|-------|-------------------:|:----------:|:-------:|:-----------:|---------:|
| `router route handler` | 1,382 | 0/5 | 1/5 | 5/5 | 15.5 |
| `middleware` | 1,172 | 0/5 | 0/5 | 5/5 | 8.3 |
| `error exception` | 764 | 0/5 | 0/5 | 4/5 | 7.9 |
| `request response` | 1,361 | 0/5 | 0/5 | 5/5 | 8.1 |
| `context bind` | 1,256 | 0/5 | 2/5 | 0/5 | 8.1 |

### fastapi/fastapi

*5,915 Odysseus chunks | 82.35s embed*

| Query | Odysseus rag tokens | Complete/5 | Split/5 | Terms-hit/5 | Query ms |
|-------|-------------------:|:----------:|:-------:|:-----------:|---------:|
| `router route handler` | 856 | 1/5 | 3/5 | 5/5 | 13.6 |
| `middleware` | 669 | 1/5 | 1/5 | 5/5 | 9.2 |
| `error exception` | 317 | 0/5 | 4/5 | 5/5 | 9.0 |
| `request response` | 454 | 0/5 | 5/5 | 5/5 | 8.6 |
| `context bind` | 395 | 0/5 | 5/5 | 5/5 | 8.5 |

### gin-gonic/gin

*733 Odysseus chunks | 11.05s embed*

| Query | Odysseus rag tokens | Complete/5 | Split/5 | Terms-hit/5 | Query ms |
|-------|-------------------:|:----------:|:-------:|:-----------:|---------:|
| `router route handler` | 1,063 | 1/5 | 1/5 | 5/5 | 11.9 |
| `middleware` | 1,326 | 2/5 | 0/5 | 5/5 | 9.9 |
| `error exception` | 1,362 | 3/5 | 0/5 | 4/5 | 10.8 |
| `request response` | 1,410 | 1/5 | 1/5 | 5/5 | 10.7 |
| `context bind` | 1,728 | 1/5 | 1/5 | 5/5 | 10.3 |
