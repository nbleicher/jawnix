import { Cluster, Stack } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { Text } from "../../design-system/primitives/typography";
import type { LiveActivity, ProgressLogEntry } from "../routes/AdminFulfillment";
import { formatDateTime } from "./format";

import "../routes/AdminFulfillmentProgressLog.css";

/**
 * The worker's own trail for one Batch: the jobs that ran and the decisions
 * between them, newest first, and what is running or blocked right now.
 */

const LIVE_JOB_TONES: Record<string, StatusTone> = {
  queued: "info",
  running: "info",
  failed: "danger",
};

const LOG_VISIBLE_ROWS = 5;

export function ProgressActivityLog({ live }: { live: LiveActivity | undefined }) {
  const rows = live?.log ?? [];
  if (!rows.length) {
    return (
      <div className="jx-progress-log jx-progress-log__empty" role="status">
        <Text size="sm" tone="muted">
          {live?.settled
            ? "This request is settled; nothing is running."
            : "Nothing has been recorded for this request yet."}
        </Text>
      </div>
    );
  }
  return (
    <ol
      className="jx-progress-log"
      aria-label="Live activity log, most recent first"
      // Hint assistive tech that ~5 rows are in view; the rest scroll.
      style={{ maxHeight: `calc(${LOG_VISIBLE_ROWS} * 2.5rem)` }}
    >
      {rows.map((row) => (
        <li className="jx-progress-log__row" key={row.id}>
          <time className="jx-progress-log__at" dateTime={row.at ?? undefined}>
            {formatLogTime(row.at)}
          </time>
          <div className="jx-progress-log__body">
            <span className="jx-progress-log__label">{row.label}</span>
            {row.detail ? (
              <span className="jx-progress-log__detail">{row.detail}</span>
            ) : null}
          </div>
          <StatusBadge tone={row.tone || "neutral"}>
            {logBadge(row)}
          </StatusBadge>
        </li>
      ))}
    </ol>
  );
}

function formatLogTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toISOString().replace("T", " ").slice(11, 19) + " UTC";
}

function logBadge(row: ProgressLogEntry): string {
  if (row.kind === "job") {
    return row.detail?.split(" · ")[0] || "job";
  }
  if (row.kind === "status") {
    return row.detail || "status";
  }
  return "decision";
}

export function LiveActivityStrip({ live }: { live: LiveActivity | undefined }) {
  if (live == null) {
    return null;
  }
  if (!live.jobs.length && live.blocker == null) {
    return null;
  }
  return (
    <Stack gap={2} aria-label="Active jobs">
      {live.blocker ? (
        <Text size="sm" tone="warning" role="status">
          {live.blocker.detail}
        </Text>
      ) : null}
      {live.jobs.map((job) => (
        <Cluster key={`${job.kind}-${job.status}-${job.attempts}`} gap={2}>
          <StatusBadge tone={LIVE_JOB_TONES[job.status] ?? "neutral"}>
            {job.status}
          </StatusBadge>
          <Text size="sm">{job.label}</Text>
          {job.attempts > 1 ? (
            <Text size="sm" tone="muted">
              attempt {job.attempts}
            </Text>
          ) : null}
          {job.lastError ? (
            <Text size="sm" tone="danger">
              {job.lastError}
            </Text>
          ) : null}
          {job.status === "queued" && job.runAfter ? (
            <Text size="sm" tone="muted">
              after {formatDateTime(job.runAfter)}
            </Text>
          ) : null}
        </Cluster>
      ))}
    </Stack>
  );
}

