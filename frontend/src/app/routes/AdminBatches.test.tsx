import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BatchDetail } from "../operator/batch";
import type { BatchRow } from "../operator/format";
import { AdminBatchesRoute, AdminBatchRoute } from "./AdminBatches";
import type { BatchListData } from "./AdminBatches";

const ID = "10421042-1042-4042-8042-104210421042";

const APPROVE = {
  name: "approve",
  label: "Approve",
  consequence: "Authorizes this Batch Request's first allocation attempt.",
  requiresReason: true,
  destructive: false,
};
const REJECT = { ...APPROVE, name: "reject", label: "Reject", consequence: "Refuses it.", destructive: true };

function batch(overrides: Partial<BatchDetail> = {}): BatchDetail {
  return {
    id: ID,
    number: 1042,
    href: "/app/admin/batches/1042",
    stage: "awaiting_approval",
    needsDecision: true,
    database: "db1",
    customer: "River Morgan",
    customerIdentity: "Northstar Insurance",
    agency: "Lakeside Agency",
    email: "river@northstar.example",
    leadCount: 25_000,
    rowsPerFile: 10_000,
    states: ["FL"],
    stateMode: "all_saved",
    status: "pending",
    statusMessage: "Awaiting approval.",
    availableCount: null,
    createdAt: new Date(Date.now() - 2 * 3_600_000).toISOString(),
    actions: [APPROVE, REJECT],
    approvedAt: null,
    deliveredAt: null,
    artifact: null,
    distribution: { committed: 0, expected: 25_000, complete: false },
    live: { settled: true, refreshSeconds: 10, jobs: [], log: [] },
    telegram: { decisionPending: false },
    history: [],
    customerRecord: { id: 12, name: "Northstar Insurance", href: "/app/admin/customers/12" },
    agencyRecord: { id: 3, name: "Lakeside Agency", href: "/app/admin/agencies/3" },
    fill: {
      needed: 25_000,
      kind: "fillable",
      available: 1_188_205,
      short: 0,
      asOf: null,
      source: "customer_snapshot",
      olderOpen: { count: 1, first: { number: 1039, customer: "Ridgeline Benefits", stage: "waiting" } },
    },
    cost: {
      billed: true,
      rateCentsPerThousand: 500,
      amountCents: 12_500,
      hold: "active",
      availableBalanceCents: 41_250,
    },
    progress: [
      { key: "submitted", label: "Submitted", done: true, at: "2026-11-09T14:41:00Z" },
      { key: "approved", label: "Approved", done: false, at: null },
      { key: "allocated", label: "Allocated", done: false, at: null },
      { key: "generated", label: "File generated", done: false, at: null },
      { key: "delivered", label: "Delivered", done: false, at: null },
    ],
    recentBatches: [
      {
        id: "x",
        number: 1038,
        href: "/app/admin/batches/1038",
        leadCount: 50_000,
        stage: "delivered",
        createdAt: "2026-11-06T10:00:00Z",
        deliveredAt: "2026-11-07T10:00:00Z",
      },
    ],
    openLeadReports: 0,
    ...overrides,
  };
}

function renderBatch(data: BatchDetail, path = "/admin/batches/1042") {
  const router = createMemoryRouter(
    [
      { id: "batch", path: "/admin/batches/:batchId", loader: () => data, element: <AdminBatchRoute /> },
    ],
    { initialEntries: [path], hydrationData: { loaderData: { batch: data } } },
  );
  const view = render(<RouterProvider router={router} />);
  view.container.querySelectorAll("details").forEach((item) => {
    item.open = true;
  });
  return { router, view };
}

function ok(body: unknown = { ok: true }) {
  // A fresh Response per call: a body can only be read once.
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("the Batch page", () => {
  it("names the batch by number and puts its actions in the header", () => {
    renderBatch(batch());
    expect(screen.getByRole("heading", { level: 1, name: "Batch #1042 · Northstar Insurance" })).toBeVisible();
    expect(screen.getByText(/25,000 leads · FL · DB1 · submitted 2 hours ago/)).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Reject" })).toBeVisible();
    expect(screen.getByText("Awaiting approval")).toBeVisible();
  });

  it("answers whether it can be filled and what it costs", () => {
    renderBatch(batch());
    const fill = screen.getByRole("region", { name: "Can it be filled?" });
    expect(within(fill).getByText("1,188,205")).toBeVisible();
    expect(within(fill).getByText("Fillable in full")).toBeVisible();
    expect(within(fill).getByText("1 batch (Ridgeline Benefits, waiting)")).toBeVisible();
    const cost = screen.getByRole("region", { name: "Cost" });
    expect(within(cost).getByText("$5.00 per 1,000")).toBeVisible();
    expect(within(cost).getByText("$125.00, held at submission")).toBeVisible();
    expect(within(cost).getByText("$412.50")).toBeVisible();
  });

  it("links the customer, agency, and the customer's recent batches", () => {
    renderBatch(batch());
    const panel = screen.getByRole("region", { name: "Batch" });
    expect(within(panel).getByRole("link", { name: "Northstar Insurance" })).toHaveAttribute(
      "href",
      "/admin/customers/12",
    );
    expect(within(panel).getByText("10,000")).toBeVisible();
    const recent = screen.getByRole("region", { name: "Customer's recent batches" });
    expect(within(recent).getByText("#1038 · 50,000 leads")).toBeVisible();
    expect(within(recent).getByText("None open")).toBeVisible();
  });

  it("posts an action's reason to the Batch Request endpoint", async () => {
    const user = userEvent.setup();
    const fetchSpy = ok(batch());
    renderBatch(batch());
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Inventory verified.");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/requests/${ID}/approve`,
        expect.objectContaining({ method: "POST", body: JSON.stringify({ reason: "Inventory verified." }) }),
      ),
    );
  });

  it("routes regeneration to the artifact endpoint", async () => {
    const user = userEvent.setup();
    const fetchSpy = ok(batch());
    renderBatch(
      batch({
        status: "delivered",
        stage: "file_expired",
        actions: [{ ...APPROVE, name: "regenerate", label: "Regenerate artifact", consequence: "Rebuilds it." }],
      }),
    );
    await user.click(screen.getByRole("button", { name: "Regenerate artifact" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Customer asked.");
    await user.click(within(dialog).getByRole("button", { name: "Regenerate artifact" }));
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/requests/${ID}/artifact/regenerate`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("offers re-notify while open, names a live Telegram decision, and drops it once delivered", () => {
    const { view } = renderBatch(batch({ telegram: { decisionPending: true } }));
    const progress = screen.getByRole("region", { name: "Progress" });
    expect(within(progress).getByRole("button", { name: "Re-notify in Telegram" })).toBeVisible();
    expect(
      within(progress).getByText("Also open in Telegram. Acting here settles it in both places."),
    ).toBeVisible();
    view.unmount();
    renderBatch(batch({ status: "delivered", stage: "delivered", actions: [] }));
    expect(screen.queryByRole("button", { name: "Re-notify in Telegram" })).toBeNull();
  });

  it("shows the file, its failure, and its leads by state once allocated", () => {
    renderBatch(
      batch({
        status: "failed",
        stage: "delivery_failed",
        artifact: {
          filename: "northstar_1042.csv",
          rowCount: 25_000,
          sha256: "b".repeat(64),
          deliveryStatus: "failed",
          deliveryAttempts: 2,
          lastError: "Provider returned HTTP 503",
          expiresAt: "2026-12-01T00:00:00Z",
          sentAt: null,
          available: false,
        },
        fill: { needed: 25_000, kind: "committed", committed: 25_000, olderOpen: null },
        distribution: {
          committed: 25_000,
          expected: 25_000,
          complete: true,
          byState: [
            { state: "FL", niche: "Dental", count: 20_000 },
            { state: "FL", niche: "Unmapped", count: 5_000 },
          ],
        },
      }),
    );
    const file = screen.getByRole("region", { name: "File" });
    expect(within(file).getByText("northstar_1042.csv")).toBeVisible();
    expect(within(file).getByText("Expired")).toBeVisible();
    expect(within(file).getByText("Provider returned HTTP 503")).toBeVisible();
    const byState = screen.getByRole("table", { name: "Leads by state" });
    expect(within(byState).getByText("Dental")).toBeVisible();
    expect(within(byState).getByText("25,000")).toBeVisible();
    expect(screen.getByText("Delivery failed")).toBeVisible();
  });

  it("reads activity as who did what, in words", () => {
    renderBatch(
      batch({
        history: [
          {
            action: "batch_request_approve",
            actor: "telegram:12345",
            reason: "Telegram Batch Request decision",
            recordedAt: "2026-11-09T15:00:00Z",
            before: { status: "pending" },
            after: { status: "approved" },
          },
        ],
      }),
    );
    const activity = screen.getByRole("table", { name: "Activity" });
    expect(within(activity).getByText("Operator in Telegram")).toBeVisible();
    expect(within(activity).getByText("Approved · Telegram Batch Request decision")).toBeVisible();
    expect(within(activity).getByText("River Morgan")).toBeVisible();
    expect(within(activity).getByText("Requested 25,000 leads · FL")).toBeVisible();
  });

  it("keeps the worker's live trail for a batch that is moving", () => {
    renderBatch(
      batch({
        live: {
          settled: false,
          refreshSeconds: 10,
          blocker: {
            kind: "emit_lead_assigned",
            label: "Emitting lead.assigned metrics",
            status: "running",
            jobId: 41,
            requestId: null,
            detail: "The worker is busy with Emitting lead.assigned metrics. Jobs for this request stay queued until that finishes.",
          },
          jobs: [
            {
              kind: "notify_request",
              label: "Sending the Telegram approval message",
              status: "queued",
              attempts: 0,
              runAfter: null,
              lastError: null,
            },
          ],
          log: [
            {
              id: "job:1",
              at: "2026-11-09T15:00:00Z",
              kind: "job",
              label: "Sending the Telegram approval message",
              detail: "queued",
              tone: "info",
            },
          ],
        },
      }),
    );
    const strip = screen.getByLabelText("Active jobs");
    expect(within(strip).getByText(/worker is busy/)).toBeVisible();
    expect(within(strip).getByText("queued")).toBeVisible();
    const log = screen.getByLabelText("Live activity log, most recent first");
    expect(within(log).getByText("Sending the Telegram approval message")).toBeInTheDocument();
  });

  it("settles a UUID address on the batch number", async () => {
    const { router } = renderBatch(batch(), `/admin/batches/${ID}`);
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/batches/1042"));
  });
});

function row(overrides: Partial<BatchRow>): BatchRow {
  return {
    id: "a",
    number: 1039,
    href: "/app/admin/batches/1039",
    customer: { id: 14, name: "Ridgeline Benefits", href: "/app/admin/customers/14" },
    leadCount: 100_000,
    states: ["AZ"],
    database: "db1",
    status: "waiting_inventory",
    stage: "waiting",
    statusMessage: "",
    availability: { kind: "short", available: 68_410, short: 31_590 },
    needsDecision: false,
    createdAt: new Date(Date.now() - 72 * 3_600_000).toISOString(),
    deliveredAt: null,
    closedAt: null,
    ...overrides,
  };
}

const LIST: BatchListData = {
  items: [
    row({}),
    row({
      id: ID,
      number: 1042,
      href: "/app/admin/batches/1042",
      customer: { id: 12, name: "Northstar Insurance", href: "/app/admin/customers/12" },
      leadCount: 25_000,
      states: ["FL"],
      status: "pending",
      stage: "awaiting_approval",
      availability: { kind: "fillable", available: 1_188_205, short: 0 },
      needsDecision: true,
    }),
  ],
  page: 1,
  pageSize: 25,
  total: 2,
  pages: 1,
  counts: { delivered: 67, expired: 3 },
};

function renderList(path = "/admin/batches") {
  const router = createMemoryRouter(
    [{ id: "batches", path: "/admin/batches", loader: () => LIST, element: <AdminBatchesRoute /> }],
    { initialEntries: [path], hydrationData: { loaderData: { batches: LIST } } },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("All batches", () => {
  it("lists batches with the columns the operator scans", () => {
    renderList();
    const table = screen.getByRole("table", { name: "Batches" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Batch", "Customer", "Leads", "States", "DB", "Available", "Status", "Age",
    ]);
    expect(within(table).getByText("68,410 · short 31,590")).toBeVisible();
    expect(within(table).getByText("Awaiting approval")).toBeVisible();
    expect(screen.getByText("Showing 1–2 of 2")).toBeVisible();
    expect(screen.getByRole("link", { name: "Expired files (3)" })).toHaveAttribute(
      "href",
      "/app/admin/batches?status=expired",
    );
  });

  it("keeps filters in the address", async () => {
    const user = userEvent.setup();
    const router = renderList();
    await user.click(screen.getByRole("checkbox", { name: "Needs a decision" }));
    expect(router.state.location.search).toBe("?needsDecision=true");
    await user.selectOptions(screen.getByLabelText("Status"), "expired");
    expect(router.state.location.search).toContain("status=expired");
    await user.selectOptions(screen.getByLabelText("Database"), "db2");
    expect(router.state.location.search).toContain("database=db2");
  });

  it("opens the selected batch beside the list", async () => {
    const user = userEvent.setup();
    ok(batch());
    const router = renderList();
    const table = screen.getByRole("table", { name: "Batches" });
    await user.click(within(table).getByRole("link", { name: "#1042" }));
    expect(router.state.location.search).toBe("?batch=1042");
    const panel = await screen.findByRole("complementary", { name: "#1042 · Northstar Insurance" });
    expect(await within(panel).findByText("$125.00 at $5.00 per 1,000")).toBeVisible();
    expect(within(panel).getByText("$412.50")).toBeVisible();
    expect(within(panel).getByRole("button", { name: "Approve" })).toBeVisible();
    expect(within(table).getAllByRole("row")[2]).toHaveAttribute("aria-current", "true");
  });
});
