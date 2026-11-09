import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BatchRequest, BatchRequestWorkspace } from "./batchRequests";
import { CustomerBatchesRoute, CustomerBatchRoute } from "./CustomerBatches";

const NOW = new Date();

function day(offset: number, hours = 12): string {
  return new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate() + offset, hours, 5).toISOString();
}

function batch(number: number, overrides: Partial<BatchRequest> = {}): BatchRequest {
  const digits = String(number);
  return {
    id: `${digits}${digits}-${digits}-4${digits.slice(1)}-8${digits.slice(1)}-${digits.repeat(3)}`,
    number,
    lead_count: 50_000,
    rows_per_file: 10_000,
    states: ["FL", "TX"],
    submitted_at: day(-40, 14),
    delivered_at: day(-39, 8),
    status: { label: "Delivered", description: "Your Batch has been delivered.", tone: "success" },
    milestones: { milestones: [], current_key: null, pause: null, outcome: null },
    can_cancel: false,
    next_action: null,
    href: `/app/batches/${number}`,
    receipt_href: `/app/batches/${number}`,
    artifact: {
      filename: "batch.zip",
      row_count: 50_000,
      parts: [],
      columns: ["phone", "title", "state"],
      expires_at: day(-9, 8),
      available: false,
      download_href: null,
    },
    stage: "file_expired",
    can_request_copy: true,
    cost: { rate_cents_per_thousand: 500, amount_cents: 25_000, state: "charged", at: day(-39, 8) },
    history: [
      { key: "submitted", label: "Submitted", at: day(-40, 14), done: true },
      { key: "approved", label: "Approved", at: day(-40, 16), done: true },
      { key: "delivered", label: "Delivered", at: day(-39, 8), done: true },
      { key: "file_expired", label: "File expired", at: day(-9, 8), done: true },
    ],
    leads_by_state: [
      { state: "FL", name: "Florida", lead_count: 31_240 },
      { state: "TX", name: "Texas", lead_count: 18_760 },
    ],
    ...overrides,
  };
}

const EXPIRED = batch(1017);
const WAITING = batch(1042, {
  lead_count: 25_000,
  states: ["FL"],
  submitted_at: day(0, 9),
  delivered_at: null,
  artifact: null,
  stage: "waiting_approval",
  can_cancel: true,
  can_request_copy: false,
  cost: { rate_cents_per_thousand: 500, amount_cents: 12_500, state: "held", at: day(0, 9) },
  history: [
    { key: "submitted", label: "Submitted", at: day(0, 9), done: true },
    { key: "approved", label: "Approved", at: null, done: false },
    { key: "delivered", label: "Ready to download", at: null, done: false },
  ],
  leads_by_state: null,
});

const WORKSPACE: BatchRequestWorkspace = {
  limits: {
    minimum_lead_count: 1,
    maximum_lead_count: 100_000,
    licensed_states: ["FL", "TX"],
    state_names: { FL: "Florida", TX: "Texas" },
  },
  blocker: null,
  requests: [WAITING, EXPIRED],
  history: { page: 1, page_size: 20, total: 2 },
};

function stubFetch(handler: (url: string) => Response) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => Promise.resolve(handler(String(input))));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderAt(path: string, data: unknown) {
  const router = createMemoryRouter(
    [
      { id: "batches", path: "/batches", loader: () => data, element: <CustomerBatchesRoute /> },
      { id: "batch", path: "/batches/:batchId", loader: () => data, element: <CustomerBatchRoute /> },
    ],
    {
      basename: "/app",
      initialEntries: [path],
      hydrationData: { loaderData: { batches: data, batch: data } },
    },
  );
  render(<RouterProvider router={router} />);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return router;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CustomerBatchesRoute", () => {
  it("lists each batch with its status and one action", async () => {
    stubFetch(() => new Response("{}", { status: 503 }));
    await renderAt("/app/batches", WORKSPACE);

    const table = screen.getByRole("table", { name: "Batches" });
    const [, waiting, expired] = within(table).getAllByRole("row");
    expect(within(waiting!).getByRole("link", { name: "Batch #1042" })).toHaveAttribute("href", "/app/batches/1042");
    expect(waiting).toHaveTextContent("$125.00 held");
    expect(within(waiting!).getByRole("button", { name: "Cancel Batch #1042" })).toBeVisible();
    expect(within(expired!).getByRole("button", { name: "Get a new copy of Batch #1017" })).toBeVisible();
    expect(screen.getByText("Showing 1–2 of 2")).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Batch pages" })).toBeNull();
  });

  it("asks for a new copy and says so above the table", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url) =>
      url.endsWith("/new-copy")
        ? new Response(JSON.stringify({ ...EXPIRED, stage: "copy_on_the_way", can_request_copy: false }), { status: 202 })
        : new Response("{}", { status: 503 }),
    );
    await renderAt("/app/batches", WORKSPACE);

    await user.click(screen.getByRole("button", { name: "Get a new copy of Batch #1017" }));

    expect(await screen.findByRole("status")).toHaveTextContent("A new copy of Batch #1017 is on the way.");
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/me/batch-requests/${EXPIRED.id}/new-copy`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.queryByRole("button", { name: "Get a new copy of Batch #1017" })).toBeNull();
  });

  it("points a new Customer at New request", async () => {
    stubFetch(() => new Response("{}", { status: 503 }));
    await renderAt("/app/batches", { ...WORKSPACE, requests: [], history: { page: 1, page_size: 20, total: 0 } });

    expect(screen.getByRole("link", { name: "Request your first batch" })).toHaveAttribute("href", "/app/new-request");
  });
});

describe("CustomerBatchRoute", () => {
  it("shows leads per state, files, charge, and history", async () => {
    stubFetch(() => new Response("{}", { status: 503 }));
    await renderAt("/app/batches/1017", EXPIRED);

    expect(screen.getByRole("heading", { level: 1, name: "Batch #1017" })).toBeVisible();
    const byState = screen.getByRole("table", { name: "Leads by state" });
    expect(byState).toHaveTextContent("Florida31,240");
    expect(byState).toHaveTextContent("Total50,000");
    const files = screen.getByRole("region", { name: "Files" });
    expect(files).toHaveTextContent("5 CSV files of 10,000 rows, in one zip");
    expect(files).toHaveTextContent("phone, title, state");
    expect(files).toHaveTextContent("Same leads, ready in a few minutes, emailed to you");
    expect(screen.getByRole("region", { name: "Charge" })).toHaveTextContent("$5.00 per 1,000");
    expect(within(screen.getByRole("list", { name: "History of Batch #1017" })).getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("region", { name: "Report a bad lead" })).toBeVisible();
  });

  it("reports a bad lead found within the batch", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url) => {
      if (url === "/api/me/feedback/lookup") {
        return new Response(
          JSON.stringify({ distributionEventId: 77, businessName: "Sunshine Roofing", phone: "5555550100" }),
          { status: 200 },
        );
      }
      if (url === "/api/me/distributions/77/reports") {
        return new Response(JSON.stringify({ id: "report-1" }), { status: 201 });
      }
      return new Response("{}", { status: 503 });
    });
    await renderAt("/app/batches/1017", EXPIRED);
    const form = screen.getByRole("region", { name: "Report a bad lead" });

    await user.type(within(form).getByRole("textbox", { name: "Phone number" }), "(555) 555-0100");
    await user.selectOptions(within(form).getByRole("combobox", { name: "What is wrong?" }), "duplicate");
    await user.click(within(form).getByRole("button", { name: "Send report" }));

    await waitFor(() =>
      expect(within(form).getByRole("status")).toHaveTextContent("Report sent for Sunshine Roofing."),
    );
    const lookup = fetchMock.mock.calls.find(([url]) => String(url) === "/api/me/feedback/lookup") as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(String(lookup[1].body))).toEqual({ phone: "(555) 555-0100", request_id: EXPIRED.id });
    const report = fetchMock.mock.calls.find(([url]) => String(url) === "/api/me/distributions/77/reports") as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(String(report[1].body))).toEqual({ reason: "duplicate", details: "" });
  });

  it("an open batch shows what is held and what is still to come", async () => {
    stubFetch(() => new Response("{}", { status: 503 }));
    await renderAt("/app/batches/1042", WAITING);

    expect(screen.getByRole("region", { name: "Charge" })).toHaveTextContent("Held until delivery$125.00");
    expect(screen.getByText("Noah reviews each request. We email you when your batch is ready to download.")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Report a bad lead" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cancel Batch #1042" })).toBeVisible();
  });

  it("says a batch is not found rather than failing", async () => {
    await renderAt("/app/batches/9999", null);

    expect(screen.getByRole("heading", { level: 1, name: "Batch not found" })).toBeVisible();
  });
});
