import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import type {
  AgencyDetailsData,
  AgencyDirectoryData,
} from "./AdminAgencies";
import {
  AdminAgenciesRoute,
  AdminAgencyDetailsRoute,
} from "./AdminAgencies";

const STATUS = {
  label: "Active",
  description: "Customers may be assigned to this Agency.",
  tone: "success" as const,
};

function directory(): AgencyDirectoryData {
  return {
    filters: { query: "", status: "all" },
    total: 1,
    matched: 1,
    agencies: [
      {
        id: 4,
        slug: "gulf-coast",
        name: "Gulf Coast Agency",
        active: true,
        status: STATUS,
        currentMembers: 3,
        sharedHistory: {
          customers: 5,
          agencies: 2,
          distributedLeads: 240,
        },
        lastActivityAt: "2026-07-20T12:00:00Z",
        href: "/app/admin/agencies/4",
      },
    ],
  };
}

function details(): AgencyDetailsData {
  return {
    activityTimeline: {
      entries: [],
      page: 1,
      pageSize: 25,
      total: 0,
      pages: 1,
    },
    agency: {
      id: 4,
      slug: "gulf-coast",
      name: "Gulf Coast Agency",
      active: true,
      status: STATUS,
    },
    members: [
      {
        id: 7,
        slug: "harbor",
        name: "Harbor Insurance",
        active: true,
        licensedStates: ["FL", "TX"],
        href: "/app/admin/customers/7",
      },
    ],
    sharedHistory: {
      customers: 5,
      agencies: 2,
      distributedLeads: 240,
    },
    membershipHistory: [
      {
        id: 1,
        customerId: 7,
        customer: "Harbor Insurance",
        startedAt: "2026-01-20T12:00:00Z",
        endedAt: null,
        assignedBy: "admin@example.com",
        reason: "Initial assignment",
      },
    ],
    activity: [
      {
        id: "membership-1",
        action: "customer_assigned",
        label: "Harbor Insurance assigned",
        actor: "admin@example.com",
        reason: "Initial assignment",
        createdAt: "2026-01-20T12:00:00Z",
      },
    ],
    deletion: {
      dependencies: {
        customers: 1,
        distributions: 240,
        membershipHistory: 1,
      },
      requiresDeactivation: true,
      canHardDelete: false,
    },
  };
}

function renderRoute(element: React.ReactNode, data: unknown, path: string) {
  const router = createMemoryRouter(
    [
      {
        id: "route",
        path,
        loader: () => data,
        element,
      },
    ],
    {
      initialEntries: [path.replace(":agencyId", "4")],
      hydrationData: { loaderData: { route: data } },
    },
  );
  return render(<RouterProvider router={router} />);
}

describe("administrator Agency management", () => {
  it("shows searchable internal groups and shared-history impact", () => {
    renderRoute(<AdminAgenciesRoute />, directory(), "/admin/agencies");

    expect(
      screen.getByRole("heading", { level: 1, name: "Agencies" }),
    ).toBeVisible();
    expect(screen.getByRole("searchbox", { name: "Search" })).toBeVisible();
    const card = screen.getByRole("article", { name: "Gulf Coast Agency" });
    expect(
      within(card).getByRole("link", { name: "Gulf Coast Agency" }),
    ).toHaveAttribute("href", "/admin/agencies/4");
    expect(within(card).getByText("3")).toBeVisible();
    expect(within(card).getByText("5")).toBeVisible();
    expect(screen.getByText(/never sign in/)).toBeVisible();
  });

  it("keeps current membership separate from permanent merged history", () => {
    renderRoute(
      <AdminAgencyDetailsRoute />,
      details(),
      "/admin/agencies/:agencyId",
    );

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Gulf Coast Agency",
      }),
    ).toBeVisible();
    expect(screen.getByText(/no User Account/)).toBeVisible();

    const impact = screen.getByRole("region", {
      name: "Shared-history impact",
    });
    expect(within(impact).getByText("5")).toBeVisible();
    expect(within(impact).getByText("240")).toBeVisible();

    const members = screen.getByRole("region", { name: "Current members" });
    expect(
      within(members).getByRole("link", { name: "Harbor Insurance" }),
    ).toHaveAttribute("href", "/admin/customers/7");

    const lifecycle = screen.getByRole("region", { name: "Lifecycle" });
    expect(
      within(lifecycle).getByRole("button", { name: "Delete Agency" }),
    ).toBeDisabled();
    expect(
      within(lifecycle).getByText(/Deactivate this Agency before deletion/),
    ).toBeVisible();
  });
});
