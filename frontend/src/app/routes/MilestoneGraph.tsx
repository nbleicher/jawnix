import { useId } from "react";

import { cx } from "../../design-system/primitives/cx";
import { Text, VisuallyHidden } from "../../design-system/primitives/typography";
import type { MilestoneGraphData, MilestoneState } from "./batchRequests";

import "./MilestoneGraph.css";

/**
 * How each milestone state reads. These are rendered as visible text, not just
 * announced: the state of a node is never carried by colour, position, or
 * motion alone.
 */
const STATE_LABEL: Record<MilestoneState, string> = {
  complete: "Completed",
  current: "In progress",
  paused: "Paused",
  stopped: "Stopped",
  upcoming: "Not started",
  not_reached: "Not reached",
};

/**
 * Decorative marks. They repeat what `STATE_LABEL` already says in words, so
 * they are hidden from assistive technology rather than announced twice.
 */
const STATE_MARK: Record<MilestoneState, string> = {
  complete: "✓",
  current: "●",
  paused: "❚❚",
  stopped: "✕",
  upcoming: "○",
  not_reached: "○",
};

export function formatMilestoneTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

/**
 * The one thing the drawn graph says that its nodes do not: where in the
 * journey this request currently sits. Each node already carries its own label,
 * state, and timestamp as text, so this supplies position and nothing else —
 * the equivalent of glancing at the shape.
 */
export function milestoneSummary(graph: MilestoneGraphData): string {
  const total = graph.milestones.length;
  const index = graph.milestones.findIndex(
    (milestone) =>
      milestone.state === "current"
      || milestone.state === "paused"
      || milestone.state === "stopped",
  );
  const milestone = graph.milestones[index];
  if (index === -1 || !milestone) {
    const last = graph.milestones[total - 1];
    return `All ${total} milestones complete. ${last?.label ?? ""}.`.trim();
  }
  const position = `step ${index + 1} of ${total}`;
  if (graph.outcome) {
    return `Stopped at ${position}, ${milestone.label}. ${graph.outcome.label}.`;
  }
  if (graph.pause) {
    return `At ${position}, ${milestone.label}, paused. ${graph.pause.label}.`;
  }
  return `At ${position}, ${milestone.label}, in progress.`;
}

/**
 * The Batch Request journey.
 *
 * One ordered list, drawn along the inline axis when its container is wide
 * enough and stacked when it is not. Nothing animates and nothing depends on
 * colour: every node states its own name, its state, and when it happened, so
 * the drawn graph and the announced graph say the same thing.
 */
export function MilestoneGraph({
  graph,
  label,
}: {
  graph: MilestoneGraphData;
  label: string;
}) {
  const summaryId = useId();
  return (
    <div className="jx-milestones">
      {/* The reset drops list markers from classed lists, which also drops the
          list role in some browsers. Restating it keeps "4 items, item 3 of 4"
          available to a screen reader. */}
      <ol
        className="jx-milestones__track"
        role="list"
        aria-label={label}
        aria-describedby={summaryId}
      >
        {graph.milestones.map((milestone) => (
          <li
            key={milestone.key}
            className={cx(
              "jx-milestone",
              `jx-milestone--${milestone.state}`,
            )}
            {...(milestone.state === "current" || milestone.state === "paused"
              ? { "aria-current": "step" as const }
              : {})}
          >
            <span className="jx-milestone__mark" aria-hidden="true">
              {STATE_MARK[milestone.state]}
            </span>
            <span className="jx-milestone__body">
              <Text as="span" size="sm" weight="semibold">
                {milestone.label}
              </Text>
              <Text as="span" size="xs" tone="muted">
                {STATE_LABEL[milestone.state]}
                {milestone.occurred_at
                  ? ` · ${formatMilestoneTime(milestone.occurred_at)}`
                  : ""}
              </Text>
            </span>
          </li>
        ))}
      </ol>
      <VisuallyHidden id={summaryId}>{milestoneSummary(graph)}</VisuallyHidden>
    </div>
  );
}
