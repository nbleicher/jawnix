import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MilestoneGraph, milestoneSummary } from "./MilestoneGraph";
import type { Milestone, MilestoneGraphData } from "./batchRequests";

const SUBMITTED = "2026-07-20T12:00:00Z";

function milestone(
  key: Milestone["key"],
  state: Milestone["state"],
  occurred_at: string | null = null,
): Milestone {
  const labels = {
    submitted: "Submitted",
    under_review: "Under Review",
    preparing_batch: "Preparing Batch",
    delivered: "Delivered",
  } as const;
  return {
    key,
    label: labels[key],
    description: `${labels[key]} description.`,
    state,
    occurred_at,
  };
}

function graph(overrides: Partial<MilestoneGraphData> = {}): MilestoneGraphData {
  return {
    milestones: [
      milestone("submitted", "complete", SUBMITTED),
      milestone("under_review", "current", "2026-07-20T13:00:00Z"),
      milestone("preparing_batch", "upcoming"),
      milestone("delivered", "upcoming"),
    ],
    current_key: "under_review",
    pause: null,
    outcome: null,
    ...overrides,
  };
}

describe("milestoneSummary", () => {
  it("states where an in-flight request currently sits", () => {
    expect(milestoneSummary(graph())).toBe(
      "At step 2 of 4, Under Review, in progress.",
    );
  });

  it("names the pause instead of implying progress", () => {
    const paused = graph({
      milestones: [
        milestone("submitted", "complete", SUBMITTED),
        milestone("under_review", "complete", "2026-07-20T13:00:00Z"),
        milestone("preparing_batch", "paused"),
        milestone("delivered", "upcoming"),
      ],
      current_key: "preparing_batch",
      pause: {
        kind: "inventory_wait",
        milestone_key: "preparing_batch",
        label: "Waiting for Inventory",
        description: "There is nothing you need to do.",
      },
    });

    expect(milestoneSummary(paused)).toBe(
      "At step 3 of 4, Preparing Batch, paused. Waiting for Inventory.",
    );
  });

  it("says where a stopped request stopped and why", () => {
    const stopped = graph({
      milestones: [
        milestone("submitted", "complete", SUBMITTED),
        milestone("under_review", "stopped", null),
        milestone("preparing_batch", "not_reached"),
        milestone("delivered", "not_reached"),
      ],
      current_key: null,
      outcome: {
        kind: "rejected",
        milestone_key: "under_review",
        label: "Not Approved",
        description: "This request was not approved.",
        tone: "danger",
        occurred_at: "2026-07-21T09:00:00Z",
      },
    });

    expect(milestoneSummary(stopped)).toBe(
      "Stopped at step 2 of 4, Under Review. Not Approved.",
    );
  });

  it("reports a finished journey as finished", () => {
    const delivered = graph({
      milestones: [
        milestone("submitted", "complete", SUBMITTED),
        milestone("under_review", "complete", SUBMITTED),
        milestone("preparing_batch", "complete", SUBMITTED),
        milestone("delivered", "complete", SUBMITTED),
      ],
      current_key: "delivered",
    });

    expect(milestoneSummary(delivered)).toBe(
      "All 4 milestones complete. Delivered.",
    );
  });
});

describe("MilestoneGraph", () => {
  it("puts every milestone's state in text beside its name", () => {
    render(<MilestoneGraph graph={graph()} label="Request progress" />);

    const nodes = within(
      screen.getByRole("list", { name: "Request progress" }),
    ).getAllByRole("listitem");

    expect(nodes).toHaveLength(4);
    expect(nodes[0]).toHaveTextContent("Submitted");
    expect(nodes[0]).toHaveTextContent("Completed");
    expect(nodes[1]).toHaveTextContent("In progress");
    expect(nodes[2]).toHaveTextContent("Not started");
    expect(nodes[1]).toHaveAttribute("aria-current", "step");
  });

  it("timestamps the milestones the request actually reached", () => {
    render(<MilestoneGraph graph={graph()} label="Request progress" />);

    const nodes = screen.getAllByRole("listitem");

    expect(nodes[0]?.textContent).toMatch(/Jul 20, 2026/);
    expect(nodes[2]?.textContent).not.toMatch(/2026/);
  });

  it("distinguishes never-reached milestones from ones still to come", () => {
    render(
      <MilestoneGraph
        graph={graph({
          milestones: [
            milestone("submitted", "complete", SUBMITTED),
            milestone("under_review", "stopped"),
            milestone("preparing_batch", "not_reached"),
            milestone("delivered", "not_reached"),
          ],
          current_key: null,
        })}
        label="Request progress"
      />,
    );

    const nodes = screen.getAllByRole("listitem");

    expect(nodes[1]).toHaveTextContent("Stopped");
    expect(nodes[2]).toHaveTextContent("Not reached");
    expect(nodes[2]).not.toHaveTextContent("Not started");
  });

  it("describes the graph as a whole for anyone who cannot see its shape", () => {
    render(<MilestoneGraph graph={graph()} label="Request progress" />);

    const list = screen.getByRole("list", { name: "Request progress" });
    const summaryId = list.getAttribute("aria-describedby");

    expect(summaryId).toBeTruthy();
    expect(document.getElementById(summaryId ?? "")).toHaveTextContent(
      "At step 2 of 4, Under Review, in progress.",
    );
  });
});
