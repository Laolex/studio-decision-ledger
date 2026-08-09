# Studio Decision Ledger

A release-governance agent for media distribution teams. It decides whether a
title may be made available in a territory on a given date, records the
evidence and policy that produced the outcome, and lets a reviewer **replay
that decision later, after the underlying data has changed**.

Built for the Agentic Cinema hackathon, **ClickHouse track**.

## The problem

When a title gets pulled in a territory, nobody can reconstruct why six weeks
later. The rights table has moved, the policy has moved, and the reasoning was
never written down anywhere durable. Rights systems record the decision and
lose the reasoning; logs record the reasoning and lose the decision.

Studio Decision Ledger sits in that seam. It is not a rights-management system
and it does not give legal advice — it is the accountable decision layer that
binds existing operational facts to a documented outcome.

## What makes it different

Most audit trails record *what was decided*. This records *what was knowable at
the moment of deciding*, and can prove it.

- **Decisions are immutable.** A correction creates a new record. Nothing ever
  rewrites the outcome, inputs, or evidence of an earlier one.
- **Evidence is pinned, not referenced.** Each decision binds a snapshot that
  fixes a maximum data revision, plus the canonical query text and a hash of
  every result that fed the outcome.
- **The verifier refuses what it cannot support.** Replay returns a capability
  class — never a confidence percentage. If the snapshot, policy, or a result
  hash is missing or mismatched, it reports `NOT_CERTIFIED` and names the first
  failed requirement.
- **Model output is an artifact, not evidence.** Gemini's explanation is stored
  and shown, but it never determines the outcome and is never presented as
  proof that the model's reasoning is reproducible. A deterministic policy
  evaluator, separate from the model, produces the result.

### Capability classes

| Class | Meaning |
|---|---|
| `C2` | Snapshot and policy are available, hashes match, deterministic evaluation reproduces the original outcome. |
| `C3_BOUNDARY` | Evidence and outcome are bound and reproducible, but the model rationale remains an output artifact — not evidence of hidden model reasoning. This is the honest ceiling. |
| `NOT_CERTIFIED` | Required evidence, policy, result, or binding is absent or mismatched. The verifier explains why. |

## Runtime paths

Both required integrations are load-bearing, not decorative:

- **Google Cloud** — Gemini on Google Cloud Agent Builder is the operator-facing
  agent. It interprets the request, orchestrates evidence retrieval, identifies
  missing facts, and explains the outcome in plain language.
- **ClickHouse MCP server** — every decision-relevant fact is retrieved through
  the ClickHouse MCP server at runtime. The canonical query text and result
  hash from each MCP interaction are written into the decision record. There is
  no direct-driver bypass path in the decision flow.

## Data model

ClickHouse is the analytical system of record. The schema is **bitemporal**:

- *business time* — when a fact is true in the world (`valid_from` / `valid_to`)
- *system time* — when we came to know it (`revision`, monotonic)

Evidence tables are append-only. Correcting a fact inserts a new row version
with the same natural key and a higher `revision`; it never updates or deletes
the prior version. A snapshot pins `max_revision`, and point-in-time reads
filter `revision <= max_revision`, taking the latest surviving version per key.

This is what makes replay real rather than approximate. A decision recorded
against revision 1 continues to reproduce its original outcome even after a
**retroactive** correction lands at revision 3 that would have changed it.

## The demo dataset

Entirely synthetic. `North Star` S01E06 is fictional, and no rights, clearance,
rating, or delivery fact here describes a real contractual or regulatory
condition in any territory.

Three revisions carry the story:

| Rev | Recorded | Change |
|---|---|---|
| 1 | 2026-07-01 | Clean. Everything clears for Nigeria. |
| 2 | 2026-08-05 | Music sync window corrected — ends 2026-07-31, not 2027-06-01. |
| 3 | 2026-08-06 | Nigeria grant restated as **AVOD-only, backdated** to commencement. |

Revision 3 is the one that matters. It changes what the answer *would have
been* on a date already decided. A decision pinned at revision 1 must still
replay as `AVAILABLE`, and the comparison view must be able to say the
correction would now produce `HOLD` — without touching the original record.

## Repository layout

```
db/schema.sql   ClickHouse schema, bitemporal, append-only
db/seed.py      deterministic synthetic-data generator -> db/seed.sql
src/            React + Vite web console
```

## Setup

Generate the seed SQL (no database connection required):

```bash
python3 db/seed.py
```

Apply schema and seed to a ClickHouse instance (reads `api/.env`):

```bash
python3 db/apply.py db/schema.sql
python3 db/apply.py db/seed.sql
```

Web console:

```bash
npm install
npm run dev
```

### Bootstrap the demo decisions

Decision records are deliberately **not** seeded. They are produced by running
the real decision path, so that a reviewer can watch the pipeline create them
rather than take our word for it.

## Status

Honest state of the build:

- [x] Bitemporal ClickHouse schema
- [x] Deterministic synthetic dataset generator
- [x] Web console — currently a UI sketch against mocked data
- [x] Deterministic policy evaluator (11 tests)
- [ ] ClickHouse MCP retrieval and query-evidence capture
- [ ] Gemini agent on Google Cloud Agent Builder
- [ ] Decision-record write path
- [ ] Replay verifier
- [ ] Current-vs-historical comparison surface
- [ ] Hosted deployment

## Licence

Apache-2.0. See [LICENSE](LICENSE).
