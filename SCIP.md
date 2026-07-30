# Compiler-verified references with SCIP

AST analysis is fast and language-broad, but dynamic dispatch and barrel
re-exports hide references from any static heuristic. SCIP closes that gap with
the compiler's own answer.

You generate a `.scip` index with a tool you already run, hand jCodeMunch the
file, and reference edges the AST could never see become available to the tools
you already use. jCodeMunch never runs your build, never spawns a language
server, and never leaves your machine.

---

## 1. Generate a `.scip` index

These are third-party indexers maintained by the SCIP ecosystem, not by us.
Invocations change upstream, so treat these as starting points and check each
project's own README if a flag has moved.

| Language | Indexer | Typical invocation |
|---|---|---|
| TypeScript / JavaScript | `@sourcegraph/scip-typescript` | `npx @sourcegraph/scip-typescript index` |
| Python | `@sourcegraph/scip-python` | `npx @sourcegraph/scip-python index .` |
| Go | `sourcegraph/scip-go` | `scip-go` from the module root |
| Rust | `rust-analyzer` | `rust-analyzer scip .` |
| Java / Scala / Kotlin | `sourcegraph/scip-java` | `scip-java index` |
| C / C++ | `sourcegraph/scip-clang` | needs a `compile_commands.json` |

Each writes an `index.scip` in the working directory by default.

**TypeScript is the case that pays for itself.** Barrel files (`export * from
'./foo'`) are where AST heuristics lose references, and where the compiler does
not.

---

## 2. Import it

```bash
jcodemunch-mcp import-scip index.scip
```

`.gz` is accepted directly. The repo is resolved from the current directory;
pass `--repo owner/name` to target a different indexed repo.

The command reports what it actually ingested rather than a success banner:
`edges_written`, `unique_edges`, `reference_edges`, `implementation_edges`,
`unmapped` with per-reason counts, `skipped_local`, `skipped_import`, `evicted`,
and the `tool` string the indexer stamped into the file.

Read `unmapped` before you trust a low edge count. It means the SCIP file
described a symbol jCodeMunch could not place in its own index, which usually
means the two were built from different trees.

---

## 3. What it changes

Once imported, six tools consume the evidence:

- **`find_references`** labels agreement as `verification: "compiler_verified"`
  and adds the references *only the compiler saw* as `source: "scip"` rows.
- **`get_blast_radius`** and **`get_call_hierarchy`** union compiler edges into
  the graph.
- **`find_implementations`** gains a compile-time channel at confidence 1.0.
- **`check_edit_safe`** and **`check_delete_safe`** weight severity with it.

The marquee case: `check_delete_safe` flipping from safe to blocked after an
import, because the compiler saw a caller the AST did not.

**No new MCP tool.** Ingest is CLI-only, deliberately, the same way
`import-trace` is. Nothing here costs tool-schema budget.

---

## 4. Staleness is reported, not assumed

SCIP evidence is a snapshot of one commit. jCodeMunch records the `git_head` it
was ingested at and compares it against the index's current head on every read.
When they differ, results carry a `stale` flag and a re-import hint rather than
posing as current truth.

That is the entire freshness model, and it is deliberately conservative: the
evidence is *older*, not *wrong*, and the flag says which.

Re-import after any merge you care about. In CI, that means regenerating and
re-importing in the same job that indexes, so the two heads always match.

---

## 5. In CI

```yaml
- name: Index for jCodeMunch
  run: |
    npx @sourcegraph/scip-typescript index
    jcodemunch-mcp index .
    jcodemunch-mcp import-scip index.scip
```

Order matters: `import-scip` resolves SCIP symbols against an existing
jCodeMunch index, so index first, import second. Running the import against a
missing or older index is what produces a high `unmapped` count.

---

## 6. Limits

- **Row cap.** `scip_edges` and `scip_unmapped` are capped at 200,000 rows,
  FIFO-evicted oldest-first. Override with `JCODEMUNCH_SCIP_MAX_ROWS`; an
  unparseable value or `0` falls back to the default. Env-only, not a config
  key.
- **`local N` symbols and import-role occurrences are skipped** and counted
  separately. Local symbols are not addressable across files, and the import
  graph already covers imports.
- **No cross-repo linking yet.** SCIP symbols are globally unique, so this is
  cheap to add and is deliberately demand-gated. Open an issue if you want it.

---

## 7. The charter

jCodeMunch does not run your compiler, your tests, or your code. Strong evidence
enters through a file you generate, with a tool you chose, on your schedule.
That constraint is why this is an import rather than an integration.

---

*If a command here is out of date or a claim doesn't match the source, that's a
bug in this document and we'd like the issue.*
