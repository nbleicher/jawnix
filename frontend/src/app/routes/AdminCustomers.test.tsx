import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CustomerDirectoryData, CustomerDirectoryRow } from "./AdminCustomers";
import {
  AdminCustomersRoute,
  adminCustomerDirectoryShouldRevalidate,
} from "./AdminCustomers";

const ACTIVE = {
  label: "Active",
  description: "This Customer can receive Batches.",
  tone: "success" as const,
};

function row(overrides: Partial<CustomerDirectoryRow> = {}): CustomerDirectoryRow {
  return {
    id: 7,
    slug: "harbor-insurance",
    name: "Harbor Insurance",
    agency_id: 4,
    agency: "Gulf Coast Agency",
    licensed_states: ["FL", "TX"],
    customer_status: ACTIVE,
    account_status: {
      label: "Account active",
      description: "The User Account can sign in.",
      tone: "success",
    },
    account_email: "owner@harbor.example",
    last_activity_at: "2026-07-20T12:00:00Z",
    problems: [],
    href: "/app/admin/customers/7",
    lead_database: "db1",
    needs: [
      {
        kind: "conflict",
        label: "Conflict with Lakeside Brokers",
        href: "/app/admin/fulfillment/conflicts/c-1",
      },
    ],
    billed: true,
    balance_cents: 123_200,
    held_cents: 25_000,
    held_for: [1041],
    lead_rate_cents_per_thousand: 500,
    open_batches: 1,
    open_batch: {
      number: 1041,
      leadCount: 50_000,
      stage: "generating",
      href: "/app/admin/batches/1041",
    },
    delivered_30d: 300_000,
    delivered_30d_batches: 5,
    last_delivery_at: "2026-07-20T12:00:00Z",
    available: 412_880,
    available_as_of: "2026-07-21T06:00:00Z",
    ...overrides,
  };
}

function directory(
  overrides: Partial<CustomerDirectoryData> = {},
): CustomerDirectoryData {
  return {
    filters: {
      query: "",
      status: "active",
      agency_id: null,
      state: "",
      problems_only: false,
    },
    agencies: [{ id: 4, name: "Gulf Coast Agency", active: true }],
    states: ["FL", "TX"],
    total: 3,
    matched: 2,
    counts: { active: 2, deactivated: 1 },
    customers: [
      row(),
      row({
        id: 16,
        slug: "meridian-health-brokers",
        name: "Meridian Health Brokers",
        agency_id: null,
        agency: "",
        href: "/app/admin/customers/16",
        lead_database: "db2",
        needs: [],
        billed: false,
        balance_cents: 0,
        held_cents: 0,
        held_for: [],
        open_batches: 0,
        open_batch: null,
        delivered_30d: 175_000,
        last_delivery_at: null,
        available: null,
      }),
    ],
    ...overrides,
  };
}

function renderDirectory(data: CustomerDirectoryData, path = "/admin/customers") {
  const router = createMemoryRouter(
    [
      {
        id: "customers",
        path: "/admin/customers",
        loader: () => data,
        element: <AdminCustomersRoute />,
      },
      { path: "/admin/customers/:id", element: <p>Customer page</p> },
    ],
    {
      initialEntries: [path],
      hydrationData: { loaderData: { customers: data } },
    },
  );
  return { router, ...render(<RouterProvider router={router} />) };
}

describe("administrator Customers table", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requires confirmation before restoring a verified registration's history", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail:"Decision captured" }), { status:409 }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    renderDirectory(directory({ pending_registrations:[{ id:"new-user", firstName:"River", lastName:"Morgan", email:"new@example.test", phone:"6155550100", states:["TN"], matches:[{ id:19, name:"River Morgan", slug:"river-morgan", email:"old@example.test" }] }] }));
    expect(screen.getByRole("link", { name: "Registrations (1 waiting)" })).toHaveAttribute("href", "#registrations");
    await user.click(screen.getByRole("button", { name:"Restore river-morgan (old@example.test)" }));
    expect(fetcher).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Grant new@example.test access");
    await user.click(within(dialog).getByRole("button", { name:"Confirm decision" }));
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/admin/registrations/new-user/decision");
    expect(JSON.parse(fetcher.mock.calls[0]?.[1].body)).toEqual({ decision:"restore", restore_customer_id:19 });
    expect(await screen.findByRole("alert")).toHaveTextContent("Decision captured");
  });

  it("requires a second operator action to restore matching archived history", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail: {
      message: "Confirm archived customer history.",
      archivedCustomers: [{ id: 19, name: "River Morgan", slug: "river-morgan", email: "old@example.com" }],
    } }), { status: 409, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Captured confirmation" }), { status: 409 }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    renderDirectory(directory());
    await user.click(screen.getByRole("button", { name: "Create Customer" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Customer name/), "River Morgan");
    await user.type(within(dialog).getByLabelText(/Email/), "new@example.com");
    await user.type(within(dialog).getByLabelText("First name"), "River");
    await user.type(within(dialog).getByLabelText("Last name"), "Morgan");
    await user.click(within(dialog).getByRole("button", { name: "Create Customer and send invitation" }));
    const restore = await screen.findByRole("button", { name: "Restore river-morgan and send invitation" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0]![1].body)).not.toHaveProperty("restore_customer_id");
    expect(screen.getByRole("button", { name: "Create separate Customer without old history" })).toBeVisible();
    await user.click(restore);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetcher.mock.calls[1]![1].body)).toMatchObject({ email: "new@example.com", restore_customer_id: 19 });
  });

  it("lists customers with what they need, their money, and the totals", () => {
    renderDirectory(directory());

    expect(document.querySelector(".jx-page")).toHaveClass("jx-page--wide");
    expect(screen.getByText("2 active, sorted by what each one needs. Select a row to see it beside the list.")).toBeVisible();
    const table = screen.getByRole("table", { name: "Customers" });
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Customer", "Needs", "DB", "Balance", "Open", "Delivered 30d", "Last delivery"]);

    const [, harbor, meridian, totals] = within(table).getAllByRole("row");
    // Router-relative here because this harness has no basename.
    expect(within(harbor!).getByRole("link", { name: "Harbor Insurance" })).toHaveAttribute("href", "/admin/customers/7");
    expect(within(harbor!).getByText("Conflict with Lakeside Brokers")).toHaveClass("jx-text--danger");
    expect(within(harbor!).getByText("$1,232.00")).toBeVisible();
    expect(within(harbor!).getByText("300,000")).toBeVisible();
    expect(within(meridian!).getByText("Nothing")).toBeVisible();
    expect(within(meridian!).getByText("Not billed")).toBeVisible();
    expect(within(meridian!).getByText("DB2")).toBeVisible();
    expect(within(meridian!).getByText("Never")).toBeVisible();

    expect(within(totals!).getByText("2 customers")).toBeVisible();
    expect(within(totals!).getByText("1 needs something")).toBeVisible();
    expect(within(totals!).getByText("475,000")).toBeVisible();

    expect(screen.getByRole("link", { name: "Deactivated customers (1)" })).toHaveAttribute(
      "href",
      "/app/admin/customers?status=deactivated",
    );
    expect(screen.getByText("Registrations (0 waiting)")).toBeVisible();
    // The manager select, the managed-administrator access, and the Niche
    // Assignment transfer are gone from this page.
    expect(screen.queryByLabelText(/Lead database for/)).toBeNull();
    expect(screen.queryByText(/Niche Assignment/)).toBeNull();
  });

  it("labels every filter and keeps them in the URL", async () => {
    const user = userEvent.setup();
    const { router } = renderDirectory(directory());

    expect(screen.getByRole("searchbox", { name: "Search" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Agency" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Database" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Billing" })).toBeVisible();

    const param = (key: string) => new URLSearchParams(router.state.location.search).get(key);
    await user.selectOptions(screen.getByRole("combobox", { name: "Database" }), "db2");
    await waitFor(() => expect(param("database")).toBe("db2"));
    await user.click(screen.getByRole("checkbox", { name: "Needs only" }));
    await waitFor(() => expect(param("needs_only")).toBe("true"));
    await user.selectOptions(screen.getByRole("combobox", { name: "Billing" }), "billed");
    await waitFor(() => expect(param("billing")).toBe("billed"));
  });

  it("opens the selected customer beside the table with its quick actions", async () => {
    const user = userEvent.setup();
    const { router } = renderDirectory(directory());

    await user.click(screen.getByRole("link", { name: "Harbor Insurance" }));
    const panel = await screen.findByRole("complementary", { name: "Harbor Insurance" });
    expect(new URLSearchParams(router.state.location.search).get("customer")).toBe("7");
    expect(within(panel).getByText("Customer · Gulf Coast Agency")).toBeVisible();
    expect(within(panel).getByText("$250.00 held for #1041")).toBeVisible();
    expect(within(panel).getByText("412,880")).toBeVisible();
    expect(within(panel).getByText("DB1 · FL, TX")).toBeVisible();
    expect(within(panel).getByRole("link", { name: "Conflict with Lakeside Brokers" })).toBeVisible();
    expect(within(panel).getByText("$5.00 per 1,000")).toBeVisible();
    expect(within(panel).getByRole("button", { name: "Post adjustment" })).toBeVisible();
    expect(within(panel).getByRole("button", { name: "Change database" })).toBeVisible();
    expect(within(panel).getByRole("link", { name: "Open full page" })).toHaveAttribute("href", "/app/admin/customers/7");
    expect(screen.getByRole("table", { name: "Customers" }).querySelector("[aria-current]")).toHaveTextContent("Harbor Insurance");
  });

  it("changes a customer's database from the side panel with a reason", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ customerId: 7, leadDatabase: "db2", changed: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    renderDirectory(directory(), "/admin/customers?customer=7");

    const panel = screen.getByRole("complementary", { name: "Harbor Insurance" });
    await user.click(within(panel).getByRole("button", { name: "Change database" }));
    const dialog = screen.getByRole("dialog", { name: "Change database" });
    expect(within(dialog).getByRole("combobox", { name: "Database" })).toHaveValue("db2");
    const save = within(dialog).getByRole("button", { name: "Use DB2" });
    expect(save).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Moving to uploads");
    await user.click(save);
    const call = fetcher.mock.calls.find(([url]) => url === "/api/admin/customers/7/lead-database");
    expect(call?.[1].method).toBe("PUT");
    expect(JSON.parse(call?.[1].body)).toEqual({ lead_database: "db2", reason: "Moving to uploads" });
  });

  it("presents no matches as a nothing-found row rather than a failure", () => {
    renderDirectory(directory({ matched: 0, customers: [] }));

    expect(screen.getByText(/No customer matches these filters/)).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("provisions access by invitation and never offers a password", async () => {
    const user = userEvent.setup();
    renderDirectory(directory());

    expect(document.querySelector('input[type="password"]')).toBeNull();

    await user.click(screen.getByRole("button", { name: "Create Customer" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(
      /administrators never set or see a password/i,
    );
    expect(within(dialog).getByLabelText(/Email/)).toHaveAttribute(
      "type",
      "email",
    );
    expect(within(dialog).queryByLabelText("Licensed States")).toBeNull();
    expect(within(dialog).queryByLabelText("Reason")).toBeNull();
    expect(dialog).toHaveAccessibleDescription(
      /Customer sets Licensed States from Account after accepting/,
    );
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });
});

describe("Customers revalidation", () => {
  const args = (from: string, to: string) =>
    ({
      currentUrl: new URL(`http://x${from}`),
      nextUrl: new URL(`http://x${to}`),
      defaultShouldRevalidate: true,
    }) as Parameters<typeof adminCustomerDirectoryShouldRevalidate>[0];

  it("keeps the table when only the open row changes", () => {
    expect(adminCustomerDirectoryShouldRevalidate(args("/admin/customers", "/admin/customers?customer=7"))).toBe(false);
    expect(adminCustomerDirectoryShouldRevalidate(args("/admin/customers?q=h", "/admin/customers?q=h&customer=7"))).toBe(false);
  });

  it("reads again for a new filter or an explicit refresh", () => {
    expect(adminCustomerDirectoryShouldRevalidate(args("/admin/customers", "/admin/customers?database=db2"))).toBe(true);
    expect(adminCustomerDirectoryShouldRevalidate(args("/admin/customers?customer=7", "/admin/customers?customer=7"))).toBe(true);
  });
});
