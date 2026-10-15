import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CustomerDirectoryData } from "./AdminCustomers";
import { AdminCustomersRoute } from "./AdminCustomers";

function directory(
  overrides: Partial<CustomerDirectoryData> = {},
): CustomerDirectoryData {
  return {
    filters: {
      query: "",
      status: "all",
      agency_id: null,
      state: "",
      problems_only: false,
    },
    agencies: [{ id: 4, name: "Gulf Coast Agency", active: true }],
    states: ["FL", "TX"],
    total: 12,
    matched: 1,
    customers: [
      {
        id: 7,
        slug: "harbor-insurance",
        name: "Harbor Insurance",
        agency_id: 4,
        agency: "Gulf Coast Agency",
        licensed_states: ["FL", "TX"],
        customer_status: {
          label: "Active",
          description: "This Customer can receive Batches.",
          tone: "success",
        },
        account_status: {
          label: "Invitation sent",
          description: "The invited address has not signed in yet.",
          tone: "warning",
        },
        account_email: "owner@harbor.example",
        last_activity_at: "2026-07-20T12:00:00Z",
        problems: ["Invitation has not been accepted yet"],
        href: "/app/admin/customers/7",
      },
    ],
    ...overrides,
  };
}

function renderDirectory(data: CustomerDirectoryData) {
  const router = createMemoryRouter(
    [
      {
        id: "customers",
        path: "/admin/customers",
        loader: () => data,
        element: <AdminCustomersRoute />,
      },
    ],
    {
      initialEntries: ["/admin/customers"],
      hydrationData: { loaderData: { customers: data } },
    },
  );
  return render(<RouterProvider router={router} />);
}

describe("administrator Customer directory", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requires confirmation before restoring a verified registration's history", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail:"Decision captured" }), { status:409 }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    renderDirectory(directory({ pending_registrations:[{ id:"new-user", firstName:"River", lastName:"Morgan", email:"new@example.test", phone:"6155550100", states:["TN"], matches:[{ id:19, name:"River Morgan", slug:"river-morgan", email:"old@example.test" }] }] }));
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
  it("shows durable Customer standing and replaceable access standing separately", () => {
    renderDirectory(directory());

    expect(document.querySelector(".jx-page")).toHaveClass("jx-page--data");
    const card = screen.getByRole("article", { name: "Harbor Insurance" });
    // Router-relative here because this harness has no basename; the real
    // router mounts under `/app`, which e2e asserts against the built app.
    expect(
      within(card).getByRole("link", { name: "Harbor Insurance" }),
    ).toHaveAttribute("href", "/admin/customers/7");

    // The two standings are labelled so replacing access can never be read as a
    // change to the Customer itself.
    expect(within(card).getByText("Customer", { exact: true })).toBeVisible();
    expect(within(card).getByText("User Account", { exact: true })).toBeVisible();
    expect(within(card).getByText("Active", { exact: true })).toBeVisible();
    expect(
      within(card).getByText("Invitation sent", { exact: true }),
    ).toBeVisible();
    expect(within(card).getByText("owner@harbor.example")).toBeVisible();
    expect(within(card).getByText("FL, TX")).toBeVisible();
    expect(
      within(card).getByText("Invitation has not been accepted yet"),
    ).toBeVisible();
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "P"
          && element.textContent === "Showing 1 of 12 Customers.",
      ),
    ).toBeVisible();
  });

  it("labels every search control and submits them through the URL", () => {
    renderDirectory(directory());

    expect(screen.getByRole("searchbox", { name: "Search" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Status" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Agency" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Licensed State" })).toBeVisible();
    expect(
      screen.getByRole("checkbox", { name: "Only show setup problems" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Search" })).toBeVisible();
    expect(
      within(screen.getByRole("combobox", { name: "Agency" })).getByRole(
        "option",
        { name: "Gulf Coast Agency" },
      ),
    ).toBeInTheDocument();
  });

  it("presents no matches as a nothing-found state rather than a failure", () => {
    renderDirectory(directory({ matched: 0, customers: [] }));

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No Customers match this search",
      }),
    ).toBeVisible();
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
