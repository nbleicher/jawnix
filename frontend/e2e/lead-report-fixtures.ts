import type { Page, Route } from "@playwright/test";

/**
 * A seeded Lead Report surface (#58).
 *
 * The control lists here are the ones the backend projects for each report
 * state, so the browser tests exercise the same offer surface the domain
 * produces. The report text and the Distribution Event are fixed evidence:
 * nothing in these fixtures rewrites them, because nothing in the product can.
 */

export const OPEN_REPORT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const RESOLVED_REPORT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const HOLD_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const REPORTED_LEAD_ID = 91;
export const SUPPRESSED_LEAD_ID = 92;

export const HOLD_RELEASE =
  "This Eligibility Hold is released only when an administrator resolves this Lead Report. It cannot be released or bypassed by the Customer.";

export const RESTORE_NOTICE =
  "Restoring returns this Lead to the eligible pool. It does not promise the Lead will be allocated to anyone.";

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

const DISMISS = {
  name: "dismiss",
  label: "Dismiss",
  consequence:
    "Records that the report did not hold up, releases the Eligibility Hold, and returns the Lead to the eligible pool unchanged. The delivered Distribution Event is not rewritten.",
  destructive: false,
  requiresReason: true,
  requiresOverride: false,
};

const CORRECT = {
  name: "correct",
  label: "Correct",
  consequence:
    "Records a Lead Correction that overrides the listing this Lead came from, and releases the Eligibility Hold. The delivered Distribution Event is not rewritten.",
  destructive: false,
  requiresReason: true,
  requiresOverride: true,
};

const SUPPRESS = {
  name: "suppress",
  label: "Suppress",
  consequence:
    "Withholds this Lead from every future Batch Request. Suppression does not delete what was already delivered.",
  destructive: true,
  requiresReason: true,
  requiresOverride: false,
};

const RESTORE = {
  name: "restore",
  label: "Restore",
  consequence:
    "Returns this Lead to the eligible pool. It does not promise the Lead will be allocated to anyone.",
  destructive: false,
  requiresReason: true,
  requiresOverride: false,
};

const OPEN_REPORT = {
  id: OPEN_REPORT_ID,
  reason: "wrong_number",
  reasonLabel: "Wrong number",
  details:
    "The number I was given rings a dental office, not a roofing contractor.",
  status: "open",
  createdAt: "2026-07-01T10:00:00Z",
  href: `/app/admin/fulfillment/reports/${OPEN_REPORT_ID}`,
  customer: {
    id: 7,
    name: "Northstar Insurance",
    href: "/app/admin/customers/7",
  },
  distributionEvent: {
    id: 4021,
    phone: "+15125550123",
    title: "Roofing contractor",
    state: "TX",
    customerName: "Northstar Insurance",
    agencyName: "Northstar Agency",
    deliveredAt: "2026-06-28T12:00:00Z",
    listingProvenance: { scraped_from: "directory.example" },
    requestId: "11111111-1111-4111-8111-111111111111",
  },
  lead: {
    id: REPORTED_LEAD_ID,
    phone: "+15125550123",
    title: "Roofing contractor",
    state: "TX",
    suppressed: false,
    correction: null,
  },
  evidence: {
    kind: "current_listing",
    label: "Current listing",
    title: "Ridgeline Roofing",
    state: "TX",
    observationId: 552,
    observedAt: "2026-06-01T09:00:00Z",
    source: "directory.example",
  },
  controls: {
    eligibilityHeld: true,
    holdId: HOLD_ID,
    holdReason: "An open Lead Report withholds this Lead from distribution.",
    holdReleasedAt: null,
    holdRelease: HOLD_RELEASE,
    holdReleasableByCustomer: false,
    suppressed: false,
    suppressionReason: "",
    restoreNotice: RESTORE_NOTICE,
    // Restoring is a control on the Lead, so it appears here only once the
    // Lead has actually been suppressed.
    actions: [],
  },
  resolution: null,
  actions: [DISMISS, CORRECT, SUPPRESS],
};

const RESOLVED_REPORT = {
  ...OPEN_REPORT,
  id: RESOLVED_REPORT_ID,
  reason: "disconnected",
  reasonLabel: "Disconnected number",
  details: "The line was disconnected when I called.",
  status: "dismissed",
  href: `/app/admin/fulfillment/reports/${RESOLVED_REPORT_ID}`,
  controls: {
    ...OPEN_REPORT.controls,
    eligibilityHeld: false,
    holdReason: "",
    holdReleasedAt: "2026-07-02T12:00:00Z",
  },
  resolution: {
    action: "dismiss",
    note: "Number verified as reachable.",
    actorId: "admin-1",
    createdAt: "2026-07-02T12:00:00Z",
  },
  actions: [],
};

const ELIGIBILITY_HOLD = {
  id: HOLD_ID,
  leadId: REPORTED_LEAD_ID,
  leadPhone: "+15125550123",
  reason: "wrong_number",
  reasonLabel: "Wrong number",
  createdAt: "2026-07-01T10:00:00Z",
  reportId: OPEN_REPORT_ID,
  reportStatus: "open",
  href: `/app/admin/fulfillment/reports/${OPEN_REPORT_ID}`,
  release: HOLD_RELEASE,
};

const SUPPRESSED_LEAD = {
  id: SUPPRESSED_LEAD_ID,
  phone: "+15125550987",
  title: "Storm damage repair",
  state: "FL",
  reason: "Reported as a wrong number by two Customers.",
  restoreNotice: RESTORE_NOTICE,
  actions: [RESTORE],
};

/** The queue row, which is lighter than the detail: enough to choose what to
 *  open, without every report's evidence and controls. */
const OPEN_REPORT_SUMMARY = {
  id: OPEN_REPORT.id,
  reason: OPEN_REPORT.reason,
  reasonLabel: OPEN_REPORT.reasonLabel,
  details: OPEN_REPORT.details,
  status: OPEN_REPORT.status,
  createdAt: OPEN_REPORT.createdAt,
  href: OPEN_REPORT.href,
  customer: OPEN_REPORT.customer,
  eligibilityHeld: true,
};

/** The #58 additions to `GET /api/admin/fulfillment`. Kept here so the
 *  Fulfillment fixture and this one cannot drift apart. */
export const LEAD_REPORT_SECTIONS = {
  leadReports: [OPEN_REPORT_SUMMARY],
  eligibilityHolds: [ELIGIBILITY_HOLD],
  suppressedLeads: [SUPPRESSED_LEAD],
};

const REPORTS: Record<string, unknown> = {
  [OPEN_REPORT_ID]: OPEN_REPORT,
  [RESOLVED_REPORT_ID]: RESOLVED_REPORT,
};

export async function mockLeadReports(page: Page): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];

  function record(route: Route) {
    const request = route.request();
    calls.push({
      url: new URL(request.url()).pathname,
      method: request.method(),
      body: (request.postDataJSON() as Record<string, unknown>) ?? {},
    });
  }

  await page.route(/\/api\/admin\/lead-reports(\/[^?]*)?$/, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (request.method() === "GET") {
      const report = REPORTS[path.split("/").pop() ?? ""];
      if (!report) {
        return json(route, { detail: "Lead Report was not found." }, 404);
      }
      return json(route, report);
    }

    record(route);
    if (path.endsWith("/correct")) {
      const body = (request.postDataJSON() as Record<string, unknown>) ?? {};
      return json(route, {
        correctionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        title: body.title ?? "",
        state: body.state ?? "",
        evidence: OPEN_REPORT.evidence,
      });
    }
    if (path.endsWith("/suppress")) {
      return json(route, { restoreNotice: RESTORE_NOTICE });
    }
    return json(route, { ok: true });
  });

  await page.route(
    /\/api\/admin\/leads\/\d+\/(suppression|correction)$/,
    (route) => {
      record(route);
      return json(route, { ok: true });
    },
  );

  await page.route(/\/api\/admin\/activity/, (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("entityType") !== "lead_report") {
      return route.fallback();
    }
    return json(route, {
      entries: [],
      page: 1,
      pageSize: 25,
      total: 0,
      pages: 1,
    });
  });

  return calls;
}
