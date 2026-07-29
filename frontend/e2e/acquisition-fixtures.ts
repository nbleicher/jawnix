import type { Page, Route } from "@playwright/test";

/**
 * A seeded Acquisition workspace.
 *
 * Shapes mirror `jawnix/acquisition.py`'s read model, so the browser tests
 * exercise the contract the backend actually produces.
 */

export const ANOMALY_ID = "11111111-1111-4111-8111-111111111111";
export const RECOMMENDATION_ID = "33333333-3333-4333-8333-333333333333";
export const CONFIGURATION_ID = "44444444-4444-4444-8444-444444444444";
export const SCRAPE_RUN_ID = 42;
export const EVIDENCE_CHECKSUM = "e".repeat(64);

export interface RecordedCall {
  url: string;
  body: Record<string, unknown>;
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

export function acquisitionPayload(overrides: Record<string, unknown> = {}) {
  return {
    nightlyReviews: [
      {
        id: "66666666-6666-4666-8666-666666666666",
        scraperRunId: SCRAPE_RUN_ID,
        reviewDate: "2026-07-28",
        status: "attention",
        summary: {},
        telegramDeliveryState: "unknown",
        telegramMessageId: "",
        telegramDeliveryError: "Delivery outcome could not be confirmed.",
        createdAt: "2026-07-28T09:00:00Z",
        reconcilable: true,
      },
    ],
    scrapeAnomalies: [
      {
        id: ANOMALY_ID,
        status: "pending",
        scraperRunId: SCRAPE_RUN_ID,
        configurationId: "22222222-2222-4222-8222-222222222222",
        datasetChecksum: "a".repeat(64),
        decisionBy: "",
        decisionReason: "",
        decidedAt: null,
        createdAt: "2026-07-28T02:00:00Z",
        runStatus: "held_anomaly",
        anomalousSegments: [
          { key: "roofing-austin-tx", reasons: ["more_than_50_percent_down"] },
        ],
        decidable: true,
      },
    ],
    sourceRecommendations: [
      {
        id: RECOMMENDATION_ID,
        niche: "Roofing",
        segment: "PA::roof repair",
        action: "reduce",
        status: "pending",
        evidence: {
          counts: { worked: 120, rated: 40 },
          analysis: { eligibility: "eligible", peerSegmentCount: 3 },
        },
        evidenceChecksum: EVIDENCE_CHECKSUM,
        configurationVersion: 4,
        decisionBy: "",
        decisionReason: "",
        decidedAt: null,
        createdAt: "2026-07-28T02:00:00Z",
        resultingConfigurationId: null,
        decidable: true,
      },
    ],
    nicheMappings: [
      {
        segment: "TX::roofing contractor",
        state: "TX",
        keyword: "roofing contractor",
        niche: "Roofing",
        confirmed: false,
        proposalSource: "ai_openrouter",
        evidence: { acquisitionChanged: false },
        confirmedBy: "",
        confirmedAt: null,
      },
    ],
    scraperConfigurations: [
      {
        id: CONFIGURATION_ID,
        version: 5,
        checksum: "c".repeat(64),
        status: "scheduled",
        reason: "Reduce underperforming segment",
        createdAt: "2026-07-27T08:00:00Z",
        scheduledAt: "2026-07-29T08:00:00Z",
        activatedAt: null,
        basedOnConfigurationId: "55555555-5555-4555-8555-555555555555",
        segmentCount: 2,
        anomalyThresholds: {},
      },
      {
        id: "55555555-5555-4555-8555-555555555555",
        version: 4,
        checksum: "d".repeat(64),
        status: "active",
        reason: "Baseline",
        createdAt: "2026-07-01T00:00:00Z",
        scheduledAt: null,
        activatedAt: "2026-07-01T08:00:00Z",
        basedOnConfigurationId: null,
        segmentCount: 2,
        anomalyThresholds: {},
      },
    ],
    ...overrides,
  };
}

export async function mockAcquisition(
  page: Page,
  overrides: Record<string, unknown> = {},
): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];

  await page.route(/\/api\/admin\/acquisition$/, (route) =>
    json(route, acquisitionPayload(overrides)),
  );

  await page.route(
    new RegExp(`/api/admin/scraper-configurations/${CONFIGURATION_ID}$`),
    (route) =>
      json(route, {
        id: CONFIGURATION_ID,
        version: 5,
        checksum: "c".repeat(64),
        status: "scheduled",
        createdBy: "admin@example.com",
        reason: "Reduce underperforming segment",
        anomalyThresholds: { downFraction: 0.5 },
        createdAt: "2026-07-27T08:00:00Z",
        scheduledAt: "2026-07-29T08:00:00Z",
        activatedAt: null,
        basedOnConfigurationId: null,
        segments: [
          {
            key: "roofing-austin-tx",
            niche: "Roofing",
            query: "roofing contractor",
            geography: "Austin, TX",
            parameters: {},
          },
        ],
      }),
  );

  await page.route(
    new RegExp(`/api/admin/scrape-runs/${SCRAPE_RUN_ID}$`),
    (route) =>
      json(route, {
        id: SCRAPE_RUN_ID,
        source: "google_maps",
        sourceVersion: "2026.07",
        configurationId: CONFIGURATION_ID,
        datasetVersion: null,
        manual: false,
        checksum: "a".repeat(64),
        status: "held_anomaly",
        rowsSeen: 120,
        rowsImported: 80,
        details: {},
        startedAt: "2026-07-28T02:00:00Z",
        finishedAt: "2026-07-28T02:08:00Z",
        segments: [
          {
            key: "roofing-austin-tx",
            niche: "Roofing",
            geography: "Austin, TX",
            observed: 120,
            valid: 80,
            new: 50,
            duplicates: 25,
            quarantined: 15,
            anomalous: true,
            anomalyReasons: ["more_than_50_percent_down"],
          },
        ],
      }),
  );

  for (const pattern of [
    /\/api\/admin\/scrape-anomalies\/[^/]+\/[^/]+$/,
    /\/api\/admin\/source-recommendations\/[^/]+\/[^/]+$/,
  ]) {
    await page.route(pattern, (route) => {
      calls.push({
        url: new URL(route.request().url()).pathname,
        body: (route.request().postDataJSON() ?? {}) as Record<
          string,
          unknown
        >,
      });
      return json(route, { ok: true, status: "confirmed" });
    });
  }

  return calls;
}
