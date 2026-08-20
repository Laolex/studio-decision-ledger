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

### The ablation

Any system can claim its audit trail is meaningful. This one can show the
counterfactual. `POST /api/decisions/{id}/ablate` runs the same verifier twice
against the same record — once with the evidence binding, once with it withheld:

```
WITH binding    : C2
    Reproduced AVAILABLE from the pinned evidence at revision 1 under POL-2026.07.

WITHOUT binding : NOT_CERTIFIED | snapshot binding
    Decision D-1846 names snapshot RS-2026-07-30-0001, which is not available.
```

The outcome, the reasoning and the timestamp are all still there. Only the
binding is gone, and that alone is enough for the record to stop being evidence
of anything. The endpoint is read-only: an ablation that mutated a record to
make its point would be the exact failure it exists to warn about.

### An observed production run

One operator sentence, two tools, one handoff. Captured from the deployed
service on **2026-08-17**, Cloud Run revision **`sdl-00010-vnk`**, agent running
natively on Vertex AI Agent Engine. The full machine-readable capture is at
[`examples/production-transcript.json`](examples/production-transcript.json);
the decisive lines are below.

> **Operator:** What changed after D-1846, is the release now at risk, and
> prepare the reviewer handoff.

```
CALL    check_decision_drift    {"decision_id": "D-1846"}
RESULT  recorded_outcome: AVAILABLE   current_outcome: HOLD
        drifted: true                 record_unchanged: true

CALL    draft_escalation_memo   {"decision_id": "D-1846"}
RESULT  subject: NORTHSTAR-S01E06 — recorded AVAILABLE, current evidence
                 would produce HOLD, for NG on 30 July 2026
        drifted: true                 sent: false
```

> **Agent:** Decision D-1846, which recorded the release as AVAILABLE, has
> drifted. Current evidence (revision 3, compared to revision 1 when the
> decision was recorded) would now result in a HOLD because the territory grant
> does not cover this release path. Therefore, the release is at risk. Here is a
> draft escalation memo for reviewer handoff. Please note this memo has not been
> sent.

Reproduce it against the live service:

```
curl -s -X POST https://sdl-ntvbh3dlvq-uc.a.run.app/api/agent/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "What changed after D-1846, is the release now at risk, and prepare the reviewer handoff."}'
```

Two flags in that transcript carry the whole design. `record_unchanged: true` —
the drift was discovered without touching the record, and could not have been
otherwise, because nothing the agent can reach takes a writer. `sent: false` —
the memo is a draft; sending it, approving an exception and lifting a hold
remain human actions in the console. The historical `AVAILABLE` and the current
`HOLD` are both true at once, and neither is allowed to overwrite the other.

### The negative control, and the result that does not flatter us

The ablation covers one arm. The negative control runs the whole matrix through
the same production `verify()`, offline, with no credentials and no network — so
you can run it from a clean clone before you trust anything else here:

```
python3 scripts/negative_control.py
```

```
  ok    Intact record              →  C3_BOUNDARY
  ok    Model rationale removed    →  C2
  ok    Snapshot binding removed   →  NOT_CERTIFIED
  ok    Result hash mutated        →  NOT_CERTIFIED
  ok    Original record unchanged  →  PASS
```

It exits non-zero if any expectation fails. Every mutation is an in-memory copy;
the canonical form of the record is captured before the first arm and compared
after the last, so "the record was not touched" is measured, not asserted.

**Removing Gemini's prose changes nothing about verdict reproducibility.** Both
certified arms reproduce the recorded outcome from pinned evidence — the
verifier refuses to certify at all when it cannot. Gemini operates the
workflow; deterministic evidence and policy determine the gate.

That is worth stating plainly because it cuts against the obvious pitch. The
model is load-bearing for the *operator* — it is how a person asks a question,
gets a drift handoff, and receives a drafted memo — and deliberately not
load-bearing for *truth*. Rows one and two are the proof: stripping the model's
words moves the class from `C3_BOUNDARY` to `C2`, which is not a downgrade but
the removal of a boundary statement that only existed because model text was
present. The reproduced outcome is identical either way.

### Capability classes

| Class | Meaning |
|---|---|
| `C2` | Snapshot and policy are available, hashes match, deterministic evaluation reproduces the original outcome. |
| `C3_BOUNDARY` | Evidence and outcome are bound and reproducible, but the model rationale remains an output artifact — not evidence of hidden model reasoning. This is the honest ceiling. |
| `NOT_CERTIFIED` | Required evidence, policy, result, or binding is absent or mismatched. The verifier explains why. |

## Runtime paths

Both required integrations are load-bearing, not decorative:

- **Google Cloud** — Gemini on Google Cloud Agent Builder is the operator-facing
  agent: it interprets the request, orchestrates evidence retrieval, identifies
  missing facts, and explains the outcome in plain language. The explanation step
  and the ADK agent both run on Gemini through Vertex AI today. The deterministic
  path below does not depend on either of them, by design: the model operates and
  explains the workflow; it never determines the release gate.
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
db/apply.py     apply a .sql file over the ClickHouse HTTPS interface
api/sdl/        decision service: evaluator, retrieval, resolution, MCP client
api/tests/      tests, run against a real ClickHouse service
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
the real decision path, so a reviewer can watch the pipeline create them rather
than take our word for it.

```bash
python3 db/bootstrap_demo.py --verify
```

This records two decisions that ask the *same question about the same date*:

| | Taken | Pinned at | Outcome |
|---|---|---|---|
| `D-1846` | 30 Jul | revision 1 | `AVAILABLE` |
| `D-1847` | 8 Aug | revision 3 | `HOLD` · `LIC-002` |

Both replay to `C2`. Neither is wrong. `D-1846` answers what was knowable on 30
July; `D-1847` answers the same question after the grant was restated as
AVOD-only with retroactive effect. Keeping those two answers apart, and being
able to prove each one, is the entire product.

Re-running is safe — existing decisions are left alone rather than duplicated.

Open `/?decision=D-1847` in the hosted console to inspect the later `HOLD` record.
Its resolution plan names the blocking rule and bound evidence source, then rechecks
completion by rerunning the rule against current evidence. A checkbox or model claim
cannot mark the work complete, and the historical decision remains unchanged.

## Status

Honest state of the build:

- [x] Bitemporal ClickHouse schema
- [x] Deterministic synthetic dataset generator
- [x] Web console — wired to the live API (no mocked data)
- [x] Deterministic policy evaluator (11 tests)
- [x] ClickHouse MCP retrieval and query-evidence capture (25 tests)
- [x] Gemini rationale model on Vertex AI, behind the model seam (7 tests)
- [x] ADK agent on Vertex AI Agent Engine — three read-only tools, transcript shown in the console
- [x] Decision-record write path
- [x] Replay verifier (39 tests)
- [x] Current-vs-historical comparison surface
- [x] Hosted deployment — Cloud Run, console and API on one origin

## Licence

Apache-2.0. See [LICENSE](LICENSE).
