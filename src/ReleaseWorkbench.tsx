import { useEffect, useState, type FormEvent } from "react";
import { Button, InlineLoading, Tag } from "@carbon/react";
import {
  getCatalogue, getDecisionHistory, previewRelease, recordDecision, formatReleaseInstant,
  type DecisionPayload, type DecisionSummary, type PreviewPayload,
} from "./api";

export default function ReleaseWorkbench({ current }: { current?: DecisionPayload }) {
  const [title, setTitle] = useState(current?.title_id ?? "");
  const [territory, setTerritory] = useState(current?.territory_code ?? "");
  const [date, setDate] = useState(current?.effective_at.slice(0, 19) ?? "");
  const [followUp, setFollowUp] = useState(false);
  const [catalogue, setCatalogue] = useState<Array<{ title_id: string; territory_code: string }>>([]);
  const [history, setHistory] = useState<DecisionSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [catalogueError, setCatalogueError] = useState("");
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<PreviewPayload | null>(null);
  const [busy, setBusy] = useState<"preview" | "record" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      getCatalogue().then(data => { if (active) setCatalogue(data.releases); }).catch(() => { if (active) setCatalogueError("Catalogue unavailable. You can still enter a known title and territory."); }),
      getDecisionHistory(current?.title_id).then(data => { if (active) setHistory(data.decisions); }).catch(() => { if (active) setHistoryError("Decision history is unavailable. Reload to try again."); }),
    ]).then(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [current?.title_id]);

  function invalidate() { setPreview(null); setError(""); }

  async function evaluate(event: FormEvent) {
    event.preventDefault();
    setBusy("preview"); invalidate();
    try {
      setPreview(await previewRelease({ title_id: title.trim(), territory_code: territory.trim().toUpperCase(), effective_at: followUp && current ? current.effective_at : `${date}Z` }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(null); }
  }

  async function record() {
    if (!preview || busy) return;
    setBusy("record"); setError("");
    try {
      const result = await recordDecision({
        title_id: preview.title_id, territory_code: preview.territory_code,
        effective_at: preview.effective_at, policy_revision: preview.policy_revision,
        expected_preview_token: preview.preview_token,
        supersedes: followUp ? current?.decision_id : undefined,
      });
      window.location.assign(`?decision=${encodeURIComponent(result.decision_id)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setPreview(null);
    } finally { setBusy(null); }
  }

  return (
    <details className="release-workbench" id="workbench" open={!current || undefined}>
      <summary>Evaluate a release and browse decision history</summary>
      <div className="workbench-body">
        <h2>Choose the release to evaluate</h2>
        <p>Preview the evidence first. Recording creates a permanent receipt. The public dataset contains fictional titles and evidence.</p>
        {catalogueError && <p role="status">{catalogueError}</p>}
        <form onSubmit={evaluate}>
          {current && <label className="follow-up-choice">
            <input type="checkbox" checked={followUp} disabled={Boolean(busy)} onChange={event => {
              setFollowUp(event.target.checked); invalidate();
              if (event.target.checked) { setTitle(current.title_id); setTerritory(current.territory_code); setDate(current.effective_at.slice(0, 19)); }
            }} />
            Link the new receipt to {current.decision_id} for the same release
          </label>}
          {followUp && current && <p>Exact release instant: {formatReleaseInstant(current.effective_at)}</p>}
          <fieldset disabled={Boolean(busy)}>
            <legend className="workbench-legend">Release request</legend>
            <div className="release-fields">
              <label>Title ID<input list="release-titles" value={title} required maxLength={200} disabled={followUp} onChange={event => { setTitle(event.target.value); invalidate(); }} /></label>
              <datalist id="release-titles">{[...new Set(catalogue.map(item => item.title_id))].map(value => <option key={value} value={value} />)}</datalist>
              <label>Territory code<input list="release-territories" value={territory} required pattern="[A-Za-z]{2}" maxLength={2} disabled={followUp} onChange={event => { setTerritory(event.target.value); invalidate(); }} /></label>
              <datalist id="release-territories">{[...new Set(catalogue.filter(item => item.title_id === title).map(item => item.territory_code))].map(value => <option key={value} value={value} />)}</datalist>
              <label>Release date and time (UTC)<input type="datetime-local" step="1" value={date} required disabled={followUp} onChange={event => { setDate(event.target.value); invalidate(); }} /></label>
            </div>
            <Button type="submit" kind="secondary" disabled={Boolean(busy)}>Preview current evidence</Button>
          </fieldset>
        </form>
        {busy && <InlineLoading description={busy === "preview" ? "Evaluating current evidence…" : "Recording the reviewed decision…"} />}
        {error && <p className="workbench-error" role="alert">{error}</p>}
        {preview && <section className="release-preview" aria-label="Unrecorded evaluation" aria-live="polite">
          <h3><Tag type={preview.outcome === "AVAILABLE" ? "green" : "red"}>{preview.outcome}</Tag> Preview — not recorded</h3>
          <p>{preview.blocking_condition || "All conditions in the selected policy are met."}</p>
          <p>{preview.title_id} · {preview.territory_code} · {preview.effective_at} · {preview.policy_revision} · evidence revision {preview.max_revision}</p>
          <ul>{preview.evidence_groups.map(group => <li key={group.label}><b>{group.label}:</b> {group.summary}</li>)}</ul>
          <Button onClick={() => void record()} disabled={Boolean(busy)}>Record {preview.outcome} decision</Button>
          <p>The service checks the evidence again before recording. Changed evidence requires a new preview.</p>
        </section>}
        <section className="workbench-history" aria-label="Recorded decisions">
          <h3>{current ? `Recent decisions for ${current.title_id}` : "Recent decisions"}</h3>
          <p>The latest 50 receipts, including earlier decisions. Opening a receipt does not create one.</p>
          {loading ? <InlineLoading description="Loading decision history…" /> : historyError ? <p role="status">{historyError}</p> : history.length === 0 ? <p>No decisions have been recorded for this title.</p> :
            <ul>{history.map(item => <li key={item.decision_id}>
              <a href={`?decision=${encodeURIComponent(item.decision_id)}`}>{item.decision_id}</a>
              <span>{item.outcome} · {item.territory_code} · release {formatReleaseInstant(item.effective_at)}</span>
              {item.supersedes && <small>Follows <a href={`?decision=${encodeURIComponent(item.supersedes)}`}>{item.supersedes}</a></small>}
            </li>)}</ul>}
        </section>
      </div>
    </details>
  );
}
