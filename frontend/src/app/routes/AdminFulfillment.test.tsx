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
  ControlAction,
  EligibilityHold,
  FulfillmentAction,
  LeadReportSummary,
  RequestDetail,
  RequestSummary,
  SuppressedLead,
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
    // Settled by default so existing detail tests do not start a poll loop.
    live: { settled: true, refreshSeconds: 10, jobs: [], log: [] },
    milestones: {
      milestones: [
        {
          key: "submitted",
          label: "Submitted",
          description: "We have your request.",
          state: "current",
          occurred_at: "2026-07-01T10:00:00Z",
        },
        {
          key: "under_review",
          label: "Under Review",
          description: "Checking quantity and states.",
          state: "upcoming",
          occurred_at: null,
        },
        {
          key: "preparing_batch",
          label: "Preparing Batch",
          description: "Selecting leads.",
          state: "upcoming",
          occurred_at: null,
        },
        {
          key: "delivered",
          label: "Delivered",
          description: "Available in the portal.",
          state: "upcoming",
          occurred_at: null,
        },
      ],
      current_key: "submitted",
      pause: null,
      outcome: null,
    },
    telegram: { decisionPending: false },
    history: [],
    activityTimeline: {
      entries: [],
      page: 1,
      pageSize: 25,
      total: 0,
      pages: 1,
    },
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

const LEAD_REPORT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

const HOLD_RELEASE =
  "This Eligibility Hold is released only when an administrator resolves this Lead Report. It cannot be released or bypassed by the Customer.";

const RESTORE_NOTICE =
  "Restoring returns this Lead to the eligible pool. It does not promise the Lead will be allocated to anyone.";

function controlAction(overrides: Partial<ControlAction> = {}): ControlAction {
  return {
    name: "restore",
    label: "Restore",
    consequence: "Returns this Lead to the eligible pool.",
    destructive: false,
    requiresReason: true,
    requiresOverride: false,
    ...overrides,
  };
}

function leadReport(
  overrides: Partial<LeadReportSummary> = {},
): LeadReportSummary {
  return {
    id: LEAD_REPORT_ID,
    reason: "wrong_number",
    reasonLabel: "Wrong number",
    details: "The number rings a dental office.",
    status: "open",
    createdAt: "2026-07-01T10:00:00Z",
    href: `/app/admin/fulfillment/reports/${LEAD_REPORT_ID}`,
    customer: { id: 7, name: "Northstar Insurance", href: "/app/admin/customers/7" },
    eligibilityHeld: true,
    ...overrides,
  };
}

function eligibilityHold(
  overrides: Partial<EligibilityHold> = {},
): EligibilityHold {
  return {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    leadId: 91,
    leadPhone: "+15125550123",
    reason: "wrong_number",
    reasonLabel: "Wrong number",
    createdAt: "2026-07-01T10:00:00Z",
    reportId: LEAD_REPORT_ID,
    reportStatus: "open",
    href: `/app/admin/fulfillment/reports/${LEAD_REPORT_ID}`,
    release: HOLD_RELEASE,
    ...overrides,
  };
}

function suppressedLead(overrides: Partial<SuppressedLead> = {}): SuppressedLead {
  return {
    id: 91,
    phone: "+15125550123",
    title: "Roofing contractor",
    state: "TX",
    reason: "Reported as a wrong number twice.",
    restoreNotice: RESTORE_NOTICE,
    actions: [controlAction()],
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
  const view = render(<RouterProvider router={router} />);
  view.container.querySelectorAll("details").forEach((item) => {
    item.open = true;
  });
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("the Fulfillment workspace renders only server-offered actions", () => {
  it("shows exactly the actions the contract supplied", () => {
    renderRoute(<AdminFulfillmentRoute />, workspace());

    expect(screen.getByRole("button", { name: "Approve" })).toBeVisible();
    for (const absent of ["Retry generation", "Retry notification", "Reject", "Cancel"]) {
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
                label: "Retry notification",
                consequence:
                  "Emails another portal notification for the exact existing Batch Artifact. Nothing is reallocated.",
              }),
            ],
          }),
        ],
      }),
    );

    expect(screen.getByRole("button", { name: "Retry notification" })).toBeVisible();
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
        activityTimeline: {
          entries: [{
            id: "activity-1",
            action: "batch_request_approve",
            actor: "admin-1",
            reason: "Inventory verified.",
            entityType: "batch_request",
            entityId: "11111111-1111-4111-8111-111111111111",
            entityHref:
              "/app/admin/fulfillment/requests/11111111-1111-4111-8111-111111111111",
            recordedAt: "2026-07-02T12:00:00Z",
            details: {
              before: { status: "pending" },
              after: { status: "approved" },
            },
          }],
          page: 1,
          pageSize: 25,
          total: 1,
          pages: 1,
        },
      }),
    );

    expect(screen.getByText("Inventory verified.")).toBeVisible();
    expect(screen.getByText("admin-1")).toBeVisible();
    expect(screen.getByText("pending")).toBeVisible();
    expect(screen.getByText("approved")).toBeVisible();
  });

  it("renders the Progress milestone graph", () => {
    renderRoute(<AdminFulfillmentRequestRoute />, detail());

    expect(
      screen.getByRole("heading", { level: 2, name: "Progress" }),
    ).toBeVisible();
    const graph = screen.getByLabelText("Batch Request progress");
    expect(graph).toBeVisible();
    expect(within(graph).getByText("Submitted")).toBeVisible();
    expect(within(graph).getByText("Under Review")).toBeVisible();
  });

  it("shows a running job in the live activity strip", () => {
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({
        live: {
          settled: false,
          refreshSeconds: 10,
          jobs: [
            {
              kind: "allocate_request",
              label: "Allocating inventory",
              status: "running",
              attempts: 1,
              runAfter: null,
              lastError: null,
            },
          ],
          log: [
            {
              id: "job:1",
              at: "2026-07-01T10:05:00Z",
              kind: "job",
              label: "Allocating inventory",
              detail: "running",
              tone: "info",
            },
            {
              id: "audit:1",
              at: "2026-07-01T10:01:00Z",
              kind: "decision",
              label: "Batch request approve",
              detail: "Inventory verified.",
              tone: "neutral",
            },
            {
              id: "status:1",
              at: "2026-07-01T10:00:00Z",
              kind: "status",
              label: "Approved; allocation is queued.",
              detail: "Approved",
              tone: "info",
            },
          ],
        },
      }),
    );

    const log = screen.getByLabelText("Live activity log, most recent first");
    expect(log).toBeVisible();
    expect(within(log).getByText("Allocating inventory")).toBeVisible();
    expect(within(log).getByText("Batch request approve")).toBeVisible();
    expect(within(log).getByText("Inventory verified.")).toBeVisible();
    expect(screen.getByLabelText("Active jobs")).toBeVisible();
    expect(
      within(screen.getByLabelText("Active jobs")).getByText("running"),
    ).toBeVisible();
  });

  it("surfaces a failed job error in the live strip", () => {
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({
        live: {
          settled: false,
          refreshSeconds: 10,
          jobs: [
            {
              kind: "notify_request",
              label: "Sending the Telegram approval message",
              status: "failed",
              attempts: 3,
              runAfter: null,
              lastError: "Telegram sendMessage failed: 503",
            },
          ],
          log: [
            {
              id: "job:9",
              at: "2026-07-01T10:05:00Z",
              kind: "job",
              label: "Sending the Telegram approval message",
              detail: "failed · attempt 3 · Telegram sendMessage failed: 503",
              tone: "danger",
            },
          ],
        },
      }),
    );

    const log = screen.getByLabelText("Live activity log, most recent first");
    expect(
      within(log).getByText("Sending the Telegram approval message"),
    ).toBeVisible();
    expect(
      within(log).getByText(/Telegram sendMessage failed: 503/),
    ).toBeVisible();
  });

  it("keeps the activity log to five visible rows with overflow scroll", () => {
    const log = Array.from({ length: 8 }, (_, index) => ({
      id: `job:${index}`,
      at: `2026-07-01T10:0${index}:00Z`,
      kind: "job" as const,
      label: `Step ${index}`,
      detail: "complete",
      tone: "success" as const,
    }));
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({
        live: { settled: true, refreshSeconds: 10, jobs: [], log },
      }),
    );

    const panel = screen.getByLabelText("Live activity log, most recent first");
    expect(panel).toHaveStyle({ maxHeight: "calc(5 * 2.5rem)" });
    expect(within(panel).getByText("Step 0")).toBeVisible();
    expect(within(panel).getByText("Step 7")).toBeVisible();
  });

  it("renders Distribution composition cells when byState is present", () => {
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({
        distribution: {
          committed: 3,
          expected: 3,
          complete: true,
          byState: [
            { state: "TX", niche: "Roofing", count: 2 },
            { state: "TX", niche: "Unmapped", count: 1 },
          ],
        },
      }),
    );

    const section = screen.getByRole("region", { name: "Distribution" });
    expect(within(section).getByText("Roofing")).toBeVisible();
    expect(within(section).getByText("Unmapped")).toBeVisible();
    expect(within(section).getByText("2 committed")).toBeVisible();
  });

  it("omits the Distribution section when nothing is committed", () => {
    renderRoute(<AdminFulfillmentRequestRoute />, detail());

    expect(
      screen.queryByRole("region", { name: "Distribution" }),
    ).toBeNull();
  });

  it("tolerates a payload without Progress fields", () => {
    const payload = detail();
    delete payload.milestones;
    delete payload.live;
    renderRoute(<AdminFulfillmentRequestRoute />, payload);

    expect(
      screen.queryByRole("heading", { level: 2, name: "Progress" }),
    ).toBeNull();
    expect(screen.getByText("Northstar Insurance")).toBeVisible();
  });

  it("starts a refresh interval only while unsettled", async () => {
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    const clearIntervalSpy = vi.spyOn(window, "clearInterval");

    const unsettled = detail({
      live: { settled: false, refreshSeconds: 10, jobs: [] },
    });
    const router = createMemoryRouter(
      [
        {
          id: "request",
          path: "/admin/fulfillment/requests/:requestId",
          loader: () => unsettled,
          element: <AdminFulfillmentRequestRoute />,
        },
      ],
      {
        initialEntries: [
          "/admin/fulfillment/requests/11111111-1111-1111-1111-111111111111",
        ],
        hydrationData: { loaderData: { request: unsettled } },
      },
    );
    const view = render(<RouterProvider router={router} />);

    await waitFor(() => {
      expect(
        setIntervalSpy.mock.calls.some((call) => call[1] === 10_000),
      ).toBe(true);
    });
    view.unmount();
    expect(clearIntervalSpy).toHaveBeenCalled();

    setIntervalSpy.mockClear();
    renderRoute(
      <AdminFulfillmentRequestRoute />,
      detail({ live: { settled: true, refreshSeconds: 10, jobs: [] } }),
    );
    expect(
      setIntervalSpy.mock.calls.some((call) => call[1] === 10_000),
    ).toBe(false);

    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });
});

describe("the Lead Report control sections", () => {
  it("survives a response cached before the sections existed", () => {
    // `workspace()` carries none of the #58 arrays, exactly like an older
    // cached payload. The page must still render every other section.
    renderRoute(<AdminFulfillmentRoute />, workspace());

    expect(
      screen.getByRole("heading", { level: 2, name: "No Lead Reports are open" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No Eligibility Holds are active",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 2, name: "No Leads are suppressed" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Approve" })).toBeVisible();
  });

  it("links a Lead Report to its record and names the Customer who filed it", () => {
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({ leadReports: [leadReport()] }),
    );

    const section = screen.getByRole("region", { name: "Lead Reports" });
    expect(
      within(section).getByRole("link", { name: "Wrong number" }),
    ).toHaveAttribute("href", `/admin/fulfillment/reports/${LEAD_REPORT_ID}`);
    expect(
      within(section).getByText(/Reported by Northstar Insurance/),
    ).toBeVisible();
    expect(within(section).getByText(/Eligibility Hold active/)).toBeVisible();
  });

  it("distinguishes a report with no Eligibility Hold", () => {
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({
        leadReports: [
          leadReport({ eligibilityHeld: false }),
        ],
      }),
    );

    const section = screen.getByRole("region", { name: "Lead Reports" });
    expect(
      within(section).getByText("No Eligibility Hold on this Lead."),
    ).toBeVisible();
    expect(within(section).queryByText(/Eligibility Hold active/)).toBeNull();
  });

  it("states the hold release rule in the domain's own words", () => {
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({ eligibilityHolds: [eligibilityHold()] }),
    );

    const section = screen.getByRole("region", { name: "Eligibility Holds" });
    expect(within(section).getByText(HOLD_RELEASE)).toBeVisible();
    expect(within(section).getByText("+15125550123")).toBeVisible();
  });

  it("shows the restore notice beside every Restore control", () => {
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({ suppressedLeads: [suppressedLead()] }),
    );

    const section = screen.getByRole("region", { name: "Suppressed Leads" });
    expect(within(section).getByRole("button", { name: "Restore" })).toBeVisible();
    expect(within(section).getByText(RESTORE_NOTICE)).toBeVisible();
  });

  it("restores a Lead through its suppression, with a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderRoute(
      <AdminFulfillmentRoute />,
      workspace({ suppressedLeads: [suppressedLead()] }),
    );

    await user.click(screen.getByRole("button", { name: "Restore" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(
      /Returns this Lead to the eligible pool/,
    );
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Report was mistaken.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Restore" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/leads/91/suppression",
        expect.objectContaining({
          method: "DELETE",
          body: JSON.stringify({ reason: "Report was mistaken." }),
        }),
      );
    });
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
