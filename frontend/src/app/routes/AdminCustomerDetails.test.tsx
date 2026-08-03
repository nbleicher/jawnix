import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../auth/adminMFA";

import type { CustomerDetailsData } from "./AdminCustomerDetails";
import { AdminCustomerDetailsRoute } from "./AdminCustomerDetails";

vi.mock("../auth/adminMFA", () => ({ api: vi.fn() }));

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
    ...overrides,
  };
}

function renderDetails(data: CustomerDetailsData) {
  const router = createMemoryRouter(
    [
      {
        id: "customer",
        path: "/admin/customers/:customerId",
        loader: () => data,
        element: <AdminCustomerDetailsRoute />,
      },
    ],
    {
      initialEntries: ["/admin/customers/7"],
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

  it("separates the durable Customer from its permanent history", () => {
    renderDetails(details());

    expect(
      screen.getByRole("heading", { level: 1, name: "Harbor Insurance" }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Back to Customers" }),
    ).toHaveAttribute("href", "/app/admin/customers");

    const identity = screen.getByRole("region", { name: "Customer" });
    expect(within(identity).getByText("harbor-insurance")).toBeVisible();
    expect(within(identity).getByText("Gulf Coast Agency")).toBeVisible();
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
    // The history counts belong to the Customer, not to the identity block.
    expect(within(identity).queryByText("240")).not.toBeInTheDocument();
    expect(
      within(identity).getByRole("button", { name: "Rename Customer" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Send password reset" }),
    ).toBeVisible();
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
    );

    const access = screen.getByRole("region", { name: "User Account" });
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
      screen.queryByRole("button", { name: "Replace User Account" }),
    ).not.toBeInTheDocument();
    // Nor act on an account that is about to be replaced.
    expect(
      screen.queryByRole("button", { name: "Send password reset" }),
    ).not.toBeInTheDocument();
  });

  it("offers an invitation, not a password, when there is no User Account", () => {
    renderDetails(details({ user_account: null }));

    const access = screen.getByRole("region", { name: "User Account" });
    expect(
      within(access).getByRole("heading", {
        level: 2,
        name: "No User Account yet",
      }),
    ).toBeVisible();
    expect(
      within(access).getByRole("button", { name: "Invite User Account" }),
    ).toBeVisible();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Send password reset" }),
    ).not.toBeInTheDocument();
  });

  it("shows that identity and history survived every replacement", () => {
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
    );

    const former = screen.getByRole("region", { name: "Former User Accounts" });
    expect(within(former).getByText("previous@harbor.example")).toBeVisible();
    expect(within(former).getByText(/Replaced/)).toBeVisible();
    expect(
      within(former).getByText(/survived every one of them/),
    ).toBeVisible();
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
