import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminActivityRoute, adminActivityLoader } from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";

const ACTIVITY: ActivityPage = {
  entries: [
    {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      action: "customer_inventory_scope_changed",
      actionLabel: "Lead database changed",
      refused: false,
      entityType: "customer",
      entityTypeLabel: "Customer",
      entityId: "42",
      entityLabel: "Keystone Medicare",
      entityHref: "/app/admin/customers/42",
      actor: "aaaaaaaa-0000-4000-8000-000000000001",
      actorName: "Operator",
      reason: "DB1 → DB2",
      details: {
        before: { lead_database: "db1" },
        after: { lead_database: "db2" },
      },
      recordedAt: "2026-11-08T16:44:00",
    },
    {
      id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      action: "customer_hard_delete_refused",
      actionLabel: "Refused: permanent deletion",
      refused: true,
      entityType: "customer",
      entityId: "43",
      entityLabel: "Lakeside Brokers",
      entityHref: "/app/admin/customers/43",
      actor: "aaaaaaaa-0000-4000-8000-000000000001",
      actorName: "Operator",
      reason: "Customer has history; deactivate first",
      details: {},
      recordedAt: "2026-11-07T18:22:00",
    },
  ],
  page: 2,
  pageSize: 25,
  total: 51,
  pages: 3,
  filters: {
    entityTypes: [
      { value: "batch_request", label: "Batch" },
      { value: "customer", label: "Customer" },
      { value: "scrape_run", label: "Scrape run" },
    ],
    who: [
      { value: "operator", label: "Operator" },
      { value: "system", label: "System" },
      { value: "user_account:river", label: "River Morgan" },
    ],
  },
};

function renderRoute(path: string, data: ActivityPage = ACTIVITY) {
  const router = createMemoryRouter(
    [{ id: "activity", path: "/admin/activity", element: <AdminActivityRoute />, loader: () => data }],
    { initialEntries: [path], hydrationData: { loaderData: { activity: data } } },
  );
  render(<RouterProvider router={router} />);
  return router;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("administrator Activity", () => {
  it("shows one line per entry: when, who, action, linked record, reason", () => {
    renderRoute("/admin/activity?entityType=customer&page=2");

    expect(screen.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Record type" })).toHaveValue("customer");
    const table = screen.getByRole("table", { name: "Activity" });
    const [first, second] = within(table).getAllByRole("row").slice(1);
    expect(within(first!).getByText("Operator")).toBeVisible();
    expect(within(first!).getByText("Lead database changed")).toBeVisible();
    expect(within(first!).getByRole("link", { name: "Keystone Medicare" })).toHaveAttribute(
      "href",
      "/admin/customers/42",
    );
    expect(within(first!).getByText("DB1 → DB2")).toBeVisible();
    expect(within(second!).getByText("Refused: permanent deletion")).toHaveClass("jx-text--danger");
    expect(screen.getByText("Showing 26–50 of 51")).toBeVisible();
    expect(screen.getByRole("link", { name: "Previous" })).toHaveAttribute(
      "href",
      expect.stringContaining("entityType=customer"),
    );
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      expect.stringContaining("page=3"),
    );
  });

  it("offers every recorded type and everyone who acted", () => {
    renderRoute("/admin/activity");

    const types = screen.getByRole("combobox", { name: "Record type" });
    expect(within(types).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "All types",
      "Batch",
      "Customer",
      "Scrape run",
    ]);
    const who = screen.getByRole("combobox", { name: "Who" });
    expect(within(who).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Anyone",
      "Operator",
      "System",
      "River Morgan",
    ]);
  });

  it("keeps before and after closed until the entry is opened", async () => {
    const user = userEvent.setup();
    renderRoute("/admin/activity");

    expect(screen.queryByText("lead_database: db1")).toBeNull();
    const toggle = screen.getByRole("button", { name: /Lead database changed: show what changed/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("lead_database: db1")).toBeVisible();
    expect(screen.getByText("lead_database: db2")).toBeVisible();
    expect(document.getElementById(toggle.getAttribute("aria-controls")!)).toContainElement(
      screen.getByText("lead_database: db1"),
    );
  });

  it("says when an entry recorded no change details", async () => {
    const user = userEvent.setup();
    renderRoute("/admin/activity");

    await user.click(screen.getByRole("button", { name: /Refused: permanent deletion: show/ }));
    expect(screen.getByText("No change details were recorded for this entry.")).toBeVisible();
  });

  it("puts the refused-only filter in the URL", async () => {
    const user = userEvent.setup();
    const router = renderRoute("/admin/activity");

    await user.click(screen.getByRole("checkbox", { name: "Refused only" }));

    expect(router.state.location.search).toContain("refused=true");
  });

  it("passes only shareable filters from the URL to the server", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(ACTIVITY), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await adminActivityLoader({
      request: new Request(
        "https://jawnix.test/app/admin/activity?q=durable&who=operator&refused=true&actor=admin%40example.com&action=customer_updated&entityType=customer&entityId=42&dateFrom=2026-07-20&dateTo=2026-07-23&page=2&ignored=secret",
      ),
      params: {},
    } as Parameters<typeof adminActivityLoader>[0]);

    const requested = String(fetch.mock.calls[0]?.[0]);
    for (const part of [
      "q=durable",
      "who=operator",
      "refused=true",
      "actor=admin%40example.com",
      "action=customer_updated",
      "entityType=customer",
      "entityId=42",
      "dateFrom=2026-07-20",
      "dateTo=2026-07-23",
      "page=2",
    ]) {
      expect(requested).toContain(part);
    }
    expect(requested).not.toContain("ignored");
  });
});
