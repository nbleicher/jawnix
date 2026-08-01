import { render, screen } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import type { CustomerOverviewData } from "../auth/customerAuth";
import { CustomerOverviewRoute } from "./CustomerOverview";

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

describe("CustomerOverviewRoute attention queue", () => {
  it("renders a batch-ready item as a direct artifact download", () => {
    renderOverview({
      items: [
        {
          id: "batch-ready:11111111-1111-4111-8111-111111111111",
          kind: "batch_ready",
          title: "Your Batch is ready",
          description: "Download the 750-lead Batch Artifact.",
          tone: "info",
          action: {
            kind: "download_artifact",
            label: "Download CSV",
            description: "Download this Batch Artifact while it is live.",
            href: "/api/me/batch-requests/11111111-1111-4111-8111-111111111111/artifact",
          },
        },
      ],
    } as CustomerOverviewData);

    expect(
      screen.getByRole("heading", { level: 2, name: "Your Batch is ready" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Download CSV" })).toHaveAttribute(
      "href",
      "/api/me/batch-requests/11111111-1111-4111-8111-111111111111/artifact",
    );
    expect(document.querySelector(".jx-page")).toHaveClass("jx-page--data");
    expect(document.body).not.toHaveTextContent("Recent deliveries");
    expect(document.body).not.toHaveTextContent("Licensed States");
    expect(document.body).not.toHaveTextContent("Current request");
  });

  it.each([
    {
      item: {
        id: "artifact-expiring:22222222-2222-4222-8222-222222222222",
        kind: "artifact_expiring",
        title: "Batch Artifact expires soon",
        description: "Download the 500-lead Batch Artifact within 3 days.",
        tone: "warning",
        action: {
          kind: "download_artifact",
          label: "Download CSV",
          description: "Download this Batch Artifact before it expires.",
          href: "/api/me/batch-requests/22222222-2222-4222-8222-222222222222/artifact",
        },
      },
      href: "/api/me/batch-requests/22222222-2222-4222-8222-222222222222/artifact",
      label: "Download CSV",
    },
    {
      item: {
        id: "waiting-inventory:33333333-3333-4333-8333-333333333333",
        kind: "waiting_inventory",
        title: "Batch Request is waiting for inventory",
        description: "Review or cancel your 250-lead request for FL, TX.",
        tone: "warning",
        action: {
          kind: "review_request",
          label: "Review request",
          description: "Open this Batch Request's detail page.",
          href: "/app/requests?request=33333333-3333-4333-8333-333333333333",
        },
      },
      href: "/app/requests?request=33333333-3333-4333-8333-333333333333",
      label: "Review request",
    },
    {
      item: {
        id: "feedback-nudge:44444444-4444-4444-8444-444444444444",
        kind: "feedback_nudge",
        title: "How did this Batch perform?",
        description: "Share one lead outcome from this 1,000-lead Batch.",
        tone: "info",
        action: {
          kind: "submit_feedback",
          label: "Give feedback",
          description: "Record one Lead Disposition or Quality Rating.",
          href: "/app/feedback?request=44444444-4444-4444-8444-444444444444",
        },
      },
      href: "/app/feedback?request=44444444-4444-4444-8444-444444444444",
      label: "Give feedback",
    },
    {
      item: {
        id: "setup-problem:no-licensed-states",
        kind: "setup_problem",
        title: "Add Licensed States",
        description: "Add at least one Licensed State before requesting a Batch.",
        tone: "warning",
        action: {
          kind: "add_licensed_states",
          label: "Open Account",
          description: "Add the states where you are licensed.",
          href: "/app/account",
        },
      },
      href: "/app/account",
      label: "Open Account",
    },
  ] as const)("deep-links $item.kind to its action", ({ item, href, label }) => {
    renderOverview({ items: [item] });

    expect(screen.getByRole("heading", { name: item.title })).toBeVisible();
    expect(screen.getByRole("link", { name: label })).toHaveAttribute(
      "href",
      href,
    );
  });

  it("is calm and empty when nothing needs the customer", () => {
    renderOverview({ items: [] });

    expect(
      screen.getByRole("heading", { name: "Nothing needs your attention" }),
    ).toBeVisible();
    expect(screen.getByText("You're all caught up.")).toBeVisible();
    expect(screen.queryByRole("list", { name: "Attention queue" })).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
