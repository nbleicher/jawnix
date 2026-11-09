import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminFulfillmentConflictRoute } from "./AdminFulfillment";
import type { ConflictDetail, FulfillmentAction } from "./AdminFulfillment";

/* The Fulfillment workspace moved out in A4 (queues to Today and All
   batches, suppressed Leads and pool analytics to Inventory). The shared
   confirmation flow is covered in operator/actions.test.tsx and the moved
   sections in AdminInventory.test.tsx; the Inventory Conflict page stays. */

function action(overrides: Partial<FulfillmentAction> = {}): FulfillmentAction {
  return {
    name: "approve",
    label: "Approve",
    consequence: "Authorizes this Batch Request's first allocation attempt.",
    requiresReason: true,
    destructive: false,
    ...overrides,
  };
}

function conflict(overrides: Partial<ConflictDetail> = {}): ConflictDetail {
  return {
    id: "22222222-2222-2222-2222-222222222222",
    status: "pending",
    olderRequest: {
      id: "33333333-3333-3333-3333-333333333333",
      customerIdentity: "Older Customer",
      agency: "Northstar Agency",
      leadCount: 2,
      states: ["TX"],
      status: "waiting_inventory",
      createdAt: "2026-07-01T09:00:00Z",
      eligibleCount: 1,
      available: true,
    },
    newerRequest: {
      id: "44444444-4444-4444-4444-444444444444",
      customerIdentity: "Newer Customer",
      agency: "Southstar Agency",
      leadCount: 1,
      states: ["TX"],
      status: "waiting_inventory",
      createdAt: "2026-07-01T10:00:00Z",
      eligibleCount: 1,
      available: true,
    },
    overlappingLeadCount: 1,
    snapshotChecksum: "a".repeat(64),
    recurrenceRule:
      "A decision binds to this inventory snapshot alone. Denial or silence keeps the newer Batch Request waiting, and the conflict may recur only after a material change.",
    decisionBy: "",
    decisionReason: "",
    decidedAt: null,
    consumedAt: null,
    createdAt: "2026-07-01T10:05:00Z",
    actions: [
      action({
        name: "confirm",
        label: "Confirm once",
        consequence: "Authorizes one allocation attempt for this exact snapshot.",
      }),
      action({
        name: "deny",
        label: "Deny",
        consequence: "Leaves the newer Batch Request waiting.",
        destructive: true,
      }),
    ],
    ...overrides,
  };
}

function renderRoute(element: React.ReactElement, data: unknown) {
  const router = createMemoryRouter(
    [
      {
        id: "conflict",
        path: "/admin/fulfillment/conflicts/:conflictId",
        loader: () => data,
        element,
      },
    ],
    {
      initialEntries: ["/admin/fulfillment/conflicts/22222222-2222-2222-2222-222222222222"],
      hydrationData: { loaderData: { conflict: data } },
    },
  );
  render(<RouterProvider router={router} />);
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("the Inventory Conflict detail", () => {
  it("puts the decision first and links both batches", () => {
    renderRoute(
      <AdminFulfillmentConflictRoute />,
      conflict({
        olderRequest: { ...conflict().olderRequest, number: 1036 },
        newerRequest: { ...conflict().newerRequest, number: 1037 },
      }),
    );

    const regions = screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"));
    expect(regions.indexOf("The decision")).toBeLessThan(regions.indexOf("The two batches"));
    expect(screen.getByText("Batch #1036 cannot be filled yet, and Batch #1037 could use leads both need.")).toBeVisible();
    const batches = screen.getByRole("table", { name: "The two batches" });
    expect(within(batches).getByRole("link", { name: "Batch #1036" })).toHaveAttribute(
      "href",
      "/admin/batches/1036",
    );
    expect(within(batches).getByRole("link", { name: "Batch #1037" })).toHaveAttribute(
      "href",
      "/admin/batches/1037",
    );
    expect(within(batches).getAllByText("Waiting for inventory")).toHaveLength(2);
    expect(screen.getByText("Awaiting decision")).toBeVisible();
    expect(screen.getByText(/may recur only after a material change/)).toBeVisible();
  });

  it("offers confirm and deny while the conflict is pending", () => {
    renderRoute(<AdminFulfillmentConflictRoute />, conflict());

    expect(screen.getByRole("button", { name: "Confirm once" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Deny" })).toBeVisible();
  });

  it("offers no second decision once one has been made, in plain words", () => {
    renderRoute(
      <AdminFulfillmentConflictRoute />,
      conflict({
        status: "denied",
        actions: [],
        decidedAt: "2026-07-02T12:00:00Z",
        decisionBy: "admin-1",
        decisionReason: "Preserve the older request.",
      }),
    );

    expect(screen.queryByRole("button", { name: "Confirm once" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deny" })).toBeNull();
    expect(
      screen.getByText("This conflict has been decided and cannot be decided again."),
    ).toBeVisible();
    expect(screen.getAllByText("Denied").length).toBeGreaterThan(0);
    // Identities read as roles, not raw ids.
    expect(screen.getByText("Operator")).toBeVisible();
    expect(screen.queryByText("admin-1")).toBeNull();
  });
});
