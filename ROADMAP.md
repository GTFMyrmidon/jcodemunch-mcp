# jCodeMunch Roadmap

Accepted design work that is **sequenced but not started**.

## Why this file exists

An issue is a problem to fix or a feature to build. Something we've agreed to
build *eventually*, with no start date and an unmet dependency, is neither — it
is a plan. Leaving plans open as issues makes the tracker a to-do list, and a
tracker that mixes "someone is blocked on this" with "we like this idea" tells
you nothing at a glance about either.

So: **an issue opens when work starts or when a user is blocked. Accepted but
unscheduled design lives here.**

Nothing on this page is rejected. Everything here has been reviewed, agreed to,
and given a close condition. When work begins, the entry gets an issue and this
page links to it.

The evidence-arc design (Phases 2, 4, 5 and 6 below) is
[@mightydanp](https://github.com/mightydanp)'s, proposed in
[this comment](https://github.com/jgravelle/jcodemunch-mcp/issues/377#issuecomment-5076253159)
on [#377](https://github.com/jgravelle/jcodemunch-mcp/issues/377) and accepted
as written. Entries outside that arc carry their own provenance line.

---

## The evidence arc

The through-line across all of it: **a tool that answers confidently regardless
of how little it holds is the expensive failure**, because "I never learned that
file" and "that file does not exist" look identical to every agent downstream.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Claim-scoped evidence (`claims` + per-claim `evidence_refs`) | **Shipped** — jcm 1.108.165, jdoc 1.116.0, jdata 1.25.0 |
| 2 | Exact immutable evidence receipts + producer registration | **P1/P2 shipped** (1.108.183); two P3 edges shipped (1.108.192). **P3 remainder below.** Not started |
| 3 | Absence evidence + subject state | **Shipped** — 1.108.178-.181 |
| 4 | Requirement matching | **Below.** Not started |
| 5 | Corpus / source-universe identity | **Below.** Not started |
| 6 | Path-first program understanding | **Below.** Not started |

---

## Phase 2 P3 remainder — evidence lifetime

Two of the four P3 edges shipped in **1.108.192**: the absence record is
deep-copied into the receipt at mint time (so a later scan on the same
`absent:<sha12>` key cannot contaminate or rescue it), and validation hands its
resolved envelopes to rendering (so one resolution feeds validate, render, hash
and persist). Both verified by @mightydanp against the shipped code.

What is left is the **expiry taxonomy** — a different axis from "a different
scan must not contaminate this one", and deliberately not answered by the
frozen record:

- **Successful retrieval should tombstone contradictory negative evidence**
  (design item 18). `note_absence` returns early on an `ok` state today, so a
  scan identity that recorded `absent` and now returns a match leaves the old
  record in place. Partly mitigated, not closed: a replayed cached `absent` is
  downgraded to `degraded` with its evidence token stripped
  (`subject_state.revalidate_verdict`), and a receipt stands on its own frozen
  record rather than a live lookup. The gap is the store itself, which still
  holds a record the world has contradicted.
- **Expiry and collision taxonomy** (design item 19). `lookup()` already
  separates `never_recorded` / `evicted` / `collision`. Still undistinguished:
  **wrong session**, **wrong repository or dataset**, and **expired**.
- **Session / snapshot identity and invalidation** — whether a receipt should
  expire because the tree moved on after it was minted.

**Close condition.** Design items 17-19 in full, minus the two halves shipped in
1.108.192. A caller holding a stale token must be told which of the five reasons
applies, and a negative record contradicted by a later successful scan of the
same canonical snapshot and effective search identity must not remain citable.

**Sequencing.** Nobody is blocked on this today: the two adversarial edges that
could produce a wrong attestation are closed, and the remaining cases fail
toward refusal rather than toward borrowed proof.

---

## Phase 4 — requirement matching

Caller-declared `requirements` and `coverage_requirements`: a handoff states up
front what it needed to cover, and finalization reports coverage against that
declaration rather than against whatever happened to be retrieved.

Accepted at design time, deferred with Phase 2 P2. Never tracked as work in
progress.

**Result vocabulary.** Per requirement, finalization must report exactly one of
five states, proposed by @mightydanp on #377 (comment 5124590663) and adopted
verbatim:

```text
measured and satisfied
measured and unsatisfied
not measured
unsupported at that precision
failed while measuring
```

The last three are what make this worth having as a vocabulary rather than a
boolean. `not measured` stops a declared requirement from being read as a
negative result. `unsupported at that precision` is the honest answer #339
needed, where the fix was to fail closed rather than imply a per-file precision
the tool did not have. `failed while measuring` separates a delegate that errored
from a signal that came back empty, which is the distinction
jgravelle/jdocmunch-mcp#69 was missing when unmeasured signals scored as zero.

This is a vocabulary we have already had to invent three times in narrower
places, which is the argument for building it once: `FreshnessProbe.
repo_freshness` became four-state in 1.108.180 because a boolean had nowhere to
put "I could not find out", `coverage.complete` is tri-state with a null, and
`retrieval/ledger_trust.py` puts an unclassifiable telemetry row in a third
bucket rather than folding it into the negative group.

**Aggregates must not flatten their children.** Refined by @mightydanp on #377
(comment 5134935796). The five states describe a single measurement; an
aggregate requirement is where they get lost, because a conclusive top-level
answer reads as complete whether or not every child was actually evaluated.

An aggregate retains a record per mandatory child:

```text
child requirement or signal id
requested scope and precision
actual scope and precision
result state
evidence refs
producer identity
failure or unsupported reason
```

Conclusiveness is asymmetric between the two combinators, and the asymmetry is
the operative detail:

- `all` is satisfied only when every mandatory child is measured and satisfied.
- `all` may be conclusively unsatisfied on a single measured failure, but the
  children that failed or were unsupported still have to be disclosed. A
  conclusive verdict is not a licence to drop the rest of the report.
- `any` may be conclusively satisfied on one measured child, and failures among
  the other attempted children still remain visible.
- When failed, unsupported, or absent children prevent a conclusive result, the
  aggregate reports the applicable non-measured state rather than guessing.

The aggregate result never replaces the child states. Otherwise a conclusive
top-level answer can still hide that part of the requested audit was unsupported
or failed, which is the same false-completeness hazard as a zero standing in for
an unmeasured signal, one level up.

**Close condition.** As accepted in the original design comment, plus: every
requirement in a finalized handoff resolves to exactly one of the five states
above, and no state is reachable by defaulting. A requirement that was never
evaluated reports `not measured` and must not render as unsatisfied. For
aggregates: every mandatory child keeps its own record and its own state, and no
aggregate verdict is reported that its retained child states do not support.

> A measured result requires affirmative proof that the measurement occurred.
> Missing data describes the measurement process, not the subject being measured.
>
> -- @mightydanp, #377

**Provenance binding.** Recorded as a stated direction, not scheduled: running
producer version, runtime and session identity, index schema version, index
generation, and producer capability fingerprint, bound into corpus and proof
identity. The adjacent case that motivated it (a future-version index imitating
absence) did not reproduce and is now pinned by
`tests/test_future_version_no_false_absence.py`, so this is about making
provenance legible rather than closing a known defect.

**Sequencing.** After the Phase 2 P3 remainder. Requirement coverage that cites
evidence with an unsettled lifetime inherits the unsettled lifetime.

---

## Phase 5 — corpus and source-universe identity

> A complete scan of an incomplete or misunderstood corpus is not complete
> evidence.

Phase 2 can identify an exact evidence object. Phase 5 identifies the *corpus*
and the producer capabilities behind it, so a receipt can say what universe it
was complete with respect to.

**Scope**

- **5A, common corpus manifest.** Content-addressed corpus identity that changes
  when eligible inputs, parser/profile capability, or generated/dependency
  inputs change.
- **5B, jCodeMunch source universe.** Source roots, modules, source sets,
  variants, generated roots from the resolved build model; dependency source
  provenance; per-file parse outcomes (a parser failure must not read as a
  successfully searched empty file); parser capability fingerprint; atomic index
  generations; per-repository watcher health.
- **5C, jDocMunch document universe.** Formats, conversion failures,
  content-load failures, embedding coverage, repository-group member state.
- **5D, jDataMunch dataset universe.** Row-walk coverage separated from
  column-profile, distinct-value, top-value, sample and embedding coverage.
- Proof kinds, so a producer may mint only the kind its operation actually
  supports.

**Close condition.** Every receipt names one corpus identity; corpus identity
changes when eligible inputs or producer capabilities change; failed inputs stay
visible; generated and dependency domains are explicit; document conversion and
embedding coverage are represented; data row, profile, sample and value coverage
are separate; a failed or cancelled generation cannot support absence.

**Sequencing.** After the remaining Phase 2 work above — the receipt schema
needs the extension points Phase 5 defines, and building them in the other order
means designing the extension points twice.

**Extension point already in place.** `evidence/receipts.py` carries
`coverage_fingerprint()` as the deliberate, opaque Phase 5 hook (1.108.183).

---

## Phase 6 — path-first program understanding with typed flow witnesses

We expose an import graph, a call hierarchy, framework flow edges, signal
chains, blast radius, related symbols, logical communities, compiler references
and runtime activity. Every one is a separate view. The missing abstraction is:

> an ordered, typed, evidence-backed path from an origin to an effect

A codebase is not understood because relevant symbols were found. It is
understood when the system can show where behavior begins, which ordered and
typed transitions connect its declarations, what governs them, what data and
state move through them, where paths branch, merge, cycle or become ambiguous,
which boundaries they cross, which steps are exact versus heuristic versus
unresolved versus runtime-observed, and what missing evidence stops the path
from being complete.

**Scope.** Canonical program nodes and typed edges with resolution and
provenance; ordered path witnesses whose confidence cannot exceed their weakest
required edge; signal chains that keep alternative paths separate instead of
flattening them into a node set; exact-identity path membership with ambiguity
preserved rather than silently resolved; multiple entrypoints to one handler
kept distinct; not-reached separated from unreachable; lifecycle order separated
from reachability; bounded control-flow conditions; data and state flow; runtime
adjacency preserved through ingestion; path-aware context packing that never
drops a bridge node; cross-repository contract nodes; and an immutable
`munch://path/<id>` resource a claim can cite.

jCodeMunch-led. Sibling equivalents considered later where the domain has real
path semantics.

**Close condition.** The acceptance list in the source comment, in full. **No
Cypher, SPARQL, GraphQL or external graph database** — a bounded path API is the
deliverable.

**Sequencing.** Depends on Phase 2 (exact evidence) and Phase 5 (corpus
identity). A path witness that cannot say which corpus it was traced over, or
cannot cite an exact evidence object per step, is not worth citing.

---

## Retrieval benchmark integrity — leakage split and size buckets

Not part of the evidence arc above. Proposed and accepted by the maintainers,
2026-07-29.

**The problem.** A retrieval score measured over queries that contain their own
answer's name is partly a measure of name matching, not of retrieval. We ship
exact seeding — `retrieval/query_shape.py` pins exact symbol-name matches ahead
of ranked matches in `get_ranked_context` — so a query corpus of that shape
flatters the feature by construction.

Our own authored fixture is the extreme case and is deliberately so:
`benchmarks/calibration/planted_queries.json` records that planted names are
slug-unique "so hit/miss is unambiguous and immune to corpus drift." That is
correct for what it measures — whether the verdict reports found when the
subject was found — and wrong for anything that reads it as retrieval quality.
Nothing in the repo says so where a reader would need to see it.

`benchmarks/goldset/gold.json` is **not** affected and needs no change. Its
targets are symbol identities rather than natural-language queries, and its
authored false-positive traps (module homonyms, same-name-different-domain
methods, substring decorator matches) already do the equivalent job.

**Scope**

- A deterministic leakage criterion over a query corpus: a task leaks when the
  tokenized query shares a stemmed token with the tokenized basenames or symbol
  names of its expected results.
- A `split` field per task (`easy` default, `hard`) and a corpus validator that
  exits non-zero when a `hard` task leaks, so CI can gate it.
- hit@k and MRR reported per split **and** per repository-size bucket, beside
  the overall figure rather than instead of it.
- A stated-limit line wherever a leakage-free number is published, naming n per
  split.
- An explicit note on `planted_queries.json` that it measures verdict coverage
  and is not a retrieval score.

**Close condition.** No retrieval number is published from this repository
without its split and its size bucket attached.

**Sequencing.** Bundled with the neutral third-party retrieval benchmark run.
Split machinery built before there is a corpus to split is a validator with
nothing to validate; the benchmark run without it produces a number that has to
be re-qualified afterwards. Size buckets are expected to cost us — large-repo
retrieval is our weakest measured cell, and the bucket that exposes it is the
one we would most like to leave out. That is the reason to build it into the
harness rather than decide per publication.

---

## `install-pack --from`: install an index your own CI built

`install-pack` fetches the pack catalog and pre-built indexes from one hardcoded
host (`STARTER_PACK_API`, `cli/install_pack.py:14`). The mechanism is general;
only the destination is fixed. A team that indexes its own private repo in CI has
no supported way to hand that artifact to the rest of the team, so every seat
re-indexes the same code independently.

That duplication is not only CPU. With `use_ai_summaries` on, summary generation
spends provider tokens at index time, and N seats indexing one repo pay for the
same summaries N times. A pack built once with summaries already in it removes
that cost for everyone downstream of the build.

**Scope.** A `--from <url>` (and matching catalog override) pointing at a
catalog the customer hosts, with auth for a private endpoint, reusing the
existing archive layout and extraction path unchanged.

⚠ **Known blocker, now measured rather than read off the source: symbol bodies do
not travel in the `.db`.** `get_symbol_content` seeks `byte_offset` into a file
under a separate content directory and returns `None` when that file is absent
(`storage/sqlite_store.py`), and `build_pack.py` packages `.db` files only. So a
pack delivers search, outlines and signatures, while `get_symbol_source` comes
back empty unless the content cache ships too. Any design here settles that first,
because it changes the artifact's size profile.

Probed 2026-07-30 by installing the free `nodejs` pack into an empty store with
none of the packed repos checked out anywhere on the box. Bodies returned for
**0 of 50** symbols; `get_file_content` returned `None`; no content directory was
written. Size context for the eventual decision: that pack is 10.6 MB of `.db`
against a Node checkout in the hundreds of MB, so carrying bodies is a different
product rather than a larger zip.

✅ **The silent half of this is FIXED in v1.108.204** and shipped on its own, ahead
of any `--from` design. The pack path used to return the symbol's name, line,
signature, docstring and `content_hash` alongside `"source": ""` under
`_freshness: "fresh"` and `_meta.verdict: {"state": "ok", "note": "Confident
matches returned."}`. A resolved symbol whose body cannot be read now carries
`source_status: "content_cache_missing"` and degrades the verdict, in
`get_symbol_source` and `get_context_bundle` alike.

**What remains open here is the artifact question, not the reporting one:**
whether a pack should carry the content cache at all. The tool now says what it
cannot produce; it still cannot produce it.

**Close condition.** A seat installs an index built by a CI job it controls, from
a host it controls, and every tool that works against a locally built index works
against the installed one. Where that is not true (see the content-cache blocker),
the gap is reported by the tool rather than surfacing as an empty result.

**Provenance.** Fell out of the jCodeMunch Enterprise review (2026-07-30), which
concluded that no token-level saving requires a running shared component: every
win available is a build-time artifact property. This is the artifact channel
pointed at a private repo instead of our public catalog. Independently useful to a
solo developer with two machines, which is why it belongs to jcm rather than to
any enterprise layer.

---

## Conventions

- Entries here are **accepted**, not speculative. A rejected proposal gets a
  closed issue with reasoning, not a roadmap line.
- Each entry keeps its **close condition** verbatim from the design that was
  accepted, so scope cannot drift quietly between filing and building.
- When an entry starts, it gets an issue, and its line here gains the link.
- Credit stays attached to the entry. Sequencing is not authorship.
