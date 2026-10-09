import { cx } from "../../design-system/primitives/cx";
import { Mono, Text } from "../../design-system/primitives/typography";
import type { MilestoneGraphData } from "./batchRequests";

import "./MilestoneGraph.css";

export function formatMilestoneTime(value: string): string {
  const date = new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

type StatusLine =
  | { kind: "live"; label: string; description: string }
  | { kind: "paused"; label: string; description: string }
  | {
      kind: "delivered";
      label: string;
      description: string;
      occurred_at: string | null;
    }
  | {
      kind: "stopped";
      label: string;
      description: string;
      tone: "danger" | "neutral";
    };

/**
 * Where the request currently sits, distilled to the one line the ticket
 * needs. Labels and descriptions are the backend's own words — the line
 * never rewrites copy, it only picks which row is speaking.
 */
function statusLine(graph: MilestoneGraphData): StatusLine {
  if (graph.outcome) {
    return {
      kind: "stopped",
      label: graph.outcome.label,
      description: graph.outcome.description,
      tone: graph.outcome.tone === "neutral" ? "neutral" : "danger",
    };
  }
  if (graph.pause) {
    return {
      kind: "paused",
      label: graph.pause.label,
      description: graph.pause.description,
    };
  }
  const current = graph.milestones.find(
    (milestone) => milestone.state === "current",
  );
  if (current) {
    return {
      kind: "live",
      label: current.label,
      description: current.description,
    };
  }
  const delivered = graph.milestones[graph.milestones.length - 1];
  return {
    kind: "delivered",
    label: delivered?.label ?? "Delivered",
    description: delivered?.description ?? "",
    occurred_at: delivered?.occurred_at ?? null,
  };
}

/**
 * The Batch Request status line.
 *
 * One line, one witness: a spinning hairline ring while the request is
 * moving, a hollow vermilion ring while it waits for inventory, a square
 * stamp once it has settled (ink for delivered, danger or neutral for a
 * terminal outcome). The line is a polite live region driven entirely by
 * props, so the page's revalidation polling moves it without any local
 * state; reduced motion collapses the spin to a static hollow ring.
 */
export function MilestoneGraph({
  graph,
  label,
}: {
  graph: MilestoneGraphData;
  label: string;
}) {
  const line = statusLine(graph);
  return (
    <p
      className={cx("jx-statusline", `jx-statusline--${line.kind}`)}
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      {line.kind === "live" ? (
        <span className="jx-statusline__spinner" aria-hidden="true" />
      ) : line.kind === "paused" ? (
        <span
          className="jx-statusline__ring jx-statusline__ring--paused"
          aria-hidden="true"
        />
      ) : (
        <span
          className={cx(
            "jx-statusline__witness",
            `jx-statusline__witness--${
              line.kind === "delivered" ? "ink" : line.tone
            }`,
          )}
          aria-hidden="true"
        />
      )}
      <Text as="span" size="sm" weight="semibold">
        {line.label}
      </Text>
      {" — "}
      <Text as="span" size="sm" tone="muted">
        {line.description}
      </Text>
      {line.kind === "delivered" && line.occurred_at ? (
        <>
          {" "}
          <Mono className="jx-statusline__when">
            {`· ${formatMilestoneTime(line.occurred_at)}`}
          </Mono>
        </>
      ) : null}
    </p>
  );
}
