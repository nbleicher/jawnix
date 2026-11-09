import type { Page, Route } from "@playwright/test";

/**
 * A seeded Insights read. The shape mirrors `operator_insights` in
 * jawnix/insights.py; the figures match the approved render (op-09).
 */

function json(route: Route, value: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

function customer(id: number, name: string) {
  return { id, name, href: `/app/admin/customers/${id}` };
}

function segment(
  key: string,
  niche: string,
  delivered: number,
  counts: { worked?: number; rated?: number; positive?: number; booked?: number; poor?: number } = {},
) {
  const [state, keyword] = key.split("::") as [string, string];
  const worked = counts.worked ?? 0;
  const rated = counts.rated ?? 0;
  return {
    segment: key,
    keyword,
    state,
    niche,
    nicheConfirmed: true,
    delivered,
    worked,
    rated,
    positive: counts.positive ?? 0,
    booked: counts.booked ?? 0,
    poor: counts.poor ?? 0,
    positiveRate: worked ? (counts.positive ?? 0) / worked : null,
    bookedRate: worked ? (counts.booked ?? 0) / worked : null,
    poorRate: rated ? (counts.poor ?? 0) / rated : null,
  };
}

export const INSIGHTS = {
  generatedAt: "2026-11-09T12:00:00Z",
  windowDays: 30,
  creditsUsed: { cents: 1_034_833, priorCents: 923_958 },
  creditsBought: { cents: 1_150_000, priorCents: 980_000, purchases: 9, priorPurchases: 7 },
  delivered: { leads: 2_069_666, priorLeads: 1_754_000, batches: 33, priorBatches: 30 },
  leadReports: { count: 0, priorCount: 0 },
  revenueByMonth: [
    { month: "2026-09", creditsUsedCents: 0, leadsDelivered: 1_210_000, batches: 24, daysSoFar: null },
    { month: "2026-10", creditsUsedCents: 412_000, leadsDelivered: 1_824_000, batches: 36, daysSoFar: null },
    { month: "2026-11", creditsUsedCents: 345_000, leadsDelivered: 690_000, batches: 12, daysSoFar: 9 },
  ],
  customerTrends: [
    { customer: customer(1, "Summit Benefits Group"), delivered: 450_000, priorDelivered: 300_000, reports: 0, shortBy: null },
    { customer: customer(7, "Harbor Insurance"), delivered: 300_000, priorDelivered: 352_941, reports: 0, shortBy: null },
    { customer: customer(3, "Ridgeline Benefits"), delivered: 200_000, priorDelivered: 200_000, reports: 0, shortBy: 31_590 },
    { customer: customer(4, "Prairie State Benefits"), delivered: 150_000, priorDelivered: 125_000, reports: 0, shortBy: null },
    { customer: customer(5, "Gulfshore Advisors"), delivered: 69_666, priorDelivered: 139_332, reports: 1, shortBy: null },
  ],
  sourcePerformance: {
    snapshotDate: "2026-11-09",
    windowDays: 90,
    segments: 1240,
    outcomesReported: 1,
    rows: [
      segment("TN::dentist", "Dental", 84_210),
      segment("TX::roofing", "Roofing", 77_880),
      segment("FL::chiropractor", "Chiropractic", 70_040),
      segment("OH::insurance agency", "Insurance", 59_800),
    ],
  },
  nightlyReviews: [
    {
      id: "66666666-6666-4666-8666-666666666666",
      scraperRunId: 42,
      reviewDate: "2026-11-09",
      status: "complete",
      summary: {
        configuration: { version: 12, status: "active" },
        segments: [{ anomalous: true }, ...Array.from({ length: 1239 }, () => ({ anomalous: false }))],
        inventory: { total: 9_241_870, eligible: 9_183_402 },
      },
      telegramDeliveryState: "sent",
      telegramMessageId: "812",
      telegramDeliveryError: "",
      createdAt: "2026-11-09T05:40:00Z",
      reconcilable: false,
    },
    {
      id: "66666666-6666-4666-8666-666666666665",
      scraperRunId: 41,
      reviewDate: "2026-11-08",
      status: "attention",
      summary: {
        segments: Array.from({ length: 1240 }, () => ({ anomalous: false })),
        inventory: { total: 9_238_002, eligible: 9_180_114 },
        failures: [{ kind: "inventory_sync", message: "Retried after a timeout" }],
      },
      telegramDeliveryState: "unknown",
      telegramMessageId: "",
      telegramDeliveryError: "",
      createdAt: "2026-11-08T05:40:00Z",
      reconcilable: true,
    },
  ],
};

export async function mockInsights(page: Page, overrides: Partial<typeof INSIGHTS> = {}) {
  await page.route(/\/api\/admin\/insights$/, (route) => json(route, { ...INSIGHTS, ...overrides }));
}
