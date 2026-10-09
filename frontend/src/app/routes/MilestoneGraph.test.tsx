import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MilestoneGraph } from "./MilestoneGraph";
import type { Milestone, MilestoneGraphData } from "./batchRequests";

const SUBMITTED = "2026-07-20T12:00:00Z";

function milestone(
  key: Milestone["key"],
  state: Milestone["state"],
  occurred_at: string | null = null,
): Milestone {
  const labels = {
    submitted: "Submitted",
    approved: "Approved",
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
      milestone("approved", "current", "2026-07-20T13:00:00Z"),
      milestone("preparing_batch", "upcoming"),
      milestone("delivered", "upcoming"),
    ],
    current_key: "approved",
    pause: null,
    outcome: null,
    ...overrides,
  };
}

function pausedGraph(): MilestoneGraphData {
  return graph({
    milestones: [
      milestone("submitted", "complete", SUBMITTED),
      milestone("approved", "complete", "2026-07-20T13:00:00Z"),
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
}

function stoppedGraph(tone: "danger" | "neutral"): MilestoneGraphData {
  return graph({
    milestones: [
      milestone("submitted", "complete", SUBMITTED),
      milestone("approved", "stopped", null),
      milestone("preparing_batch", "not_reached"),
      milestone("delivered", "not_reached"),
    ],
    current_key: null,
    outcome: {
      kind: tone === "danger" ? "rejected" : "canceled",
      milestone_key: "approved",
      label: tone === "danger" ? "Not Approved" : "Canceled",
      description:
        tone === "danger"
          ? "This request was not approved."
          : "You withdrew this request.",
      tone,
      occurred_at: "2026-07-21T09:00:00Z",
    },
  });
}

function deliveredGraph(): MilestoneGraphData {
  return graph({
    milestones: [
      milestone("submitted", "complete", SUBMITTED),
      milestone("approved", "complete", "2026-07-20T13:00:00Z"),
      milestone("preparing_batch", "complete", "2026-07-21T10:00:00Z"),
      milestone("delivered", "complete", "2026-07-27T16:00:00Z"),
    ],
    current_key: "delivered",
  });
}

describe("MilestoneGraph status line", () => {
  it("is one live region that names the current state in words", () => {
    render(<MilestoneGraph graph={graph()} label="Request progress" />);

    const line = screen.getByRole("status", { name: "Request progress" });
    expect(line).toHaveAttribute("aria-live", "polite");
    expect(line).toHaveTextContent("Approved — Approved description.");
  });

  it("spins a single decorative ring while the request is in flight", () => {
    const { container } = render(
      <MilestoneGraph graph={graph()} label="Request progress" />,
    );

    const spinner = container.querySelector(".jx-statusline__spinner");
    expect(spinner).not.toBeNull();
    expect(spinner).toHaveAttribute("aria-hidden", "true");
    expect(spinner).toBeEmptyDOMElement();
    expect(container.querySelectorAll(".jx-statusline__spinner")).toHaveLength(
      1,
    );
  });

  it("holds a hollow vermilion ring — no spin — while waiting for inventory", () => {
    const { container } = render(
      <MilestoneGraph graph={pausedGraph()} label="Request progress" />,
    );

    const line = screen.getByRole("status", { name: "Request progress" });
    expect(line).toHaveTextContent(
      "Waiting for Inventory — There is nothing you need to do.",
    );
    expect(container.querySelector(".jx-statusline__spinner")).toBeNull();
    const ring = container.querySelector(
      ".jx-statusline__ring--paused",
    );
    expect(ring).not.toBeNull();
    expect(ring).toHaveAttribute("aria-hidden", "true");
  });

  it("stamps a delivered request with a solid ink square and its timestamp", () => {
    const { container } = render(
      <MilestoneGraph graph={deliveredGraph()} label="Request progress" />,
    );

    const line = screen.getByRole("status", { name: "Request progress" });
    expect(line).toHaveTextContent(
      "Delivered — Delivered description. · 2026-07-27 16:00 UTC",
    );
    expect(container.querySelector(".jx-statusline__spinner")).toBeNull();
    expect(
      container.querySelector(".jx-statusline__witness--ink"),
    ).not.toBeNull();
  });

  it("stamps a stopped request in danger when the outcome is adverse", () => {
    const { container } = render(
      <MilestoneGraph graph={stoppedGraph("danger")} label="Request progress" />,
    );

    const line = screen.getByRole("status", { name: "Request progress" });
    expect(line).toHaveTextContent(
      "Not Approved — This request was not approved.",
    );
    expect(container.querySelector(".jx-statusline__spinner")).toBeNull();
    expect(
      container.querySelector(".jx-statusline__witness--danger"),
    ).not.toBeNull();
  });

  it("stamps a canceled request in neutral rather than danger", () => {
    const { container } = render(
      <MilestoneGraph
        graph={stoppedGraph("neutral")}
        label="Request progress"
      />,
    );

    const line = screen.getByRole("status", { name: "Request progress" });
    expect(line).toHaveTextContent("Canceled — You withdrew this request.");
    expect(
      container.querySelector(".jx-statusline__witness--neutral"),
    ).not.toBeNull();
    expect(
      container.querySelector(".jx-statusline__witness--danger"),
    ).toBeNull();
  });

  it("moves in place when the poll returns new milestone data", () => {
    const { container, rerender } = render(
      <MilestoneGraph graph={graph()} label="Request progress" />,
    );

    rerender(<MilestoneGraph graph={pausedGraph()} label="Request progress" />);

    const line = screen.getByRole("status", { name: "Request progress" });
    expect(line).toHaveTextContent("Waiting for Inventory");
    expect(container.querySelector(".jx-statusline__spinner")).toBeNull();
    expect(
      container.querySelector(".jx-statusline__ring--paused"),
    ).not.toBeNull();
  });

  it("collapses the spin to a static hollow ring under reduced motion", () => {
    const css = readFileSync(
      join(
        dirname(fileURLToPath(import.meta.url)),
        "MilestoneGraph.css",
      ),
      "utf8",
    );

    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*{[^}]*\.jx-statusline__spinner\s*{[^}]*animation:\s*none/s,
    );
  });
});
