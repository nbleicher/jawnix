import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AdminInventoryRoute,
  AdminPoolAnalyticsRoute,
  AdminSuppressedLeadsRoute,
  stateRows,
} from "./AdminInventory";
import type { InventoryData, PoolBreakdownData, SuppressedLeadsData } from "./AdminInventory";
import { AdminInventoryExclusionsRoute } from "./AdminInventoryExclusions";
import type { ExclusionListsData, ExclusionRow } from "./AdminInventoryExclusions";
import { AdminInventoryUploadsRoute } from "./AdminInventoryUploads";
import type { UploadWorkspace } from "./AdminInventoryUploads";

const STATES = ["TX", "FL", "CA", "PA", "OH", "NY", "GA", "NC", "MI", "NJ", "VA", "WA"];

function snapshot(database: "db1" | "db2", base: number) {
  const usableByState = Object.fromEntries(STATES.map((state, index) => [state, base - index * 1_000]));
  const usable = Object.values(usableByState).reduce((sum, value) => sum + value, 0);
  return {
    database,
    label: database.toUpperCase(),
    allRows: usable + 60_924,
    suppressed: 42_612,
    held: 18,
    excluded: 18_294,
    usable,
    usableByState,
    computedAt: new Date().toISOString(),
  };
}

const INVENTORY: InventoryData = {
  db1: snapshot("db1", 900_000),
  db2: { ...snapshot("db2", 90_000), suppressed: 11_000, held: 0, excluded: 331 },
  refresh: { status: "idle", jobId: null },
  addedLast7Days: 96_140,
  lastUpload: { id: "u1", filename: "tn-agents-nov.csv", uploadedBy: "Alex", at: "2026-11-06T15:00:00Z" },
  sections: { uploads: 14, uploadsInProgress: 0, exclusionsWaiting: 1, suppressed: 42_612 },
};

function renderPage(path: string, element: ReactElement, data: unknown, extra: { path: string; element: ReactElement }[] = []) {
  const router = createMemoryRouter(
    [{ id: "page", path: path.split("?")[0] ?? path, loader: () => data, element }, ...extra],
    { initialEntries: [path], hydrationData: { loaderData: { page: data } } },
  );
  render(<RouterProvider router={router} />);
  return router;
}

function respond(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

function setTabHidden(value: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, value });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: value ? "hidden" : "visible",
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("Inventory", () => {
  it("pauses recount polling while the tab is hidden and resumes on return", async () => {
    vi.useFakeTimers();
    const running = { status: "running", jobId: "job-1" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ refresh: running }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    renderPage("/admin/inventory", <AdminInventoryRoute />, { ...INVENTORY, refresh: running });
    await act(async () => Promise.resolve());

    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => { setTabHidden(true); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => { setTabHidden(false); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    setTabHidden(false);
    vi.useRealTimers();
  });

  it("shows each database's usable count with its breakdown and as-of time", () => {
    renderPage("/admin/inventory", <AdminInventoryRoute />, INVENTORY);

    const db1 = screen.getByRole("region", { name: "DB1 · Operator leads" });
    expect(within(db1).getByText(`${INVENTORY.db1!.usable.toLocaleString("en-US")} usable`)).toBeVisible();
    expect(within(db1).getByText(/^As of \d\d:\d\d$/)).toBeVisible();
    const facts = within(db1).getByLabelText("DB1 · Operator leads breakdown");
    expect(within(facts).getByText("−42,612")).toBeVisible();
    expect(within(facts).getByText("+96,140")).toBeVisible();
    const db2 = screen.getByRole("region", { name: "DB2 · CSV uploads" });
    expect(within(db2).getByText("Nov 6 by Alex")).toBeVisible();
  });

  it("lists the top ten states with totals, and every state on request", async () => {
    const user = userEvent.setup();
    renderPage("/admin/inventory", <AdminInventoryRoute />, INVENTORY);

    const table = screen.getByRole("table", { name: "Usable by state" });
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 10 + 1);
    expect(within(table).getByText("All 12 states")).toBeVisible();
    const total = stateRows(INVENTORY.db1, INVENTORY.db2).reduce((sum, row) => sum + row.total, 0);
    expect(within(table).getByText(total.toLocaleString("en-US"))).toBeVisible();
    expect(screen.getByRole("link", { name: "Download CSV" })).toHaveAttribute(
      "href",
      "/api/admin/inventory/usable-by-state.csv",
    );

    await user.click(screen.getByRole("button", { name: "Show all 12 states" }));
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 12 + 1);
  });

  it("links every inventory tool with its count", () => {
    renderPage("/admin/inventory", <AdminInventoryRoute />, INVENTORY);
    const tools = screen.getByRole("navigation", { name: "Inventory tools" });
    expect(within(tools).getByRole("link", { name: "DB2 uploads (14)" })).toHaveAttribute("href", "/app/admin/inventory/uploads");
    expect(within(tools).getByRole("link", { name: "Exclusion lists (1 waiting)" })).toBeVisible();
    expect(within(tools).getByRole("link", { name: "Suppressed leads (42,612)" })).toBeVisible();
    expect(within(tools).getByRole("link", { name: "Niche assignment" })).toBeVisible();
    expect(within(tools).getByRole("link", { name: "Pool analytics" })).toBeVisible();
  });

  it("opens lead lookup for the typed phone number", async () => {
    const user = userEvent.setup();
    const router = renderPage("/admin/inventory", <AdminInventoryRoute />, INVENTORY, [
      { path: "/admin/inventory/lookup", element: <p>Lookup page</p> },
    ]);
    await user.type(screen.getByLabelText("Phone number"), "(615) 555-0142");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    expect(await screen.findByText("Lookup page")).toBeVisible();
    expect(router.state.location.search).toBe(`?phone=${encodeURIComponent("(615) 555-0142")}`);
  });

  it("queues a recount and says so", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond({ db1: INVENTORY.db1, db2: INVENTORY.db2, refresh: { status: "queued", jobId: 4 } });
    renderPage("/admin/inventory", <AdminInventoryRoute />, INVENTORY);

    await user.click(screen.getByRole("button", { name: "Refresh counts" }));
    expect(await screen.findByText("Recount queued. The numbers update when it finishes.")).toBeVisible();
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/admin/inventory-snapshots/refresh",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.getByRole("button", { name: "Refresh counts" })).toBeDisabled();
  });

  it("says when a database has not been counted yet", () => {
    renderPage("/admin/inventory", <AdminInventoryRoute />, { ...INVENTORY, db2: null });
    const db2 = screen.getByRole("region", { name: "DB2 · CSV uploads" });
    expect(within(db2).getAllByText("Not counted yet")).toHaveLength(2);
  });
});

const SUPPRESSED: SuppressedLeadsData = {
  items: [
    {
      id: 92,
      phone: "5125550987",
      title: "Storm damage repair",
      state: "FL",
      reason: "Reported as a wrong number by two Customers.",
      href: "/app/admin/inventory/lookup?phone=5125550987",
      actions: [
        {
          name: "restore",
          label: "Restore eligibility",
          consequence: "Restoring eligibility returns this Lead to the ordinary allocation rules.",
          destructive: false,
          requiresReason: true,
        },
      ],
    },
  ],
  restoreNotice: "Restoring eligibility returns this Lead to the ordinary allocation rules.",
  page: 1,
  pageSize: 25,
  total: 1,
  pages: 1,
};

describe("Suppressed leads", () => {
  it("links each lead to lookup and states the restore notice", () => {
    renderPage("/admin/inventory/suppressed", <AdminSuppressedLeadsRoute />, SUPPRESSED);
    const table = screen.getByRole("table", { name: "Suppressed leads" });
    expect(within(table).getByRole("link", { name: "(512) 555-0987" })).toHaveAttribute(
      "href",
      "/admin/inventory/lookup?phone=5125550987",
    );
    expect(screen.getAllByText(SUPPRESSED.restoreNotice)[0]).toBeVisible();
    expect(screen.getByText("Showing 1–1 of 1")).toBeVisible();
  });

  it("restores a lead through its suppression, with a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond({ ok: true });
    renderPage("/admin/inventory/suppressed", <AdminSuppressedLeadsRoute />, SUPPRESSED);

    await user.click(screen.getByRole("button", { name: "Restore eligibility" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Report was mistaken.");
    await user.click(within(dialog).getByRole("button", { name: "Restore eligibility" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/leads/92/suppression",
        expect.objectContaining({ method: "DELETE", body: JSON.stringify({ reason: "Report was mistaken." }) }),
      );
    });
  });
});

describe("Pool analytics", () => {
  const POOL: PoolBreakdownData = {
    asOf: "2026-11-09T06:00:00Z",
    cells: [
      { state: "TX", niche: "Dental", count: 340 },
      { state: "TX", niche: "unmapped", count: 18 },
      { state: "FL", niche: "Roofing", count: 120 },
    ],
  };

  it("lists the breakdown largest first and filters by state", async () => {
    const user = userEvent.setup();
    renderPage("/admin/inventory/pool", <AdminPoolAnalyticsRoute />, POOL);
    const table = screen.getByRole("table", { name: "Pool breakdown" });
    const rows = within(table).getAllByRole("row").slice(1, -1);
    expect(rows.map((row) => row.textContent)).toEqual(["TXDental340", "FLRoofing120", "TXUnmapped18"]);
    expect(within(table).getByText("478")).toBeVisible();

    await user.selectOptions(screen.getByLabelText("State"), "TX");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 2 + 1);
    expect(within(table).getByText("358")).toBeVisible();
  });

  it("queues a recount on demand", async () => {
    const user = userEvent.setup();
    respond({ ...POOL, refreshQueued: true });
    renderPage("/admin/inventory/pool", <AdminPoolAnalyticsRoute />, POOL);
    await user.click(screen.getByRole("button", { name: "Refresh breakdown" }));
    expect(await screen.findByText("Recount queued. The breakdown updates when it finishes.")).toBeVisible();
  });
});

function exclusion(overrides: Partial<ExclusionRow> = {}): ExclusionRow {
  return {
    id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    customer: { id: 31, name: "Liberty Roofing", href: "/app/admin/customers/31" },
    customerName: "Liberty Roofing",
    type: "dnc",
    filename: "liberty-dnc.csv",
    status: "pending_confirmation",
    global: false,
    acceptedRows: 1_200,
    invalidRows: 4,
    duplicateRows: 2,
    poolImpact: 61,
    error: "",
    decisionReason: "",
    createdAt: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    decidedAt: null,
    ...overrides,
  };
}

describe("Exclusion lists", () => {
  const DATA: ExclusionListsData = {
    waiting: [exclusion()],
    lists: {
      items: [
        exclusion(),
        exclusion({
          id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
          customer: null,
          customerName: "Operator",
          filename: "tcpa.csv",
          type: "tcpa_litigator",
          status: "confirmed",
          global: true,
        }),
      ],
      page: 1,
      pageSize: 25,
      total: 2,
      pages: 1,
    },
  };

  it("opens a waiting list in the side panel and confirms it with a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond({ ok: true });
    const router = renderPage("/admin/inventory/exclusions", <AdminInventoryExclusionsRoute />, DATA);

    const waiting = screen.getByRole("table", { name: "Waiting for a decision" });
    expect(within(waiting).getByText("2d")).toBeVisible();
    await user.click(within(waiting).getByRole("link", { name: "Liberty Roofing" }));
    const panel = await screen.findByRole("complementary", { name: "Liberty Roofing" });
    expect(router.state.location.search).toBe(`?list=${DATA.waiting[0]!.id}`);
    expect(within(panel).getByText("liberty-dnc.csv")).toBeVisible();

    await user.click(within(panel).getByRole("button", { name: "Confirm globally" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Verified the DNC source.");
    await user.click(within(dialog).getByRole("button", { name: "Confirm globally" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/exclusion-lists/${DATA.waiting[0]!.id}/confirm`,
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("lists every list with a status badge and who uploaded it", () => {
    renderPage("/admin/inventory/exclusions", <AdminInventoryExclusionsRoute />, DATA);
    const all = screen.getByRole("table", { name: "All exclusion lists" });
    expect(within(all).getByText("Waiting")).toBeVisible();
    expect(within(all).getByText("Global")).toBeVisible();
    expect(within(all).getByText("Operator")).toBeVisible();
    expect(within(all).getByRole("link", { name: "Liberty Roofing" })).toHaveAttribute("href", "/admin/customers/31");
  });

  it("requires a reason for the global bulk upload", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderPage("/admin/inventory/exclusions", <AdminInventoryExclusionsRoute />, DATA);
    const form = screen.getByRole("form", { name: "Bulk upload" });
    await user.upload(within(form).getByLabelText(/CSV file/), new File(["phone\n6155550142\n"], "dnc.csv", { type: "text/csv" }));
    await user.click(within(form).getByRole("button", { name: "Upload globally" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("An upload reason is required.");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("DB2 uploads", () => {
  const UPLOADS: UploadWorkspace = {
    uploads: [
      {
        id: "11111111-aaaa-4aaa-8aaa-111111111111",
        filename: "tn-agents-nov.csv",
        state: "TN",
        status: "complete",
        bytes: 1_024,
        processedRows: 50_000,
        validRows: 49_990,
        rejectedRows: 10,
        importedRows: 40_000,
        updatedRows: 9_990,
        duplicateRows: 0,
        error: "",
        uploadedBy: "Alex",
        createdAt: "2026-11-06T15:00:00Z",
        completedAt: "2026-11-06T15:05:00Z",
      },
      {
        id: "22222222-aaaa-4aaa-8aaa-222222222222",
        filename: "broken.csv",
        state: "KY",
        status: "failed",
        bytes: 10,
        processedRows: 0,
        validRows: 0,
        rejectedRows: 0,
        importedRows: 0,
        updatedRows: 0,
        duplicateRows: 0,
        error: "The CSV contains no valid phone records.",
        uploadedBy: "Operator",
        createdAt: "2026-11-07T15:00:00Z",
        completedAt: null,
      },
    ],
    states: [{ code: "TN", name: "Tennessee" }],
    inventory: { total: 1_082_619, states: { TN: 1_082_619 }, asOf: null },
  };

  it("lists every upload with one action per row", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond({ ...UPLOADS.uploads[1], status: "queued", error: "" });
    renderPage("/admin/inventory/uploads", <AdminInventoryUploadsRoute />, UPLOADS);

    const table = screen.getByRole("table", { name: "DB2 uploads" });
    expect(within(table).getByText("Ready")).toBeVisible();
    expect(within(table).getByText("Failed")).toBeVisible();
    expect(within(table).getByRole("link", { name: "Download rejected rows" })).toHaveAttribute(
      "href",
      `/api/admin/lead-uploads/${UPLOADS.uploads[0]!.id}/rejected`,
    );
    expect(screen.getByText("1,082,619 phone numbers in DB2 across every upload.")).toBeVisible();

    await user.click(within(table).getByRole("button", { name: "Retry import" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/lead-uploads/${UPLOADS.uploads[1]!.id}/retry`,
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await within(table).findByText("Queued")).toBeVisible();
  });

  it("asks for a file and a state before uploading", () => {
    renderPage("/admin/inventory/uploads", <AdminInventoryUploadsRoute />, UPLOADS);
    const form = screen.getByRole("form", { name: "New DB2 upload" });
    expect(within(form).getByRole("button", { name: "Upload CSV" })).toBeDisabled();
    expect(within(form).getByRole("option", { name: "Tennessee (TN)" })).toBeInTheDocument();
  });
});
