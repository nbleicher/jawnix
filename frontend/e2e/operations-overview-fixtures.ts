import type { Page, Route } from "@playwright/test";

import {
  CONFLICT_ID,
  FAILED_WITH_ARTIFACT_ID,
  FAILED_WITHOUT_ARTIFACT_ID,
  PENDING_REQUEST_ID,
} from "./fulfillment-fixtures";
import { ANOMALY_ID } from "./acquisition-fixtures";
import { OPEN_REPORT_ID } from "./lead-report-fixtures";

function json(route: Route, value: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

interface OverviewItem {
  id: string;
  title: string;
  summary: string;
  status: string;
  tone: "neutral" | "info" | "success" | "warning" | "danger";
  nextAction: string;
  recordedAt: string;
  action: { label: string; href: string };
}

interface OverviewQueue {
  key: string;
  title: string;
  description: string;
  count: number;
  items: OverviewItem[];
  emptyTitle: string;
  emptyDescription: string;
}

interface OverviewSource {
  key: string;
  title: string;
  description: string;
  status: "available" | "unavailable";
  count: number | null;
  queues: OverviewQueue[];
  workspace: { label: string; href: string };
  errorTitle: string | null;
  errorDescription: string | null;
}

interface OperationsOverviewPayload {
  generatedAt: string;
  availableCount: number;
  degraded: boolean;
  sources: OverviewSource[];
}

function item(
  id: string,
  title: string,
  summary: string,
  status: string,
  nextAction: string,
  label: string,
  href: string,
): OverviewItem {
  return {
    id,
    title,
    summary,
    status,
    tone: status.toLowerCase().includes("fail") ? "danger" : "warning",
    nextAction,
    recordedAt: "2026-07-29T10:00:00Z",
    action: { label, href },
  };
}

function queue(
  key: string,
  title: string,
  description: string,
  work: OverviewItem[],
): OverviewQueue {
  return {
    key,
    title,
    description,
    count: work.length,
    items: work,
    emptyTitle: `No ${title} need attention`,
    emptyDescription: `New ${title} work will appear here.`,
  };
}

export const OPERATIONS_OVERVIEW: OperationsOverviewPayload = {
  generatedAt: "2026-07-29T10:05:00Z",
  availableCount: 8,
  degraded: false,
  sources: [
    {
      key: "fulfillment",
      title: "Fulfillment",
      description: "Approval, allocation, eligibility, and delivery recovery work.",
      status: "available",
      count: 5,
      queues: [
        queue(
          "batchRequests",
          "Pending Batch Requests",
          "Outstanding Customer requests.",
          [
            item(
              PENDING_REQUEST_ID,
              "Northstar Insurance",
              "250 Leads · TX. Awaiting approval.",
              "Pending",
              "Approve",
              "Open Batch Request",
              `/app/admin/fulfillment/requests/${PENDING_REQUEST_ID}`,
            ),
          ],
        ),
        queue(
          "inventoryConflicts",
          "Inventory Conflicts",
          "Overlapping-inventory decisions.",
          [
            item(
              CONFLICT_ID,
              "Older Customer ↔ Newer Customer",
              "1 overlapping eligible Lead.",
              "Awaiting decision",
              "Confirm once or deny",
              "Decide Inventory Conflict",
              `/app/admin/fulfillment/conflicts/${CONFLICT_ID}`,
            ),
          ],
        ),
        queue(
          "leadReports",
          "Lead Reports",
          "Immutable Customer evidence.",
          [
            item(
              OPEN_REPORT_ID,
              "Wrong number report",
              "Filed by Harbor Insurance. Eligibility Hold active.",
              "Open",
              "Resolve Lead Report",
              "Review Lead Report",
              `/app/admin/fulfillment/reports/${OPEN_REPORT_ID}`,
            ),
          ],
        ),
        queue(
          "eligibilityHolds",
          "Eligibility Holds",
          "Leads withheld from allocation.",
          [
            item(
              "hold-1",
              "Lead 2155550199",
              "Invalid phone. Resolving the related report releases this hold.",
              "Eligibility held",
              "Resolve Lead Report",
              "Review Eligibility Hold",
              `/app/admin/fulfillment/reports/${OPEN_REPORT_ID}`,
            ),
          ],
        ),
        queue(
          "deliveryFailures",
          "Delivery failures",
          "Generated batches that did not reach their Customer.",
          [
            item(
              FAILED_WITH_ARTIFACT_ID,
              "Gulfshore Advisors",
              "Provider returned HTTP 503. 2 delivery attempts.",
              "Delivery failed",
              "Retry notification",
              "Recover Delivery",
              `/app/admin/fulfillment/requests/${FAILED_WITH_ARTIFACT_ID}`,
            ),
          ],
        ),
      ],
      workspace: {
        label: "Open Fulfillment",
        href: "/app/admin/fulfillment",
      },
      errorTitle: null,
      errorDescription: null,
    },
    {
      key: "backgroundJobs",
      title: "Background jobs",
      description: "Worker failures whose affected records need checking.",
      status: "available",
      count: 1,
      queues: [
        queue("failedJobs", "Failed jobs", "Background work that stopped.", [
          item(
            "job-17",
            "Allocate request · Job 17",
            "Failed after 2 attempts. Review the affected record.",
            "Failed",
            "Review affected record",
            "Open Batch Request",
            `/app/admin/fulfillment/requests/${FAILED_WITHOUT_ARTIFACT_ID}`,
          ),
        ]),
      ],
      workspace: {
        label: "Review Activity",
        href: "/app/admin/activity",
      },
      errorTitle: null,
      errorDescription: null,
    },
    {
      key: "acquisition",
      title: "Acquisition",
      description: "Held publication decisions and Nightly Review recovery.",
      status: "available",
      count: 2,
      queues: [
        queue(
          "scrapeAnomalies",
          "Scrape Anomalies",
          "Staged output held before publication.",
          [
            item(
              ANOMALY_ID,
              "Scrape Run 42",
              "1 anomalous Source Segment held before publication.",
              "Awaiting decision",
              "Confirm or deny held output",
              "Review Scrape Anomaly",
              "/app/admin/acquisition#held-scrape-anomalies",
            ),
          ],
        ),
        queue(
          "nightlyReviews",
          "Nightly Reviews",
          "Durable nightly summaries needing recovery.",
          [
            item(
              "review-29",
              "2026-07-29",
              "Telegram delivery outcome could not be confirmed.",
              "Delivery unknown",
              "Review delivery evidence",
              "Review Nightly Review",
              "/app/admin/acquisition#nightly-reviews",
            ),
          ],
        ),
      ],
      workspace: {
        label: "Open Acquisition",
        href: "/app/admin/acquisition",
      },
      errorTitle: null,
      errorDescription: null,
    },
  ],
};

type SourceKey = "fulfillment" | "backgroundJobs" | "acquisition";

export async function mockOperationsOverview(
  page: Page,
  options: {
    unavailable?: SourceKey;
    empty?: boolean;
  } = {},
) {
  const payload = structuredClone(OPERATIONS_OVERVIEW);
  if (options.empty) {
    payload.availableCount = 0;
    for (const source of payload.sources) {
      source.count = 0;
      for (const workQueue of source.queues) {
        workQueue.count = 0;
        workQueue.items = [];
      }
    }
  }
  if (options.unavailable) {
    const source = payload.sources.find(
      (candidate) => candidate.key === options.unavailable,
    );
    if (source) {
      payload.degraded = true;
      payload.availableCount -= source.count ?? 0;
      source.status = "unavailable";
      source.count = null;
      source.queues = [];
      source.errorTitle = `${source.title} work is temporarily unavailable`;
      source.errorDescription =
        "Only this section could not be refreshed. The other Operations sections remain usable.";
    }
  }
  await page.route(/\/api\/admin\/operations-overview$/, (route) =>
    json(route, payload),
  );
}
