import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import type {
  CustomerHomeBatch,
  CustomerHomeData,
  CustomerHomeInProgress,
} from "../customer/home";
import { CustomerHomeRoute } from "./CustomerHome";

const NOW = new Date();

function daysFromNow(days: number, hours = 9, minutes = 41): string {
  const date = new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate() + days, hours, minutes);
  return date.toISOString();
}

function batch(overrides: Partial<CustomerHomeBatch> = {}): CustomerHomeBatch {
  const id = overrides.id ?? "38383838-3838-4838-8838-383838383838";
  return {
    id,
    number: 1038,
    lead_count: 50_000,
    states: ["FL", "TX"],
    submitted_at: daysFromNow(-2),
    delivered_at: daysFromNow(-2),
    cost_cents: 25_000,
    href: "/app/batches/1038",
    file_available: true,
    file_expires_at: daysFromNow(28),
    download_href: `/api/me/batch-requests/${id}/artifact`,
    ...overrides,
  };
}

const IN_PROGRESS_ID = "42424242-4242-4242-8242-424242424242";

function inProgress(overrides: Partial<CustomerHomeInProgress> = {}): CustomerHomeInProgress {
  return {
    batch: batch({
      id: IN_PROGRESS_ID,
      number: 1042,
      lead_count: 25_000,
      states: ["FL"],
      submitted_at: daysFromNow(0, 9, 41),
      delivered_at: null,
      cost_cents: 12_500,
      href: "/app/batches/1042",
      file_available: false,
      file_expires_at: null,
      download_href: null,
    }),
    steps: [
      { key: "submitted", label: "Submitted", state: "complete", occurred_at: daysFromNow(0, 9, 41) },
      { key: "approval", label: "Waiting for approval", state: "current", occurred_at: null },
      { key: "preparing", label: "Preparing your leads", state: "upcoming", occurred_at: null },
      { key: "ready", label: "Ready to download", state: "upcoming", occurred_at: null },
    ],
    note: "Noah reviews each request, then we deliver the full batch at once and email you.",
    held_cents: 12_500,
    can_cancel: true,
    ...overrides,
  };
}

function home(overrides: Partial<CustomerHomeData> = {}): CustomerHomeData {
  const expiring = batch({
    id: "24242424-2424-4242-8242-242424242424",
    number: 1024,
    delivered_at: daysFromNow(-27),
    file_expires_at: daysFromNow(3),
    download_href: "/api/me/batch-requests/24242424-2424-4242-8242-242424242424/artifact",
  });
  return {
    customer_name: "Northstar Insurance",
    appointed_states: ["FL", "TX"],
    can_request: true,
    billing: {
      available_balance_cents: 41_250,
      held_cents: 12_500,
      held_for: [{ id: IN_PROGRESS_ID, number: 1042 }],
      lead_rate_cents_per_thousand: 500,
      covers_leads: 82_500,
      spent_this_month_cents: 75_000,
    },
    month: { starts_at: daysFromNow(-8, 0, 0), delivered_leads: 150_000, delivered_batches: 2 },
    alerts: [
      { kind: "file_expiring", batch: expiring, problem: null, reference_lead_count: null },
    ],
    in_progress: [inProgress()],
    recent_batches: [
      batch(),
      expiring,
      batch({
        id: "17171717-1717-4717-8717-171717171717",
        number: 1017,
        delivered_at: daysFromNow(-38),
        file_available: false,
        file_expires_at: daysFromNow(-8),
        download_href: null,
        href: "/app/batches/1017",
        can_request_copy: true,
      }),
    ],
    batch_count: 24,
    ...overrides,
  };
}

function renderHome(data: CustomerHomeData | (() => CustomerHomeData)) {
  const read = typeof data === "function" ? data : () => data;
  const router = createMemoryRouter(
    [
      {
        id: "customer",
        path: "/app",
        loader: () => read(),
        element: <Outlet />,
        children: [{ path: "home", element: <CustomerHomeRoute /> }],
      },
    ],
    {
      initialEntries: ["/app/home"],
      hydrationData: { loaderData: { customer: read() } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CustomerHomeRoute", () => {
  it("opens with the customer and the New request action", () => {
    renderHome(home());

    expect(screen.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
    expect(screen.getByText("Northstar Insurance")).toBeVisible();
    expect(screen.getAllByRole("link", { name: "New request" })[0]).toHaveAttribute(
      "href",
      "/app/new-request",
    );
  });

  it("shows the four numbers in Customer words", () => {
    renderHome(home());

    const numbers = screen.getByLabelText("Account summary");
    expect(within(numbers).getByText("Balance")).toBeVisible();
    expect(within(numbers).getByText("$412.50")).toBeVisible();
    expect(within(numbers).getByText("Covers 82,500 leads at $5.00 per 1,000")).toBeVisible();
    expect(within(numbers).getByText("Appointed states")).toBeVisible();
    expect(within(numbers).getByText("FL, TX")).toBeVisible();
    expect(within(numbers).getByRole("link", { name: "Edit appointed states" })).toHaveAttribute(
      "href",
      "/app/account",
    );
    expect(within(numbers).getByText("150,000")).toBeVisible();
    expect(within(numbers).getByText("2 batches")).toBeVisible();
    expect(within(numbers).getByText("$750.00")).toBeVisible();
    expect(within(numbers).getByText("$125.00 held for #1042")).toBeVisible();
  });

  it("alerts about a file about to expire with its download", () => {
    renderHome(home());

    const alerts = screen.getByRole("list", { name: "Needs your attention" });
    expect(alerts).toHaveTextContent(
      /File expires in 3 days\. Your 50,000-lead batch from \w{3} \d{1,2}(, \d{4})? is available until \w{3} \d{1,2}/,
    );
    expect(within(alerts).getByRole("link", { name: "Download Batch #1024" })).toHaveAttribute(
      "href",
      "/api/me/batch-requests/24242424-2424-4242-8242-242424242424/artifact",
    );
  });

  it("alerts about a low balance against the last batch, with Buy credits", async () => {
    renderHome(
      home({
        billing: {
          available_balance_cents: 3_750,
          held_cents: 0,
          held_for: [],
          lead_rate_cents_per_thousand: 500,
          covers_leads: 7_500,
          spent_this_month_cents: 0,
        },
        alerts: [{ kind: "low_balance", batch: null, problem: null, reference_lead_count: 50_000 }],
      }),
    );

    const alerts = screen.getByRole("list", { name: "Needs your attention" });
    expect(alerts).toHaveTextContent(
      "Low balance. $37.50 covers 7,500 leads at $5.00 per 1,000. Your last batch was 50,000 leads.",
    );
    await userEvent.click(within(alerts).getByRole("button", { name: "Buy credits" }));
    expect(screen.getByRole("dialog", { name: "Buy credits" })).toBeVisible();
    expect(screen.getByText("Nothing held")).toBeVisible();
  });

  it("alerts about unfinished setup with the fix", () => {
    renderHome(
      home({
        appointed_states: [],
        can_request: false,
        alerts: [{ kind: "setup_incomplete", batch: null, problem: "no_appointed_states", reference_lead_count: null }],
      }),
    );

    const alerts = screen.getByRole("list", { name: "Needs your attention" });
    expect(alerts).toHaveTextContent("Setup incomplete. Add your appointed states before you request a batch.");
    expect(within(alerts).getByRole("link", { name: "Add appointed states" })).toHaveAttribute(
      "href",
      "/app/account",
    );
    expect(screen.getByText("None")).toBeVisible();
  });

  it("lists recent batches with one action each", () => {
    renderHome(home());

    const table = screen.getByRole("table", { name: "Recent batches" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    const [live, , expired] = rows;
    expect(within(live!).getByRole("link", { name: "#1038" })).toHaveAttribute(
      "href",
      "/app/batches/1038",
    );
    expect(live).toHaveTextContent("50,000");
    expect(live).toHaveTextContent("$250.00");
    expect(within(live!).getByRole("link", { name: "Download Batch #1038" })).toHaveAttribute(
      "href",
      "/api/me/batch-requests/38383838-3838-4838-8838-383838383838/artifact",
    );
    // An expired file can be copied again right here.
    expect(
      within(expired!).getByRole("button", { name: "Get a new copy of Batch #1017" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "All batches (24)" })).toHaveAttribute(
      "href",
      "/app/batches",
    );
  });

  it("asks for a new copy of an expired file, then reads Home again", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 202 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    let asked = false;
    renderHome(() => {
      const data = home();
      if (!asked) return data;
      return {
        ...data,
        recent_batches: data.recent_batches.map((item) =>
          item.number === 1017 ? { ...item, can_request_copy: false, copy_pending: true } : item,
        ),
      };
    });

    asked = true;
    await userEvent.click(screen.getByRole("button", { name: "Get a new copy of Batch #1017" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/me/batch-requests/17171717-1717-4717-8717-171717171717/new-copy",
      expect.objectContaining({ method: "POST" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "A new copy of Batch #1017 is on the way.",
    );
    expect(await screen.findByText("New copy on the way")).toBeVisible();
  });

  it("shows the batch in progress step by step", () => {
    renderHome(home());

    const panel = screen.getByRole("region", { name: "In progress · #1042" });
    expect(panel).toHaveTextContent(/25,000 leads · FL · submitted today 09:41/);
    const steps = within(panel).getAllByRole("listitem");
    expect(steps.map((step) => step.textContent)).toEqual([
      "Done: Submitted09:41",
      "Now: Waiting for approvalNow",
      "Not yet: Preparing your leads",
      "Not yet: Ready to download",
    ]);
    expect(steps[1]).toHaveAttribute("aria-current", "step");
    expect(panel).toHaveTextContent("$125.00 is held from your balance until then.");
  });

  it("cancels a batch in progress after confirming, then reads Home again", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    let canceled = false;
    renderHome(() => (canceled ? home({ in_progress: [] }) : home()));

    const panel = screen.getByRole("region", { name: "In progress · #1042" });
    await userEvent.click(within(panel).getByRole("button", { name: "Cancel batch" }));
    const dialog = screen.getByRole("dialog", { name: "Cancel Batch #1042?" });
    expect(dialog).toHaveAccessibleDescription(
      "Nothing has been delivered yet. Canceling releases the $125.00 held from your balance and cannot be undone.",
    );
    canceled = true;
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel batch" }));

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/me/batch-requests/${IN_PROGRESS_ID}/cancel`,
      expect.objectContaining({ method: "POST" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "In progress · #1042" })).toBeNull(),
    );
  });

  it("points a new Customer at their first request", () => {
    renderHome(
      home({
        alerts: [],
        in_progress: [],
        recent_batches: [],
        batch_count: 0,
        month: { starts_at: daysFromNow(-8, 0, 0), delivered_leads: 0, delivered_batches: 0 },
      }),
    );

    expect(screen.getByRole("heading", { level: 2, name: "No batches yet" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Request your first batch" })).toHaveAttribute(
      "href",
      "/app/new-request",
    );
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("leaves money out for Customers who are not billed", () => {
    renderHome(
      home({
        billing: null,
        alerts: [],
        recent_batches: [batch({ cost_cents: null })],
      }),
    );

    const numbers = screen.getByLabelText("Account summary");
    expect(within(numbers).queryByText("Balance")).toBeNull();
    expect(within(numbers).queryByText("Spent this month")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Cost" })).toBeNull();
  });

  it("uses Customer words only: batch numbers, no internal codes or jargon", () => {
    renderHome(home());

    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/REQ-|TR-|CP-/);
    expect(text).not.toMatch(/Batch Artifact|Batch Hold|Eligibility Hold|Licensed States/);
    expect(text).not.toMatch(/available now|in stock|inventory/i);
  });

  it("falls back to the short reference until batch numbers exist", () => {
    renderHome(
      home({
        alerts: [],
        in_progress: [],
        recent_batches: [batch({ number: null })],
      }),
    );

    expect(screen.getByRole("link", { name: "REQ-38383838" })).toBeVisible();
  });
});
