// Client for the decision API. Types mirror the server payloads exactly; if
// they drift, that is a bug in one of the two, not something to paper over
// with `any`.

export type Tone = "clear" | "hold";

export interface EvidenceItem {
  name: string;
  value: string;
  tone: Tone;
}

export interface EvidenceGroup {
  label: string;
  tone: Tone;
  summary: string;
  items: EvidenceItem[];
}

export interface DecisionPayload {
  decision_id: string;
  title_id: string;
  territory_code: string;
  effective_at: string;
  decided_at: string;
  outcome: "AVAILABLE" | "HOLD" | "ESCALATE";
  rule_hits: string[];
  blocking_condition: string;
  policy_revision: string;
  snapshot_id: string;
  source_manifest_hash: string;
  max_revision: number;
  retrieval_count: number;
  model_rationale: string;
  evidence_groups: EvidenceGroup[];
}

export type CapabilityClass = "C2" | "C3_BOUNDARY" | "NOT_CERTIFIED";

export interface VerificationPayload {
  decision_id: string;
  capability_class: CapabilityClass;
  failed_requirement: string;
  detail: string;
}

export interface ComparisonPayload {
  historical: {
    outcome: string;
    rule_hits: string[];
    max_revision: number;
    snapshot_id: string;
    decided_at: string;
  };
  current: {
    outcome: string;
    rule_hits: string[];
    max_revision: number;
    blocking_condition: string;
  };
  differences: string[];
  record_unchanged: boolean;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body.slice(0, 300)}`);
  }
  return (await response.json()) as T;
}

export function recordDecision(input: {
  title_id: string;
  territory_code: string;
  effective_at: string;
}): Promise<DecisionPayload> {
  return request<DecisionPayload>("/api/decisions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getDecision(decisionId: string): Promise<DecisionPayload> {
  return request<DecisionPayload>(`/api/decisions/${decisionId}`);
}

export function verifyDecision(decisionId: string): Promise<VerificationPayload> {
  return request<VerificationPayload>(`/api/decisions/${decisionId}/verify`, {
    method: "POST",
  });
}

export interface AblationPayload {
  decision_id: string;
  withheld: string;
  with_binding: VerificationPayload;
  without_binding: VerificationPayload;
  explanation: string;
}

export function ablateDecision(decisionId: string): Promise<AblationPayload> {
  return request<AblationPayload>(`/api/decisions/${decisionId}/ablate`, {
    method: "POST",
  });
}

export function compareDecision(decisionId: string): Promise<ComparisonPayload> {
  return request<ComparisonPayload>(`/api/decisions/${decisionId}/compare`);
}

export interface ResolutionItem {
  rule_id: string;
  kind: "CORRECT_KNOWN_FAILURE" | "ACQUIRE_MISSING_EVIDENCE";
  instruction: string;
  evidence_sources: Array<{
    table_name: string;
    canonical_query: string;
    result_hash: string;
    snapshot_id: string;
  }>;
  completion_condition: string;
  status: "OPEN" | "COMPLETE" | "UNKNOWN";
}

export interface ResolutionPlanPayload {
  decision_id: string;
  snapshot_id: string;
  policy_revision: string;
  assessed_at: string;
  items: ResolutionItem[];
  all_complete: boolean;
  record_unchanged: boolean;
  next_action: string;
}

export function getResolutionPlan(decisionId: string): Promise<ResolutionPlanPayload> {
  return request<ResolutionPlanPayload>(`/api/decisions/${decisionId}/resolution-plan`);
}

export function recheckResolutionPlan(decisionId: string): Promise<ResolutionPlanPayload> {
  return request<ResolutionPlanPayload>(
    `/api/decisions/${decisionId}/resolution-plan/recheck`,
    { method: "POST" },
  );
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

// The agent transcript. The tool call and the gate it returned are carried
// alongside the model's text so the console can show that the determination
// came from the evaluator, not from the model.

export interface AgentToolCall {
  kind: "tool_call";
  name: string;
  args: Record<string, string>;
}

export interface AgentToolResult {
  kind: "tool_result";
  name: string;
  outcome: "AVAILABLE" | "HOLD" | "ESCALATE" | null;
  rule_hits: string[];
  blocking_condition: string;
  policy_revision: string;
  detail: Record<string, string | number | boolean>;
  error: string | null;
}

export interface AgentText {
  kind: "text";
  text: string;
}

export type AgentEvent = AgentToolCall | AgentToolResult | AgentText;

export interface AgentAnswer {
  session_id: string;
  events: AgentEvent[];
}

export function askAgent(question: string, sessionId?: string): Promise<AgentAnswer> {
  return request<AgentAnswer>("/api/agent/ask", {
    method: "POST",
    body: JSON.stringify({ question, session_id: sessionId ?? null }),
  });
}

export interface MemoPayload {
  subject: string;
  body: string;
  blocking_condition: string;
  grounded_in: {
    decision_id: string;
    snapshot_id: string;
    policy_revision: string;
  };
  template_revision: string;
  sent: boolean;
}

export function draftMemo(decisionId: string): Promise<MemoPayload> {
  return request<MemoPayload>(`/api/decisions/${decisionId}/memo`, {
    method: "POST",
  });
}

// Clipboard access is unavailable on insecure origins and when permission is
// refused, so this reports whether it worked rather than assuming. A button
// that says "Copied" when nothing was copied is worse than one that does not.
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
