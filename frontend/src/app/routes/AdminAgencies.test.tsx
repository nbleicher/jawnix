import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../auth/adminMFA";

import type {
  AgencyDetailsData,
  AgencyDirectoryData,
} from "./AdminAgencies";
import {
  AdminAgenciesRoute,
  AdminAgencyDetailsRoute,
} from "./AdminAgencies";
import type { CustomerDetailsData } from "./AdminCustomerDetails";

vi.mock("../auth/adminMFA", () => ({ api: vi.fn() }));

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
        members: [
          {
            id: 7,
            slug: "harbor-insurance",
            name: "Harbor Insurance",
            active: true,
            licensedStates: ["FL", "TX"],
            href: "/app/admin/customers/7",
          },
        ],
        currentMembers: 1,
        sharedHistory: {
          customers: 5,
          agencies: 2,
          distributedLeads: 240,
        },
        lastActivityAt: "2026-07-20T12:00:00Z",
        href: "/app/admin/agencies/4",
      },
    ],
    independent: [
      {
        id: 12,
        slug: "independent-risk",
        name: "Independent Risk",
        active: true,
        licensedStates: ["GA"],
        href: "/app/admin/customers/12",
      },
    ],
    openCustomer: null,
  };
}

function customerDetails(): CustomerDetailsData {
  return {
    activityTimeline: { entries: [], page: 1, pageSize: 25, total: 0, pages: 1 },
    customer: {
      id: 7,
      slug: "harbor-insurance",
      name: "Harbor Insurance",
      agency_id: 4,
      agency: "Gulf Coast Agency",
      active: true,
      licensed_states: ["FL", "TX"],
      status: {
        label: "Active",
        description: "This Customer can receive Batches.",
        tone: "success",
      },
      last_activity_at: "2026-07-20T12:00:00Z",
    },
    history: {
      requests: 1,
      distributions: 1,
      outcomes: 0,
      reports: 0,
      first_delivered_at: null,
      last_delivered_at: null,
    },
    user_account: {
      auth_user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "owner@harbor.example",
      name: "Casey Reyes",
      active: true,
      created_at: "2026-01-05T12:00:00Z",
      replaced_at: null,
      replaced_by_auth_user_id: null,
    },
    invitation: null,
    former_accounts: [],
    activity: [],
    deletion: {
      dependencies: {},
      requires_deactivation: true,
      can_hard_delete: false,
      tombstoned: false,
    },
    agencies: [],
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

function renderRoute(
  element: React.ReactNode,
  data: unknown,
  path: string,
  entry = path.replace(":agencyId", "4"),
) {
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
      initialEntries: [entry],
      hydrationData: { loaderData: { route: data } },
    },
  );
  return render(<RouterProvider router={router} />);
}

describe("administrator Agency management", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
  });

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
    expect(within(card).getByText("1")).toBeVisible();
    expect(within(card).getByText("5")).toBeVisible();
    expect(
      within(card).getByRole("link", { name: /Harbor Insurance/ }),
    ).toHaveAttribute("href", "/admin/agencies?customer=7");
    expect(
      screen.getByRole("article", { name: "Independent Customers" }),
    ).toBeVisible();
    expect(screen.getByText(/never sign in/)).toBeVisible();
  });

  it("names the Independent group's empty state after the group", () => {
    const data = directory();
    data.independent = [];
    renderRoute(<AdminAgenciesRoute />, data, "/admin/agencies");

    const independent = screen.getByRole("article", {
      name: "Independent Customers",
    });
    expect(
      within(independent).getByText("No Independent Customers."),
    ).toBeVisible();
    expect(
      within(independent).queryByText("No Customers in this Agency."),
    ).not.toBeInTheDocument();
  });

  it("uses the Agency status when showing a member's effective status", () => {
    const data = directory();
    const agency = data.agencies[0];
    if (!agency) throw new Error("Expected an Agency fixture");
    data.agencies[0] = {
      ...agency,
      active: false,
      status: {
        label: "Deactivated",
        description: "New assignments and Customer work are blocked.",
        tone: "warning",
      },
    };
    renderRoute(<AdminAgenciesRoute />, data, "/admin/agencies");

    const card = screen.getByRole("article", { name: "Gulf Coast Agency" });
    expect(within(card).getByText("Inactive", { exact: true })).toBeVisible();
  });

  it("opens customer actions and preserves full-replace fields", async () => {
    const user = userEvent.setup();
    const data = directory();
    data.openCustomer = customerDetails();
    renderRoute(
      <AdminAgenciesRoute />,
      data,
      "/admin/agencies",
      "/admin/agencies?customer=7",
    );

    const card = screen.getByRole("dialog", { name: "Harbor Insurance" });
    expect(within(card).getByRole("button", { name: "Rename Customer" })).toBeVisible();
    expect(within(card).getByRole("button", { name: "Deactivate Customer" })).toBeVisible();
    expect(within(card).getByRole("button", { name: "Send password reset" })).toBeVisible();
    expect(within(card).getByRole("link", { name: "Open full record" })).toHaveAttribute(
      "href",
      "/app/admin/customers/7",
    );

    await user.click(within(card).getByRole("button", { name: "Rename Customer" }));
    const rename = screen.getByRole("dialog", { name: "Rename Customer" });
    await user.clear(within(rename).getByLabelText("Customer name (required)"));
    await user.type(within(rename).getByLabelText("Customer name (required)"), "Harbor Group");
    await user.type(within(rename).getByLabelText("Reason (required)"), "Legal name changed");
    await user.click(within(rename).getByRole("button", { name: "Rename Customer" }));

    expect(vi.mocked(api)).toHaveBeenCalledWith(
      "/api/admin/customers/7",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          name: "Harbor Group",
          agency_id: 4,
          active: true,
          reason: "Legal name changed",
        }),
      }),
    );

    await user.click(within(card).getByRole("button", { name: "Send password reset" }));
    const reset = screen.getByRole("dialog", { name: "Send password reset" });
    await user.click(within(reset).getByRole("button", { name: "Send password reset" }));
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      "/api/admin/user-accounts/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/send-password-reset",
      { method: "POST" },
    );
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
