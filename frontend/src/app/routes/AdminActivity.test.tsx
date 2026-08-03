import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AdminActivityRoute,
  adminActivityLoader,
} from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";

const ACTIVITY: ActivityPage = {
  entries: [
    {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      action: "customer_updated",
      entityType: "customer",
      entityId: "42",
      entityHref: "/app/admin/customers/42",
      actor: "admin@example.com",
      reason: "Correct the durable Customer name.",
      details: {
        before: { name: "North Shore" },
        after: { name: "North Shore Insurance" },
      },
      recordedAt: "2026-07-23T12:00:00Z",
    },
  ],
  page: 2,
  pageSize: 25,
  total: 51,
  pages: 3,
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("administrator Activity", () => {
  it("keeps log detail collapsed until requested, then shows attribution and safe changes", async () => {
    const user = userEvent.setup();
    const router = createMemoryRouter(
      [
        {
          id: "activity",
          path: "/admin/activity",
          element: <AdminActivityRoute />,
          loader: () => ACTIVITY,
        },
      ],
      {
        initialEntries: [
          "/admin/activity?actor=admin%40example.com&entityType=customer&page=2",
        ],
        hydrationData: { loaderData: { activity: ACTIVITY } },
      },
    );
    render(<RouterProvider router={router} />);

    expect(screen.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Actor" })).toHaveValue(
      "admin@example.com",
    );
    expect(screen.getByRole("combobox", { name: "Entity type" })).toHaveValue(
      "customer",
    );
    const entry = screen.getByRole("article");
    expect(within(entry).getByText("Customer updated")).toBeVisible();
    expect(within(entry).getByText("North Shore")).not.toBeVisible();
    await user.click(within(entry).getByText("Customer updated"));
    expect(within(entry).getByText("admin@example.com")).toBeVisible();
    expect(
      within(entry).getByRole("link", { name: /Customer 42/ }),
    ).toHaveAttribute("href", "/app/admin/customers/42");
    expect(within(entry).getByText("North Shore")).toBeVisible();
    expect(within(entry).getByText("North Shore Insurance")).toBeVisible();
    expect(screen.getByRole("link", { name: "Previous page" })).toHaveAttribute(
      "href",
      expect.stringContaining("page=1"),
    );
    expect(screen.getByRole("link", { name: "Next page" })).toHaveAttribute(
      "href",
      expect.stringContaining("page=3"),
    );
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
        "https://jawnix.test/app/admin/activity?q=durable&actor=admin%40example.com&action=customer_updated&entityType=customer&entityId=42&dateFrom=2026-07-20&dateTo=2026-07-23&page=2&ignored=secret",
      ),
      params: {},
    } as Parameters<typeof adminActivityLoader>[0]);

    const requested = String(fetch.mock.calls[0]?.[0]);
    expect(requested).toContain("q=durable");
    expect(requested).toContain("actor=admin%40example.com");
    expect(requested).toContain("action=customer_updated");
    expect(requested).toContain("entityType=customer");
    expect(requested).toContain("entityId=42");
    expect(requested).toContain("dateFrom=2026-07-20");
    expect(requested).toContain("dateTo=2026-07-23");
    expect(requested).toContain("page=2");
    expect(requested).not.toContain("ignored");
  });
});
