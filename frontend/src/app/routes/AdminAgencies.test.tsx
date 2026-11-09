import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../auth/adminMFA";

import type { AgencyDetailsData, AgencyDirectoryData } from "./AdminAgencies";
import { AdminAgenciesRoute, AdminAgencyDetailsRoute } from "./AdminAgencies";

vi.mock("../auth/adminMFA", () => ({ api: vi.fn() }));

const STATUS = {
  label: "Active",
  description: "Customers may be assigned to this Agency.",
  tone: "success",
};

function member(overrides: Partial<AgencyDirectoryData["independent"][number]> = {}) {
  return {
    id: 7,
    slug: "harbor-insurance",
    name: "Harbor Insurance",
    active: true,
    licensedStates: ["FL", "TX"],
    delivered30d: 300_000,
    openBatches: 1,
    href: "/app/admin/customers/7",
    ...overrides,
  };
}

function directory(overrides: Partial<AgencyDirectoryData> = {}): AgencyDirectoryData {
  return {
    filters: { query: "", status: "all" },
    total: 2,
    matched: 2,
    agencies: [
      {
        id: 4,
        slug: "gulf-coast",
        name: "Gulf Coast Agency",
        active: true,
        status: STATUS,
        members: [member(), member({ id: 8, slug: "pinecrest", name: "Pinecrest Agency", delivered30d: 75_000, openBatches: 0, href: "/app/admin/customers/8" })],
        currentMembers: 2,
        delivered30d: 375_000,
        openBatches: 1,
        sharedHistory: { customers: 5, agencies: 2, distributedLeads: 1_240_000 },
        lastActivityAt: "2026-10-02T12:00:00Z",
        href: "/app/admin/agencies/4",
      },
      {
        id: 9,
        slug: "lakeside",
        name: "Lakeside Agency",
        active: false,
        status: { ...STATUS, label: "Deactivated", tone: "warning" },
        members: [member({ id: 11, slug: "lakeside-brokers", name: "Lakeside Brokers", licensedStates: [], delivered30d: 0, openBatches: 0, href: "/app/admin/customers/11" })],
        currentMembers: 1,
        delivered30d: 0,
        openBatches: 0,
        sharedHistory: { customers: 1, agencies: 1, distributedLeads: 0 },
        lastActivityAt: null,
        href: "/app/admin/agencies/9",
      },
    ],
    independent: [member({ id: 12, slug: "independent-risk", name: "Independent Risk", licensedStates: ["GA"], delivered30d: 50_000, openBatches: 2, href: "/app/admin/customers/12" })],
    ...overrides,
  };
}

function details(): AgencyDetailsData {
  return {
    agency: { id: 4, slug: "gulf-coast", name: "Gulf Coast Agency", active: true, status: STATUS },
    members: [member()],
    sharedHistory: { customers: 5, agencies: 2, distributedLeads: 1_240_000 },
    membershipHistory: [
      {
        id: 1,
        customerId: 7,
        customer: "Harbor Insurance",
        customerHref: "/app/admin/customers/7",
        startedAt: "2026-01-20T12:00:00Z",
        endedAt: null,
        assignedBy: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        assignedByName: "Operator",
        reason: "Initial assignment",
      },
      {
        id: 2,
        customerId: 3,
        customer: "Deleted Customer",
        customerHref: null,
        startedAt: "2025-06-01T12:00:00Z",
        endedAt: "2025-12-01T12:00:00Z",
        assignedBy: "system:existing-membership",
        assignedByName: "System",
        reason: "Imported",
      },
    ],
    activityTimeline: {
      entries: [
        {
          id: "e1",
          action: "agency_updated",
          actionLabel: "Agency updated",
          entityType: "agency",
          entityId: "4",
          entityHref: "/app/admin/agencies/4",
          actor: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          actorName: "Operator",
          reason: "Renamed after the merger",
          details: { before: { name: "Gulf" }, after: { name: "Gulf Coast Agency" } },
          recordedAt: "2026-10-02T12:00:00Z",
        },
      ],
      page: 1,
      pageSize: 25,
      total: 1,
      pages: 1,
    },
    deletion: {
      dependencies: { customers: 1, distributions: 240, membershipHistory: 2 },
      requiresDeactivation: true,
      canHardDelete: false,
    },
  };
}

function renderRoute(element: React.ReactNode, data: unknown, path: string, entry = path.replace(":agencyId", "4")) {
  const router = createMemoryRouter(
    [{ id: "route", path, loader: () => data, element }],
    { initialEntries: [entry], hydrationData: { loaderData: { route: data } } },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("Agencies", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
  });

  it("lists agencies with members, recent deliveries, and open batches", () => {
    renderRoute(<AdminAgenciesRoute />, directory(), "/admin/agencies");

    const table = screen.getByRole("table", { name: "Agencies" });
    const [gulf, lakeside] = within(table).getAllByRole("row").slice(1);
    expect(within(gulf!).getByRole("link", { name: "Gulf Coast Agency" })).toHaveAttribute(
      "href",
      "/admin/agencies/4",
    );
    expect(within(gulf!).getByText("2")).toBeVisible();
    expect(within(gulf!).getByText("375,000")).toBeVisible();
    expect(within(gulf!).getByText("1,240,000")).toBeVisible();
    expect(within(lakeside!).getByText("Deactivated")).toBeVisible();
    expect(screen.getByText("Showing 2 of 2 agencies.")).toBeVisible();
  });

  it("links every member to its customer page and its agency", () => {
    renderRoute(<AdminAgenciesRoute />, directory(), "/admin/agencies");

    const members = screen.getByRole("table", { name: "Agency members" });
    expect(within(members).getByRole("link", { name: "Harbor Insurance" })).toHaveAttribute(
      "href",
      "/admin/customers/7",
    );
    expect(within(members).getAllByRole("link", { name: "Gulf Coast Agency" })).toHaveLength(2);
    // A member of a deactivated agency cannot work, whatever its own state.
    const lakeside = within(members).getByRole("link", { name: "Lakeside Brokers" }).closest("tr")!;
    expect(within(lakeside).getByText("Deactivated")).toBeVisible();

    const independent = screen.getByRole("table", { name: "Independent customers" });
    expect(within(independent).getByRole("link", { name: "Independent Risk" })).toBeVisible();
    expect(within(independent).getByText("50,000")).toBeVisible();
  });

  it("filters by search and status in the URL", async () => {
    const user = userEvent.setup();
    const router = renderRoute(<AdminAgenciesRoute />, directory(), "/admin/agencies");

    await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "deactivated");
    await waitFor(() => expect(router.state.location.search).toContain("status=deactivated"));
    await user.type(screen.getByRole("searchbox", { name: "Search" }), "harbor");
    await waitFor(() => expect(router.state.location.search).toContain("q=harbor"));
  });
});

describe("An Agency's page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
  });

  it("leads with its numbers and links members and history", () => {
    renderRoute(<AdminAgencyDetailsRoute />, details(), "/admin/agencies/:agencyId");

    const numbers = screen.getByLabelText("Agency numbers");
    expect(within(numbers).getByText("Shared-history leads")).toBeVisible();
    expect(within(numbers).getByText("1,240,000")).toBeVisible();
    const members = screen.getByRole("table", { name: "Members" });
    expect(within(members).getByRole("link", { name: "Harbor Insurance" })).toHaveAttribute(
      "href",
      "/admin/customers/7",
    );
    const history = screen.getByRole("table", { name: "Membership history" });
    expect(within(history).getByRole("link", { name: "Harbor Insurance" })).toBeVisible();
    expect(within(history).getByText("Deleted Customer")).toBeVisible();
    expect(within(history).getByText("Current")).toBeVisible();
    expect(within(history).getByText("System")).toBeVisible();
    const activity = screen.getByRole("table", { name: "Agency activity" });
    expect(within(activity).getByText("Renamed after the merger")).toBeVisible();
  });

  it("keeps deletion behind deactivation and explains retained history", () => {
    renderRoute(<AdminAgencyDetailsRoute />, details(), "/admin/agencies/:agencyId");

    expect(screen.getByText("Deactivate this Agency before deleting it.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Delete Agency" })).toBeDisabled();
  });

  it("deactivates with a reason", async () => {
    const user = userEvent.setup();
    renderRoute(<AdminAgencyDetailsRoute />, details(), "/admin/agencies/:agencyId");

    await user.click(screen.getByRole("button", { name: "Deactivate Agency" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Agency closed");
    await user.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith("/api/admin/agencies/4", {
        method: "PATCH",
        body: JSON.stringify({ name: "Gulf Coast Agency", active: false, reason: "Agency closed" }),
      }),
    );
  });
});
