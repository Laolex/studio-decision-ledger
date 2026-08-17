import { useCallback, useEffect, useState, type ReactElement } from "react";
import {
  ArrowRight,
  CheckmarkFilled,
  ChevronDown,
  Document,
  ErrorFilled,
  FlashFilled,
  Copy,
  Locked,
  PlayFilledAlt,
  Search,
  Settings,
  TaskComplete,
  Time,
  WarningFilled,
} from "@carbon/icons-react";
import { Button, InlineLoading, Modal, Tag } from "@carbon/react";

import AgentPanel from "./AgentPanel";
import {
  ablateDecision,
  compareDecision,
  copyText,
  draftMemo,
  formatDate,
  getDecision,
  recordDecision,
  verifyDecision,
  type AblationPayload,
  type ComparisonPayload,
  type DecisionPayload,
  type EvidenceGroup,
  type MemoPayload,
  type Tone,
  type VerificationPayload,
} from "./api";

const TITLE_ID = "NORTHSTAR-S01E06";
const TERRITORY = "NG";
const EFFECTIVE_AT = "2026-07-30T00:00:00Z";

// The console opens on the decision taken before the corrections landed. Its
// record says AVAILABLE; current evidence would now say HOLD. Showing a freshly
// minted decision instead would pin to current data, and the record could never
// disagree with the present — which is the one thing worth seeing.
const DEMO_DECISION_ID = "D-1846";

const GROUP_ICONS: Record<string, ReactElement> = {
  "Rights & clearances": <Document size={18} aria-hidden="true" />,
  "Delivery & continuity": <TaskComplete size={18} aria-hidden="true" />,
  "Release policy": <Settings size={18} aria-hidden="true" />,
};

function StatusMark({ tone }: { tone: Tone }) {
  return tone === "hold" ? (
    <WarningFilled className="status-icon hold" size={16} aria-label="Needs attention" />
  ) : (
    <CheckmarkFilled className="status-icon clear" size={16} aria-label="Verified" />
  );
}

function outcomeHeadline(outcome: string): string {
  if (outcome === "AVAILABLE") return "Cleared for release";
  if (outcome === "ESCALATE") return "Send for human review";
  return "Hold for clearance review";
}

function outcomeCopy(outcome: string): string {
  if (outcome === "AVAILABLE")
    return "Every mandatory release condition is met for this territory and date. The evidence behind this decision is pinned and can be replayed after the underlying data changes.";
  if (outcome === "ESCALATE")
    return "The facts on file are incomplete or contradictory, so no safe determination is possible. This is deliberately not a hold: the policy is declining to assert something the evidence does not support.";
  return "A mandatory release condition is not met. The release stays paused until the blocking condition is resolved or an approved exception is recorded.";
}

export default function App() {
  const [decision, setDecision] = useState<DecisionPayload | null>(null);
  const [loadError, setLoadError] = useState<string>("");

  const [showReplay, setShowReplay] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [verification, setVerification] = useState<VerificationPayload | null>(null);
  const [replayError, setReplayError] = useState("");

  const [showCompare, setShowCompare] = useState(false);
  const [comparison, setComparison] = useState<ComparisonPayload | null>(null);

  const [showMemo, setShowMemo] = useState(false);
  const [memoCopied, setMemoCopied] = useState(false);
  const [memo, setMemo] = useState<MemoPayload | null>(null);
  const [memoError, setMemoError] = useState("");
  const [draftingMemo, setDraftingMemo] = useState(false);

  // Copies the draft rather than closing the modal, which is what the button
  // said it did. The modal stays open so the operator can see it worked.
  const copyMemo = useCallback(() => {
    if (!memo) return;
    const text = [
      memo.subject,
      "",
      memo.body,
      "",
      `Decision ${memo.grounded_in.decision_id} · snapshot ${memo.grounded_in.snapshot_id} · policy ${memo.grounded_in.policy_revision}`,
      "Draft — not sent.",
    ].join("\n");
    void copyText(text).then((ok) => {
      if (ok) {
        setMemoCopied(true);
        window.setTimeout(() => setMemoCopied(false), 2000);
      }
    });
  }, [memo]);

  // Drafted on open rather than on load: a memo nobody asked for is a model
  // call nobody needed, and the draft is not stored anywhere.
  const openMemo = useCallback(() => {
    setShowMemo(true);
    setMemoCopied(false);
    if (memo || draftingMemo) return;
    setDraftingMemo(true);
    setMemoError("");
    draftMemo(DEMO_DECISION_ID)
      .then(setMemo)
      .catch((error) => setMemoError(error instanceof Error ? error.message : String(error)))
      .finally(() => setDraftingMemo(false));
  }, [memo, draftingMemo]);

  const [showAblation, setShowAblation] = useState(false);
  const [ablation, setAblation] = useState<AblationPayload | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDecision(DEMO_DECISION_ID)
      .catch(() =>
        // Not bootstrapped yet — record one against current evidence so the
        // console still works on a fresh database.
        recordDecision({
          title_id: TITLE_ID,
          territory_code: TERRITORY,
          effective_at: EFFECTIVE_AT,
        }),
      )
      .then((payload) => {
        if (!cancelled) setDecision(payload);
      })
      .catch((error: Error) => {
        if (!cancelled) setLoadError(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runReplay = useCallback(() => {
    if (!decision) return;
    setReplaying(true);
    setReplayError("");
    verifyDecision(decision.decision_id)
      .then(setVerification)
      .catch((error: Error) => setReplayError(error.message))
      .finally(() => setReplaying(false));
  }, [decision]);

  const openAblation = useCallback(() => {
    setShowAblation(true);
    if (!decision || ablation) return;
    ablateDecision(decision.decision_id).then(setAblation).catch(() => undefined);
  }, [decision, ablation]);

  const openCompare = useCallback(() => {
    setShowCompare(true);
    if (!decision || comparison) return;
    compareDecision(decision.decision_id).then(setComparison).catch(() => undefined);
  }, [decision, comparison]);

  useEffect(() => {
    if (!decision) return;
    compareDecision(decision.decision_id).then(setComparison).catch(() => undefined);
  }, [decision]);

  if (loadError) {
    return (
      <main className="shell">
        <section className="content" id="top">
          <div className="page-head">
            <div>
              <p className="eyebrow">Release gate</p>
              <h1>Cannot reach the decision service</h1>
              <p className="subtitle">{loadError}</p>
            </div>
          </div>
        </section>
      </main>
    );
  }

  if (!decision) {
    return (
      <main className="shell">
        <section className="content" id="top">
          <div className="page-head">
            <div>
              <p className="eyebrow">Release gate</p>
              <h1>Resolving evidence…</h1>
              <p className="subtitle">
                Retrieving pinned rights, clearance, rating and delivery facts.
              </p>
            </div>
          </div>
        </section>
      </main>
    );
  }

  const groups: EvidenceGroup[] = decision.evidence_groups;
  const gateTone = decision.outcome === "AVAILABLE" ? "green" : "red";
  const drifted = (comparison?.differences.length ?? 0) > 0;

  return (
    <main className="shell with-agent">
      <aside className="sidebar" aria-label="Primary navigation">
        <a className="brand" href="#top" aria-label="Studio Decision Ledger home">
          <span className="brand-mark"><span /></span>
          <span>Studio<br />Decision Ledger</span>
        </a>

        <nav className="nav-list">
          <a href="#queue">Decision queue</a>
          <a className="active" href="#top" aria-current="page">Release readiness</a>
          <a href="#records">Decision records</a>
          <a href="#evidence">Evidence sources</a>
        </nav>

        <div className="sidebar-bottom">
          <a href="#settings" className="settings-link"><Settings size={16} /> Workspace settings</a>
          <div className="workspace-switcher">
            <span className="workspace-avatar">NS</span>
            <span><b>North Star</b><small>Production workspace</small></span>
            <ChevronDown size={16} aria-hidden="true" />
          </div>
        </div>
      </aside>

      <section className="content" id="top">
        <header className="topbar">
          <div className="crumbs"><span>North Star</span><ArrowRight size={14} /><span>Release readiness</span></div>
          <div className="topbar-actions">
            <button className="icon-button" aria-label="Search decisions"><Search size={18} /></button>
            <button className="avatar-button" aria-label="Account menu">LO</button>
          </div>
        </header>

        <div className="page-head">
          <div>
            <p className="eyebrow">Release gate</p>
            <h1>North Star</h1>
            <p className="subtitle">
              Season 1 · Episode 6 · Nigeria · {formatDate(decision.effective_at)}
            </p>
          </div>
          <div className="head-actions">
            <Button kind="tertiary" renderIcon={Time} onClick={openCompare}>Compare current state</Button>
            <Button renderIcon={PlayFilledAlt} onClick={() => setShowReplay(true)}>Replay decision</Button>
          </div>
        </div>

        <section className="decision-hero" aria-labelledby="decision-title">
          <div className="decision-rule" />
          <div className="decision-content">
            <div>
              <p className="eyebrow">Current release decision</p>
              <h2 id="decision-title">{outcomeHeadline(decision.outcome)}</h2>
              <p className="decision-copy">{outcomeCopy(decision.outcome)}</p>
              {decision.blocking_condition && (
                <div className="reason-line">
                  <WarningFilled size={18} />
                  <span><b>Blocking condition:</b> {decision.blocking_condition}</span>
                </div>
              )}
            </div>
            <div className="gate-panel" aria-label="Release status">
              <span className="gate-label">Release gate</span>
              <span className="gate-value">{decision.outcome}</span>
              <span className="gate-note">
                {decision.rule_hits.length === 0
                  ? "No blocking conditions"
                  : `${decision.rule_hits.length} issue${decision.rule_hits.length > 1 ? "s" : ""} need review`}
              </span>
            </div>
          </div>
        </section>

        <section className="drift-section" id="queue" aria-labelledby="drift-title">
          <div className="drift-intro">
            <p className="eyebrow">Decision Drift Radar</p>
            <h2 id="drift-title">
              {drifted ? "This release changed after approval." : "No material change since this decision."}
            </h2>
            <p>
              The release gate is monitored for material rights, policy, rating and delivery
              changes. Only changes that alter a release condition appear here.
            </p>
            <button className="text-button" onClick={openCompare}>
              Review the current evidence <ArrowRight size={15} />
            </button>
          </div>
          <article className="drift-card">
            <div className="drift-card-head">
              <div>
                <span className="drift-kicker">
                  {drifted ? <WarningFilled size={15} /> : <CheckmarkFilled size={15} />}
                  {drifted ? " Action required" : " Stable"}
                </span>
                <h3>{drifted ? "Evidence changed" : "Evidence unchanged"}</h3>
              </div>
              <Tag type={drifted ? "red" : "green"}>
                {drifted ? "Release at risk" : "In line with record"}
              </Tag>
            </div>
            <div className="timeline" aria-label="Decision drift timeline">
              <div className="timeline-event complete">
                <span className="timeline-dot"><CheckmarkFilled size={14} /></span>
                <div>
                  <b>{formatDate(decision.decided_at)} · Recorded</b>
                  <p>
                    {decision.decision_id} recorded {decision.outcome} from evidence pinned at
                    revision {decision.max_revision}.
                  </p>
                </div>
              </div>
              {comparison?.differences.map((difference) => (
                <div className="timeline-event active" key={difference}>
                  <span className="timeline-dot"><WarningFilled size={14} /></span>
                  <div><b>Current data</b><p>{difference}</p></div>
                </div>
              ))}
              {!drifted && comparison && (
                <div className="timeline-event">
                  <span className="timeline-dot"><Time size={14} /></span>
                  <div>
                    <b>Current data</b>
                    <p>Current evidence still produces {comparison.current.outcome} for this date.</p>
                  </div>
                </div>
              )}
            </div>
            <div className="drift-actions">
              <Button kind="secondary" renderIcon={Document} onClick={openMemo}>
                Draft escalation memo
              </Button>
            </div>
          </article>
        </section>

        <div className="section-heading">
          <div><p className="eyebrow">Bound evidence</p><h2>Everything used to make this decision</h2></div>
          <span className="snapshot-chip"><Locked size={14} /> Snapshot {decision.snapshot_id}</span>
        </div>

        <section className="evidence-grid" id="evidence" aria-label="Decision evidence">
          {groups.map((group) => (
            <article className="evidence-panel" key={group.label}>
              <div className="evidence-head">
                <div className="evidence-title">
                  <span className="source-icon">{GROUP_ICONS[group.label]}</span>
                  <h3>{group.label}</h3>
                </div>
                <Tag type={group.tone === "hold" ? "red" : "green"}>{group.summary}</Tag>
              </div>
              <div className="evidence-list">
                {group.items.map((item) => (
                  <div className="evidence-row" key={item.name}>
                    <StatusMark tone={item.tone} />
                    <div><b>{item.name}</b><span>{item.value}</span></div>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </section>

        <section className="record-section" id="records">
          <div className="record-intro">
            <p className="eyebrow">Decision record</p>
            <h2>A receipt, not a summary.</h2>
            <p>
              Each decision binds the outcome to the evidence and policy available at that
              moment. A later change cannot silently rewrite the record.
            </p>
          </div>
          <article className="record-card">
            <div className="record-header">
              <div>
                <span className="record-number">{decision.decision_id}</span>
                <h3>Can North Star be available in Nigeria on {formatDate(decision.effective_at)}?</h3>
              </div>
              <Tag type={gateTone}>{decision.outcome}</Tag>
            </div>
            <div className="record-meta">
              <span><Time size={15} /> Recorded {formatDate(decision.decided_at)}</span>
              <span><FlashFilled size={15} /> Policy {decision.policy_revision}</span>
            </div>
            <div className="record-proof">
              <div><span>Evidence binding</span><b>Snapshot {decision.snapshot_id}</b></div>
              <div>
                <span>Capability class</span>
                <b className={verification?.capability_class === "NOT_CERTIFIED" ? "" : "verified-text"}>
                  {verification ? verification.capability_class : "Not yet replayed"}
                </b>
              </div>
              <div>
                <span>Decision inputs</span>
                <b>{decision.retrieval_count} pinned retrievals · revision {decision.max_revision}</b>
              </div>
            </div>
            <div className="record-actions">
              <button className="text-button" onClick={() => setShowReplay(true)}>
                Open verifier <ArrowRight size={15} />
              </button>
              <button className="text-button" onClick={openAblation}>
                Test the binding <ArrowRight size={15} />
              </button>
              {/* A value, not a destination. It previously carried a Launch
                  icon and no handler, which promised a page that does not
                  exist. Full hash on hover; click copies it. */}
              <button
                className="text-button"
                title={decision.source_manifest_hash}
                onClick={() => void copyText(decision.source_manifest_hash)}
              >
                Manifest {decision.source_manifest_hash.slice(0, 12)}… <Copy size={14} />
              </button>
            </div>
          </article>
        </section>
      </section>

      <AgentPanel />

      <Modal
        open={showReplay}
        modalHeading={`Replay decision ${decision.decision_id}`}
        primaryButtonText={replaying ? "Replaying" : verification ? "Replay again" : "Run replay"}
        secondaryButtonText="Close"
        primaryButtonDisabled={replaying}
        onRequestSubmit={runReplay}
        onRequestClose={() => setShowReplay(false)}
      >
        <div className="modal-copy">
          <p>
            The verifier re-issues every pinned query against snapshot {decision.snapshot_id},
            re-applies policy {decision.policy_revision}, and compares the replayed outcome to
            the record. It reports a capability class, never a confidence score.
          </p>

          {!verification && !replaying && !replayError && (
            <div className="verification-state neutral">
              <Locked size={20} />
              <span>Snapshot {decision.snapshot_id} is immutable and available.</span>
            </div>
          )}

          {replaying && (
            <div className="verification-state pending">
              <InlineLoading description="Re-issuing pinned queries and re-applying policy…" />
            </div>
          )}

          {replayError && (
            <div className="verification-state warning">
              <ErrorFilled size={20} />
              <span><b>Verifier unavailable.</b> {replayError}</span>
            </div>
          )}

          {verification?.capability_class === "NOT_CERTIFIED" && (
            <div className="verification-state warning">
              <ErrorFilled size={20} />
              <span>
                <b>Not certified — {verification.failed_requirement}.</b> {verification.detail}
              </span>
            </div>
          )}

          {verification && verification.capability_class !== "NOT_CERTIFIED" && (
            <div className="verification-state success">
              <CheckmarkFilled size={20} />
              <span>
                <b>{verification.capability_class} certified.</b> {verification.detail}
              </span>
            </div>
          )}
        </div>
      </Modal>

      <Modal
        open={showCompare}
        modalHeading="Current state beside the record"
        primaryButtonText="Close"
        secondaryButtonText="Close"
        onRequestSubmit={() => setShowCompare(false)}
        onRequestClose={() => setShowCompare(false)}
      >
        <div className="modal-copy">
          {!comparison && <InlineLoading description="Resolving current evidence…" />}
          {comparison && (
            <>
              <p>
                The record stands at <b>{comparison.historical.outcome}</b>, pinned to revision{" "}
                {comparison.historical.max_revision}. Current evidence is at revision{" "}
                {comparison.current.max_revision} and would produce{" "}
                <b>{comparison.current.outcome}</b> for the same date.
              </p>
              {comparison.differences.map((difference) => (
                <div className="verification-state warning" key={difference}>
                  <WarningFilled size={20} />
                  <span>{difference}</span>
                </div>
              ))}
              <div className="verification-state neutral">
                <Locked size={20} />
                <span>
                  Current state cannot replace the snapshot used by {decision.decision_id}. This
                  view never writes to the record.
                </span>
              </div>
            </>
          )}
        </div>
      </Modal>

      <Modal
        open={showAblation}
        modalHeading="What is this record worth without its binding?"
        primaryButtonText="Close"
        secondaryButtonText="Close"
        onRequestSubmit={() => setShowAblation(false)}
        onRequestClose={() => setShowAblation(false)}
      >
        <div className="modal-copy">
          {!ablation && <InlineLoading description="Running the verifier twice…" />}
          {ablation && (
            <>
              <p>
                The same verifier, run twice against {ablation.decision_id}. The second run
                withholds one thing: the {ablation.withheld}.
              </p>
              <div className="verification-state success">
                <CheckmarkFilled size={20} />
                <span>
                  <b>With the binding — {ablation.with_binding.capability_class}.</b>{" "}
                  {ablation.with_binding.detail}
                </span>
              </div>
              <div className="verification-state warning">
                <ErrorFilled size={20} />
                <span>
                  <b>
                    Without it — {ablation.without_binding.capability_class}
                    {ablation.without_binding.failed_requirement
                      ? ` (${ablation.without_binding.failed_requirement})`
                      : ""}.
                  </b>{" "}
                  {ablation.without_binding.detail}
                </span>
              </div>
              <p>{ablation.explanation}</p>
            </>
          )}
        </div>
      </Modal>

      <Modal
        open={showMemo}
        modalHeading="Clearance escalation memo — draft"
        primaryButtonText={memoCopied ? "Copied" : "Copy draft"}
        secondaryButtonText="Close"
        primaryButtonDisabled={!memo}
        onRequestSubmit={copyMemo}
        onRequestClose={() => setShowMemo(false)}
      >
        <div className="modal-copy">
          {draftingMemo && <InlineLoading description="Drafting…" />}
          {memoError && (
            <div className="verification-state warning">
              <ErrorFilled size={20} />
              <span>{memoError}</span>
            </div>
          )}
          {memo && (
            <>
              <p><b>Subject:</b> {memo.subject}</p>
              <p>{memo.body}</p>
              <div className="verification-state neutral">
                <Document size={20} />
                <span>
                  Grounded in decision {memo.grounded_in.decision_id}, snapshot{" "}
                  {memo.grounded_in.snapshot_id}, and policy{" "}
                  {memo.grounded_in.policy_revision}. This is a draft — sending it
                  is a human action.
                </span>
              </div>
            </>
          )}
        </div>
      </Modal>
    </main>
  );
}
