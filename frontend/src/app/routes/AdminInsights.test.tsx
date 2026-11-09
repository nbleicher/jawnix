import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { AdminInsightsRoute, changeLine } from "./AdminInsights";
import type { InsightsData } from "./AdminInsights";

function insights(overrides: Partial<InsightsData> = {}): InsightsData {
  const year = new Date().getFullYear();
  return {
    generatedAt: new Date().toISOString(),
    windowDays: 30,
    creditsUsed: { cents: 1_034_833, priorCents: 923_958 },
    creditsBought: { cents: 1_150_000, priorCents: 800_000, purchases: 9, priorPurchases: 6 },
    delivered: { leads: 2_069_666, priorLeads: 1_754_000, batches: 33, priorBatches: 30 },
    leadReports: { count: 0, priorCount: 0 },
    revenueByMonth: [
      { month: `${year}-01`, creditsUsedCents: 0, leadsDelivered: 1_210_000, batches: 20, daysSoFar: null },
      { month: `${year}-02`, creditsUsedCents: 412_000, leadsDelivered: 1_824_000, batches: 31, daysSoFar: null },
      { month: `${year}-03`, creditsUsedCents: 345_000, leadsDelivered: 690_000, batches: 12, daysSoFar: 9 },
    ],
    customerTrends: [
      { customer: { id: 1, name: "Summit Benefits Group", href: "/app/admin/customers/1" }, delivered: 450_000, priorDelivered: 300_000, reports: 0, shortBy: null },
      { customer: { id: 2, name: "Ridgeline Benefits", href: "/app/admin/customers/2" }, delivered: 200_000, priorDelivered: 200_000, reports: 0, shortBy: 31_590 },
      { customer: { id: 3, name: "Gulfshore Advisors", href: "/app/admin/customers/3" }, delivered: 69_666, priorDelivered: 139_332, reports: 1, shortBy: null },
      { customer: { id: 4, name: "Prairie State Benefits", href: "/app/admin/customers/4" }, delivered: 150_000, priorDelivered: 0, reports: 0, shortBy: null },
    ],
    sourcePerformance: {
      snapshotDate: "2026-11-09",
      windowDays: 90,
      segments: 1240,
      outcomesReported: 1,
      rows: [
        { segment: "TN::dentist", keyword: "dentist", state: "TN", niche: "Dental", nicheConfirmed: true, delivered: 84_210, worked: 0, rated: 0, positive: 0, booked: 0, poor: 0, positiveRate: null, bookedRate: null, poorRate: null },
        { segment: "TX::roofing", keyword: "roofing", state: "TX", niche: "", nicheConfirmed: false, delivered: 77_880, worked: 10, rated: 0, positive: 4, booked: 0, poor: 0, positiveRate: 0.4, bookedRate: 0, poorRate: null },
      ],
    },
    nightlyReviews: [
      {
        id: "r1",
        scraperRunId: 42,
        reviewDate: "2026-11-09",
        status: "complete",
        summary: {
          segments: [{ anomalous: false }, { anomalous: true }],
          inventory: { total: 9_241_870, eligible: 9_183_402 },
        },
        telegramDeliveryState: "sent",
        telegramMessageId: "1",
        telegramDeliveryError: "",
        createdAt: "2026-11-09T05:40:00Z",
        reconcilable: false,
      },
      {
        id: "r2",
        scraperRunId: null,
        reviewDate: "2026-11-08",
        status: "attention",
        summary: { failures: [{ kind: "inventory_sync", message: "timed out" }] },
        telegramDeliveryState: "unknown",
        telegramMessageId: "",
        telegramDeliveryError: "",
        createdAt: "2026-11-08T05:40:00Z",
        reconcilable: true,
      },
    ],
    ...overrides,
  };
}

function renderRoute(data: InsightsData) {
  const router = createMemoryRouter(
    [{ id: "insights", path: "/admin/insights", loader: () => data, element: <AdminInsightsRoute /> }],
    { initialEntries: ["/admin/insights"], hydrationData: { loaderData: { insights: data } } },
  );
  render(<RouterProvider router={router} />);
}

describe("Insights", () => {
  it("leads with four 30-day numbers against the prior 30 days", () => {
    renderRoute(insights());

    const numbers = screen.getByLabelText("Last 30 days");
    expect(within(numbers).getByText("$10,348.33")).toBeVisible();
    expect(within(numbers).getByText("Up 12% on the prior 30 days")).toBeVisible();
    expect(within(numbers).getByText("$11,500.00")).toBeVisible();
    expect(within(numbers).getByText("9 purchases")).toBeVisible();
    expect(within(numbers).getByText("2,069,666")).toBeVisible();
    expect(within(numbers).getByText("Up 18% on the prior 30 days")).toBeVisible();
    expect(within(numbers).getByText("None in the prior 30 days either")).toBeVisible();
  });

  it("shows revenue by month with full numbers and the month so far", () => {
    renderRoute(insights());

    const table = screen.getByRole("table", { name: "Revenue by month" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("Jan")).toBeVisible();
    expect(within(rows[0]!).getByText("$0.00")).toBeVisible();
    expect(within(rows[0]!).getByText("No billed batches")).toBeVisible();
    expect(within(rows[1]!).getByText("1,824,000")).toBeVisible();
    expect(within(rows[2]!).getByText("Mar (9 days)")).toBeVisible();
  });

  it("marks only problems red in customer trends", () => {
    renderRoute(insights());

    const table = screen.getByRole("table", { name: "Customer trends" });
    expect(within(table).getByRole("link", { name: "Summit Benefits Group" })).toHaveAttribute(
      "href",
      "/admin/customers/1",
    );
    expect(within(table).getByText("+50%")).not.toHaveClass("jx-text--danger");
    expect(within(table).getByText("Short 31,590")).toHaveClass("jx-text--danger");
    expect(within(table).getByText("−50%")).toHaveClass("jx-text--danger");
    expect(within(table).getByText("1")).toHaveClass("jx-text--danger");
    expect(within(table).getByText("New")).toBeVisible();
  });

  it("shows — for rates without outcomes, explains why, and links all segments", () => {
    renderRoute(insights());

    expect(screen.getByRole("heading", { level: 2, name: "Source performance · trailing 90 days" })).toBeVisible();
    expect(screen.getByText("Rates stay empty until a segment has outcomes. 1 reported so far.")).toBeVisible();
    const table = screen.getByRole("table", { name: "Source performance" });
    const [dentist, roofing] = within(table).getAllByRole("row").slice(1);
    expect(within(dentist!).getAllByText("—")).toHaveLength(3);
    expect(within(roofing!).getByText("40.0%")).toBeVisible();
    expect(screen.getByRole("link", { name: "All 1,240 segments" })).toHaveAttribute(
      "href",
      "/app/admin/insights/sources",
    );
  });

  it("keeps the nightly review history, with problems in red", () => {
    renderRoute(insights());

    const section = screen.getByRole("region", { name: "Nightly reviews" });
    expect(section).toHaveAttribute("id", "nightly-reviews");
    const table = within(section).getByRole("table", { name: "Nightly reviews" });
    const [complete, attention] = within(table).getAllByRole("row").slice(1);
    expect(within(complete!).getByRole("link", { name: /Nov 9/ })).toHaveAttribute(
      "href",
      "/admin/acquisition/runs/42",
    );
    expect(within(complete!).getByText("1 held")).toHaveClass("jx-text--danger");
    expect(within(complete!).getByText("9,183,402")).toBeVisible();
    expect(within(attention!).getByText("Needs attention")).toBeVisible();
    expect(within(attention!).getByText("Not recorded")).toBeVisible();
    expect(
      within(attention!).getByText(/Inventory sync: timed out · Telegram delivery needs reconciling/),
    ).toBeVisible();
  });

  it("says none reported when no customer has reported an outcome", () => {
    renderRoute(insights({ sourcePerformance: { ...insights().sourcePerformance, outcomesReported: 0 } }));

    expect(screen.getByText("Rates stay empty until customers report outcomes. None reported yet.")).toBeVisible();
  });
});

describe("changeLine", () => {
  it("compares with the prior window, or says there was none", () => {
    expect(changeLine(120, 100, 30, "None")).toBe("Up 20% on the prior 30 days");
    expect(changeLine(80, 100, 30, "None")).toBe("Down 20% on the prior 30 days");
    expect(changeLine(100, 100, 30, "None")).toBe("Level with the prior 30 days");
    expect(changeLine(5, 0, 30, "None used")).toBe("None used in the prior 30 days");
  });
});
