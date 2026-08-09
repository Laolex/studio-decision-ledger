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

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}
