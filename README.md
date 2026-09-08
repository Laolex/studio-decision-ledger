# Studio Decision Ledger

Six weeks ago, a distribution team correctly cleared an episode for release in
Nigeria. Current rights evidence now blocks it. Studio Decision Ledger proves
both statements, preserves the original decision, and tells the reviewer what
evidence-bound work must happen next.

It is the release decision layer for media distribution teams: decide whether a
title may be made available in a territory on a given date, bind the outcome to
the evidence and policy that produced it, then replay the decision after the
underlying data changes without rewriting history.

> Correct then. Blocked now. History stays intact.

Built for the Agentic Cinema hackathon, **ClickHouse track**.

**Current production release:** application commit/image `0601fb0`, Cloud Run
revision `sdl-00020-ghc`. The release passed 163 backend tests against ClickHouse, the
six-check production pre-flight, the five-arm negative control, and browser
inspection of the deployed console. No schema migration was required.

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
- **The receipt can test the current serving path.** An on-demand integrity
  probe identifies the exact Cloud Run revision and installed MCP worker build,
  measures checkout wait from a bounded two-worker pool, then reruns every
  stored canonical query serially and compares the new hashes with the receipt.
  These are labelled as live diagnostics, never historical decision evidence.
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
service on **2026-08-17**, Cloud Run revision **`sdl-00010-vnk`**, with the agent running
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

### The live integrity probe

Open **Inspect the evidence receipt** in the console and press **Run live
integrity probe**, or call the same read-only path directly:

```bash
curl -s -X POST \
  https://sdl-ntvbh3dlvq-uc.a.run.app/api/decisions/D-1846/integrity-probe
```

The promoted release reports its actual runtime identity and recomputes each
binding rather than printing a predetermined green state. A verified response
has this shape; timing values vary by request:

```json
{
  "service_revision": "sdl-00020-ghc",
  "worker": {
    "package": "mcp-clickhouse",
    "version": "0.5.0",
    "pool_size": 2
  },
  "pool_wait": {
    "samples": 5,
    "max_ms": 0.02
  },
  "serial_canonical_rehash": {
    "status": "VERIFIED",
    "checked": 5,
    "matched": 5
  }
}
```

`pool_wait` measures time waiting to lease an MCP worker, separately from query
execution. `VERIFIED` means every newly computed result hash matched the hash
stored in the named evidence snapshot. A source error or mismatch produces a
different status; the probe does not edit the decision or its snapshot.

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

### Release workbench

The console's release workbench accepts a title ID, territory and UTC release
instant. Catalogue suggestions come from stored licence evidence. Previewing
reads current evidence without writing a receipt; **Record decision** is an
explicit subsequent action. A fingerprint binds the previewed request, policy
and evidence, and recording returns HTTP 409 if they no longer match. Edit or
preview again before retrying.

The recent-history view lists up to fifty receipts for the selected title.
Follow-up mode links a new receipt to the open receipt through `supersedes` and
locks the title, territory and release instant. Earlier receipts remain readable
and unchanged; multiple follow-ups can reference one predecessor. There is no
claim that this establishes a unique approved head.

`GET /api/catalogue` and `GET /api/decisions?title_id=…&limit=50` are read-only.
`POST /api/evidence` returns `preview_token`; the workbench sends it as
`expected_preview_token` to `POST /api/decisions`, with optional `supersedes`.
These fields are optional for existing API callers. The fingerprint is a
consistency check, not an authentication token. The public dataset remains
synthetic; private studio onboarding, imports and reviewer assignment are not
provided by this workbench.

### Private workspace API access

The optional private mode protects this API deployment with individually issued
bearer credentials. It is **one workspace per isolated deployment and ClickHouse
service**, not shared-database tenant isolation. The workspace ID is an identity
label, not a SQL filter. Never point a private studio deployment at the public
synthetic service or another studio's database. Database provisioning and browser
sign-in are not automated by this change; the existing console does not yet send
credentials. Use an authenticated API client for this mode.

Set these in the process environment (not `api/.env`): `SDL_ACCESS_MODE=private`,
`SDL_WORKSPACE_ID` to a stable alphanumeric/hyphen/underscore identifier, and
`SDL_ACCESS_KEYS` to a JSON array of objects with exactly `sha256`, `subject` and
`role` fields. Generate each credential with `secrets.token_urlsafe(32)` in a
trusted provisioning environment; distribute the raw secret securely to its
owner, and configure only its lowercase SHA-256 hex digest. Roles are `reader`
or `operator`. Never put raw credentials in source, URLs, browser storage, shell
history or logs. Serve only over HTTPS. Replace/remove the digest and restart
all instances to rotate/revoke a credential; there is no session or expiry store.

Clients send `Authorization: Bearer <credential>` on every API request.
`GET /api/workspace/session` reports the authenticated subject, role and workspace
without exposing credentials. Readers can browse, preview, compare, verify, probe
and inspect/recheck resolution plans. Operators can additionally record decisions
and generate memos. New endpoints require operator permission by default. Missing
or invalid credentials return 401; insufficient authority returns 403, before
data/model dependencies run. Authentication identity is not yet persisted into
historical decision receipts; this is access control, not an actor audit trail.

Private mode disables cross-origin access and the public API documentation routes.
Static assets and the minimal `/api/health` response remain public. The remote
agent endpoint returns 503 even for operators because its separately deployed
data/session store has not been workspace-isolated. Do not reuse that external
agent for private data. Misconfigured private mode fails application construction.
The default remains `public` for compatibility with the existing synthetic release;
workspace configuration supplied in public mode is rejected to catch accidental
downgrades. No production configuration is changed by adding this feature.

Both required integrations are load-bearing, not decorative:

- **Google Cloud** — Gemini on Google Cloud Agent Builder is the operator-facing
  agent: it interprets the request, orchestrates evidence retrieval, identifies
  missing facts, and explains the outcome in plain language. The explanation step
  and the ADK agent both run on Gemini through Vertex AI today. The deterministic
  path below does not depend on either of them, by design: the model operates and
  explains the workflow; it never determines the release gate.
- **ClickHouse MCP server** — every decision-relevant fact is retrieved through
  a bounded pool of two long-lived ClickHouse MCP workers at runtime. The
  canonical query text and result hash from each interaction are stored in the
  immutable evidence snapshot named by the decision. There is no direct-driver
  bypass path in the decision flow. Pool checkout wait is measured at the lease
  boundary rather than inferred from query duration.

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
api/sdl/        decision service: evaluator, retrieval, resolution, MCP worker pool
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
- [x] Deterministic policy evaluator
- [x] ClickHouse MCP retrieval and query-evidence capture
- [x] Bounded two-worker MCP pool with measured checkout wait
- [x] Live serial canonical re-hash probe with explicit mismatch/source-error states
- [x] Expanded evidence receipt with canonical queries and full result hashes
- [x] Gemini rationale model on Vertex AI, behind the model seam
- [x] ADK agent on Vertex AI Agent Engine — three read-only tools, transcript shown in the console
- [x] Decision-record write path
- [x] Replay verifier and offline five-arm negative control
- [x] Current-vs-historical comparison surface
- [x] Hosted deployment — Cloud Run, console and API on one origin
- [x] Release verification — 163 backend tests, six live pre-flight checks, production browser inspection

## Licence

Apache-2.0. See [LICENSE](LICENSE).
