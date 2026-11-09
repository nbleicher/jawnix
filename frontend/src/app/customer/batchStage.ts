import type { StatusTone } from "../../design-system/primitives/status";
import { formatCents } from "../billing/money";
import type { BatchCost, BatchRequest, BatchStage } from "../routes/batchRequests";
import { formatDay } from "./format";

/**
 * How a batch's stage reads as a badge (V4): red when the Customer has
 * something to do or it failed, black while in progress or done, grey once
 * closed or expired.
 */
const STAGES: Record<BatchStage, { label: string; tone: StatusTone }> = {
  waiting_approval: { label: "Waiting for approval", tone: "info" },
  preparing: { label: "Preparing", tone: "info" },
  ready: { label: "Ready", tone: "success" },
  expiring: { label: "Expires", tone: "danger" },
  file_expired: { label: "File expired", tone: "neutral" },
  copy_on_the_way: { label: "New copy on the way", tone: "info" },
  delivered: { label: "Delivered", tone: "success" },
  not_approved: { label: "Not approved", tone: "danger" },
  canceled: { label: "Canceled", tone: "neutral" },
  needs_attention: { label: "Needs attention", tone: "danger" },
};

/** Stages that move on their own, so their pages listen for changes. */
const MOVING: ReadonlySet<BatchStage> = new Set([
  "waiting_approval",
  "preparing",
  "copy_on_the_way",
]);

export function isMoving(batch: Pick<BatchRequest, "stage">): boolean {
  return MOVING.has(batch.stage);
}

/**
 * The badge for a batch. An expiring file names its day ("Expires Nov 12");
 * with `withDate`, an expired one does too ("File expired Nov 1").
 */
export function stageBadge(
  batch: Pick<BatchRequest, "stage" | "artifact">,
  { now = new Date(), withDate = false }: { now?: Date; withDate?: boolean } = {},
): { label: string; tone: StatusTone } {
  const { label, tone } = STAGES[batch.stage];
  const expiresAt = batch.artifact?.expires_at;
  if (batch.stage === "expiring" && expiresAt) {
    return { label: `${label} ${formatDay(expiresAt, now)}`, tone };
  }
  if (withDate && batch.stage === "file_expired" && expiresAt) {
    return { label: `${label} ${formatDay(expiresAt, now)}`, tone };
  }
  return { label, tone };
}

/** The one thing a batch's row offers (V3): at most one button. */
export type BatchActionKind = "cancel" | "download" | "new_copy";

export function batchAction(batch: BatchRequest): BatchActionKind | null {
  if (batch.can_cancel) return "cancel";
  if (batch.artifact?.download_href) return "download";
  if (batch.can_request_copy) return "new_copy";
  return null;
}

/** "$125.00 held", "$250.00", "$0.00" once a hold is released, or "—". */
export function formatBatchCost(cost: BatchCost | null): string {
  if (!cost || cost.amount_cents === null) return "—";
  if (cost.state === "released") return formatCents(0);
  const amount = formatCents(cost.amount_cents);
  return cost.state === "held" ? `${amount} held` : amount;
}

/**
 * "5 CSV files of 10,000 rows, in one zip", "4 CSV files of up to 15,000
 * rows, in one zip" when the last file holds the remainder, or "One CSV file".
 */
export function formatFiles(leadCount: number, rowsPerFile: number): string {
  if (rowsPerFile >= leadCount || rowsPerFile < 1) return "One CSV file";
  const files = Math.ceil(leadCount / rowsPerFile);
  const upTo = leadCount % rowsPerFile === 0 ? "" : "up to ";
  return `${files.toLocaleString("en-US")} CSV files of ${upTo}${rowsPerFile.toLocaleString("en-US")} rows, in one zip`;
}
