import { redirect } from "react-router";
import type { LoaderFunctionArgs } from "react-router";
import { signInPath } from "../auth/customerAuth";
import { apiFetch } from "../api";
import { clearReadPrefetch, readResponse } from "../routeReadCache";

/**
 * The Customer read/write contract for batches: New request, Batches, and the
 * batch page.
 *
 * Every label, bound, and state on these types is decided by the backend, so
 * the screens never reason about fulfillment internals or keep their own copy
 * of a domain rule. Supply stays hidden: nothing here says what is available.
 */

export type MilestoneKey =
  | "submitted"
  | "approved"
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

/** One CSV entry inside the delivered zip. */
export interface BatchArtifactPart {
  filename: string;
  row_count: number;
}

export interface BatchArtifact {
  filename: string;
  row_count: number;
  parts: BatchArtifactPart[];
  /** The CSV columns, in order. */
  columns: string[];
  expires_at: string | null;
  available: boolean;
  download_href: string | null;
}

/**
 * Where a batch is, in the Customer's words. `expiring` is a live file never
 * downloaded that expires within a week; `file_expired` can be copied again,
 * and `copy_on_the_way` is that copy being made.
 */
export type BatchStage =
  | "waiting_approval"
  | "preparing"
  | "ready"
  | "expiring"
  | "file_expired"
  | "copy_on_the_way"
  | "delivered"
  | "not_approved"
  | "canceled"
  | "needs_attention";

/** What a billed batch costs, and where the money is. */
export interface BatchCost {
  rate_cents_per_thousand: number | null;
  amount_cents: number | null;
  state: "held" | "charged" | "released" | null;
  at: string | null;
}

export interface BatchEvent {
  key:
    | "submitted"
    | "approved"
    | "delivered"
    | "file_expired"
    | "copy_ready"
    | "copy_on_the_way"
    | "not_approved"
    | "canceled"
    | "needs_attention";
  label: string;
  at: string | null;
  done: boolean;
}

export interface BatchStateCount {
  state: string;
  name: string;
  lead_count: number;
}

export interface BatchRequest {
  id: string;
  /** "Batch #1042", shared with the operator. */
  number: number;
  lead_count: number;
  /** Frozen at submission; sizes the CSV parts inside the zip. */
  rows_per_file: number;
  states: string[];
  submitted_at: string;
  delivered_at: string | null;
  status: { label: string; description: string; tone: Tone };
  milestones: MilestoneGraphData;
  can_cancel: boolean;
  next_action: RequestAction | null;
  /** The batch page, "/app/batches/1042". */
  href: string;
  receipt_href: string;
  artifact: BatchArtifact | null;
  stage: BatchStage;
  /** The file expired and no copy is on the way. */
  can_request_copy: boolean;
  /** Absent for batches that are not billed. */
  cost: BatchCost | null;
  history: BatchEvent[];
  /** Leads per state once delivered; only on one batch's own read. */
  leads_by_state?: BatchStateCount[] | null;
}

export interface RequestLimits {
  minimum_lead_count: number;
  maximum_lead_count: number;
  /** The appointed states, as codes. */
  licensed_states: string[];
  /** Full names for the appointed states. */
  state_names: Record<string, string>;
}

export interface RequestBlocker {
  reason: "mapping_unconfirmed" | "no_licensed_states";
  label: string;
  description: string;
  action: { kind: string; label: string; description: string; href: string };
}

/** Where this page of batches sits in the Customer's whole history. */
export interface RequestHistoryPage {
  page: number;
  page_size: number;
  total: number;
}

export interface BatchRequestWorkspace {
  limits: RequestLimits;
  blocker: RequestBlocker | null;
  /** One page of batches, newest first. */
  requests: BatchRequest[];
  history: RequestHistoryPage;
}

export interface BatchRequestReceipt {
  created: boolean;
  request: BatchRequest;
}

export interface BatchRequestSubmission {
  idempotency_key: string;
  lead_count: number;
  expected_lead_rate_cents_per_thousand?: number;
  /** Omit for one file; the backend freezes `lead_count` as the part size. */
  rows_per_file?: number | null;
  state_mode: "all_saved" | "selected";
  states: string[];
}

/** One delivered lead found by phone within a batch, for a report. */
export interface BatchLead {
  distributionEventId: number;
  businessName: string;
  phone: string;
}

/** The reasons a lead report takes (`LeadReportCreate`). */
export type LeadReportReason =
  | "invalid_phone"
  | "wrong_business_or_title"
  | "wrong_state"
  | "duplicate"
  | "do_not_contact_or_legal"
  | "other";

const WORKSPACE_PATH = "/api/me/batch-requests";

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

/** A refused write, carrying the HTTP status for callers that branch on it. */
export class BatchRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function mutate<T>(path: string, body?: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method: "POST",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const parsed = (await response.json().catch(() => ({}))) as ErrorBody;
  if (!response.ok) {
    throw new BatchRequestError(
      refusal(parsed, "The request could not be completed. Please try again."),
      response.status,
    );
  }
  return parsed as T;
}

const REQUEST_ID_ONLY =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** True when `value` is a request id the backend could resolve. */
export function isRequestId(value: string | null): value is string {
  return value !== null && REQUEST_ID_ONLY.test(value);
}

/** True when `value` names a batch: its number or its UUID. */
export function isBatchReference(value: string | undefined): value is string {
  return value !== undefined && (/^\d{1,9}$/.test(value) || isRequestId(value));
}

/** The 1-based history page a `?page=` value names; anything else is page 1. */
export function historyPage(value: string | null): number {
  if (!value || !/^\d{1,6}$/.test(value)) return 1;
  return Math.max(1, Number(value));
}

function signInRedirect(url: URL): Response {
  // Keeps the query, so an emailed link opens its batch after sign-in.
  return redirect(signInPath(url));
}

async function readWorkspace(url: URL, page: number): Promise<BatchRequestWorkspace> {
  const response = await readResponse(
    page > 1 ? `${WORKSPACE_PATH}?page=${page}` : WORKSPACE_PATH,
  );
  if (response.status === 401 || response.status === 403) {
    throw signInRedirect(url);
  }
  if (!response.ok) {
    throw new Response("Batches are temporarily unavailable.", {
      status: response.status,
    });
  }
  return response.json() as Promise<BatchRequestWorkspace>;
}

/** New request: the bounds, the appointed states, and the latest batch. */
export function newRequestLoader({
  request,
}: LoaderFunctionArgs): Promise<BatchRequestWorkspace> {
  return readWorkspace(new URL(request.url), 1);
}

/** Batches: one page of the history, newest first. */
export function batchesLoader({
  request,
}: LoaderFunctionArgs): Promise<BatchRequestWorkspace> {
  const url = new URL(request.url);
  return readWorkspace(url, historyPage(url.searchParams.get("page")));
}

/**
 * The batch page. `null` means the address names no batch of this
 * Customer's, which the page shows as not found rather than an error.
 */
export async function batchLoader({
  request,
  params,
}: LoaderFunctionArgs): Promise<BatchRequest | null> {
  const reference = params.batchId;
  if (!isBatchReference(reference)) return null;
  const url = new URL(request.url);
  const response = await readResponse(`${WORKSPACE_PATH}/${reference}`);
  if (response.status === 401 || response.status === 403) throw signInRedirect(url);
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Response("This batch is temporarily unavailable.", {
      status: response.status,
    });
  }
  return response.json() as Promise<BatchRequest>;
}

/** The unfinished request form a Stripe return carries back. */
const REQUEST_FORM_KEYS = ["quantity", "states", "rows_per_file"] as const;

/**
 * /requests was the request form and the history in one page. Emailed and
 * bookmarked links keep working: one request opens its batch page, a page
 * of history opens Batches, and anything else (including a Stripe return to
 * the old form) opens New request with the form's own keys.
 */
export function requestsRedirectLoader({ request }: LoaderFunctionArgs): Response {
  const params = new URL(request.url).searchParams;
  const requestId = params.get("request");
  if (requestId !== null) {
    return redirect(isRequestId(requestId) ? `/batches/${requestId}` : "/batches");
  }
  if (params.has("page")) {
    return redirect(`/batches?page=${historyPage(params.get("page"))}`);
  }
  const kept = new URLSearchParams();
  for (const key of [...REQUEST_FORM_KEYS, "purchase"]) {
    const value = params.get(key);
    if (value !== null) kept.set(key, value);
  }
  const query = kept.toString().replace(/%2C/gi, ",");
  return redirect(`/new-request${query ? `?${query}` : ""}`);
}

/**
 * Feedback left the portal; "Report a bad lead" lives on each batch page.
 * A link scoped to one batch opens that batch; any other opens Batches.
 */
export function feedbackRedirectLoader({ request }: LoaderFunctionArgs): Response {
  const requestId = new URL(request.url).searchParams.get("request");
  return redirect(isRequestId(requestId) ? `/batches/${requestId}` : "/batches");
}

/**
 * Submit the request.
 *
 * `idempotency_key` identifies the form, not the attempt, so retrying a
 * submission that may already have landed is always safe: the backend answers
 * with the batch that key created rather than making another one.
 */
export function submitBatchRequest(
  submission: BatchRequestSubmission,
): Promise<BatchRequestReceipt> {
  clearReadPrefetch();
  return mutate<BatchRequestReceipt>(WORKSPACE_PATH, submission).finally(clearReadPrefetch);
}

export function cancelBatchRequest(id: string): Promise<BatchRequest> {
  clearReadPrefetch();
  return mutate<BatchRequest>(`${WORKSPACE_PATH}/${id}/cancel`).finally(clearReadPrefetch);
}

/** Ask for a new copy of an expired file; answers with the batch. */
export function requestNewCopy(id: string): Promise<BatchRequest> {
  clearReadPrefetch();
  return mutate<BatchRequest>(`${WORKSPACE_PATH}/${id}/new-copy`).finally(clearReadPrefetch);
}

export async function loadBatchRequest(reference: string): Promise<BatchRequest> {
  const response = await apiFetch(`${WORKSPACE_PATH}/${reference}`);
  if (!response.ok) throw Object.assign(new Error("Batch updates are temporarily unavailable."), { status: response.status });
  return response.json() as Promise<BatchRequest>;
}

/** Find a delivered lead by phone within one batch, or null if it is not there. */
export async function findBatchLead(
  requestId: string,
  phone: string,
): Promise<BatchLead | null> {
  try {
    return await mutate<BatchLead>("/api/me/feedback/lookup", {
      phone,
      request_id: requestId,
    });
  } catch (caught) {
    if (caught instanceof BatchRequestError && caught.status === 404) return null;
    throw caught;
  }
}

/** File a lead report through the existing report endpoint. */
export function reportLead(
  distributionEventId: number,
  reason: LeadReportReason,
  details: string,
): Promise<{ id: string }> {
  return mutate<{ id: string }>(
    `/api/me/distributions/${distributionEventId}/reports`,
    { reason, details },
  );
}

/** One key per request form, minted when the form starts. */
export function newSubmissionKey(): string {
  return crypto.randomUUID();
}

/**
 * The short reference for a batch payload that has no number: kind prefix +
 * first 8 of the id, uppercase ("REQ-7C3D19AB").
 */
export function formatRequestRef(id: string): string {
  return `REQ-${id.replace(/-/g, "").slice(0, 8).toUpperCase()}`;
}

const REQUEST_ID_PATTERN =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/** Derives the short reference from a string that carries a request id
 *  (an attention-item id like "batch-ready:<uuid>", or an href). Returns
 *  null when the string holds no request id at all. */
export function deriveRequestRef(source: string): string | null {
  const match = source.match(REQUEST_ID_PATTERN);
  return match ? formatRequestRef(match[0]) : null;
}
