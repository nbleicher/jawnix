import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalSearch } from "./GlobalSearch";

const RESULTS = {
  query: "north",
  groups: [
    {
      key: "customers",
      label: "Customers",
      results: [
        {
          kind: "customer",
          id: "12",
          title: "Northstar Insurance",
          detail: "northstar · river@northstar.example",
          href: "/app/admin/customers/12",
        },
      ],
    },
    {
      key: "batches",
      label: "Batches",
      results: [
        {
          kind: "batch",
          id: "b",
          title: "Batch #1042 · Northstar Insurance",
          detail: "25,000 leads · Awaiting approval · Nov 9",
          href: "/app/admin/batches/1042",
        },
      ],
    },
  ],
};

function renderSearch() {
  const router = createMemoryRouter(
    [
      { path: "/admin/today", element: <GlobalSearch /> },
      { path: "/admin/customers/:id", element: <p>Customer page</p> },
      { path: "/admin/batches/:id", element: <p>Batch page</p> },
      { path: "/admin/inventory/lookup", element: <p>Lead lookup</p> },
    ],
    { initialEntries: ["/admin/today"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

function respond(body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("GlobalSearch", () => {
  it("is a combobox inside a search landmark that lists grouped results", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond(RESULTS);
    renderSearch();

    expect(screen.getByRole("search")).toBeInTheDocument();
    const box = screen.getByRole("combobox", { name: "Search customers, batches, uploads, and phones" });
    expect(box).toHaveAttribute("aria-expanded", "false");
    await user.type(box, "north");

    const listbox = await screen.findByRole("listbox", { name: "Search results" });
    await waitFor(() => expect(within(listbox).getAllByRole("option")).toHaveLength(2));
    expect(fetchSpy).toHaveBeenCalledWith("/api/admin/search?q=north", expect.anything());
    expect(box).toHaveAttribute("aria-expanded", "true");
    expect(within(listbox).getByRole("group", { name: "Customers" })).toBeInTheDocument();
    expect(within(listbox).getByRole("group", { name: "Batches" })).toBeInTheDocument();
    // The first result is highlighted, so Enter has somewhere to go.
    const [first, second] = within(listbox).getAllByRole("option");
    expect(first).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", first!.id);

    await user.keyboard("{ArrowDown}");
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", second!.id);
    await user.keyboard("{ArrowDown}");
    expect(first).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowUp}");
    expect(second).toHaveAttribute("aria-selected", "true");
  });

  it("opens the highlighted result on Enter", async () => {
    const user = userEvent.setup();
    respond(RESULTS);
    const router = renderSearch();

    await user.type(screen.getByRole("combobox"), "north");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    await user.keyboard("{ArrowDown}{Enter}");

    expect(await screen.findByText("Batch page")).toBeVisible();
    expect(router.state.location.pathname).toBe("/admin/batches/1042");
  });

  it("opens a result by pointer", async () => {
    const user = userEvent.setup();
    respond(RESULTS);
    const router = renderSearch();

    await user.type(screen.getByRole("combobox"), "north");
    await user.click(await screen.findByRole("option", { name: /Northstar Insurance northstar/ }));
    expect(router.state.location.pathname).toBe("/admin/customers/12");
  });

  it("closes on Escape, then clears on a second Escape", async () => {
    const user = userEvent.setup();
    respond(RESULTS);
    renderSearch();

    const box = screen.getByRole("combobox");
    await user.type(box, "north");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    await user.keyboard("{Escape}");
    expect(box).toHaveAttribute("aria-expanded", "false");
    expect(box).toHaveValue("north");
    await user.keyboard("{Escape}");
    expect(box).toHaveValue("");
  });

  it("says when nothing matches and waits for two characters", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond({ query: "zz", groups: [] });
    renderSearch();

    const box = screen.getByRole("combobox");
    await user.type(box, "z");
    expect(fetchSpy).not.toHaveBeenCalled();
    await user.type(box, "z");
    // Shown in the list and announced through the status line.
    const message = "No customers, batches, uploads, or phones match “zz”.";
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(message));
    expect(screen.getAllByText(message)[0]).toBeVisible();
  });

  it("opens lead lookup for a phone number's Lead result", async () => {
    const user = userEvent.setup();
    respond({
      query: "615 555 0142",
      groups: [
        {
          key: "lead",
          label: "Lead",
          results: [
            {
              kind: "lead",
              id: "77",
              title: "(615) 555-0142 · Nashville Family Dental · TN",
              detail: "DB1 and DB2 · usable",
              href: "/app/admin/inventory/lookup?phone=6155550142",
            },
          ],
        },
        {
          key: "leadBatches",
          label: "Delivered in",
          results: [
            {
              kind: "batch",
              id: "b",
              title: "Batch #1028 · Keystone Medicare",
              detail: "Nov 6 · DB2",
              href: "/app/admin/batches/1028",
            },
          ],
        },
      ],
    });
    const router = renderSearch();

    await user.type(screen.getByRole("combobox"), "615 555 0142");
    const listbox = await screen.findByRole("listbox", { name: "Search results" });
    await waitFor(() => expect(within(listbox).getAllByRole("option")).toHaveLength(2));
    expect(within(listbox).getByRole("group", { name: "Lead" })).toBeInTheDocument();
    expect(within(listbox).getByRole("group", { name: "Delivered in" })).toBeInTheDocument();
    await user.keyboard("{Enter}");

    expect(await screen.findByText("Lead lookup")).toBeVisible();
    expect(router.state.location.pathname).toBe("/admin/inventory/lookup");
    expect(router.state.location.search).toBe("?phone=6155550142");
  });
});
