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

The design work below is [@mightydanp](https://github.com/mightydanp)'s,
proposed in [this comment](https://github.com/jgravelle/jcodemunch-mcp/issues/377#issuecomment-5076253159)
on [#377](https://github.com/jgravelle/jcodemunch-mcp/issues/377) and accepted
as written.

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

**Close condition.** As accepted in the original design comment.

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

## Conventions

- Entries here are **accepted**, not speculative. A rejected proposal gets a
  closed issue with reasoning, not a roadmap line.
- Each entry keeps its **close condition** verbatim from the design that was
  accepted, so scope cannot drift quietly between filing and building.
- When an entry starts, it gets an issue, and its line here gains the link.
- Credit stays attached to the entry. Sequencing is not authorship.
