import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import type { OperationsOverviewData } from "./AdminDestinations";
import { AdminDestinationRoute } from "./AdminDestinations";

function source(
  overrides: Partial<OperationsOverviewData["sources"][number]> = {},
): OperationsOverviewData["sources"][number] {
  return {
    key: "fulfillment",
    title: "Fulfillment",
    description: "Fulfillment work.",
    status: "available",
    count: 1,
    queues: [
      {
        key: "batchRequests",
        title: "Pending Batch Requests",
        description: "Requests waiting for a valid action.",
        count: 1,
        items: [
          {
            id: "request-1",
            title: "Northstar Insurance",
            summary: "250 Leads · TX. Awaiting approval.",
            status: "Pending",
            tone: "warning",
            nextAction: "Approve",
            recordedAt: "2026-07-29T10:00:00Z",
            action: {
              label: "Open Batch Request",
              href: "/app/admin/fulfillment/requests/request-1",
            },
          },
        ],
        emptyTitle: "No Batch Requests need attention",
        emptyDescription: "New requests will appear here.",
      },
    ],
    workspace: {
      label: "Open Fulfillment",
      href: "/app/admin/fulfillment",
    },
    errorTitle: null,
    errorDescription: null,
    ...overrides,
  };
}

function overview(
  overrides: Partial<OperationsOverviewData> = {},
): OperationsOverviewData {
  return {
    generatedAt: "2026-07-29T10:00:00Z",
    availableCount: 1,
    degraded: false,
    sources: [source()],
    ...overrides,
  };
}

function renderOverview(data: OperationsOverviewData) {
  const router = createMemoryRouter(
    [
      {
        id: "overview",
        path: "/admin/overview",
        loader: () => data,
        element: <AdminDestinationRoute />,
      },
    ],
    {
      initialEntries: ["/admin/overview"],
      hydrationData: { loaderData: { overview: data } },
    },
  );
  render(<RouterProvider router={router} />);
}

describe("administrator Operations overview", () => {
  it("identifies the work, its next action, and its real record link", () => {
    renderOverview(overview());

    const queue = screen.getByRole("region", {
      name: "Pending Batch Requests",
    });
    expect(within(queue).getByText("1 pending")).toBeVisible();
    expect(within(queue).getByText("Next: Approve")).toBeVisible();
    expect(
      within(queue).getByRole("link", { name: "Open Batch Request" }),
    ).toHaveAttribute(
      "href",
      "/app/admin/fulfillment/requests/request-1",
    );
  });

  it("keeps healthy sources usable when one source is unavailable", () => {
    const healthyQueue = source().queues[0]!;
    const healthyItem = healthyQueue.items[0]!;
    renderOverview(
      overview({
        degraded: true,
        sources: [
          source({
            status: "unavailable",
            count: null,
            queues: [],
            errorTitle: "Fulfillment work is temporarily unavailable",
            errorDescription:
              "Only this section could not be refreshed. Other sections remain usable.",
          }),
          source({
            key: "backgroundJobs",
            title: "Background jobs",
            description: "Worker recovery.",
            queues: [
              {
                ...healthyQueue,
                key: "failedJobs",
                title: "Failed jobs",
                items: [
                  {
                    ...healthyItem,
                    id: "job-8",
                    title: "Run scraper · Job 8",
                    action: {
                      label: "Open Acquisition",
                      href: "/app/admin/acquisition",
                    },
                  },
                ],
              },
            ],
          }),
        ],
      }),
    );

    expect(
      screen.getByRole("heading", {
        name: "Fulfillment work is temporarily unavailable",
      }),
    ).toBeVisible();
    expect(screen.getByRole("article", { name: "Run scraper · Job 8" })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Open Acquisition" }),
    ).toHaveAttribute("href", "/app/admin/acquisition");
  });

  it("makes an empty queue explain what will appear next", () => {
    const empty = source();
    const queue = empty.queues[0]!;
    empty.count = 0;
    empty.queues = [
      {
        ...queue,
        count: 0,
        items: [],
      },
    ];
    renderOverview(
      overview({
        availableCount: 0,
        sources: [empty],
      }),
    );

    expect(
      screen.getByRole("heading", {
        name: "No Batch Requests need attention",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Open Fulfillment" }),
    ).toHaveAttribute("href", "/app/admin/fulfillment");
  });
});
