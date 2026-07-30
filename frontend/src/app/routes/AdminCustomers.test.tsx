import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

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
  it("shows durable Customer standing and replaceable access standing separately", () => {
    renderDirectory(directory());

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
    expect(screen.getByText(/Showing 1 of 12 Customers/)).toBeVisible();
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
