import type { Page, Route } from "@playwright/test";

/**
 * A seeded Activity log. Shapes mirror `query_activity` and
 * `activity_filters` in jawnix/activity.py.
 */

export interface ActivityMockState {
  requests: string[];
}

function json(route: Route, value: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

const OPERATOR = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RIVER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const CASEY = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const NAMES: Record<string, string> = {
  [OPERATOR]: "Operator",
  [RIVER]: "River Morgan",
  [CASEY]: "Casey Reyes",
};

interface Seed {
  at: string;
  actor: string;
  action: string;
  actionLabel: string;
  entityType: string;
  entityTypeLabel: string;
  entityId: string;
  entityLabel: string;
  entityHref: string;
  reason: string;
  details?: Record<string, unknown>;
}

const SEEDS: Seed[] = [
  { at: "2026-11-09T11:20:00", actor: "system:delivery", action: "batch_request_delivery_failed", actionLabel: "Delivery failed", entityType: "batch_request", entityTypeLabel: "Batch", entityId: "1040", entityLabel: "Batch #1040", entityHref: "/app/admin/batches/1040", reason: "Email provider returned 503" },
  { at: "2026-11-09T09:41:00", actor: RIVER, action: "batch_request_submitted", actionLabel: "Batch requested", entityType: "batch_request", entityTypeLabel: "Batch", entityId: "1042", entityLabel: "Batch #1042", entityHref: "/app/admin/batches/1042", reason: "25,000 leads · FL" },
  { at: "2026-11-09T09:30:00", actor: "system:stripe", action: "credit_purchase_settled", actionLabel: "Credits bought", entityType: "customer", entityTypeLabel: "Customer", entityId: "7", entityLabel: "Northstar Insurance", entityHref: "/app/admin/customers/7", reason: "$500.00 through Stripe" },
  { at: "2026-11-09T08:02:00", actor: CASEY, action: "batch_request_submitted", actionLabel: "Batch requested", entityType: "batch_request", entityTypeLabel: "Batch", entityId: "1041", entityLabel: "Batch #1041", entityHref: "/app/admin/batches/1041", reason: "50,000 leads · FL, TX" },
  { at: "2026-11-09T05:40:00", actor: "system:nightly-scheduler", action: "nightly_review_sent", actionLabel: "Nightly review sent", entityType: "nightly_review", entityTypeLabel: "Nightly review", entityId: "66666666-6666-4666-8666-666666666666", entityLabel: "Nightly review Nov 9", entityHref: "/app/admin/insights#nightly-reviews", reason: "Telegram delivered" },
  { at: "2026-11-09T05:12:00", actor: "system:inventory_sync", action: "scrape_run_committed", actionLabel: "Scrape run committed", entityType: "scrape_run", entityTypeLabel: "Scrape run", entityId: "42", entityLabel: "Run 42", entityHref: "/app/admin/acquisition/runs/42", reason: "1 segment held" },
  { at: "2026-11-08T17:03:00", actor: OPERATOR, action: "customer_billing_updated", actionLabel: "Billing configured", entityType: "customer", entityTypeLabel: "Customer", entityId: "8", entityLabel: "Pinecrest Agency", entityHref: "/app/admin/customers/8", reason: "$5.00 per 1,000", details: { before: { billing_enabled: false }, after: { billing_enabled: true, lead_rate_cents_per_thousand: 500 } } },
  { at: "2026-11-08T16:44:00", actor: OPERATOR, action: "customer_inventory_scope_changed", actionLabel: "Lead database changed", entityType: "customer", entityTypeLabel: "Customer", entityId: "7", entityLabel: "Keystone Medicare", entityHref: "/app/admin/customers/7", reason: "DB1 → DB2", details: { before: { lead_database: "db1" }, after: { lead_database: "db2" } } },
  { at: "2026-11-08T12:30:00", actor: "telegram:12345", action: "batch_request_approve", actionLabel: "Batch approved", entityType: "batch_request", entityTypeLabel: "Batch", entityId: "1039", entityLabel: "Batch #1039", entityHref: "/app/admin/batches/1039", reason: "Approved from Telegram", details: { before: { status: "pending" }, after: { status: "approved" } } },
  { at: "2026-11-07T18:22:00", actor: OPERATOR, action: "customer_hard_delete_refused", actionLabel: "Refused: permanent deletion", entityType: "customer", entityTypeLabel: "Customer", entityId: "11", entityLabel: "Lakeside Brokers", entityHref: "/app/admin/customers/11", reason: "Customer has history; deactivate first" },
  { at: "2026-11-07T08:12:00", actor: "system:delivery", action: "batch_request_delivered", actionLabel: "Batch delivered", entityType: "batch_request", entityTypeLabel: "Batch", entityId: "1038", entityLabel: "Batch #1038", entityHref: "/app/admin/batches/1038", reason: "50,000 leads to river@northstar.example" },
  { at: "2026-11-06T15:28:00", actor: "system:lead-uploads", action: "operator_lead_upload_published", actionLabel: "DB2 upload published", entityType: "lead_upload", entityTypeLabel: "DB2 upload", entityId: "99999999-9999-4999-8999-999999999999", entityLabel: "tn-agents-nov.csv", entityHref: "/app/admin/lead-uploads", reason: "210,044 added · 812 rejected" },
];

export const ACTIVITY_ENTRIES = [
  ...SEEDS,
  // Older agency reviews, enough to page through.
  ...Array.from({ length: 39 }, (_, index): Seed => ({
    at: new Date(Date.UTC(2026, 10, 5, 16, 0) - index * 3_600_000).toISOString(),
    actor: OPERATOR,
    action: "agency_updated",
    actionLabel: "Agency updated",
    entityType: "agency",
    entityTypeLabel: "Agency",
    entityId: String((index % 2) === 0 ? 4 : 9),
    entityLabel: index % 2 === 0 ? "Gulf Coast Agency" : "Lakeside Agency",
    entityHref: `/app/admin/agencies/${index % 2 === 0 ? 4 : 9}`,
    reason: `Agency review ${index + 1}`,
  })),
].map((seed, index) => ({
  id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  action: seed.action,
  actionLabel: seed.actionLabel,
  refused: seed.action.endsWith("_refused"),
  entityType: seed.entityType,
  entityTypeLabel: seed.entityTypeLabel,
  entityId: seed.entityId,
  entityLabel: seed.entityLabel,
  entityHref: seed.entityHref,
  actor: seed.actor,
  actorName: seed.actor.startsWith("system:") ? "System" : NAMES[seed.actor] ?? "Operator",
  reason: seed.reason,
  details: seed.details ?? {},
  recordedAt: seed.at,
}));

const FILTERS = {
  entityTypes: [
    { value: "administrator_mfa", label: "Administrator security" },
    { value: "agency", label: "Agency" },
    { value: "batch_request", label: "Batch" },
    { value: "customer", label: "Customer" },
    { value: "lead_upload", label: "DB2 upload" },
    { value: "exclusion_list", label: "Exclusion list" },
    { value: "lead", label: "Lead" },
    { value: "lead_report", label: "Lead report" },
    { value: "source_segment", label: "Niche mapping" },
    { value: "nightly_review", label: "Nightly review" },
    { value: "scrape_run", label: "Scrape run" },
    { value: "scraper_configuration", label: "Scraper configuration" },
  ],
  who: [
    { value: "operator", label: "Operator" },
    { value: "system", label: "System" },
    { value: CASEY, label: "Casey Reyes" },
    { value: RIVER, label: "River Morgan" },
  ],
};

function whoMatches(who: string | null, actor: string): boolean {
  if (!who) return true;
  if (who === "system") return actor.startsWith("system:");
  if (who === "operator") return actor === OPERATOR || actor.startsWith("telegram:");
  return actor === who;
}

export async function mockActivity(page: Page): Promise<ActivityMockState> {
  const state: ActivityMockState = { requests: [] };

  await page.route(/\/api\/admin\/activity(?:\/.*)?(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    state.requests.push(route.request().url());
    const parts = url.pathname.split("/").filter(Boolean);
    const activityIndex = parts.indexOf("activity");
    const pathType = parts[activityIndex + 1];
    const pathId = parts[activityIndex + 2];
    const actor = url.searchParams.get("actor");
    const who = url.searchParams.get("who");
    const refused = url.searchParams.get("refused") === "true";
    const query = url.searchParams.get("q")?.toLocaleLowerCase() ?? "";
    const action = url.searchParams.get("action");
    const entityType = pathType ?? url.searchParams.get("entityType");
    const entityId = pathId ?? url.searchParams.get("entityId");
    const dateFrom = url.searchParams.get("dateFrom");
    const dateTo = url.searchParams.get("dateTo");

    const filtered = ACTIVITY_ENTRIES.filter((entry) => {
      const day = entry.recordedAt.slice(0, 10);
      return (
        (!query ||
          [entry.action, entry.entityType, entry.entityId, entry.actor, entry.reason].some((value) =>
            value.toLocaleLowerCase().includes(query),
          )) &&
        (!actor || entry.actor === actor) &&
        whoMatches(who, entry.actor) &&
        (!refused || entry.refused) &&
        (!action || entry.action === action) &&
        (!entityType || entry.entityType === entityType) &&
        (!entityId || entry.entityId === entityId) &&
        (!dateFrom || day >= dateFrom) &&
        (!dateTo || day <= dateTo)
      );
    });
    const pageNumber = Math.max(1, Number(url.searchParams.get("page") ?? 1));
    const pageSize = 25;
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    const boundedPage = Math.min(pageNumber, pages);
    return json(route, {
      entries: filtered.slice((boundedPage - 1) * pageSize, boundedPage * pageSize),
      page: boundedPage,
      pageSize,
      total: filtered.length,
      pages,
      ...(pathType ? {} : { filters: FILTERS }),
    });
  });

  return state;
}
