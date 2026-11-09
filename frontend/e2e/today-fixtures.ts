import type { Page, Route } from "@playwright/test";

import { ANOMALY_ID, EXCLUSION_ID } from "./acquisition-fixtures";
import { CONFLICT_ID } from "./fulfillment-fixtures";
import { OPEN_REPORT_ID } from "./lead-report-fixtures";

/**
 * The operator's Today page, All batches, the Batch page, and global search.
 *
 * Figures match the approved renders (V9): Northstar Insurance's Batch #1042
 * is 25,000 FL leads, $125.00 held at $5.00 per 1,000, balance $412.50.
 * Action lists are the ones jawnix/fulfillment.py projects for each state.
 */

export interface RecordedCall {
  url: string;
  method: string;
  body: Record<string, unknown>;
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString();
}

function todayAt(hours: number, minutes = 0): string {
  const date = new Date();
  date.setHours(hours, minutes, 0, 0);
  return date.toISOString();
}

const ID = {
  1039: "10391039-1039-4039-8039-103910391039",
  1040: "10401040-1040-4040-8040-104010401040",
  1041: "10411041-1041-4041-8041-104110411041",
  1042: "10421042-1042-4042-8042-104210421042",
  1043: "10431043-1043-4043-8043-104310431043",
  1038: "10381038-1038-4038-8038-103810381038",
  1031: "10311031-1031-4031-8031-103110311031",
} as const;

export const BATCH_IDS = ID;

export const OPERATOR_NUMBERS = {
  generatedAt: new Date().toISOString(),
  windowDays: 30,
  inventory: {
    db1: { usable: 9_183_402, asOf: todayAt(6) },
    db2: { usable: 1_071_288, asOf: todayAt(6) },
  },
  openBatches: { total: 4, awaitingApproval: 1, shortOfInventory: 1 },
  delivered: { leads: 2_069_666, batches: 33, customers: 13 },
  customers: { active: 14, withSetupProblems: 1 },
  creditBalance: { availableCents: 841_250, billedCustomers: 11 },
  creditsUsed: { cents: 1_034_833, priorCents: 923_958 },
  decisions: {
    waiting: 7,
    oldestAt: hoursAgo(48),
    kinds: [
      { kind: "approval", label: "Approvals", count: 1, oldestAt: hoursAgo(2) },
      { kind: "failedBatch", label: "Failed batches", count: 1, oldestAt: hoursAgo(3) },
      { kind: "inventoryConflict", label: "Inventory conflicts", count: 1, oldestAt: hoursAgo(5) },
      { kind: "leadReport", label: "Lead reports", count: 1, oldestAt: hoursAgo(24) },
      { kind: "failedJob", label: "Failed jobs", count: 1, oldestAt: hoursAgo(6) },
      { kind: "scrapeAnomaly", label: "Scrape anomalies", count: 1, oldestAt: hoursAgo(4) },
      { kind: "nicheMapping", label: "Niche mappings", count: 0, oldestAt: null },
      { kind: "exclusionList", label: "Exclusion lists", count: 1, oldestAt: hoursAgo(48) },
      { kind: "registration", label: "Registrations", count: 0, oldestAt: null },
    ],
  },
};

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

interface RowSeed {
  number: keyof typeof ID;
  customer: [number, string];
  leadCount: number;
  states: string[];
  status: string;
  stage: string;
  availability: Record<string, unknown>;
  createdAt: string;
  deliveredAt?: string | null;
  needsDecision?: boolean;
  fileAt?: string | null;
}

function row(seed: RowSeed) {
  return {
    id: ID[seed.number],
    number: seed.number,
    href: `/app/admin/batches/${seed.number}`,
    customer: {
      id: seed.customer[0],
      name: seed.customer[1],
      href: `/app/admin/customers/${seed.customer[0]}`,
    },
    leadCount: seed.leadCount,
    states: seed.states,
    database: "db1",
    status: seed.status,
    stage: seed.stage,
    statusMessage: "",
    availability: seed.availability,
    needsDecision: seed.needsDecision ?? false,
    createdAt: seed.createdAt,
    deliveredAt: seed.deliveredAt ?? null,
    closedAt: null,
    fileAt: seed.fileAt ?? null,
  };
}

const ROWS = {
  1039: row({
    number: 1039,
    customer: [14, "Ridgeline Benefits"],
    leadCount: 100_000,
    states: ["AZ"],
    status: "waiting_inventory",
    stage: "waiting",
    availability: { kind: "short", available: 68_410, short: 31_590, asOf: null, source: "last_attempt" },
    createdAt: hoursAgo(72),
  }),
  1040: row({
    number: 1040,
    customer: [13, "Pinecrest Agency"],
    leadCount: 25_000,
    states: ["GA"],
    status: "failed",
    stage: "delivery_failed",
    availability: { kind: "committed", committed: 25_000 },
    createdAt: hoursAgo(3),
    needsDecision: true,
    fileAt: todayAt(9, 2),
  }),
  1041: row({
    number: 1041,
    customer: [7, "Harbor Insurance"],
    leadCount: 50_000,
    states: ["FL", "TX"],
    status: "processing",
    stage: "generating",
    availability: { kind: "committed", committed: 50_000 },
    createdAt: hoursAgo(0.34),
  }),
  1042: row({
    number: 1042,
    customer: [12, "Northstar Insurance"],
    leadCount: 25_000,
    states: ["FL"],
    status: "pending",
    stage: "awaiting_approval",
    availability: {
      kind: "fillable",
      available: 1_188_205,
      short: 0,
      asOf: todayAt(6),
      source: "customer_snapshot",
    },
    createdAt: hoursAgo(2),
    needsDecision: true,
  }),
  1043: row({
    number: 1043,
    customer: [8, "Summit Benefits Group"],
    leadCount: 100_000,
    states: ["PA", "OH"],
    status: "delivered",
    stage: "delivered",
    availability: { kind: "committed", committed: 100_000 },
    createdAt: hoursAgo(10),
    deliveredAt: todayAt(8, 40),
  }),
};

function decision(
  kind: string,
  label: string,
  recordId: string,
  subject: string,
  detail: string,
  hours: number,
  href: string,
  extra: Record<string, unknown> = {},
) {
  return {
    key: `${kind}:${recordId}`,
    kind,
    label,
    recordId,
    subject,
    detail,
    since: hoursAgo(hours),
    href,
    batch: null,
    facts: null,
    ...extra,
  };
}

export const DECISIONS = [
  decision(
    "exclusionList",
    "Exclusion list",
    EXCLUSION_ID,
    "Liberty Roofing",
    "DNC · 61 in pool",
    48,
    "/app/admin/acquisition",
    {
      facts: {
        id: EXCLUSION_ID,
        customerId: 21,
        customerName: "Liberty Roofing",
        type: "dnc",
        typeLabel: "DNC",
        filename: "liberty-dnc.csv",
        status: "pending_confirmation",
        acceptedRows: 248,
        invalidRows: 3,
        duplicateRows: 1,
        poolImpact: 61,
        createdAt: hoursAgo(48),
      },
    },
  ),
  decision(
    "leadReport",
    "Lead report",
    OPEN_REPORT_ID,
    "Gulfshore Advisors",
    "Wrong number · hold active",
    24,
    `/app/admin/fulfillment/reports/${OPEN_REPORT_ID}`,
  ),
  decision(
    "failedJob",
    "Failed job",
    "17",
    "Allocate #1039",
    "Failed after 2 attempts · Timed out",
    6,
    "/app/admin/batches/1039",
    {
      batch: null,
      facts: {
        job: 17,
        kind: "allocate_request",
        attempts: 2,
        lastError: "Timed out",
        affected: { label: "Open Batch Request", href: "/app/admin/batches/1039" },
      },
    },
  ),
  decision(
    "inventoryConflict",
    "Inventory conflict",
    CONFLICT_ID,
    "Ridgeline Benefits ↔ Harbor Insurance",
    "1,204 overlapping leads",
    5,
    `/app/admin/fulfillment/conflicts/${CONFLICT_ID}`,
  ),
  decision(
    "scrapeAnomaly",
    "Scrape anomaly",
    ANOMALY_ID,
    "Run 42",
    "roofing · TX · down more than 50%",
    4,
    "/app/admin/acquisition/runs/42",
    {
      facts: {
        runId: 42,
        segments: [{ key: "TX::roofing", label: "roofing · TX", reasons: ["down more than 50%"] }],
      },
    },
  ),
  decision(
    "deliveryFailed",
    "Delivery failed",
    ID[1040],
    "Pinecrest Agency",
    "Email provider returned 503 · 2 attempts",
    3,
    "/app/admin/batches/1040",
    { batch: { id: ID[1040], number: 1040 } },
  ),
  decision(
    "approval",
    "Approval",
    ID[1042],
    "Northstar Insurance",
    "25,000 leads · FL · fillable",
    2,
    "/app/admin/batches/1042",
    { batch: { id: ID[1042], number: 1042 } },
  ),
];

export const TODAY = {
  generatedAt: new Date().toISOString(),
  numbers: OPERATOR_NUMBERS,
  lastNight: {
    run: {
      id: 42,
      committedAt: todayAt(5, 12),
      syncStatus: "complete",
      href: "/app/admin/acquisition/runs/42",
    },
    newLeads: 18_420,
    heldSegments: 1,
    review: {
      id: "99999999-9999-4999-8999-999999999999",
      reviewDate: todayAt(0).slice(0, 10),
      status: "complete",
      telegram: "sent",
      telegramAt: todayAt(5, 40),
    },
  },
  decisions: { items: DECISIONS, total: DECISIONS.length, unavailable: [] },
  batches: {
    inProgress: [ROWS[1039], ROWS[1041]],
    deliveredToday: [ROWS[1040], ROWS[1043]],
    allBatches: 80,
  },
};

export const BATCH_LIST = {
  items: [ROWS[1039], ROWS[1040], ROWS[1041], ROWS[1042]],
  page: 1,
  pageSize: 25,
  total: 4,
  pages: 1,
  counts: { delivered: 67, expired: 3 },
};

function detail(number: keyof typeof ROWS, overrides: Record<string, unknown> = {}) {
  const base = ROWS[number];
  return {
    ...base,
    customer: "River Morgan",
    customerIdentity: base.customer.name,
    agency: "Lakeside Agency",
    email: "river@northstar.example",
    leadCount: base.leadCount,
    rowsPerFile: 10_000,
    stateMode: "all_saved",
    availableCount: null,
    actions: [],
    approvedAt: null,
    artifact: null,
    distribution: { committed: 0, expected: base.leadCount, complete: false, byState: [] },
    live: { settled: true, refreshSeconds: 10, blocker: null, jobs: [], log: [] },
    telegram: { decisionPending: false },
    history: [],
    customerRecord: base.customer,
    agencyRecord: { id: 3, name: "Lakeside Agency", href: "/app/admin/agencies/3" },
    fill: { needed: base.leadCount, ...base.availability, olderOpen: null },
    cost: { billed: false },
    progress: [
      { key: "submitted", label: "Submitted", done: true, at: base.createdAt },
      { key: "approved", label: "Approved", done: false, at: null },
      { key: "allocated", label: "Allocated", done: false, at: null },
      { key: "generated", label: "File generated", done: false, at: null },
      { key: "delivered", label: "Delivered", done: false, at: null },
    ],
    recentBatches: [],
    openLeadReports: 0,
    ...overrides,
  };
}

export const BATCH_DETAILS: Record<string, Record<string, unknown>> = {
  1042: detail(1042, {
    actions: [APPROVE, REJECT, CANCEL],
    telegram: { decisionPending: true },
    fill: {
      needed: 25_000,
      ...ROWS[1042].availability,
      olderOpen: {
        count: 1,
        first: { number: 1039, customer: "Ridgeline Benefits", stage: "waiting" },
      },
    },
    cost: {
      billed: true,
      rateCentsPerThousand: 500,
      amountCents: 12_500,
      hold: "active",
      availableBalanceCents: 41_250,
    },
    history: [
      {
        action: "batch_request_notify",
        actor: "system:worker",
        reason: "Held $125.00 · sent Telegram approval prompt",
        recordedAt: ROWS[1042].createdAt,
        before: null,
        after: null,
      },
    ],
    recentBatches: [
      {
        id: ID[1038],
        number: 1038,
        href: "/app/admin/batches/1038",
        leadCount: 50_000,
        stage: "delivered",
        createdAt: hoursAgo(60),
        deliveredAt: hoursAgo(48),
      },
      {
        id: ID[1031],
        number: 1031,
        href: "/app/admin/batches/1031",
        leadCount: 100_000,
        stage: "file_expired",
        createdAt: hoursAgo(200),
        deliveredAt: hoursAgo(196),
      },
    ],
  }),
  1040: detail(1040, {
    customer: "Pat Lee",
    email: "pat@pinecrest.example",
    agencyRecord: null,
    actions: [RETRY_DELIVERY],
    statusMessage:
      "Portal notification failed. The existing Batch Artifact is preserved for retry.",
    artifact: {
      filename: "pinecrest_1040.csv",
      rowCount: 25_000,
      parts: [],
      sha256: "b".repeat(64),
      deliveryStatus: "failed",
      deliveryAttempts: 2,
      lastError: "Email provider returned 503",
      expiresAt: hoursAgo(-24 * 29),
      sentAt: null,
      available: true,
    },
    distribution: {
      committed: 25_000,
      expected: 25_000,
      complete: true,
      byState: [{ state: "GA", niche: "Dental", count: 25_000 }],
    },
    progress: [
      { key: "submitted", label: "Submitted", done: true, at: hoursAgo(3) },
      { key: "approved", label: "Approved", done: true, at: hoursAgo(2.9) },
      { key: "allocated", label: "Allocated", done: true, at: hoursAgo(2.8) },
      { key: "generated", label: "File generated", done: true, at: todayAt(9, 2) },
      { key: "ended", label: "Delivery failed", done: true, at: todayAt(9, 5) },
    ],
  }),
  1043: detail(1043, {
    actions: [REGENERATE],
    approvedAt: hoursAgo(9),
    deliveredAt: todayAt(8, 40),
  }),
  1039: detail(1039),
  1041: detail(1041),
};

export const SEARCH_RESULTS: Record<string, unknown> = {
  "1042": {
    query: "1042",
    groups: [
      {
        key: "batches",
        label: "Batches",
        results: [
          {
            kind: "batch",
            id: ID[1042],
            title: "Batch #1042 · Northstar Insurance",
            detail: "25,000 leads · Awaiting approval · Nov 9",
            href: "/app/admin/batches/1042",
          },
        ],
      },
    ],
  },
  north: {
    query: "north",
    groups: [
      {
        key: "customers",
        label: "Customers",
        results: [
          {
            kind: "customer",
            id: "12",
            title: "Northstar Insurance",
            detail: "northstar · river@northstar.example",
            href: "/app/admin/customers/12",
          },
          {
            kind: "customer",
            id: "15",
            title: "Northwind Benefits",
            detail: "northwind · Deactivated",
            href: "/app/admin/customers/15",
          },
        ],
      },
      {
        key: "uploads",
        label: "DB2 uploads",
        results: [
          {
            kind: "upload",
            id: "u1",
            title: "north-carolina-agents.csv",
            detail: "Alex · NC · Nov 6 · Published",
            href: "/app/admin/lead-uploads",
          },
        ],
      },
    ],
  },
};

export const CONFLICT_DETAIL = {
  id: CONFLICT_ID,
  status: "pending",
  olderRequest: {
    id: ID[1039],
    number: 1039,
    customerIdentity: "Ridgeline Benefits",
    agency: "Lakeside Agency",
    leadCount: 100_000,
    states: ["AZ"],
    status: "waiting_inventory",
    createdAt: hoursAgo(72),
    eligibleCount: 68_410,
    available: true,
  },
  newerRequest: {
    id: ID[1041],
    number: 1041,
    customerIdentity: "Harbor Insurance",
    agency: "Gulf Coast Agency",
    leadCount: 50_000,
    states: ["FL", "TX"],
    status: "waiting_inventory",
    createdAt: hoursAgo(6),
    eligibleCount: 412_880,
    available: true,
  },
  overlappingLeadCount: 1_204,
  snapshotChecksum: "a".repeat(64),
  recurrenceRule:
    "A decision binds to this inventory snapshot alone. Denial or silence keeps the newer Batch Request waiting, and the conflict may recur only after a material change.",
  decisionBy: "",
  decisionReason: "",
  decidedAt: null,
  consumedAt: null,
  createdAt: hoursAgo(5),
  actions: [
    {
      name: "confirm",
      label: "Confirm once",
      consequence:
        "Authorizes one allocation attempt for this exact inventory snapshot. The authorization is spent by that attempt whether or not it succeeds.",
      requiresReason: true,
      destructive: false,
    },
    {
      name: "deny",
      label: "Deny",
      consequence:
        "Leaves the newer Batch Request waiting. This snapshot will not be reconsidered; the conflict can only recur after a material change.",
      requiresReason: true,
      destructive: true,
    },
  ],
};

/** An empty answer for every search, so the header box works on any page. */
export async function mockEmptySearch(page: Page) {
  await page.route(/\/api\/admin\/search\?/, (route) => {
    const query = new URL(route.request().url()).searchParams.get("q") ?? "";
    return json(route, { query, groups: [] });
  });
}

/** Today, All batches, the Batch page, global search, and their actions. */
export async function mockOperatorDesk(
  page: Page,
  { today = TODAY }: { today?: unknown } = {},
): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];
  function record(route: Route) {
    const request = route.request();
    calls.push({
      url: new URL(request.url()).pathname,
      method: request.method(),
      body: (request.postDataJSON() as Record<string, unknown>) ?? {},
    });
  }

  await page.route(/\/api\/admin\/today(\?.*)?$/, (route) => json(route, today));

  await page.route(/\/api\/admin\/batches(\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    // One Customer's batches belong to the Customers fixtures.
    if (url.searchParams.has("customer")) return route.fallback();
    const needs = url.searchParams.get("needsDecision") === "true";
    const status = url.searchParams.get("status") ?? "open";
    const items = BATCH_LIST.items.filter(
      (item) =>
        (!needs || item.needsDecision) &&
        (status === "open" || status === "all" || item.stage === status),
    );
    return json(route, { ...BATCH_LIST, items, total: items.length });
  });

  await page.route(/\/api\/admin\/batches\/[^/?]+$/, (route) => {
    const reference = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    const match =
      BATCH_DETAILS[reference] ??
      Object.values(BATCH_DETAILS).find((item) => item.id === reference);
    // Unknown here: let an earlier fixture (Fulfillment's) answer.
    return match ? json(route, match) : route.fallback();
  });

  await page.route(/\/api\/admin\/requests\/[^/]+\/[^/]+$/, (route) => {
    record(route);
    return json(route, { ok: true });
  });

  await page.route(/\/api\/admin\/inventory-conflicts\/[^/]+(\/[^/]+)?$/, (route) => {
    if (route.request().method() === "GET") return json(route, CONFLICT_DETAIL);
    record(route);
    return json(route, { conflictId: CONFLICT_ID, status: "confirmed" });
  });

  await page.route(/\/api\/admin\/(scrape-anomalies|exclusion-lists|source-niches)\/.+$/, (route) => {
    record(route);
    return json(route, { ok: true });
  });

  await page.route(/\/api\/admin\/search\?/, (route) => {
    const query = (new URL(route.request().url()).searchParams.get("q") ?? "").trim().toLowerCase();
    return json(route, SEARCH_RESULTS[query] ?? { query, groups: [] });
  });

  return calls;
}
