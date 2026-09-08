import { useEffect, useState, type FormEvent } from "react";
import { Button, InlineLoading } from "@carbon/react";
import { request } from "./api";

type Template = {table: string; columns: string[]; required: string[]};
type Row = Record<string, unknown>;
type Preview = {valid: boolean; row_count: number; rows: Row[]; content_sha256: string | null; expected_head: number; corrections: number; changes: Array<{key: string[]; before: Row | null; after: Row}>; issues: Array<{row: number; field: string; message: string}>};
type Receipt = {import_id: string; status: string; table: string; actor: string; recorded_at: string; revision: number; row_count?: number; source_reference: string; content_sha256: string};
const labels: Record<string, string> = {title_licenses: "Licences", clearances: "Clearances", ratings: "Ratings", deliveries: "Deliveries", continuity_exceptions: "Continuity exceptions", synthetic_content: "Synthetic-content declarations", performer_consents: "Performer consents"};

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function EvidenceImport({canImport, onPublished}: {canImport: boolean; onPublished: () => void}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [history, setHistory] = useState<Receipt[]>([]);
  const [table, setTable] = useState("title_licenses");
  const [source, setSource] = useState("");
  const [csv, setCsv] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [allowCorrections, setAllowCorrections] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const template = templates.find(item => item.table === table);
  function invalidate() { setPreview(null); setReceipt(null); setAllowCorrections(false); setError(""); }
  async function loadHistory() {
    try { setHistory((await request<{imports: Receipt[]}>("/api/imports")).imports); setHistoryError(""); }
    catch { setHistoryError("Import history is unavailable. Retry to check for pending imports before publishing."); }
  }
  useEffect(() => {
    void request<{templates: Template[]}>("/api/imports/templates").then(result => setTemplates(result.templates)).catch(() => setError("Cannot load import formats. Reload to retry."));
    void loadHistory();
  }, []);
  async function readFile(file?: File) {
    invalidate(); setCsv("");
    if (!file) return;
    if (file.size > 262144) { setError("The file exceeds 256 KiB. Split it into smaller reviewed batches."); return; }
    setBusy("Reading file…");
    try { setCsv(new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer())); }
    catch { setError("This file is not valid UTF-8 CSV. Export it as UTF-8 and retry."); }
    finally { setBusy(""); }
  }
  async function review(event: FormEvent) {
    event.preventDefault(); invalidate(); setBusy("Checking the file and existing evidence…");
    try { setPreview(await request<Preview>("/api/imports/preflight", {method: "POST", body: JSON.stringify({table, source_reference: source, csv_text: csv})})); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  }
  async function publish() {
    if (!preview?.valid) return;
    setBusy("Publishing the reviewed evidence…"); setError("");
    try {
      setReceipt(await request<Receipt>("/api/imports", {method: "POST", body: JSON.stringify({table, source_reference: source, csv_text: csv, expected_head: preview.expected_head, content_sha256: preview.content_sha256, allow_corrections: allowCorrections})}));
      setPreview(null); onPublished();
    } catch (cause) { setError(String(cause)); setPreview(null); }
    finally { setBusy(""); await loadHistory(); }
  }
  async function retry(id: string) {
    setBusy("Reconciling the original import…"); setError("");
    try { setReceipt(await request<Receipt>(`/api/imports/${encodeURIComponent(id)}/retry`, {method: "POST"})); onPublished(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(""); await loadHistory(); }
  }
  return <section className="studio-import" aria-labelledby="import-title">
    <h2 id="import-title">Bring your evidence into the workspace</h2>
    <p>CSV files are checked before anything is published. Imports preserve earlier versions and record who submitted the evidence. A source reference is not independent verification of a rights claim.</p>
    {canImport ? <form onSubmit={review}>
      <fieldset disabled={Boolean(busy)}>
        <legend>Evidence file</legend>
        <div className="studio-fields">
          <label>Evidence type<select value={table} onChange={event => {setTable(event.target.value); invalidate();}}>{templates.map(item => <option key={item.table} value={item.table}>{labels[item.table]}</option>)}</select></label>
          <label>Source document reference<input required maxLength={500} value={source} onChange={event => {setSource(event.target.value); invalidate();}} placeholder="Contract, certificate or delivery record ID" /></label>
          <label>UTF-8 CSV file<input type="file" accept=".csv,text/csv" onChange={event => void readFile(event.target.files?.[0])} /></label>
        </div>
        <p>Up to 500 records and 256 KiB. Timestamps need a timezone; corrections need an amendment note.</p>
        <div className="studio-actions"><Button kind="ghost" type="button" disabled={!template} onClick={() => template && download(`${table}-template.csv`, template.columns.join(",") + "\n", "text/csv")}>Download CSV headers</Button><Button type="submit" disabled={!csv || !source.trim()}>Review file</Button></div>
        {template && <details><summary>Required columns and accepted values</summary><p className="studio-code">{template.required.join(", ")}</p><p>Licences: SVOD, AVOD, FAST or TVOD; ACTIVE, SUSPENDED or TERMINATED. Clearances: MUSIC_SYNC, MUSIC_MASTER, STOCK_FOOTAGE or TALENT; ACTIVE, EXPIRED or REVOKED. Ratings: VALID, EXPIRED or WITHDRAWN. Deliveries: APPROVED, PENDING or ABSENT; approval time may be blank. Continuity: BLOCKING or ADVISORY; OPEN, RESOLVED or WAIVED. Generation: SYNTHETIC, ASSISTED or NONE. Consent: likeness, voice or both; ACTIVE, WITHDRAWN or EXPIRED.</p></details>}
      </fieldset>
    </form> : <p>You have reader access. Ask a studio operator to import or correct evidence; you can inspect history and evaluate releases below.</p>}
    {busy && <InlineLoading description={busy} />}
    {error && <p role="alert" className="workbench-error">{error}</p>}
    {preview && <section className="studio-review" aria-live="polite">
      <h3>{preview.valid ? `Review ${preview.row_count} evidence ${preview.row_count === 1 ? "record" : "records"}` : "Fix the file before importing"}</h3>
      {!preview.valid ? <ul>{preview.issues.map((issue, index) => <li key={index}>Record {issue.row}, {issue.field}: {issue.message}</li>)}</ul> : <>
        <p>Nothing published yet. {preview.corrections} corrections to existing evidence. Current workspace revision: {preview.expected_head}.</p>
        <details><summary>Inspect every normalized record and prior value</summary><div className="studio-change-list">{preview.changes.map(change => <section key={change.key.join("|")}>
          <h4>{change.key.join(" · ")}</h4>
          <table><thead><tr><th>Field</th><th>Previous evidence</th><th>Reviewed value</th></tr></thead><tbody>{Object.entries(change.after).map(([field, value]) => <tr key={field}><th scope="row">{field}</th><td>{change.before ? String(change.before[field] ?? "Not supplied") : "New record"}</td><td>{String(value ?? "Not supplied")}</td></tr>)}</tbody></table>
        </section>)}</div></details>
        <p className="studio-code">Content SHA-256: {preview.content_sha256}</p>
        {preview.corrections > 0 && <label className="studio-confirm"><input type="checkbox" checked={allowCorrections} disabled={Boolean(busy)} onChange={event => setAllowCorrections(event.target.checked)} />I reviewed these corrections. Keep earlier versions and append the changes.</label>}
        <Button onClick={() => void publish()} disabled={Boolean(busy) || (preview.corrections > 0 && !allowCorrections)}>Publish {preview.row_count} {preview.row_count === 1 ? "record" : "records"}</Button>
      </>}
    </section>}
    {receipt && <section className="studio-review" aria-live="polite"><h3>Evidence published at revision {receipt.revision}</h3><p>{receipt.actor} · {receipt.recorded_at} · {receipt.source_reference}</p><p className="studio-code">{receipt.import_id}</p><Button kind="ghost" onClick={() => download(`${receipt.import_id}.json`, JSON.stringify(receipt, null, 2), "application/json")}>Download import receipt</Button><a href="#workbench">Evaluate a release with this evidence</a></section>}
    <details className="studio-history"><summary>Import history and pending work</summary>
      {historyError && <p role="alert">{historyError}</p>}<Button kind="ghost" size="sm" disabled={Boolean(busy)} onClick={() => void loadHistory()}>Refresh history</Button>
      {!historyError && history.length === 0 && <p>No imports yet. Start with a source file above, then add the other evidence required by the release policy.</p>}
      <ul>{history.map(item => <li key={item.import_id}><strong>{labels[item.table]} · revision {item.revision} · {item.status}</strong><p>{item.actor} · {item.recorded_at} · {item.source_reference}</p><p className="studio-code">{item.import_id}</p>{item.status === "pending" && canImport && <Button size="sm" disabled={Boolean(busy)} onClick={() => void retry(item.import_id)}>Retry this import</Button>}</li>)}</ul>
      <p>Latest 100 imports. A pending import blocks new publication until its original payload is reconciled.</p>
    </details>
  </section>;
}
