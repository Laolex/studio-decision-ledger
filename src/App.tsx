import { useState } from "react";
import {
  ArrowRight,
  CheckmarkFilled,
  ChevronDown,
  Document,
  ErrorFilled,
  FlashFilled,
  Launch,
  Locked,
  PlayFilledAlt,
  Search,
  Settings,
  TaskComplete,
  Time,
  WarningFilled,
} from "@carbon/icons-react";
import { Button, Modal, Tag } from "@carbon/react";

type ReplayState = "idle" | "running" | "verified";

const evidence = [
  {
    icon: <Document size={18} aria-hidden="true" />,
    label: "Rights & clearances",
    summary: "1 blocking condition",
    tone: "hold",
    items: [
      ["Series licence", "Active through 31 Dec 2027", "clear"],
      ["Music cue: Midnight Drive", "Expired 31 Jul 2026", "hold"],
      ["Territory restriction", "Nigeria permitted", "clear"],
    ],
  },
  {
    icon: <TaskComplete size={18} aria-hidden="true" />,
    label: "Delivery & continuity",
    summary: "Ready to release",
    tone: "clear",
    items: [
      ["Final master", "Approved · IMF v7", "clear"],
      ["Continuity exceptions", "0 unresolved", "clear"],
      ["Accessibility package", "Captions and audio description ready", "clear"],
    ],
  },
  {
    icon: <Settings size={18} aria-hidden="true" />,
    label: "Release policy",
    summary: "Revision 3.4 applied",
    tone: "clear",
    items: [
      ["Territory rating", "15+ certificate valid", "clear"],
      ["Business rule", "No expired music clearance", "hold"],
      ["Policy revision", "Distribution policy v3.4", "clear"],
    ],
  },
];

function StatusMark({ tone }: { tone: string }) {
  return tone === "hold" ? (
    <WarningFilled className="status-icon hold" size={16} aria-label="Needs attention" />
  ) : (
    <CheckmarkFilled className="status-icon clear" size={16} aria-label="Verified" />
  );
}

export default function App() {
  const [replayState, setReplayState] = useState<ReplayState>("idle");
  const [showReplay, setShowReplay] = useState(false);
  const [showCompare, setShowCompare] = useState(false);
  const [showMemo, setShowMemo] = useState(false);

  function runReplay() {
    setReplayState("running");
    window.setTimeout(() => setReplayState("verified"), 900);
  }

  return (
    <main className="shell">
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
            <p className="subtitle">Season 1 · Episode 6 · Nigeria · 8 August 2026</p>
          </div>
          <div className="head-actions">
            <Button kind="tertiary" renderIcon={Time} onClick={() => setShowCompare(true)}>Compare current state</Button>
            <Button renderIcon={PlayFilledAlt} onClick={() => setShowReplay(true)}>Replay decision</Button>
          </div>
        </div>

        <section className="decision-hero" aria-labelledby="decision-title">
          <div className="decision-rule" />
          <div className="decision-content">
            <div>
              <p className="eyebrow">Current release decision</p>
              <h2 id="decision-title">Hold for clearance review</h2>
              <p className="decision-copy">A music-rights window expired after the title was last approved. The release remains paused until clearance is renewed or the cue is replaced.</p>
              <div className="reason-line"><WarningFilled size={18} /><span><b>Blocking condition:</b> Midnight Drive, scene 04:12-04:45, expired 31 July 2026.</span></div>
            </div>
            <div className="gate-panel" aria-label="Release status">
              <span className="gate-label">Release gate</span>
              <span className="gate-value">HOLD</span>
              <span className="gate-note">1 issue needs review</span>
            </div>
          </div>
        </section>

        <section className="drift-section" id="queue" aria-labelledby="drift-title">
          <div className="drift-intro">
            <p className="eyebrow">Decision Drift Radar</p>
            <h2 id="drift-title">This release changed after approval.</h2>
            <p>The release gate is monitored for material rights, policy, rating, and delivery changes. Only changes that alter a release condition appear here.</p>
            <button className="text-button" onClick={() => setShowCompare(true)}>Review the current evidence <ArrowRight size={15} /></button>
          </div>
          <article className="drift-card">
            <div className="drift-card-head"><div><span className="drift-kicker"><WarningFilled size={15} /> Action required</span><h3>Clearance changed</h3></div><Tag type="red">Release at risk</Tag></div>
            <div className="timeline" aria-label="Decision drift timeline">
              <div className="timeline-event complete"><span className="timeline-dot"><CheckmarkFilled size={14} /></span><div><b>30 Jul · Approved</b><p>D-1846 recorded AVAILABLE from a valid rights snapshot.</p></div></div>
              <div className="timeline-event active"><span className="timeline-dot"><WarningFilled size={14} /></span><div><b>31 Jul · Clearance expired</b><p>Music cue Midnight Drive is no longer covered for Nigeria.</p></div></div>
              <div className="timeline-event"><span className="timeline-dot"><Time size={14} /></span><div><b>08 Aug · Release hold</b><p>D-1847 requires a reviewer before the scheduled availability date.</p></div></div>
            </div>
            <div className="drift-actions"><Button kind="secondary" renderIcon={Document} onClick={() => setShowMemo(true)}>Draft escalation memo</Button><button className="text-button">Assign review <ArrowRight size={15} /></button></div>
          </article>
        </section>

        <div className="section-heading">
          <div><p className="eyebrow">Bound evidence</p><h2>Everything used to make this decision</h2></div>
          <span className="snapshot-chip"><Locked size={14} /> Snapshot RS-2026-08-08-0142</span>
        </div>

        <section className="evidence-grid" id="evidence" aria-label="Decision evidence">
          {evidence.map((group) => (
            <article className="evidence-panel" key={group.label}>
              <div className="evidence-head">
                <div className="evidence-title"><span className="source-icon">{group.icon}</span><h3>{group.label}</h3></div>
                <Tag type={group.tone === "hold" ? "red" : "green"}>{group.summary}</Tag>
              </div>
              <div className="evidence-list">
                {group.items.map(([name, value, tone]) => <div className="evidence-row" key={name}><StatusMark tone={tone} /><div><b>{name}</b><span>{value}</span></div></div>)}
              </div>
              <button className="text-button">Inspect evidence <ArrowRight size={15} /></button>
            </article>
          ))}
        </section>

        <section className="record-section" id="records">
          <div className="record-intro">
            <p className="eyebrow">Decision record</p>
            <h2>A receipt, not a summary.</h2>
            <p>Each decision binds the outcome to the evidence and policy available at that moment. A later change cannot silently rewrite the record.</p>
          </div>
          <article className="record-card">
            <div className="record-header"><div><span className="record-number">D-1847</span><h3>Can North Star be available in Nigeria today?</h3></div><Tag type="red">HOLD</Tag></div>
            <div className="record-meta"><span><Time size={15} /> Recorded 08 Aug 2026, 14:32 UTC</span><span><FlashFilled size={15} /> Policy engine v3.4</span></div>
            <div className="record-proof">
              <div><span>Evidence binding</span><b>Snapshot RS-2026-08-08-0142</b></div>
              <div><span>Capability class</span><b className="verified-text">C2 · reproducible</b></div>
              <div><span>Decision inputs</span><b>6 query results · 3 policy checks</b></div>
            </div>
            <div className="record-actions"><button className="text-button" onClick={() => setShowReplay(true)}>Open verifier <ArrowRight size={15} /></button><button className="text-button">View evidence manifest <Launch size={14} /></button></div>
          </article>
        </section>
      </section>

      <Modal
        open={showReplay}
        modalHeading="Replay decision D-1847"
        primaryButtonText={replayState === "verified" ? "Verified" : replayState === "running" ? "Replaying" : "Run replay"}
        secondaryButtonText="Close"
        primaryButtonDisabled={replayState !== "idle"}
        onRequestSubmit={runReplay}
        onRequestClose={() => { setShowReplay(false); setReplayState("idle"); }}
      >
        <div className="modal-copy">
          <p>This verifier will query the bound snapshot, apply Distribution Policy v3.4, and compare the replayed outcome to the original record.</p>
          {replayState === "idle" && <div className="verification-state neutral"><Locked size={20} /><span>Snapshot RS-2026-08-08-0142 is immutable and available.</span></div>}
          {replayState === "running" && <div className="verification-state pending"><Time size={20} /><span>Replaying bound evidence and policy checks…</span></div>}
          {replayState === "verified" && <div className="verification-state success"><CheckmarkFilled size={20} /><span><b>C2 certified.</b> The policy engine reproduced HOLD from the bound evidence.</span></div>}
        </div>
      </Modal>

      <Modal open={showCompare} modalHeading="Current state differs from the record" primaryButtonText="Open current evidence" secondaryButtonText="Close" onRequestSubmit={() => setShowCompare(false)} onRequestClose={() => setShowCompare(false)}>
        <div className="modal-copy"><p>The original decision record remains intact. Current data now shows a renewed series licence, but the music cue is still expired.</p><div className="verification-state warning"><ErrorFilled size={20} /><span>Current state cannot replace the snapshot used by D-1847.</span></div></div>
      </Modal>

      <Modal open={showMemo} modalHeading="Clearance escalation memo" primaryButtonText="Copy memo" secondaryButtonText="Close" onRequestSubmit={() => setShowMemo(false)} onRequestClose={() => setShowMemo(false)}>
        <div className="modal-copy"><p><b>Subject:</b> North Star S1E6: clearance renewal needed for Nigeria</p><p>The release gate changed to HOLD on 8 August because the Midnight Drive music clearance expired on 31 July. Please confirm renewal, replacement, or an approved exception before the scheduled release.</p><div className="verification-state neutral"><Document size={20} /><span>Draft grounded in decision D-1847, snapshot RS-2026-08-08-0142, and Distribution Policy v3.4.</span></div></div>
      </Modal>
    </main>
  );
}
