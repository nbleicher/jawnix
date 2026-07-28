import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AdminFulfillmentConflictRoute,
  AdminFulfillmentRequestRoute,
  AdminFulfillmentRoute,
} from "./AdminFulfillment";
import type {
  ConflictDetail,
  FulfillmentAction,
  RequestDetail,
  RequestSummary,
  WorkspaceData,
} from "./AdminFulfillment";

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

function summary(overrides: Partial<RequestSummary> = {}): RequestSummary {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    customer: "Dana Reed",
    customerIdentity: "Northstar Insurance",
    agency: "Northstar Agency",
    email: "dana@example.com",
    leadCount: 250,
    states: ["TX"],
    stateMode: "all_saved",
    status: "pending",
    statusMessage: "Awaiting Telegram approval.",
    availableCount: null,
    createdAt: "2026-07-01T10:00:00Z",
    actions: [action()],
    ...overrides,
  };
}

function workspace(overrides: Partial<WorkspaceData> = {}): WorkspaceData {
  return {
    batchRequests: [summary()],
    inventoryConflicts: [],
    deliveryFailures: [],
    expiredArtifacts: [],
    ...overrides,
  };
}

function detail(overrides: Partial<RequestDetail> = {}): RequestDetail {
  return {
    ...summary(),
    approvedAt: null,
    deliveredAt: null,
    artifact: null,
    distribution: { committed: 0, expected: 250, complete: false },
    telegram: { decisionPending: false },
    history: [],
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
        id: "fulfillment",
        path: "/admin/fulfillment",
        loader: () => data,
        element,
      },
    ],
    {
      initialEntries: ["/admin/fulfillment"],
      hydrationData: { loaderData: { fulfillment: data } },
    },
  );
  render(<RouterProvider router={router} />);
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("the Fulfillment workspace renders only server-offered actions", () => {
  it("shows exactly the actions the contract supplied", () => {
    renderRoute(<AdminFulfillmentRoute />, workspace());

    expect(screen.getByRole("button", { name: "Approve" })).toBeVisible();
    for (const absent of ["Retry generation", "Retry delivery", "Reject", "Cancel"]) {
      expect(screen.queryByRole("button", { name: absent })).toBeNull();
    }
  });

  it("says so plainly when a record has no valid action left", () => {
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({ batchRequests: [summary({ status: "delivered", actions: [] })] }),
    );

    expect(screen.getByText("No action is valid in this state.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("keeps the two delivery recovery actions distinguishable", () => {
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({
        batchRequests: [
          summary({
            status: "failed",
            actions: [
              action({
                name: "retry_delivery",
                label: "Retry delivery",
                consequence:
                  "Emails the exact existing Batch Artifact again. Nothing is reallocated.",
              }),
            ],
          }),
        ],
      }),
    );

    expect(screen.getByRole("button", { name: "Retry delivery" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retry generation" })).toBeNull();
  });

  it("gives each empty section a safe next step", () => {
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({ batchRequests: [] }),
    );

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No Batch Requests need attention",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No Inventory Conflicts are waiting",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 2, name: "No delivery failures" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No Batch Artifacts have expired",
      }),
    ).toBeVisible();
  });
});

describe("confirming a consequential action", () => {
  it("states the consequence and posts the typed reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderRoute(<AdminFulfillmentRoute />, workspace());

    await user.click(screen.getByRole("button", { name: "Approve" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(
      /Authorizes this Batch Request's first allocation attempt/,
    );

    await user.type(
      screen.getByLabelText("Reason (required)"),
      "Inventory verified.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/requests/11111111-1111-1111-1111-111111111111/approve",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ reason: "Inventory verified." }),
        }),
      );
    });
  });

  it("refuses to submit without a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderRoute(<AdminFulfillmentRoute />, workspace());

    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Approve" }),
    );

    expect(await screen.findByText("A reason is required.")).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("surfaces a refused action without losing the dialog", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "Action approve is not valid while request is approved." }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderRoute(<AdminFulfillmentRoute />, workspace());

    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(screen.getByLabelText("Reason (required)"), "Stale view.");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    expect(
      await screen.findByText(
        "Action approve is not valid while request is approved.",
      ),
    ).toBeVisible();
  });

  it("routes regeneration to the artifact endpoint, not the transition one", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({
        batchRequests: [
          summary({
            status: "delivered",
            actions: [
              action({
                name: "regenerate",
                label: "Regenerate artifact",
                consequence: "Rebuilds the exact expired CSV.",
              }),
            ],
          }),
        ],
      }),
    );

    await user.click(screen.getByRole("button", { name: "Regenerate artifact" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(screen.getByLabelText("Reason (required)"), "Customer asked.");
    await user.click(
      within(dialog).getByRole("button", { name: "Regenerate artifact" }),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/requests/11111111-1111-1111-1111-111111111111/artifact/regenerate",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});

describe("the Batch Request detail", () => {
  it("shows the context an administrator decides on", () => {
    renderRoute(<AdminFulfillmentRequestRoute />, detail());

    expect(screen.getByText("Northstar Agency")).toBeVisible();
    // CONTEXT.md: Customer is the durable party, User Account the login.
    expect(screen.getByText("Northstar Insurance")).toBeVisible();
    expect(screen.getByText("Dana Reed")).toBeVisible();
    expect(screen.getByText("250 Leads")).toBeVisible();
    expect(screen.getByText("TX")).toBeVisible();
    expect(screen.getByText("0 of 250 committed")).toBeVisible();
  });

  it("names a live Telegram decision without offering it twice", () => {
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({ telegram: { decisionPending: true } }),
    );

    expect(
      screen.getByText(
        "This decision is also open in Telegram. Acting here settles it in both places.",
      ),
    ).toBeVisible();
    // One Approve, not one per channel.
    expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(1);
  });

  it("distinguishes an expired artifact from a missing one", () => {
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({
        artifact: {
          filename: "batch.csv",
          rowCount: 250,
          sha256: "b".repeat(64),
          deliveryStatus: "sent",
          deliveryAttempts: 1,
          lastError: "",
          expiresAt: "2026-06-01T00:00:00Z",
          sentAt: "2026-05-02T00:00:00Z",
          available: false,
        },
      }),
    );

    expect(
      screen.getByText(
        "Expired — it can be regenerated from committed Distribution Events",
      ),
    ).toBeVisible();
  });

  it("says plainly when nothing has been recorded yet", () => {
    renderRoute(<AdminFulfillmentRequestRoute />, detail());

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Nothing has been recorded yet",
      }),
    ).toBeVisible();
  });

  it("renders recorded history with actor, reason, and transition", () => {
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({
        history: [
          {
            action: "batch_request_approve",
            actor: "admin-1",
            reason: "Inventory verified.",
            recordedAt: "2026-07-02T12:00:00Z",
            before: { status: "pending" },
            after: { status: "approved" },
          },
        ],
      }),
    );

    expect(screen.getByText("Inventory verified.")).toBeVisible();
    expect(screen.getByText(/admin-1 · pending → approved/)).toBeVisible();
  });
});

describe("the Inventory Conflict detail", () => {
  it("explains both competing requests and the decision scope", () => {
    renderRoute(<AdminFulfillmentConflictRoute />, conflict());

    expect(
      screen.getByRole("heading", { level: 3, name: "Older Batch Request" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 3, name: "Newer Batch Request" }),
    ).toBeVisible();
    expect(
      screen.getByText("1 Leads eligible for both requests"),
    ).toBeVisible();
    expect(screen.getByText(/may recur only after a material change/)).toBeVisible();
  });

  it("offers confirm and deny while the conflict is pending", () => {
    renderRoute(<AdminFulfillmentConflictRoute />, conflict());

    expect(screen.getByRole("button", { name: "Confirm once" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Deny" })).toBeVisible();
  });

  it("offers no second decision once one has been made", () => {
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
      screen.getByText(
        "This Inventory Conflict has already been decided and cannot be decided again.",
      ),
    ).toBeVisible();
  });
});
