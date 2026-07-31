import { redirect } from "react-router";

/**
 * The Customer read/write contract for Batch Requests.
 *
 * Every label, description, bound, and state on these types is decided by the
 * backend, so the screen never has to reason about fulfillment internals or
 * keep its own copy of a domain rule.
 */

export type MilestoneKey =
  | "submitted"
  | "under_review"
  | "preparing_batch"
  | "delivered";

export type MilestoneState =
  | "complete"
  | "current"
  | "paused"
  | "stopped"
  | "upcoming"
  | "not_reached";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

export interface Milestone {
  key: MilestoneKey;
  label: string;
  description: string;
  state: MilestoneState;
  occurred_at: string | null;
}

/** An explained wait inside the journey. Never an error. */
export interface MilestonePause {
  kind: "inventory_wait";
  milestone_key: MilestoneKey;
  label: string;
  description: string;
}

/** Why a request stopped short of Delivered. */
export interface MilestoneOutcome {
  kind: "rejected" | "canceled" | "failed";
  milestone_key: MilestoneKey;
  label: string;
  description: string;
  tone: Tone;
  occurred_at: string | null;
}

export interface MilestoneGraphData {
  milestones: Milestone[];
  current_key: MilestoneKey | null;
  pause: MilestonePause | null;
  outcome: MilestoneOutcome | null;
}

export interface RequestAction {
  kind: "request_batch" | "submit_feedback" | "contact_support";
  label: string;
  description: string;
  href: string;
}

export interface BatchArtifact {
  filename: string;
  row_count: number;
  expires_at: string | null;
  available: boolean;
  download_href: string | null;
}

export interface BatchRequest {
  id: string;
  lead_count: number;
  states: string[];
  submitted_at: string;
  delivered_at: string | null;
  status: { label: string; description: string; tone: Tone };
  milestones: MilestoneGraphData;
  can_cancel: boolean;
  next_action: RequestAction | null;
  receipt_href: string;
  artifact: BatchArtifact | null;
}

export interface RequestLimits {
  minimum_lead_count: number;
  maximum_lead_count: number;
  licensed_states: string[];
}

export interface RequestBlocker {
  reason: "mapping_unconfirmed" | "no_licensed_states";
  label: string;
  description: string;
  action: { kind: string; label: string; description: string; href: string };
}

export interface BatchRequestWorkspace {
  limits: RequestLimits;
  blocker: RequestBlocker | null;
  requests: BatchRequest[];
}

export interface BatchRequestReceipt {
  created: boolean;
  request: BatchRequest;
}

export interface BatchRequestSubmission {
  idempotency_key: string;
  lead_count: number;
  state_mode: "all_saved" | "selected";
  states: string[];
}

const WORKSPACE_PATH = "/api/me/batch-requests";

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

interface ErrorBody {
  detail?: string | { msg?: string }[];
}

/**
 * The backend's own words for a refusal.
 *
 * Field-level validation errors arrive as FastAPI's array form; both shapes
 * collapse to one sentence, because a Customer never needs to see which
 * schema rule fired.
 */
function refusal(body: ErrorBody, fallback: string): string {
  if (typeof body.detail === "string") return body.detail;
  const first = Array.isArray(body.detail) ? body.detail[0]?.msg : undefined;
  return first ?? fallback;
}

async function mutate<T>(path: string, body?: unknown): Promise<T> {
  const headers = new Headers({ "X-CSRF-Token": csrf() });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    method: "POST",
    headers,
    credentials: "same-origin",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const parsed = (await response.json().catch(() => ({}))) as ErrorBody;
  if (!response.ok) {
    throw new Error(
      refusal(parsed, "The request could not be completed. Please try again."),
    );
  }
  return parsed as T;
}

export async function batchRequestsLoader(): Promise<BatchRequestWorkspace> {
  const response = await fetch(WORKSPACE_PATH, { credentials: "same-origin" });
  if (response.status === 401 || response.status === 403) {
    throw redirect(`/sign-in?next=${encodeURIComponent("/app/requests")}`);
  }
  if (!response.ok) {
    throw new Response("Batch Requests are temporarily unavailable.", {
      status: response.status,
    });
  }
  return response.json() as Promise<BatchRequestWorkspace>;
}

/**
 * Submit the reviewed request.
 *
 * `idempotency_key` identifies the guided flow, not the attempt, so retrying a
 * submission that may already have landed is always safe: the backend answers
 * with the Batch Request that key created rather than making another one.
 */
export function submitBatchRequest(
  submission: BatchRequestSubmission,
): Promise<BatchRequestReceipt> {
  return mutate<BatchRequestReceipt>(WORKSPACE_PATH, submission);
}

export function cancelBatchRequest(id: string): Promise<BatchRequest> {
  return mutate<BatchRequest>(`${WORKSPACE_PATH}/${id}/cancel`);
}

/** One key per guided flow, minted when the flow starts. */
export function newSubmissionKey(): string {
  return crypto.randomUUID();
}
