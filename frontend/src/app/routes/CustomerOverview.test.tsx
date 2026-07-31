import { render, screen } from "@testing-library/react";
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
} from "react-router";
import { describe, expect, it } from "vitest";

import type { CustomerOverviewData } from "../auth/customerAuth";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import { CustomerOverviewRoute } from "./CustomerOverview";

function overview(
  overrides: Partial<CustomerOverviewData> = {},
): CustomerOverviewData {
  return {
    first_name: "Casey",
    licensed_states: ["NY", "TX"],
    current_request: {
      id: "11111111-1111-4111-8111-111111111111",
      lead_count: 750,
      states: ["NY", "TX"],
      submitted_at: "2026-07-25T12:00:00Z",
      delivered_at: null,
      status: {
        label: "Preparing Batch",
        description:
          "We are waiting for enough matching leads. There is nothing you need to do.",
        tone: "warning",
      },
    },
    recent_deliveries: [
      {
        request_id: "22222222-2222-4222-8222-222222222222",
        lead_count: 500,
        states: ["TX"],
        delivered_at: "2026-07-20T12:00:00Z",
      },
    ],
    next_action: {
      kind: "review_request",
      label: "Review Request",
      description: "See the latest progress for your current Batch Request.",
      href: "/app/requests",
    },
    primary_actions: [
      {
        kind: "request_batch",
        label: "Request a Batch",
        description: "Choose the scope for your next Batch.",
        href: "/app/requests",
      },
      {
        kind: "submit_feedback",
        label: "Submit Lead Feedback",
        description: "Share the result of a delivered lead.",
        href: "/app/feedback",
      },
    ],
    ...overrides,
  };
}

function renderOverview(data: CustomerOverviewData) {
  const router = createMemoryRouter(
    [
      {
        id: "customer",
        path: "/app",
        loader: () => data,
        element: <Outlet />,
        children: [
          {
            path: "overview",
            element: <CustomerOverviewRoute />,
          },
        ],
      },
    ],
    {
      initialEntries: ["/app/overview"],
      hydrationData: { loaderData: { customer: data } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

describe("CustomerOverviewRoute", () => {
  it("renders only the public request vocabulary and accessible actions", () => {
    renderOverview(overview());

    expect(screen.getByText("Preparing Batch", { exact: true })).toBeVisible();
    expect(screen.getByText("750 leads", { exact: true })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Request a Batch" }),
    ).toHaveAttribute("href", "/app/requests");
    expect(
      screen.getByRole("link", { name: "Submit Lead Feedback" }),
    ).toHaveAttribute("href", "/app/feedback");
    expect(document.querySelector(".jx-page")).toHaveClass("jx-page--data");
    expect(document.body).not.toHaveTextContent("waiting_inventory");
  });

  it("renders empty collections as useful nothing-yet states", () => {
    renderOverview(
      overview({
        licensed_states: [],
        current_request: null,
        recent_deliveries: [],
        next_action: {
          kind: "add_licensed_states",
          label: "Add Licensed States",
          description:
            "Add at least one Licensed State before requesting a Batch.",
          href: "/app/account",
        },
      }),
    );

    expect(
      screen.getByRole("heading", { name: "No Batch Requests yet" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "No Licensed States yet" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "No deliveries yet" }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
