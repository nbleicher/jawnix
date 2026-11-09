import type { Page, Route } from "@playwright/test";

import { LEAD_REPORT_SECTIONS } from "./lead-report-fixtures";

/**
 * A seeded Fulfillment workspace.
 *
 * The action lists here are the ones jawnix/fulfillment.py projects for each
 * state, so the browser tests exercise the same offer surface the backend
 * produces rather than a hand-written approximation of it.
 */

export const PENDING_REQUEST_ID = "11111111-1111-4111-8111-111111111111";
export const FAILED_WITH_ARTIFACT_ID = "22222222-2222-4222-8222-222222222222";
export const FAILED_WITHOUT_ARTIFACT_ID = "33333333-3333-4333-8333-333333333333";
export const DELIVERED_REQUEST_ID = "44444444-4444-4444-8444-444444444444";
export const CONFLICT_ID = "55555555-5555-4555-8555-555555555555";
export const EXPIRED_ARTIFACT_ID = "88888888-8888-4888-8888-888888888888";

export interface RecordedCall {
  url: string;
  reason: string;
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

const APPROVE = {
  name: "approve",
  label: "Approve",
  consequence:
    "Authorizes this Batch Request's first allocation attempt and queues it for Fulfillment Rotation.",
  requiresReason: true,
  destructive: false,
};

const REJECT = {
  name: "reject",
  label: "Reject",
  consequence:
    "Refuses this Batch Request. Rejection is terminal and the Customer must submit a new request.",
  requiresReason: true,
  destructive: true,
};

const CANCEL = {
  name: "cancel",
  label: "Cancel",
  consequence:
    "Withdraws this Batch Request before any Distribution Event commits. Cancellation is terminal.",
  requiresReason: true,
  destructive: true,
};

const RETRY = {
  name: "retry",
  label: "Retry generation",
  consequence:
    "Returns this Batch Request to the approved queue and runs allocation again. It may select different Leads than a previous attempt.",
  requiresReason: true,
  destructive: false,
};

const RETRY_DELIVERY = {
  name: "retry_delivery",
  label: "Retry notification",
  consequence:
    "Emails another portal notification for the exact existing Batch Artifact. Nothing is reallocated and the delivered Leads do not change.",
  requiresReason: true,
  destructive: false,
};

const REGENERATE = {
  name: "regenerate",
  label: "Regenerate artifact",
  consequence:
    "Rebuilds the exact expired CSV from its committed Distribution Events, starting a new 30-day retention period. The file's contents and checksum are unchanged.",
  requiresReason: true,
  destructive: false,
};

const CONFIRM_CONFLICT = {
  name: "confirm",
  label: "Confirm once",
  consequence:
    "Authorizes one allocation attempt for this exact inventory snapshot. The authorization is spent by that attempt whether or not it succeeds.",
  requiresReason: true,
  destructive: false,
};

const DENY_CONFLICT = {
  name: "deny",
  label: "Deny",
  consequence:
    "Leaves the newer Batch Request waiting. This snapshot will not be reconsidered; the conflict can only recur after a material change.",
  requiresReason: true,
  destructive: true,
};

function requestSummary(overrides: Record<string, unknown> = {}) {
  return {
    id: PENDING_REQUEST_ID,
    number: 1042,
    customer: "Dana Reed",
    customerIdentity: "Northstar Insurance",
    agency: "Northstar Agency",
    email: "dana@example.com",
    leadCount: 250,
    states: ["TX"],
    stateMode: "all_saved",
    status: "pending",
    statusMessage: "Awaiting approval.",
    availableCount: null,
    createdAt: "2026-07-01T10:00:00Z",
    actions: [APPROVE, REJECT, CANCEL],
    ...overrides,
  };
}

const CONFLICT = {
  id: CONFLICT_ID,
  status: "pending",
  olderRequest: {
    id: "66666666-6666-4666-8666-666666666666",
    number: 1036,
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
    id: "77777777-7777-4777-8777-777777777777",
    number: 1037,
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
  actions: [CONFIRM_CONFLICT, DENY_CONFLICT],
};

const DETAILS: Record<string, Record<string, unknown>> = {
  [PENDING_REQUEST_ID]: {
    ...requestSummary(),
    approvedAt: null,
    deliveredAt: null,
    artifact: null,
    distribution: { committed: 0, expected: 250, complete: false },
    telegram: { decisionPending: true },
    history: [
      {
        action: "batch_request_approve",
        actor: "telegram:12345",
        reason: "Telegram Batch Request decision",
        recordedAt: "2026-07-01T11:00:00Z",
        before: { status: "pending" },
        after: { status: "approved" },
      },
    ],
  },
  [FAILED_WITH_ARTIFACT_ID]: {
    ...requestSummary({
      id: FAILED_WITH_ARTIFACT_ID,
      number: 1040,
      customerIdentity: "Gulfshore Advisors",
      status: "failed",
      statusMessage:
        "Portal notification failed. The existing Batch Artifact is preserved for retry.",
      actions: [RETRY_DELIVERY],
    }),
    approvedAt: "2026-07-01T11:00:00Z",
    deliveredAt: null,
    artifact: {
      filename: "batch.csv",
      rowCount: 250,
      sha256: "b".repeat(64),
      deliveryStatus: "failed",
      deliveryAttempts: 2,
      lastError: "Provider returned HTTP 503",
      expiresAt: "2026-08-01T00:00:00Z",
      sentAt: null,
      available: true,
    },
    distribution: { committed: 250, expected: 250, complete: true },
    telegram: { decisionPending: false },
    history: [],
  },
  [FAILED_WITHOUT_ARTIFACT_ID]: {
    ...requestSummary({
      id: FAILED_WITHOUT_ARTIFACT_ID,
      number: 1044,
      customerIdentity: "Lakeside Group",
      status: "failed",
      statusMessage: "Allocation failed before a batch was generated.",
      actions: [RETRY],
    }),
    approvedAt: "2026-07-01T11:00:00Z",
    deliveredAt: null,
    artifact: null,
    distribution: { committed: 0, expected: 250, complete: false },
    telegram: { decisionPending: false },
    history: [],
  },
  [EXPIRED_ARTIFACT_ID]: {
    ...requestSummary({
      id: EXPIRED_ARTIFACT_ID,
      number: 1031,
      customerIdentity: "Harbor Point",
      status: "delivered",
      statusMessage:
        "Batch available in the portal; notification emailed to dana@example.com.",
      actions: [REGENERATE],
    }),
    approvedAt: "2026-07-01T11:00:00Z",
    deliveredAt: "2026-07-01T12:00:00Z",
    artifact: {
      filename: "batch.csv",
      rowCount: 250,
      sha256: "d".repeat(64),
      deliveryStatus: "delivered",
      deliveryAttempts: 1,
      lastError: "",
      expiresAt: "2026-06-01T00:00:00Z",
      sentAt: "2026-07-01T12:00:00Z",
      available: false,
    },
    distribution: { committed: 250, expected: 250, complete: true },
    telegram: { decisionPending: false },
    history: [],
  },
  [DELIVERED_REQUEST_ID]: {
    ...requestSummary({
      id: DELIVERED_REQUEST_ID,
      number: 1043,
      customerIdentity: "Harbor Point",
      status: "delivered",
      statusMessage:
        "Batch available in the portal; notification emailed to dana@example.com.",
      actions: [],
    }),
    approvedAt: "2026-07-01T11:00:00Z",
    deliveredAt: "2026-07-01T12:00:00Z",
    artifact: {
      filename: "batch.csv",
      rowCount: 250,
      sha256: "c".repeat(64),
      deliveryStatus: "delivered",
      deliveryAttempts: 1,
      lastError: "",
      expiresAt: "2026-08-01T00:00:00Z",
      sentAt: "2026-07-01T12:00:00Z",
      available: true,
    },
    distribution: { committed: 250, expected: 250, complete: true },
    telegram: { decisionPending: false },
    history: [],
  },
};

function stageOf(detail: Record<string, unknown>): string {
  const artifact = detail.artifact as { available?: boolean } | null;
  switch (detail.status) {
    case "pending":
      return "awaiting_approval";
    case "failed":
      return artifact ? "delivery_failed" : "failed";
    case "delivered":
      return artifact && artifact.available === false ? "file_expired" : "delivered";
    default:
      return String(detail.status);
  }
}

/** The Batch page contract (GET /api/admin/batches/{number or id}) for one of
 *  the Batch Requests above. */
export function asBatch(detail: Record<string, unknown>) {
  const artifact = detail.artifact as { rowCount: number } | null;
  return {
    ...detail,
    href: `/app/admin/batches/${detail.number}`,
    stage: stageOf(detail),
    needsDecision: detail.status === "pending" || detail.status === "failed",
    database: "db1",
    customerRecord: {
      id: 7,
      name: detail.customerIdentity,
      href: "/app/admin/customers/7",
    },
    agencyRecord: { id: 3, name: detail.agency, href: "/app/admin/agencies/3" },
    fill: artifact
      ? { needed: detail.leadCount, kind: "committed", committed: artifact.rowCount, olderOpen: null }
      : { needed: detail.leadCount, kind: "unknown", olderOpen: null },
    cost: { billed: false },
    progress: [
      { key: "submitted", label: "Submitted", done: true, at: detail.createdAt },
      { key: "approved", label: "Approved", done: Boolean(detail.approvedAt), at: detail.approvedAt ?? null },
      { key: "allocated", label: "Allocated", done: Boolean(artifact), at: null },
      { key: "generated", label: "File generated", done: Boolean(artifact), at: null },
      { key: "delivered", label: "Delivered", done: Boolean(detail.deliveredAt), at: detail.deliveredAt ?? null },
    ],
    recentBatches: [],
    openLeadReports: 0,
    live: detail.live ?? { settled: true, refreshSeconds: 10, jobs: [], log: [] },
  };
}

export function batchDetailFor(reference: string): Record<string, unknown> | null {
  const detail =
    DETAILS[reference] ??
    Object.values(DETAILS).find((item) => String(item.number) === reference);
  return detail ? asBatch(detail) : null;
}

export async function mockFulfillment(page: Page): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];

  await page.route(/\/api\/admin\/fulfillment$/, (route) =>
    json(route, {
      // #58 added the Lead Report sections to this same aggregate. They live in
      // the Lead Report fixture so the two cannot describe different worlds.
      ...LEAD_REPORT_SECTIONS,
      batchRequests: [
        requestSummary(),
        DETAILS[FAILED_WITH_ARTIFACT_ID],
        DETAILS[FAILED_WITHOUT_ARTIFACT_ID],
      ],
      inventoryConflicts: [CONFLICT],
      expiredArtifacts: [
        requestSummary({
          id: EXPIRED_ARTIFACT_ID,
          customerIdentity: "Harbor Point",
          status: "delivered",
          statusMessage:
            "Batch available in the portal; notification emailed to dana@example.com.",
          actions: [REGENERATE],
        }),
      ],
      deliveryFailures: [
        {
          ...requestSummary({
            id: FAILED_WITH_ARTIFACT_ID,
            customerIdentity: "Gulfshore Advisors",
            status: "failed",
            actions: [RETRY_DELIVERY],
          }),
          lastError: "Provider returned HTTP 503",
          deliveryAttempts: 2,
          deliveryStatus: "failed",
        },
      ],
    }),
  );

  let poolBreakdown = {
    asOf: "2026-08-03T12:00:00Z",
    cells: [
      { state: "FL", niche: "Dental", count: 120 },
      { state: "TX", niche: "Dental", count: 340 },
      { state: "TX", niche: "unmapped", count: 18 },
    ],
  };

  await page.route(/\/api\/admin\/pool-breakdown(?:\/refresh)?$/, (route) => {
    if (route.request().method() === "POST") {
      // The server only queues the background recount and answers with the
      // current snapshot. The next read stands in for the finished recount.
      const current = poolBreakdown;
      poolBreakdown = {
        asOf: "2026-08-03T15:00:00Z",
        cells: [
          { state: "FL", niche: "Dental", count: 121 },
          { state: "TX", niche: "Dental", count: 341 },
          { state: "TX", niche: "unmapped", count: 18 },
        ],
      };
      return json(route, { ...current, refreshQueued: true, jobId: 41 });
    }
    return json(route, poolBreakdown);
  });

  await page.route(/\/api\/admin\/inventory-conflicts\/[^/]+$/, (route) => {
    if (route.request().method() === "GET") {
      return json(route, CONFLICT);
    }
    calls.push({
      url: new URL(route.request().url()).pathname,
      reason:
        (route.request().postDataJSON() as { reason?: string })?.reason ?? "",
    });
    return json(route, { conflictId: CONFLICT_ID, status: "denied" });
  });

  await page.route(
    /\/api\/admin\/requests\/[^/]+(\/.*)?$/,
    (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "GET") {
        const id = path.split("/").pop() ?? "";
        const detail = DETAILS[id];
        if (!detail) {
          return json(route, { detail: "Request was not found." }, 404);
        }
        return json(route, detail);
      }
      calls.push({
        url: path,
        reason:
          (request.postDataJSON() as { reason?: string })?.reason ?? "",
      });
      return json(route, { ok: true });
    },
  );

  await page.route(/\/api\/admin\/batches\/[^/?]+$/, (route) => {
    const reference = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    const batch = batchDetailFor(reference);
    return batch
      ? json(route, batch)
      : json(route, { detail: "Batch was not found." }, 404);
  });

  await page.route(/\/api\/admin\/activity(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("entityType") !== "batch_request") {
      return route.fallback();
    }
    const id = url.searchParams.get("entityId") ?? "";
    const detail = DETAILS[id];
    const history = (detail?.history ?? []) as {
      action: string;
      actor: string;
      reason: string;
      recordedAt: string;
      before: Record<string, unknown> | null;
      after: Record<string, unknown> | null;
    }[];
    return json(route, {
      entries: history.map((entry, index) => ({
        id: `activity-${id}-${index}`,
        action: entry.action,
        entityType: "batch_request",
        entityId: id,
        entityHref: `/app/admin/batches/${id}`,
        actor: entry.actor,
        reason: entry.reason,
        details: {
          before: entry.before,
          after: entry.after,
        },
        recordedAt: entry.recordedAt,
      })),
      page: 1,
      pageSize: 25,
      total: history.length,
      pages: 1,
    });
  });

  return calls;
}
