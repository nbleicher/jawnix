import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";

import type { CustomerDetailsData } from "./AdminCustomerDetails";
import {
  AdminCustomerDetailsRoute,
  adminCustomerDetailsLoader,
  adminCustomerDetailsShouldRevalidate,
} from "./AdminCustomerDetails";

vi.mock("../api", () => ({ api: vi.fn() }));

const ACCOUNT = {
  auth_user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "owner@harbor.example",
  name: "Casey Reyes",
  active: true,
  created_at: "2026-01-05T12:00:00Z",
  replaced_at: null,
  replaced_by_auth_user_id: null,
};

function details(
  overrides: Partial<CustomerDetailsData> = {},
): CustomerDetailsData {
  return {
    activityTimeline: {
      entries: [],
      page: 1,
      pageSize: 25,
      total: 0,
      pages: 1,
    },
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
      lead_database: "db1",
    },
    history: {
      requests: 18,
      distributions: 16,
      outcomes: 240,
      reports: 9,
      first_delivered_at: "2026-01-20T12:00:00Z",
      last_delivered_at: "2026-07-20T12:00:00Z",
    },
    user_account: ACCOUNT,
    invitation: null,
    former_accounts: [],
    activity: [],
    deletion: {
      dependencies: { requests: 18, distributions: 16 },
      requires_deactivation: true,
      can_hard_delete: false,
      tombstoned: false,
    },
    agencies: [
      { id: 4, name: "Gulf Coast Agency", active: true },
      { id: 9, name: "Lakeside Agency", active: true },
    ],
    billing: {
      customerId: 7,
      billingEnabled: false,
      leadRateCentsPerThousand: null,
      balanceCents: 0,
      activeHoldsCents: 0,
      availableBalanceCents: 0,
      purchases: [],
      ledger: [],
    },
    cooldown: { days: 7 },
    nichePolicy: { rows: [] },
    exclusionLists: [],
    leadReports: [],
    summary: {
      customerId: 7,
      database: "db1",
      states: ["FL", "TX"],
      customerSince: "2026-01-05T12:00:00Z",
      needs: [
        {
          kind: "conflict",
          label: "Conflict with Lakeside Brokers",
          href: "/app/admin/fulfillment/conflicts/c-1",
        },
      ],
      balance: {
        billed: true,
        availableCents: 123_200,
        heldCents: 25_000,
        heldFor: [1041],
        rateCentsPerThousand: 500,
      },
      available: { count: 412_880, asOf: "2026-07-21T06:00:00Z" },
      openBatches: {
        count: 1,
        first: { number: 1041, leadCount: 50_000, stage: "generating", href: "/app/admin/batches/1041" },
      },
      delivered: { windowDays: 30, leads: 300_000, batches: 5 },
      lastDeliveryAt: "2026-07-20T12:00:00Z",
      leadReports: { open: 0, total: 0 },
    },
    ...overrides,
  };
}

function renderDetails(data: CustomerDetailsData, path = "/admin/customers/7") {
  const router = createMemoryRouter(
    [
      {
        id: "customer",
        path: "/admin/customers/:customerId/:tab?",
        loader: () => data,
        element: <AdminCustomerDetailsRoute />,
      },
    ],
    {
      initialEntries: [path],
      hydrationData: { loaderData: { customer: data } },
    },
  );
  return render(<RouterProvider router={router} />);
}

describe("administrator Customer details", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
  });

  it("keeps core Customer details available when an ancillary endpoint fails", async () => {
    const full = details();
    const {
      agencies: _agencies,
      activityTimeline: _activityTimeline,
      billing: _billing,
      cooldown: _cooldown,
      nichePolicy: _nichePolicy,
      availability: _availability,
      exclusionLists: _exclusionLists,
      leadReports: _leadReports,
      summary: _summary,
      ...core
    } = full;
    vi.mocked(api)
      .mockResolvedValueOnce(core)
      .mockRejectedValue(new Error("analytics unavailable"));

    const loaded = await adminCustomerDetailsLoader({
      params: { customerId: "7" },
    } as unknown as Parameters<typeof adminCustomerDetailsLoader>[0]);

    expect(loaded.customer.name).toBe("Harbor Insurance");
    expect(loaded.agencies).toEqual([]);
    expect(loaded.activityTimeline.entries).toEqual([]);
    expect(loaded.availability).toBeUndefined();
    // A missing section and a broken one must not look the same.
    expect(loaded.summary).toBeUndefined();
    expect(loaded.unavailableSections).toEqual([
      "Numbers",
      "Billing",
      "Cooldown window",
      "Availability",
      "Niche policy",
      "Exclusion lists",
      "Lead reports",
      "Activity",
      "Agency directory",
    ]);
  });

  it("names the sections a failed fetch removed instead of hiding them", () => {
    renderDetails(
      details({ unavailableSections: ["Billing", "Niche Policy"] }),
    );

    const notice = screen.getByRole("alert");
    expect(notice).toHaveTextContent(/Billing, Niche Policy/);
    expect(screen.getByRole("button", { name: "Retry loading" })).toBeVisible();
  });

  it("shows no failure notice when every section loaded", () => {
    renderDetails(details());

    expect(screen.queryByRole("button", { name: "Retry loading" })).toBeNull();
  });

  it("leads with the numbers row and the record's tabs", () => {
    renderDetails(details());

    expect(
      screen.getByRole("heading", { level: 1, name: "Harbor Insurance" }),
    ).toBeVisible();
    expect(screen.getByText(/^Gulf Coast Agency · FL, TX · DB1 · customer since Jan 5/)).toBeVisible();

    const numbers = document.querySelector<HTMLElement>('dl[aria-label="Customer numbers"]')!;
    expect(numbers).not.toBeNull();
    expect(within(numbers).getByText("$1,232.00")).toBeVisible();
    expect(within(numbers).getByText("$250.00 held for #1041")).toBeVisible();
    expect(within(numbers).getByText("412,880")).toBeVisible();
    expect(within(numbers).getByText("#1041 generating")).toBeVisible();
    expect(within(numbers).getByText("300,000")).toBeVisible();
    expect(within(numbers).getByText("5 batches")).toBeVisible();
    expect(within(numbers).getByText("Conflict with Lakeside Brokers")).toBeVisible();

    const tabs = screen.getByRole("navigation", { name: "Customer sections" });
    const links = within(tabs).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Summary", "Batches", "Billing", "Rules", "Reports", "Access", "Activity",
    ]);
    expect(within(tabs).getByRole("link", { name: "Summary" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Billing" })).toHaveAttribute("href", "/app/admin/customers/7/billing");
  });

  it("puts the customer's identity, needs, and permanent history on Summary", () => {
    renderDetails(details());

    expect(screen.getByRole("button", { name: "Rename" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Replace user account" })).toBeVisible();

    const needs = screen.getByRole("region", { name: "Needs" });
    expect(within(needs).getByRole("link", { name: "Conflict with Lakeside Brokers" })).toHaveAttribute(
      "href",
      "/admin/fulfillment/conflicts/c-1",
    );

    const identity = screen.getByRole("region", { name: "Customer" });
    expect(within(identity).getByText("harbor-insurance")).toBeVisible();
    expect(within(identity).getByRole("link", { name: "Gulf Coast Agency" })).toHaveAttribute("href", "/admin/agencies/4");
    expect(within(identity).getByText("FL, TX")).toBeVisible();
    expect(
      within(identity).getByText(/do not change when access is replaced/),
    ).toBeVisible();

    const permanent = screen.getByRole("region", { name: "Permanent history" });
    expect(within(permanent).getByText("18")).toBeVisible();
    expect(within(permanent).getByText("240")).toBeVisible();
    expect(
      within(permanent).getByText(
        /Replacing a User Account never resets any of it/,
      ),
    ).toBeVisible();
    expect(within(identity).queryByText("240")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change Agency assignment" })).toBeVisible();
  });

  it("keeps the current User Account active while a replacement is pending", () => {
    renderDetails(
      details({
        invitation: {
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          email: "newowner@harbor.example",
          invited_at: "2026-07-25T12:00:00Z",
          replaces_auth_user_id: ACCOUNT.auth_user_id,
          status: {
            label: "Awaiting acceptance",
            description: "The invited address has not signed in yet.",
            tone: "info",
          },
        },
      }),
      "/admin/customers/7/access",
    );

    const access = screen.getByRole("region", { name: "User account" });
    const current = within(access).getByRole("article", {
      name: "Current User Account",
    });
    expect(within(current).getByText("owner@harbor.example")).toBeVisible();
    expect(within(current).getByText("Active", { exact: true })).toBeVisible();

    const pending = within(access).getByRole("article", {
      name: "Pending invitation",
    });
    expect(within(pending).getByText(/newowner@harbor.example/)).toBeVisible();
    expect(
      within(pending).getByText(
        /The current User Account stays active until this invitation is accepted/,
      ),
    ).toBeVisible();
    expect(within(pending).getByText(/Nothing has been replaced yet/)).toBeVisible();
    expect(
      within(pending).getByRole("button", { name: "Cancel invitation" }),
    ).toBeVisible();
    // A pending replacement must not offer to start a second one.
    expect(
      screen.queryByRole("button", { name: /Replace user account/i }),
    ).not.toBeInTheDocument();
    // Nor act on an account that is about to be replaced.
    expect(
      screen.queryByRole("button", { name: "Send password reset" }),
    ).not.toBeInTheDocument();
  });

  it("offers an invitation, not a password, when there is no User Account", () => {
    renderDetails(details({ user_account: null }), "/admin/customers/7/access");

    const access = screen.getByRole("region", { name: "User account" });
    expect(within(access).getByText("No user account yet")).toBeVisible();
    expect(screen.getByRole("button", { name: "Invite user account" })).toBeVisible();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Send password reset" }),
    ).not.toBeInTheDocument();
  });

  it("lists former User Accounts on Access", () => {
    renderDetails(
      details({
        former_accounts: [
          {
            ...ACCOUNT,
            auth_user_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            email: "previous@harbor.example",
            active: false,
            replaced_at: "2026-06-01T12:00:00Z",
          },
        ],
      }),
      "/admin/customers/7/access",
    );

    const former = screen.getByRole("table", { name: "Former user accounts" });
    expect(within(former).getByText("previous@harbor.example")).toBeVisible();
    expect(within(former).getByText("Jun 1")).toBeVisible();
    expect(screen.getByRole("button", { name: "Send password reset" })).toBeVisible();
  });

  it("guards deletion behind deactivation and names what blocks it", () => {
    renderDetails(details());

    const lifecycle = screen.getByRole("region", { name: "Lifecycle" });
    expect(
      within(lifecycle).getByRole("button", { name: "Deactivate Customer" }),
    ).toBeEnabled();
    expect(
      within(lifecycle).getByRole("button", { name: "Delete Customer" }),
    ).toBeDisabled();
    expect(
      within(lifecycle).getByRole("button", { name: "Erase personal data" }),
    ).toBeDisabled();
    expect(
      within(lifecycle).getByText(
        /Deletion and erasure need the Customer deactivated first/,
      ),
    ).toBeVisible();
  });

  it("explains the dependencies that block permanent deletion", () => {
    renderDetails(
      details({
        customer: { ...details().customer, active: false },
        deletion: {
          dependencies: { requests: 18, distributions: 16, userAccounts: 1 },
          requires_deactivation: false,
          can_hard_delete: false,
          tombstoned: false,
        },
      }),
    );

    const lifecycle = screen.getByRole("region", { name: "Lifecycle" });
    expect(
      within(lifecycle).getByText(/Permanent deletion is blocked/),
    ).toBeVisible();
    expect(within(lifecycle).getByText("Batch Requests: 18")).toBeVisible();
    expect(within(lifecycle).getByText("User Accounts: 1")).toBeVisible();
    expect(
      within(lifecycle).getByRole("button", { name: "Reactivate Customer" }),
    ).toBeVisible();
    expect(screen.getByText("Deactivated", { selector: ".jx-status" })).toBeVisible();
  });

  it("gathers the allocation rules on Rules", () => {
    renderDetails(
      details({ nichePolicy: { rows: [{ state: "TX", mode: "only", niches: ["solar"] }] } }),
      "/admin/customers/7/rules",
    );

    expect(screen.getByRole("navigation", { name: "Customer sections" }))
      .toHaveTextContent("Rules");
    expect(screen.getByRole("region", { name: "Database" })).toHaveTextContent("DB1 · Operator leads");
    expect(screen.getByRole("region", { name: "Cooldown window" })).toHaveTextContent("7 days");
    const policy = screen.getByRole("table", { name: "Current Niche Policy" });
    expect(within(policy).getByText("TX")).toBeVisible();
    expect(within(policy).getByText("Only")).toBeVisible();
    expect(within(policy).getByText("solar")).toBeVisible();
    expect(screen.getByRole("region", { name: "Exclusion lists" })).toHaveTextContent(
      "This customer has not uploaded an exclusion list.",
    );
  });

  it("lists the customer's lead reports with their batch on Reports", () => {
    renderDetails(
      details({
        leadReports: [
          {
            id: "r-1",
            href: "/app/admin/fulfillment/reports/r-1",
            reason: "invalid_phone",
            reasonLabel: "Invalid phone",
            details: "Disconnected",
            status: "open",
            statusLabel: "Open",
            createdAt: "2026-07-22T12:00:00Z",
            lead: { phone: "2155550101", title: "Buckeye Dental", state: "FL" },
            batch: { number: 1033, href: "/app/admin/batches/1033" },
            eligibilityHeld: true,
          },
        ],
      }),
      "/admin/customers/7/reports",
    );

    const table = screen.getByRole("table", { name: "Lead reports" });
    expect(within(table).getByRole("link", { name: "Jul 22" })).toHaveAttribute("href", "/admin/fulfillment/reports/r-1");
    expect(within(table).getByRole("link", { name: "#1033" })).toHaveAttribute("href", "/admin/batches/1033");
    expect(within(table).getByText("(215) 555-0101 · Buckeye Dental")).toBeVisible();
    expect(within(table).getByText("Open")).toHaveClass("jx-status--danger");
    expect(within(table).getByText("Held from allocation")).toBeVisible();
  });

  it("renders no password input anywhere", () => {
    renderDetails(details());

    expect(document.querySelector('input[type="password"]')).toBeNull();
  });

  it("keeps lifecycle behavior after extracting the shared action", async () => {
    const user = userEvent.setup();
    renderDetails(details());

    await user.click(screen.getByRole("button", { name: "Deactivate Customer" }));
    const dialog = screen.getByRole("dialog", { name: "Deactivate Customer" });
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Customer requested closure");
    await user.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    expect(vi.mocked(api)).toHaveBeenCalledWith(
      "/api/admin/customers/7",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          name: "Harbor Insurance",
          agency_id: 4,
          active: false,
          reason: "Customer requested closure",
        }),
      }),
    );
  });
});

describe("Customer page revalidation", () => {
  const args = (from: string, to: string, fromId = "7", toId = "7") =>
    ({
      currentUrl: new URL(`http://x${from}`),
      nextUrl: new URL(`http://x${to}`),
      currentParams: { customerId: fromId },
      nextParams: { customerId: toId },
      defaultShouldRevalidate: true,
    }) as unknown as Parameters<typeof adminCustomerDetailsShouldRevalidate>[0];

  it("keeps what it read when the operator switches tabs or opens a batch", () => {
    expect(adminCustomerDetailsShouldRevalidate(args("/admin/customers/7", "/admin/customers/7/billing"))).toBe(false);
    expect(adminCustomerDetailsShouldRevalidate(args("/admin/customers/7/batches", "/admin/customers/7/batches?batch=1041"))).toBe(false);
  });

  it("reads again for an explicit refresh or another customer", () => {
    expect(adminCustomerDetailsShouldRevalidate(args("/admin/customers/7/rules", "/admin/customers/7/rules"))).toBe(true);
    expect(adminCustomerDetailsShouldRevalidate(args("/admin/customers/7", "/admin/customers/8", "7", "8"))).toBe(true);
  });
});
