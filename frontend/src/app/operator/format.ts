import type { StatusTone } from "../../design-system/primitives/status";
import { formatCents } from "../billing/money";

/**
 * Operator wording and number formats (V4, V5, V6), shared by Today, All
 * batches, the Batch page, and their side panels.
 */

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function valid(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function day(date: Date, now: Date): string {
  const month = date.toLocaleString("en-US", { month: "short" });
  const year = date.getFullYear() === now.getFullYear() ? "" : `, ${date.getFullYear()}`;
  return `${month} ${date.getDate()}${year}`;
}

/** "Nov 9", with the year only when it differs (V5). Local time. */
export function formatDay(value: string | null | undefined, now: Date = new Date()): string {
  const date = valid(value);
  return date ? day(date, now) : "—";
}

/** "09:41" (V5). Local time. */
export function formatTime(value: string | null | undefined): string {
  const date = valid(value);
  return date ? `${pad(date.getHours())}:${pad(date.getMinutes())}` : "—";
}

/** "Nov 9 09:41" (V5). Local time. */
export function formatDateTime(value: string | null | undefined, now: Date = new Date()): string {
  const date = valid(value);
  return date ? `${day(date, now)} ${formatTime(value)}` : "—";
}

/** Ages as 20m, 2h, 3d (V5). */
export function formatAge(value: string | null | undefined, now: Date = new Date()): string {
  const date = valid(value);
  if (!date) return "—";
  const minutes = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** "2 hours ago", for page descriptions. */
export function formatAgo(value: string | null | undefined, now: Date = new Date()): string {
  const date = valid(value);
  if (!date) return "";
  const minutes = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** Rates as "$5.00 per 1,000" (V5). */
export function formatRate(centsPerThousand: number | null | undefined): string {
  return centsPerThousand == null ? "—" : `${formatCents(centsPerThousand)} per 1,000`;
}

export function formatCount(value: number): string {
  return value.toLocaleString("en-US");
}

export function plural(count: number, one: string, many: string = `${one}s`): string {
  return `${formatCount(count)} ${count === 1 ? one : many}`;
}

/** "6155550142" reads as "(615) 555-0142". Anything else is left as is. */
export function formatPhone(phone: string): string {
  return /^\d{10}$/.test(phone)
    ? `(${phone.slice(0, 3)}) ${phone.slice(3, 6)}-${phone.slice(6)}`
    : phone;
}

export function formatDatabase(value: string | null | undefined): string {
  return value === "db2" ? "DB2" : "DB1";
}

/** Where a Batch is, as the backend's `stage` names it. */
export type BatchStage =
  | "awaiting_approval"
  | "queued"
  | "generating"
  | "waiting"
  | "generated"
  | "delivered"
  | "file_expired"
  | "delivery_failed"
  | "failed"
  | "rejected"
  | "canceled";

/** Red needs action or failed; black is in progress or done; grey is
 *  closed or expired (V4). */
const STAGES: Record<BatchStage, { label: string; tone: StatusTone }> = {
  awaiting_approval: { label: "Awaiting approval", tone: "danger" },
  waiting: { label: "Waiting", tone: "danger" },
  queued: { label: "Approved", tone: "info" },
  generating: { label: "Generating", tone: "info" },
  generated: { label: "Generated", tone: "info" },
  delivered: { label: "Delivered", tone: "success" },
  file_expired: { label: "File expired", tone: "neutral" },
  delivery_failed: { label: "Delivery failed", tone: "danger" },
  failed: { label: "Failed", tone: "danger" },
  rejected: { label: "Rejected", tone: "neutral" },
  canceled: { label: "Canceled", tone: "neutral" },
};

export function stageLabel(stage: string): string {
  return STAGES[stage as BatchStage]?.label ?? stage.replaceAll("_", " ");
}

export function stageTone(stage: string): StatusTone {
  return STAGES[stage as BatchStage]?.tone ?? "neutral";
}

export interface BatchAvailability {
  kind: "committed" | "fillable" | "short" | "unknown" | "none";
  committed?: number;
  available?: number;
  short?: number;
  asOf?: string | null;
  source?: "last_attempt" | "customer_snapshot";
}

export interface RecordLink {
  id: number;
  name: string;
  href: string;
}

export interface BatchRow {
  id: string;
  number: number;
  href: string;
  customer: RecordLink;
  leadCount: number;
  states: string[];
  database: string;
  status: string;
  stage: string;
  statusMessage: string;
  availability: BatchAvailability;
  needsDecision: boolean;
  createdAt: string;
  deliveredAt: string | null;
  closedAt: string | null;
  fileAt?: string | null;
}

export function batchName(number: number): string {
  return `Batch #${number}`;
}

export function formatStates(states: string[]): string {
  return states.length ? states.join(", ") : "No states";
}

/** The Available column: what a Batch holds, or what it could get. */
export function availabilityText(fill: BatchAvailability): string {
  switch (fill.kind) {
    case "committed":
      return `${formatCount(fill.committed ?? 0)} committed`;
    case "short":
      return `${formatCount(fill.available ?? 0)} · short ${formatCount(fill.short ?? 0)}`;
    case "fillable":
      return formatCount(fill.available ?? 0);
    case "none":
      return "—";
    default:
      return "Not counted yet";
  }
}
