import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Decision } from "../operator/DecisionPanel";
import type { BatchRow } from "../operator/format";
import { AdminTodayRoute, adminTodayLoader } from "./AdminToday";
import type { ScraperHealth, TodayData } from "./AdminToday";

function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString();
}

function decision(overrides: Partial<Decision>): Decision {
  return {
    key: "exclusionList:e1",
    kind: "exclusionList",
    label: "Exclusion list",
    recordId: "e1",
    subject: "Liberty Roofing",
    detail: "DNC · 61 in pool",
    since: hoursAgo(48),
    href: "/app/admin/acquisition",
    batch: null,
    facts: {
      customerName: "Liberty Roofing",
      typeLabel: "DNC",
      filename: "liberty-dnc.csv",
      acceptedRows: 248,
      invalidRows: 3,
      duplicateRows: 1,
      poolImpact: 61,
      createdAt: hoursAgo(48),
    },
    ...overrides,
  };
}

function row(overrides: Partial<BatchRow>): BatchRow {
  return {
    id: "r1",
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
    createdAt: hoursAgo(72),
    deliveredAt: null,
    closedAt: null,
    ...overrides,
  };
}

function today(overrides: Partial<TodayData> = {}): TodayData {
  return {
    generatedAt: new Date().toISOString(),
    numbers: null,
    lastNight: {
      run: { id: 42, committedAt: hoursAgo(9), syncStatus: "complete", href: "/app/admin/acquisition/runs/42" },
      newLeads: 18_420,
      heldSegments: 1,
      review: { id: "r", reviewDate: null, status: "complete", telegram: "unknown", telegramAt: null },
    },
    decisions: {
      items: [
        decision({}),
        decision({
          key: "scrapeAnomaly:a1",
          kind: "scrapeAnomaly",
          label: "Scrape anomaly",
          recordId: "a1",
          subject: "Run 42",
          detail: "roofing · TX · down more than 50%",
          since: hoursAgo(4),
          href: "/app/admin/acquisition/runs/42",
          facts: { runId: 42, segments: [{ key: "TX::roofing", label: "roofing · TX", reasons: ["down more than 50%"] }] },
        }),
      ],
      total: 2,
      unavailable: [],
    },
    batches: {
      inProgress: [row({})],
      deliveredToday: [
        row({
          id: "r2",
          number: 1043,
          href: "/app/admin/batches/1043",
          customer: { id: 8, name: "Summit Benefits Group", href: "/app/admin/customers/8" },
          status: "delivered",
          stage: "delivered",
          availability: { kind: "committed", committed: 100_000 },
          deliveredAt: hoursAgo(1),
        }),
      ],
      allBatches: 80,
    },
    scraper: { granted: false },
    ...overrides,
  };
}

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const HEALTHY: ScraperHealth = {
  granted: true,
  serviceState: "connected",
  checkedAt: minutesAgo(0),
  pipeline: { key: "running", label: "Running" },
  workersRunning: 6,
  workersTotal: 8,
  queueDepth: 812,
  latestWriteAt: minutesAgo(0),
  writeIsFresh: true,
  lastSuccessfulAt: minutesAgo(0),
};

function renderToday(data: TodayData, path = "/admin/today") {
  const router = createMemoryRouter(
    [{ id: "today", path: "/admin/today", loader: () => data, element: <AdminTodayRoute /> }],
    { initialEntries: [path], hydrationData: { loaderData: { today: data } } },
  );
  render(<RouterProvider router={router} />);
  return router;
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("Today", () => {
  it("reads last night in one line", () => {
    renderToday(today());
    const line = screen.getByRole("region", { name: "Last night" });
    expect(within(line).getByRole("link", { name: "Run 42" })).toHaveAttribute(
      "href",
      "/admin/acquisition/runs/42",
    );
    expect(within(line).getByText("+18,420 new leads")).toBeVisible();
    expect(within(line).getByText("1 segment held")).toBeVisible();
    expect(within(line).getByText("Telegram delivery unknown")).toBeVisible();
    expect(within(line).queryByText(/workers/)).toBeNull();
  });

  it("asks for the Scraper verification instead of showing health without it", () => {
    renderToday(today({ scraper: { granted: false } }));
    const health = screen.getByRole("region", { name: "Scraper health" });
    expect(within(health).getByRole("link", { name: "Scraper · verify to see health" })).toHaveAttribute(
      "href",
      "/admin/acquisition/scraper",
    );
    expect(within(health).queryByText(/workers|queue/)).toBeNull();
  });

  it("shows running state, workers, queue, and last write with a live grant", () => {
    renderToday(today({ scraper: HEALTHY }));
    const health = screen.getByRole("region", { name: "Scraper health" });
    expect(health).toHaveTextContent("Scraper running · 6 of 8 workers · queue 812 · last write just now");
    expect(within(health).getByRole("link", { name: "Scraper" })).toHaveAttribute(
      "href",
      "/admin/acquisition/scraper",
    );
  });

  it("marks a running Scraper that has stopped writing", () => {
    renderToday(
      today({
        scraper: { ...HEALTHY, latestWriteAt: minutesAgo(12), writeIsFresh: false, workersTotal: null },
      }),
    );
    const health = screen.getByRole("region", { name: "Scraper health" });
    expect(health).toHaveTextContent("Scraper running · 6 workers running · queue 812 · last write 12 minutes ago");
    expect(within(health).getByText("last write 12 minutes ago")).toHaveClass("jx-text--danger");
  });

  it("marks a Scraper with no healthy worker", () => {
    renderToday(
      today({
        scraper: {
          ...HEALTHY,
          pipeline: { key: "stopped", label: "No workers" },
          workersRunning: 0,
          queueDepth: 0,
          latestWriteAt: null,
          writeIsFresh: false,
        },
      }),
    );
    const health = screen.getByRole("region", { name: "Scraper health" });
    expect(health).toHaveTextContent("Scraper no workers · 0 of 8 workers · queue 0 · no writes yet");
    expect(within(health).getByText("no workers")).toHaveClass("jx-text--danger");
  });

  it("says when the Scraper could not be reached", () => {
    renderToday(
      today({
        scraper: {
          ...HEALTHY,
          serviceState: "unavailable",
          pipeline: null,
          workersRunning: null,
          workersTotal: null,
          queueDepth: null,
          latestWriteAt: null,
          writeIsFresh: null,
          lastSuccessfulAt: null,
        },
      }),
    );
    expect(screen.getByRole("region", { name: "Scraper health" })).toHaveTextContent("Scraper unavailable");
  });

  it("keeps Today when the health check itself fails", () => {
    renderToday(today({ scraper: null }));
    expect(screen.getByRole("region", { name: "Scraper health" })).toHaveTextContent(
      "Scraper health could not be checked",
    );
    expect(screen.getByRole("region", { name: "Last night" })).toBeVisible();
  });

  it("reads health beside Today and survives its failure", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/api/admin/scraper/health")) {
        return new Response(JSON.stringify({ detail: "Scraper down" }), { status: 500 });
      }
      return new Response(JSON.stringify(today({})), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const data = await adminTodayLoader();
    expect(fetchSpy.mock.calls.map(([url]) => String(url).split("?")[0])).toEqual([
      "/api/admin/today",
      "/api/admin/scraper/health",
    ]);
    expect(data.scraper).toBeNull();
    expect(data.lastNight?.run?.id).toBe(42);
  });

  it("lists decisions in the order given, oldest first, each opening beside the list", async () => {
    const user = userEvent.setup();
    const router = renderToday(today());
    const table = screen.getByRole("table", { name: "Decisions" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((item) => within(item).getAllByRole("cell")[0]!.textContent)).toEqual([
      "Exclusion list",
      "Scrape anomaly",
    ]);

    await user.click(within(table).getByRole("link", { name: "Exclusion list" }));
    // Opening is a navigation (the row lives in the address), so it settles
    // asynchronously.
    const panel = await screen.findByRole("complementary", { name: "Liberty Roofing" });
    expect(router.state.location.search).toBe("?decision=exclusionList%3Ae1");
    expect(rows[0]).toHaveAttribute("aria-current", "true");
    expect(within(panel).getByText("liberty-dnc.csv")).toBeVisible();
    expect(within(panel).getByRole("button", { name: "Confirm globally" })).toBeVisible();
    expect(within(panel).getByRole("button", { name: "Deny global effect" })).toBeVisible();
    await waitFor(() =>
      expect(within(panel).getByRole("heading", { name: "Liberty Roofing" })).toHaveFocus(),
    );

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("complementary")).toBeNull());
    expect(router.state.location.search).toBe("");
    expect(within(table).getByRole("link", { name: "Exclusion list" })).toHaveFocus();
  });

  it("opens the shared row from the address without taking focus", () => {
    renderToday(today(), "/admin/today?decision=scrapeAnomaly%3Aa1");
    const panel = screen.getByRole("complementary", { name: "Run 42" });
    expect(within(panel).getByText("roofing · TX")).toBeVisible();
    // Application links keep the /app base outside the real router basename.
    expect(within(panel).getByRole("link", { name: "Open full page" })).toHaveAttribute(
      "href",
      "/app/admin/acquisition/runs/42",
    );
    expect(document.activeElement).toBe(document.body);
  });

  it("confirms a held anomaly through its own endpoint with a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "confirmed" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderToday(today(), "/admin/today?decision=scrapeAnomaly%3Aa1");
    const panel = screen.getByRole("complementary", { name: "Run 42" });
    await user.click(within(panel).getByRole("button", { name: "Confirm" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Source change.");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/scrape-anomalies/a1/confirm",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ reason: "Source change." }) }),
      ),
    );
  });

  it("shows batches moving on their own and the way to all of them", () => {
    renderToday(today());
    const progress = screen.getByRole("table", { name: "Batches in progress" });
    expect(within(progress).getByRole("link", { name: "#1039" })).toHaveAttribute(
      "href",
      "/admin/batches/1039",
    );
    expect(within(progress).getByText("Waiting")).toBeVisible();
    expect(within(progress).getByText("short 31,590")).toBeVisible();
    expect(within(screen.getByRole("table", { name: "Delivered today" })).getByText("Delivered")).toBeVisible();
    expect(screen.getByRole("link", { name: "All batches (80)" })).toHaveAttribute(
      "href",
      "/app/admin/batches",
    );
  });

  it("stays usable when parts of the page cannot be read", () => {
    renderToday(
      today({
        numbers: null,
        decisions: { items: [], total: 0, unavailable: ["scrapeAnomalies"] },
        batches: null,
      }),
    );
    expect(screen.getByText(/numbers are temporarily unavailable/)).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("scrape anomalies");
    expect(screen.getByRole("heading", { name: "Nothing is waiting on you" })).toBeVisible();
    expect(screen.getByText(/Batches are temporarily unavailable/)).toBeVisible();
  });
});
