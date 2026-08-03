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
written. Size context recorded at the time: that pack is 10.6 MB of `.db` against
a Node checkout in the hundreds of MB, so carrying bodies looked like a different
product rather than a larger zip.

⚠⚠ **That conclusion was WRONG and is superseded by measurement (2026-08-01, see
"What the artifact actually weighs" below). It was wrong for a structural reason
worth keeping:** it compared the `.db` against the SOURCE, when the number that
decides the question is the content cache against **the `.db` we already ship**.
Measured, the cache adds about **1.5x to the shipped zip**. A larger zip, not a
different product. ⚠ **The general lesson, since this cost a design conclusion:
size the increment against the thing it joins, not against the thing it replaces.**

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

### What the artifact actually weighs

Measured 2026-08-01, because the paragraph above had been reasoning from an
unmeasured size profile since 2026-07-30. Method: fresh shallow clone, indexed
locally, all three sizes taken from the same tree in the same run. Both sides of
a ratio have to be measured together or the stale side drifts unaudited, which is
the same failure `benchmarks/` hit for four months (see Maintenance Practices #4
in `CLAUDE.md`). ⚠ **The pre-existing local indexes could not carry the SOURCE
side of this**: truncated at the 2,000-file `max_folder_files` cap, with stale or
absent `source_root`s, so any source ratio taken from them would have been a
fresh number over a stale one. They remain valid for the **cache-against-`.db`**
ratio only, because both of those come from the same artifact and truncation
drops files from each together; that is the one number they are quoted for below.

Zipped, which is what a pack actually ships:

| repo | `.db` zip | + content cache | source zip (no `.git`) | cache multiple | source / artifact |
|---|---|---|---|---|---|
| fastapi | 1.91 MB | 2.91 MB | 19.46 MB | **1.52x** | 6.69 |
| flask | 0.39 MB | 0.57 MB | 0.84 MB | **1.47x** | 1.48 |
| gin | 0.32 MB | 0.50 MB | 0.24 MB | **1.56x** | **0.47** |

**The number that decides the design is the zipped one, ~1.5x**, because a pack
ships zipped. Uncompressed the same three repos run 1.28x-1.34x; compression
favours the `.db` over the cache, which is why the shipped figure is the higher
of the two. ⚠ **Quote them separately - a single blended "about 1.5x" hides a
real 0.2x spread between the two methods.**

Corroboration, cache-against-`.db` only, from five older truncated indexes:
react 1.6x, django 1.4x, sqlalchemy 1.6x, langchain 1.6x, celery 1.5x
(uncompressed). So eight repos across Python, Go and TypeScript, none outside
1.28x-1.6x by either method. **Carrying bodies is a bounded multiple of an
artifact we already ship, not a new order of magnitude.** That answers the
artifact question in the affirmative on size grounds. ⚠ It does NOT answer the
licensing or trust questions, which are independent and unaddressed.

⚠⚠ **Unasked-for finding, and the one to carry forward: the artifact is NOT
reliably smaller than the source it indexes.** Gin's zipped artifact is **2.1x
LARGER** than gin's own zipped source; flask is only 1.5x smaller. Only fastapi
posts a big ratio, and that is composition rather than compression - roughly 20
of its 35 MB is docs and translations, and only 1,191 of 3,137 files were indexed
at all. **The "Nx smaller than the source" framing holds only for repos whose
bulk is non-code**, which every hand-picked pack so far happens to be. It does
not survive a code-dense repo, and gin is one of our own benchmark repos. ⚠ Do
not attach a size-savings claim to a pack for a repo we did not pick without
measuring that repo first; the Console's badge reads its ratio from the catalog's
`$PACK_SOURCE_MB` and cannot tell the difference.

⚠ **Stated limits of this measurement, so it is not over-read:** one shallow
clone each, single run, no repeats, NTFS. Fastapi's index covers a third of its
files. Reproduce with a clone + index + `du` on the `.db` and its sibling content
directory; there is no committed harness for this yet, and the numbers above are
not wired to `benchmarks/jcm_reference.json`. ⚠ **They are therefore hand-typed
figures, which is exactly what Maintenance Practices #4 forbids for anything
published outward.** They are fine as an internal design input; if any of them is
ever to appear in a README, on the site, or in a pack badge, it needs a harness
that writes them first.

### Validating an index you did not build

Raised 2026-08-01. The section above assumes the installing seat trusts the
builder, because in the `--from` case it *is* the builder: its own CI, its own
host. The interesting widening is the case where it is not, two unrelated users
who both depend on the same public third-party repo and would each otherwise
index it. ⚠ **That case dodges both findings that killed the enterprise broker**
(see `project_jcm_enterprise`): a shared dependency is a tree neither party
edits, so the freshness objection does not apply, and a public repo has nothing
to ACL. It is the Starter Pack shape with the catalog opened up, not a new
mechanism.

The question that gates it is whether a recipient can validate a received index
more cheaply than rebuilding it. **The answer splits in two, and only one half
is cheap.**

**The content cache can be attested cheaply, and the digest width is already
sufficient.** ⚠ **Checked, because the first pass through this got it wrong:**
`compute_content_hash` (`parser/symbols.py:80`) returns a full 64-character
SHA-256 over the symbol's slice, pinned by `test_hardening.py:356`. The truncated
12-hex helper is `config.py:_content_hash`, which caches project-config files and
has nothing to do with symbols. **No widening is required.**

What is missing is not width but an **external referent**. The digest is
self-referential: `verify_against="cache"` says so in its own docstring, and the
externally attested `git_sha` mode (`tools/get_symbol.py:109`) needs
`source_root`, precisely the checkout a recipient does not have. The construction
that works is to record the **git blob OID per file plus the built-from commit**,
so the recipient hashes the shipped cache locally and compares against tree
objects the forge publishes independently. No parse, no clone of file bodies, and
the comparison target stops coming from the uploader. ⚠ The manifest already
records a built-from commit for attribution (v1.108.205); this needs it per file
and for a different purpose, so do not assume the existing field covers it.

**Derived rows cannot be attested by any hash, only re-derived.** Symbols and
summaries are parse output; a digest over them proves internal consistency and
nothing else. The affordable form is a **spot-check re-parse** of a random sample
of files, which works only if parsing is deterministic for a fixed grammar set
and `INDEX_VERSION`, and that is an assumption with no test behind it today. Cost
then scales with the sample rather than the repo. ⚠⚠ **AI summaries are outside
this entirely.** They are non-deterministic, so unre-derivable, so unverifiable
by sampling, and they are also the one genuinely duplicated *token* cost the
paragraphs above give as the reason to share at all. A shared artifact probably
has to be heuristic-summaries-only and say so, or the field carries builder trust
that nothing else in the artifact does.

⚠ **State the guarantee accurately or it will be oversold.** Validation reduces
trust-in-uploader to trust-in-repo. It does not make a shared index safe: an
honest index of hostile code is still hostile, and it reaches the recipient's
agent as authoritative retrieval. Trust-in-repo is the right bar because it is
the same exposure as reading the repo, but it is not the same claim as "verified".

**One finding here is a live cost rather than a design, and it stands on its own
whether or not any of the above is ever built.** `tools/get_symbol.py:346` puts
`content_hash` in every `get_symbol_source` response entry unconditionally,
alongside `signature` and `source`, **not gated on `verify`**. A 64-hex digest is
about 83 characters per symbol returned, so a 20-symbol batch spends on the order
of 400 tokens on a field the caller was not offered a use for: `verify` already
answers the drift question as a boolean (`content_verified`), `get_changed_symbols`
answers it across a diff, and receipts carry `content_sha256` out of band with
only the id on the wire (`evidence/producers.py:483`). ⚠ **This is the same cost
in every response today; it is not created by anything in this section.**

⚠ **Gating it is a response-shape change on a shipped tool and wants an explicit
decision against the 1.x no-removal contract, so it is recorded here rather than
done.** Checked, in case it made the call easy: **no test asserts the field in a
tool response.** Every assertion found is storage-layer or parser-layer
(`test_hardening.py`, `test_call_references_model.py`, `test_css.py`,
`test_json.py`) and none is affected either way.

⚠ Adding blob OIDs is an `INDEX_VERSION` bump, which re-indexes every user and
re-downloads every pack. Do not land it alone; bank it against the next bump.

**Close condition for this sub-item.** A recipient can take an index built
elsewhere and establish, without a full re-index, that its content cache matches
a named upstream commit and that a sampled fraction of its symbol rows re-derive
from that content. Until the sampling assumption has a determinism test behind
it, the honest state is that only the content half is checkable.

**Non-goal.** Nothing here is approved work, and it does not become approved by
being written down. See the note under `#385`/`#386` in `CLAUDE.md`: parked
design with a close condition belongs in this file and not on the tracker.

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

## Catalog moratorium — IN FORCE from v1.108.218

No new top-level catalog actions until the exit conditions below are met. Pinned
and enforced by `tests/test_catalog_moratorium.py`; the contributor-facing
statement is in `CONTRIBUTING.md`.

**Why.** 91 actions, and `route` proposes the right one for a plain-language
task 45.8% of the time at rank 1. An action `route` never proposes is
functionally absent while still costing a schema, a 1.x compatibility promise,
an output contract and a test matrix. #397 is the sharp end: generated
`CLAUDE.md` named 25 tools against a server exposing 6.

**Exit conditions, named before the work** (same discipline as Arc 4's
thresholds — neither side picks the bar after seeing results):

1. `route@1` >= **60%** on `benchmarks/route_recall/queries.json`;
2. mean name leakage <= **0.15** at that same measurement;
3. generated guidance references only actions callable under the active surface.

⚠⚠ **Condition 2 is not decoration.** A recall bar with no leakage bar is met
by writing queries that paraphrase tool descriptions — the exact failure
v1.108.218's target audit was run to avoid. Both move together or neither
counts.

⚠ **Progress is measured from 45.8, never from 42.4.** The 3.4-point move
between them was v1.108.218's corpus correction, not routing work. A reader who
anchors to 42.4 will credit routing with a correction it did not make.

**What is withheld, and it is a surface not a capability.** Both exist, are
tested, and are unregistered: `investigate_deletion_safety` (v1.108.214, 19
tests) and the retrieval counterfactuals (v1.108.217). A test asserts both stay
importable, so the policy cannot quietly become deletion.

⚠ **Two of the report's four proposed conditions were DROPPED as unmeasurable,
deliberately rather than silently.** "Every catalog action has a clear usage
rate" needs telemetry that is opt-in and off by default, so it cannot gate
anything today. "Adjacent tools consolidated behind common intents" has no
threshold anyone could fail. A gate nobody can evaluate is a gate that gets
waived on the first argument; better to state three that bind than four that
sound thorough.

**The fastest way out** is route-recall work.
`benchmarks/route_recall/explain_misses.py` prints the live defect list with
each miss labelled by the gate that caused it: as of v1.108.218, 7
`rule_preempted` (a curated rule claims the query and the right action is never
scored — ranking work cannot touch these) and 8 `no_lexical_overlap` (zero
shared tokens; unreachable at any weight).

---

## Adaptive large-repository data path (#398, @rknighton)

Four accepted arcs from a five-arc proposal. **Arc 5 shipped in v1.108.210** and
is not repeated here. The evidence pack behind all five is SHA-pinned to
`c2201a55`, order-balanced A/B, with canonical response parity; every code
citation in it was verified against our tree before acceptance.

⚠ **Do not quote the reporter's multipliers in any shipped artifact.** Re-measure
on our side first. Arc 5's own numbers moved substantially when we did: he
measured a 2.63%-7.65% fetch fraction on FastAPI and Django, and jcm's own repo
came out at **22.48%** (2,838 of 12,625 vectors). Both are real; they describe
different repository shapes. The saving held, the headline did not transfer.

### Arc 1 — generation-safe read contract

One read snapshot per request, with the code generation distinguished from the
embedding generation, and cache identity including branch, index format version,
embedding model, and vector dimension.

Accepted on **correctness** grounds; the reporter's own estimate is ~1.0x and
that is honest. We currently have three surfaces answering one question:
`subject_state.capture()` publishes `indexed_at` as `generation`,
`evidence/producers.py` re-exposes it as `index_generation`, and
`_db_mtime_ns` is read separately. That is the [[feedback_two_paths_one_decision]]
shape, and v1.108.209 shipped a fix for the same shape one layer down (a per-file
freshness classifier answering `fresh` on five paths where it could not measure,
while the repo-level classifier 40 lines above already had `unknown`).

**Close condition:** the three generation surfaces resolve to one authoritative
committed-revision contract, receipt semantics preserved byte-for-byte, and the
read-only SQLite path sees committed WAL data when sidecars exist without
creating them when they do not.

✅ **SHIPPED v1.108.215.** `storage/generation.py` holds both halves:
`IndexGeneration` / `describe()` for the generation contract, and
`connect_readonly()` for the read contract. Receipts byte-identical.

⚠ **The arc was worth more than its ~1.0x estimate, and the reason is worth
keeping.** The v1.108.185 note said `immutable=1` costs only un-read vectors,
i.e. a weaker ranking. Measured 2026-08-02: against an un-checkpointed WAL an
`immutable=1` reader raises **`no such table`**, and `EmbeddingStore.has_any()`
maps that to `False` — a confident "this repository has no embeddings" about a
repository embedded moments earlier. **Fourteen call sites shared the hazard in
both directions** (ten blind to the WAL, four creating sidecars, including a
`token_tracker` loop that did it to every index in `~/.code-index` at once).
A correctness arc with an honest ~1.0x throughput estimate was carrying a live
false-absence defect; do not price these arcs on the multiplier alone.

### Arc 2 — transactional selective code snapshots

Load repository/file metadata plus only the rows a tool needs, inside one SQLite
read transaction. An explicit storage-owned read view, **not** a silent change to
`load_index`; broad modes promote to the existing complete path.

Best median result in the proposal (2.24x-2.35x). Sequenced after Arc 1 because
its consistency guarantee is part of Arc 1's contract.

⚠⚠ **`immutable=1` MUST NOT be lifted into this path, and the reason is the
argument that justifies it elsewhere.** `get_all_readonly` / `get_many` accept
that un-checkpointed WAL vectors go unread, on the stated grounds that the
similarity channel only ADDS candidates, so a missed vector can weaken a ranking
and cannot manufacture a false absence. Arc 2 is exact row reads. There, an
invisible un-checkpointed row is not a weaker ranking - it is a symbol that
exists and was not returned, which is a false absence of exactly the kind the
evidence work exists to prevent. **The trade-off is channel-specific; it does not
generalize** (@rknighton, 2026-08-01, and this is a sharper statement of the
sequencing than the one we gave).

That makes Arc 1 a genuine prerequisite rather than a tidiness preference: Arc 1's
read contract is the thing that has to satisfy both sides at once - see committed
WAL data when sidecars already exist, create nothing when they do not - and only
that contract lets this path read exactly.

✅ **That contract now exists**: `storage.generation.connect_readonly` (v1.108.215).
Arc 2's exact-row reads open through it and inherit the guarantee, so the
`immutable=1` prohibition below is enforced by using the shared opener rather
than by remembering not to type the flag - `tests/test_generation_contract.py`
fails on any hand-rolled read-only URI outside `storage/generation.py`.

**Close condition:** supported narrow calls complete without hydrating the full
`CodeIndex`; unsupported and broad modes promote once with no change to errors,
suggestions, ordering, branch behavior, or response fields; **and no read on this
path uses `immutable=1`.**

✅ **SHIPPED v1.108.216.** `storage/selective.py` + `store.open_selective()`;
`get_symbol_source` is the first wired caller, byte-identical on hit, batch and
miss. A non-empty branch returns `None` (take the ordinary path) rather than
reproducing delta composition against a partial row set.

⚠ **The promotion boundary is `__getattr__`, not an allow-list, and that choice
is the arc.** Exact fields are copied onto the instance; everything else falls
through and promotes, **including fields that do not exist yet**. Forgetting to
update the module makes a request slower, never wrong. A test proves it with a
fake future field.

⚠⚠ **Near-miss worth not repeating.** The view uses `__slots__`, and
`_stamp_load_provenance` writes `_db_path`/`_loaded_mtime_ns` inside a bare
`except Exception`. Omitting those two slots would have sent
`subject_state.capture` — which runs on essentially every response — through
`__getattr__`, hydrating the whole corpus to answer a question about a file
mtime, **while every test about symbols still passed**. A silent swallow plus a
promoting fallback is a performance defect that hides behind green tests.

**Measured on our side** (both sides, one interleaved run, cold cache each
iteration), jcm's own index at 12,826 symbols: selective median 7.6 ms vs
hydrate 159.7 ms, **20.96x**. ⚠ **Not quotable as the arc's result** — narrowest
possible call, control corpus. The 2.24x-2.35x figure is a mixed-group median;
Django and FastAPI remain the load-bearing points and neither has been
re-measured here.

**Remaining, not blocking the close:** only `get_symbol_source` is wired.
`get_file_outline` and `get_context_bundle` are the obvious next callers (the
`files=` scope already exists and is tested); each is a separate wiring with its
own parity assertion, not a change to this path.

### Arc 3 — generation-scoped shared promotion — GATED

Promote a broad request's snapshot into one immutable full view keyed by the code
generation, single-flighted across concurrent callers.

⚠ **Gated on disclosure, not on the technique.** Retaining a view across requests
is background behavior, and every new background/persistent/network behavior must
be README-disclosed before shipping (the standing post-quarantine rule). It also
overlaps `JCODEMUNCH_INDEX_CACHE_TTL` (v1.108.172), which is deliberately opt-in
because cold hydration of a 665k-symbol index was measured at 7.5-11.4 minutes
(#370). **Reconcile with that switch; do not add a second retention policy beside
it.**

**Which direction the reconciliation runs — DECIDED 2026-08-02.** The proposal
asked whether Arc 3 sits under the existing switch or makes it unnecessary, on
the argument that a byte budget bounds a leaked process by construction and does
so without the idle-eviction bill that forced the TTL to default off.

Arc 3's budget is the **primary** policy and the TTL does not gate it. But the
byte budget does **not** subsume the TTL, and the reason is worth pinning:

⚠ **A byte budget is per PROCESS; the leak the TTL was built for is a count of
processes.** #375 was 25+ leaked stdio servers holding ~17 GB between them. A
per-process cap bounds how much each one can GROW to; it never reclaims the floor
each one is already sitting on, and 25 x budget is still 25 x budget. The TTL
evicts an idle cache toward zero, which is a different axis, and the only one
that reaches a process nobody is talking to.

So: **one policy, two axes** - byte/entry pressure and generation change (always
on, Arc 3's own), plus idle reclamation (opt-in, the existing
`JCODEMUNCH_INDEX_CACHE_TTL` spelling, retained under the 1.x no-removal
contract). Arc 3 must not introduce a second switch, a second eviction loop, or a
second vocabulary for either axis.

**Close condition:** byte- and entry-bounded retention with safe eviction and a
per-request fallback when the budget is full; expensive construction stays lazy;
`JCODEMUNCH_INDEX_CACHE_TTL` continues to mean idle reclamation and is honored by
the same holder rather than by a parallel one; README background-behavior section
updated in the same release.

### Arc 4 — adaptive certified semantic scoring — GATE CLEARED; LANE 1 SHIPPED, LANE 3 PARKED ON NEED

**Disposition recorded 2026-08-03, closing [#403](https://github.com/jgravelle/jcodemunch-mcp/issues/403).
Verdict: the measurement gate below is CLEARED.** @rknighton measured the
three-bucket breadth on real embeddings — Django 0.1591%, FastAPI 0.1809%, jcm
control 0.2686%, against a 10% PASS ceiling, with **0 genuine boundary
disagreements** against a 0.5% fail line. The thresholds were fixed in advance
and neither side moved them.

**What we verified ourselves, and what we did not.** We did not download the
86.9 MB archive or re-run the harness; the figures above are as reported.
(The no-LICENSE caveat that stood here is **resolved**: relicensed **MIT-0** on
2026-08-03, verified against the LICENSE file itself. Vendoring is permitted.)
What we did check is the part that could invalidate the result from our side —
the gate's own stated precondition — and it had NOT been met:

⚠⚠ **The `(score, symbol_id)` tie-break key had not shipped when the measurement
ran.** This section says it "is required regardless and ships FIRST, alone"
precisely so bucket (1) collapses *before* the number is taken. Every ranking
sort was still `key=lambda x: x[0]` at v1.108.227. **This does not invalidate the
verdict, and the direction is why:** an uncollapsed exact-tie bucket makes
measured breadth an UPPER bound, so a 0.159% pass holds a fortiori — it can only
shrink. It would have invalidated a FAIL or anything near the 10% line. Recorded
because the next person to read "gate cleared" should know which half of the
protocol actually ran.

**Lane 1 has already shipped, and not as Arc 4 work.** v1.108.223 answered
[#399](https://github.com/jgravelle/jcodemunch-mcp/issues/399) with exactly this
arc's first lane: a retained, L2-normalised float32 matrix cached per store
stamp, NumPy as an optional accelerator with a tested pure-Python fallback and
zero mandatory dependency. Measured 1942 ms → 2.9 ms warm on 30,479 × 384.

⚠⚠ **That changes what this evidence is for.** Bucket (3) — float32 and float64
disagreeing on ordering — now describes **production code**, not parked design.
And measured here on a deliberately near-tied synthetic 4,000-vector corpus, the
two shipped lanes **disagreed at rank 0**: two installs of the same version
ranking differently based only on whether NumPy was importable. @rknighton's
zero-disagreement result on real corpora is the counterweight, and both belong
together — the hazard is real in principle and does not fire in practice on
Django, FastAPI or jcm. **The tie-break key shipped in v1.108.228 on the strength
of that**, which is a better reason than the one this section originally gave it.

**Lane 3 (certified uncertainty-set rescoring) is PARKED, and the premise to
re-examine is its own.** It exists to make an approximate scorer safe by
rescoring only uncertain candidates. The exact scorer is now 2.9 ms warm. Before
building it, someone has to show what it accelerates that is still slow —
otherwise it is a certification subsystem and a memory cap bolted to a path that
already costs single-digit milliseconds. **Lane 2 (chunked streaming) is
likewise parked**; v1.108.223's 2-repo cache bound addresses part of the memory
concern it was for.

⚠ **Stated limit of the evidence, not held against it:** four fixed queries per
corpus, one logical candidate row each. The gate never specified a query count,
and picking one now — after seeing a result we like — is exactly the move the
"neither side picks the bar after seeing results" rule forbids. It bounds how
strongly the zero-disagreement figure generalises; it does not bound the verdict.

---

Three exact-result lanes (retained float32 matrix / chunked streaming / certified
uncertainty-set rescoring), NumPy as an optional accelerator only.

⚠⚠ **The gate is a number we do not have.** Every semantic figure in the proposal
uses deterministic synthetic 384-dim vectors attached to real symbol IDs. Scoring
cost scales with vector count and dimension, so the matrix lane should carry over
— but **lane 3 certification breadth depends on embedding CONTENT**, and real code
embeddings produce more near-ties than the fixture. The reporter names this
himself. If certification fires often on real embeddings, the fast lane degrades
toward the exact scorer it replaces while carrying an optional native dependency
and a memory-cap subsystem.

**The measurement is SPECIFIED, 2026-08-02**, because a pooled number cannot fail
this arc for the right reason. Breadth reports in three buckets, never one
(@rknighton's refinement, accepted):

1. **exact ties** — identical embeddings, identical scores. Duplicated
   docstrings, boilerplate, generated files, near-identical test methods.
2. **near ties** — distinct scores inside float32 epsilon.
3. **genuine boundary cases** — float32 and float64 actually disagree on the
   ordering.

They have opposite remedies. If (1) dominates the answer is a deterministic
tie-break key, not abandoning the lane. If (3) dominates the gate has found the
real thing and the lane fails.

⚠ **The tie-break key is required regardless and ships FIRST, alone.** Today the
ranking sorts on score with a stable sort, so ties break on insertion order:
deterministic but arbitrary. Certifying that means reproducing an arbitrary row
order bit for bit. A `(score, symbol_id)` key is independently right - a ranking
should not depend on which row SQLite handed back first - and it collapses bucket
(1) before the measurement runs, so the number measures what it claims to.

**Corpus — Django is authoritative, FastAPI is the second point, jcm's own index
is a control and NOT authoritative.** It is the cheapest (already embedded) and
it is the one we tune against, so it is the wrong place to certify. Tie density
is a function of corpus HOMOGENEITY rather than size, so it gets reported as a
corpus property alongside the result, per repository, not folded into a pooled
figure.

**Threshold — named before the run, deliberately.** On the authoritative corpus:

- certification fires on **<= 10%** of scored candidates → PASS
- 10-25% → the lane needs a design answer before it ships
- **> 25%** → FAIL, the lane does not carry its dependency
- bucket (3) above **0.5%** → FAIL regardless of speed; that is a correctness
  signal, not a cost one

The 10% line is where the reported 9.7x-16.2x generation-warm result still clears
5x once the certified fraction pays exact-scorer cost. Fixing the number in
advance is the point: neither side picks the bar after seeing results.

**Close condition:** the three-bucket breadth measured on REAL embeddings on
Django, with tie density reported per corpus, against the thresholds above.
Until that exists this is accepted design, not approved work.

⚠ NumPy is native code, so the accelerated lane inherits
[[feedback_native_imports_belong_on_the_main_thread]]: one guarded main-thread
import before the four serve runners, extending `warm_up_embedding_backend()`
rather than adding a second startup mechanism, and disabled when
`JCODEMUNCH_EAGER_EMBED_IMPORT=0`. The base install must keep zero mandatory
NumPy dependency.

**Supporting material.** The Arc 1 / Arc 2 harnesses and fixed-schema CSVs are
published at [`rknighton/jcm-398-evidence`](https://github.com/rknighton/jcm-398-evidence)
with a `verify.py` that recomputes the quoted figures from the shipped CSVs.
⚠ Two standing cautions from the author, both worth honoring before aggregating:
the archive retains earlier exploratory rows at `6996cc08` beside the `c2201a55`
rows and **no `6996cc08` row backs a quoted figure** (filter on the provenance
column first), and Arc 2's classification screen is one pair per case, so its 58
Express/Gin sub-50ms cases cannot separate from timing variance - Django (5.17x)
and FastAPI (2.59x) are the load-bearing part. ✅ **Licensing resolved 2026-08-03: the repository is now
[MIT-0](https://github.com/rknighton/jcm-398-evidence/blob/main/LICENSE)** (MIT
No Attribution), relicensed by @rknighton on his own initiative after noticing
the omission. Verified against the LICENSE file, not the announcement. No
conditions, no attribution requirement, no notice to carry, so `verify.py` and
the harnesses may be vendored into this tree outright. The earlier
read-and-rerun-only restriction no longer applies.

**Provenance.** Filed by @rknighton as #398 on 2026-08-01 with a full research
archive offered on request. Split per the one-issue-one-verdict rule; #398 closed
as the umbrella once Arc 5 shipped and these four landed here. A post-close review
of the shipped Arc 5 code by the same reporter produced **v1.108.211** (the
`count()` removal collapsed a repository-level state, and the note attached to it
was giving wrong advice), which is why this section stays live after the umbrella
closed.

---

## Conventions

- Entries here are **accepted**, not speculative. A rejected proposal gets a
  closed issue with reasoning, not a roadmap line.
- Each entry keeps its **close condition** verbatim from the design that was
  accepted, so scope cannot drift quietly between filing and building.
- When an entry starts, it gets an issue, and its line here gains the link.
- Credit stays attached to the entry. Sequencing is not authorship.
