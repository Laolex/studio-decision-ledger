import { useEffect, useState, type FormEvent } from "react";
import { Button, InlineLoading } from "@carbon/react";
import App from "./App";
import ReleaseWorkbench from "./ReleaseWorkbench";
import EvidenceImport from "./EvidenceImport";
import { request } from "./api";

export interface StudioSession { mode: string; workspace_id: string; subject: string; role: "reader" | "operator" }

export default function StudioRoot() {
  const [config, setConfig] = useState<{mode: string; browser_login: boolean} | null>(null);
  const [session, setSession] = useState<StudioSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [credential, setCredential] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    request<{mode: string; browser_login: boolean}>("/api/workspace/config").then(async value => {
      if (!active) return;
      setConfig(value);
      if (value.mode === "private") {
        const response = await fetch("/api/workspace/session", {cache: "no-store"});
        if (response.ok && active) setSession(await response.json());
        else if (response.status !== 401) throw new Error("Cannot check your studio session. Reload to retry.");
      }
    }).catch(cause => { if (active) setError(String(cause)); }).finally(() => { if (active) setLoading(false); });
    const expired = () => { setSession(null); setError("Your session ended. Sign in again to continue."); };
    window.addEventListener("sdl-session-expired", expired);
    return () => { active = false; window.removeEventListener("sdl-session-expired", expired); };
  }, []);

  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    const key = credential; setCredential("");
    try { setSession(await request<StudioSession>("/api/workspace/login", {method: "POST", body: JSON.stringify({credential: key})})); }
    catch { setError("Sign-in failed. Check your access key, or contact the studio administrator. After repeated attempts, wait ten minutes."); }
    finally { setBusy(false); }
  }
  async function logout() {
    setBusy(true);
    try { await request("/api/workspace/logout", {method: "POST"}); setSession(null); setError(""); }
    catch { setError("Sign-out could not be confirmed. Retry before leaving this device."); }
    finally { setBusy(false); }
  }
  if (loading) return <main className="studio-login"><InlineLoading description="Checking workspace access…" /></main>;
  if (config?.mode === "public") return <App />;
  if (!session) return <main className="studio-login">
    <h1>Sign in to your studio</h1>
    <p>Review release evidence and preserve the decisions your team makes.</p>
    {!config ? <p role="alert">{error || "Workspace unavailable. Reload to retry."}</p> : !config.browser_login ? <p role="alert">Browser sign-in has not been configured. Contact the deployment administrator.</p> :
      <form onSubmit={login}>
        <label htmlFor="studio-key">Studio access key</label>
        <input id="studio-key" type="password" required minLength={32} maxLength={512} value={credential} autoComplete="off" disabled={busy} onChange={event => setCredential(event.target.value)} aria-describedby="key-help" />
        <p id="key-help">Use the individual key issued by your administrator. Never share a team key. Sessions expire after eight hours.</p>
        <Button type="submit" disabled={busy}>Sign in</Button>
        {busy && <InlineLoading description="Signing in…" />}
        {error && <p role="alert">{error}</p>}
      </form>}
  </main>;
  return <div className="private-studio">
    <header className="studio-bar"><a href="/">{session.workspace_id}</a><span>{session.subject} · {session.role}</span><Button kind="ghost" onClick={() => void logout()} disabled={busy}>Sign out</Button></header>
    {error && <p className="studio-notice" role="alert">{error}</p>}
    {new URLSearchParams(window.location.search).has("decision") ? <App privateWorkspace canRecord={session.role === "operator"} /> :
      <main className="studio-content">
        <h1>Your studio’s release evidence</h1>
        <p className="studio-intro">Import the source records, review any corrections, then evaluate a release. Missing evidence stays visible—it does not become permission to release.</p>
        <EvidenceImport canImport={session.role === "operator"} onPublished={() => setRefresh(value => value + 1)} />
        <ReleaseWorkbench key={refresh} privateWorkspace canRecord={session.role === "operator"} />
      </main>}
  </div>;
}
