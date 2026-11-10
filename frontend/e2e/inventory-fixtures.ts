import type { Page, Route } from "@playwright/test";

/**
 * Inventory (P4), its tools, and lead lookup (P5).
 *
 * Figures match the approved renders (V9): DB1 9,183,402 usable of 9,244,326
 * rows, DB2 1,071,288 usable of 1,082,619, and (615) 555-0142, Nashville
 * Family Dental, delivered to Keystone Medicare (Batch #1028) and Harbor
 * Insurance (Batch #1010).
 */

export interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
}

function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString();
}

function todayAt(hours: number, minutes = 0): string {
  const date = new Date();
  date.setHours(hours, minutes, 0, 0);
  return date.toISOString();
}

export const LOOKUP_PHONE = "6155550142";
export const LOOKUP_LEAD_ID = 4401;
export const SUPPRESSED_ID = 92;
export const WAITING_LIST_ID = "88888888-8888-4888-8888-888888888888";
export const FAILED_UPLOAD_ID = "33333333-aaaa-4aaa-8aaa-333333333333";
export const READY_UPLOAD_ID = "11111111-aaaa-4aaa-8aaa-111111111111";
export const NEW_UPLOAD_ID = "44444444-aaaa-4aaa-8aaa-444444444444";

const STATE_CODES = [
  "TX", "FL", "CA", "PA", "OH", "NY", "IL", "GA", "NC", "MI", "NJ", "VA", "WA", "AZ", "MA", "TN", "IN", "MO",
  "MD", "WI", "CO", "MN", "SC", "AL", "LA", "KY", "OR", "OK", "CT", "UT", "IA", "NV", "AR", "MS", "KS",
  "NM", "NE", "ID", "WV", "HI", "NH", "ME", "RI", "MT", "DE", "SD", "ND", "AK", "DC", "VT", "WY",
];

/** Spreads the remainder of a total over the states after the named ones,
 *  so the 51-state footer adds up to the render's figures exactly. */
function byState(named: Record<string, number>, total: number): Record<string, number> {
  const rest = STATE_CODES.filter((code) => !(code in named));
  let left = total - Object.values(named).reduce((sum, value) => sum + value, 0);
  const result: Record<string, number> = { ...named };
  rest.forEach((code, index) => {
    const share = index === rest.length - 1 ? left : Math.floor(left / (rest.length - index) * 0.9);
    result[code] = share;
    left -= share;
  });
  return result;
}

const DB1_STATES = byState(
  { TX: 1_204_002, FL: 1_011_550, CA: 902_330, PA: 980_411, OH: 611_208 },
  9_183_402,
);
const DB2_STATES = byState(
  { TX: 182_410, FL: 240_118, CA: 98_402, PA: 0, OH: 120_551 },
  1_071_288,
);

export const INVENTORY = {
  db1: {
    database: "db1",
    label: "DB1",
    allRows: 9_244_326,
    suppressed: 42_612,
    held: 18,
    excluded: 18_294,
    usable: 9_183_402,
    usableByState: DB1_STATES,
    computedAt: todayAt(6),
  },
  db2: {
    database: "db2",
    label: "DB2",
    allRows: 1_082_619,
    suppressed: 11_000,
    held: 0,
    excluded: 331,
    usable: 1_071_288,
    usableByState: DB2_STATES,
    computedAt: todayAt(6),
  },
  refresh: { status: "idle", jobId: null },
  addedLast7Days: 96_140,
  lastUpload: {
    id: READY_UPLOAD_ID,
    filename: "tn-agents-nov.csv",
    uploadedBy: "Alex",
    at: hoursAgo(72),
  },
  sections: { uploads: 14, uploadsInProgress: 0, exclusionsWaiting: 1, suppressed: 42_612 },
};

const RESTORE_NOTICE =
  "Restoring eligibility returns this Lead to the ordinary allocation rules -- Global Cooldown, permanent no-repeat history, and Licensed State scope still apply. It does not guarantee the Lead will be allocated.";

const RESTORE = {
  name: "restore",
  label: "Restore eligibility",
  consequence: RESTORE_NOTICE,
  destructive: false,
  requiresReason: true,
  requiresOverride: false,
};

export const SUPPRESSED_LEADS = {
  items: [
    {
      id: SUPPRESSED_ID,
      phone: "5125550987",
      title: "Storm damage repair",
      state: "FL",
      reason: "Reported as a wrong number by two Customers.",
      href: "/app/admin/inventory/lookup?phone=5125550987",
      actions: [RESTORE],
    },
    {
      id: 91,
      phone: "7135550111",
      title: "Gulf Roofing Supply",
      state: "TX",
      reason: "Disconnected number.",
      href: "/app/admin/inventory/lookup?phone=7135550111",
      actions: [RESTORE],
    },
  ],
  restoreNotice: RESTORE_NOTICE,
  page: 1,
  pageSize: 25,
  total: 2,
  pages: 1,
};

function upload(overrides: Record<string, unknown>) {
  return {
    id: READY_UPLOAD_ID,
    filename: "tn-agents-nov.csv",
    state: "TN",
    status: "complete",
    bytes: 4_200_000,
    processedRows: 50_000,
    validRows: 49_940,
    rejectedRows: 60,
    importedRows: 41_200,
    updatedRows: 8_740,
    duplicateRows: 0,
    error: "",
    uploadedBy: "Alex",
    createdAt: hoursAgo(72),
    completedAt: hoursAgo(71.8),
    ...overrides,
  };
}

export const UPLOADS = {
  uploads: [
    upload({
      id: FAILED_UPLOAD_ID,
      filename: "ky-agents.csv",
      state: "KY",
      status: "failed",
      processedRows: 0,
      validRows: 0,
      rejectedRows: 0,
      importedRows: 0,
      updatedRows: 0,
      error: "The CSV contains no valid phone records. Review the rejected rows.",
      uploadedBy: "Operator",
      createdAt: hoursAgo(20),
      completedAt: null,
    }),
    upload({}),
    upload({
      id: "22222222-aaaa-4aaa-8aaa-222222222222",
      filename: "fl-agents-oct.csv",
      state: "FL",
      rejectedRows: 0,
      importedRows: 49_000,
      updatedRows: 1_000,
      uploadedBy: "Operator",
      createdAt: hoursAgo(24 * 9),
      completedAt: hoursAgo(24 * 9 - 0.2),
    }),
  ],
  states: [
    { code: "FL", name: "Florida" },
    { code: "TN", name: "Tennessee" },
    { code: "TX", name: "Texas" },
  ],
  inventory: { total: 1_082_619, states: { TN: 412_000, FL: 670_619 }, asOf: hoursAgo(71.8) },
};

function exclusion(overrides: Record<string, unknown>) {
  return {
    id: WAITING_LIST_ID,
    customer: { id: 31, name: "Liberty Roofing", href: "/app/admin/customers/31" },
    customerName: "Liberty Roofing",
    type: "dnc",
    filename: "liberty-dnc.csv",
    status: "pending_confirmation",
    global: false,
    acceptedRows: 1_200,
    invalidRows: 4,
    duplicateRows: 2,
    poolImpact: 61,
    error: "",
    decisionReason: "",
    createdAt: hoursAgo(48),
    decidedAt: null,
    ...overrides,
  };
}

export const EXCLUSION_LISTS = {
  waiting: [exclusion({})],
  lists: {
    items: [
      exclusion({}),
      exclusion({
        id: "99999999-9999-4999-8999-999999999991",
        customer: null,
        customerName: "Operator",
        type: "tcpa_litigator",
        filename: "tcpa-litigators-q4.csv",
        status: "confirmed",
        global: true,
        acceptedRows: 18_210,
        poolImpact: 18_294,
        createdAt: hoursAgo(24 * 6),
        decidedAt: hoursAgo(24 * 6),
      }),
      exclusion({
        id: "99999999-9999-4999-8999-999999999992",
        customer: { id: 12, name: "Northstar Insurance", href: "/app/admin/customers/12" },
        customerName: "Northstar Insurance",
        type: "landline",
        filename: "northstar-landlines.csv",
        status: "denied",
        acceptedRows: 3_400,
        poolImpact: 0,
        createdAt: hoursAgo(24 * 12),
        decidedAt: hoursAgo(24 * 11),
      }),
    ],
    page: 1,
    pageSize: 25,
    total: 3,
    pages: 1,
  },
};

export const POOL_BREAKDOWN = {
  asOf: todayAt(6),
  cells: [
    { state: "TX", niche: "Dental", count: 340_118 },
    { state: "TX", niche: "Roofing", count: 290_440 },
    { state: "FL", niche: "Roofing", count: 410_002 },
    { state: "FL", niche: "unmapped", count: 18_040 },
    { state: "CA", niche: "Insurance", count: 220_514 },
    { state: "PA", niche: "Dental", count: 120_008 },
  ],
};

const CORRECT = {
  name: "correct",
  label: "Correct title or state",
  consequence:
    "Overrides the Lead's title or state for every future delivery. The override outranks the Current Listing until an administrator removes it; deliveries already made keep the values they were sent with.",
  destructive: false,
  requiresReason: true,
  requiresOverride: true,
};

const SUPPRESS = {
  name: "suppress",
  label: "Suppress",
  consequence:
    "Makes this Lead ineligible for every Customer until an administrator restores it. Its Listing Observations and Distribution Events are kept intact.",
  destructive: true,
  requiresReason: true,
  requiresOverride: false,
};

function daysAgo(days: number): string {
  return hoursAgo(days * 24);
}

function daysFromNow(days: number): string {
  return hoursAgo(-days * 24);
}

export const LOOKUP = {
  query: "(615) 555-0142",
  phone: LOOKUP_PHONE,
  valid: true,
  lead: {
    id: LOOKUP_LEAD_ID,
    phone: LOOKUP_PHONE,
    title: "Nashville Family Dental",
    state: "TN",
    niche: "dentist",
    firstSeenAt: daysAgo(56),
    lastDeliveredAt: daysAgo(3),
  },
  usable: { usable: true, reasons: [] },
  databases: {
    db1: { member: true, source: "Google Maps listing" },
    db2: {
      member: true,
      state: "TN",
      upload: { id: READY_UPLOAD_ID, filename: "tn-agents-nov.csv", uploadedBy: "Alex", at: daysAgo(3) },
    },
  },
  deliveries: [
    {
      id: 2,
      deliveredAt: daysAgo(3),
      customer: { id: 4, name: "Keystone Medicare", href: "/app/admin/customers/4" },
      customerName: "Keystone Medicare",
      agencyName: "Lakeside Agency",
      batch: { number: 1028, href: "/app/admin/batches/1028" },
      database: "db2",
      title: "Nashville Family Dental",
      state: "TN",
      disposition: null,
    },
    {
      id: 1,
      deliveredAt: daysAgo(19),
      customer: { id: 3, name: "Harbor Insurance", href: "/app/admin/customers/3" },
      customerName: "Harbor Insurance",
      agencyName: "Gulf Coast Agency",
      batch: { number: 1010, href: "/app/admin/batches/1010" },
      database: "db1",
      title: "Nashville Family Dental",
      state: "TN",
      disposition: "Positive response",
    },
  ],
  listings: [
    {
      database: "db2",
      observedAt: daysAgo(3),
      source: "DB2 · tn-agents-nov.csv (Alex)",
      title: "Nashville Family Dental",
      state: "TN",
      valid: true,
    },
    {
      database: "db1",
      observedAt: daysAgo(7),
      source: "Run 39 · dentist · TN",
      title: "Nashville Family Dental",
      state: "TN",
      valid: true,
      current: true,
    },
    {
      database: "db1",
      observedAt: daysAgo(56),
      source: "Run 12 · dentist · TN",
      title: "Nashville Family Dentistry",
      state: "TN",
      valid: true,
      current: false,
    },
  ],
  reports: [],
  holds: [],
  suppression: { suppressed: false, reason: "", restoreNotice: RESTORE_NOTICE },
  corrections: [],
  exclusions: [],
  nextRecipients: [
    { key: "customer:4", label: "Keystone Medicare", href: "/app/admin/customers/4", status: "never", reason: "delivered", at: daysAgo(3), count: 1, customers: ["Keystone Medicare"] },
    { key: "customer:3", label: "Harbor Insurance", href: "/app/admin/customers/3", status: "never", reason: "delivered", at: daysAgo(19), count: 1, customers: ["Harbor Insurance"] },
    { key: "agency:2", label: "Gulf Coast Agency members", href: "/app/admin/agencies/2", status: "never", reason: "shared_history", count: 2, customers: ["Pelican Benefits", "Tarpon Life"] },
    { key: "agency:4", label: "Lakeside Agency members", href: "/app/admin/agencies/4", status: "never", reason: "shared_history", count: 3, customers: ["Ridgeline Benefits", "Summit Benefits", "Lakeside Brokers"] },
    { key: "cooldown:7", label: "Customers with 7-day cooldown", status: "later", reason: "cooldown", at: daysFromNow(4), count: 6, customers: [] },
  ],
  evidence: {
    kind: "current_listing",
    label: "Current Listing (Google Maps observation)",
    title: "Nashville Family Dental",
    state: "TN",
    observationId: 9001,
    observedAt: daysAgo(7),
    source: "TN::dentist",
  },
  actions: [CORRECT, SUPPRESS],
};

export const PHONE_SEARCH = {
  query: "615 555 0142",
  groups: [
    {
      key: "lead",
      label: "Lead",
      results: [
        {
          kind: "lead",
          id: String(LOOKUP_LEAD_ID),
          title: "(615) 555-0142 · Nashville Family Dental · TN",
          detail: "DB1 and DB2 · usable",
          href: `/app/admin/inventory/lookup?phone=${LOOKUP_PHONE}`,
        },
      ],
    },
    {
      key: "leadBatches",
      label: "Delivered in",
      results: [
        { kind: "batch", id: "b1028", title: "Batch #1028 · Keystone Medicare", detail: "Nov 6 · DB2", href: "/app/admin/batches/1028" },
        { kind: "batch", id: "b1010", title: "Batch #1010 · Harbor Insurance", detail: "Oct 21 · DB1", href: "/app/admin/batches/1010" },
      ],
    },
    {
      key: "leadUpload",
      label: "Uploaded in",
      results: [
        { kind: "upload", id: READY_UPLOAD_ID, title: "tn-agents-nov.csv", detail: "Alex · Nov 6", href: "/app/admin/inventory/uploads" },
      ],
    },
  ],
};

/** Inventory, its tools, lead lookup, and phone search, with their actions. */
export async function mockInventory(page: Page): Promise<RecordedCall[]> {
  const calls: RecordedCall[] = [];
  function record(route: Route) {
    const request = route.request();
    let body: unknown = {};
    try {
      body = request.postDataJSON() ?? {};
    } catch {
      body = request.postData() ?? "";
    }
    calls.push({ url: new URL(request.url()).pathname, method: request.method(), body });
  }

  await page.route(/\/api\/admin\/inventory$/, (route) => json(route, INVENTORY));
  await page.route(/\/api\/admin\/inventory-snapshots(\/refresh)?$/, (route) => {
    if (route.request().method() === "POST") {
      record(route);
      return json(route, { db1: INVENTORY.db1, db2: INVENTORY.db2, refresh: { status: "queued", jobId: 41 } }, 202);
    }
    return json(route, { db1: INVENTORY.db1, db2: INVENTORY.db2, refresh: { status: "queued", jobId: 41 } });
  });

  // The operator's upload is queued, then finishes on its first status read.
  let submitted: ReturnType<typeof upload> | null = null;
  await page.route(/\/api\/admin\/lead-uploads(\/[^?]*)?$/, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/admin/lead-uploads" && request.method() === "GET") return json(route, UPLOADS);
    if (path.endsWith("/retry")) {
      record(route);
      return json(route, { ...UPLOADS.uploads[0], status: "queued", error: "" });
    }
    if (path === "/api/admin/lead-uploads" && request.method() === "POST") {
      record(route);
      const body = request.postData() ?? "";
      submitted = upload({
        id: NEW_UPLOAD_ID,
        filename: /filename="([^"]+)"/.exec(body)?.[1] ?? "new.csv",
        state: /name="state"\r\n\r\n([A-Z]{2})/.exec(body)?.[1] ?? "",
        status: "queued",
        bytes: 32,
        processedRows: 0,
        validRows: 0,
        rejectedRows: 0,
        importedRows: 0,
        updatedRows: 0,
        uploadedBy: "Operator",
        createdAt: new Date().toISOString(),
        completedAt: null,
      });
      return json(route, submitted, 202);
    }
    if (submitted && path === `/api/admin/lead-uploads/${submitted.id}`) {
      return json(route, { ...submitted, status: "complete", processedRows: 1, validRows: 1, importedRows: 1, completedAt: new Date().toISOString() });
    }
    const match = UPLOADS.uploads.find((item) => path.endsWith(item.id));
    return match ? json(route, match) : json(route, { detail: "Upload was not found." }, 404);
  });

  await page.route(/\/api\/admin\/exclusion-lists(\?.*)?$/, (route) => {
    if (route.request().method() === "POST") {
      record(route);
      return json(route, { ...EXCLUSION_LISTS.lists.items[1], id: "99999999-9999-4999-8999-999999999993", status: "queued", filename: "dnc.csv" }, 202);
    }
    return json(route, EXCLUSION_LISTS);
  });

  await page.route(/\/api\/admin\/suppressed-leads(\?.*)?$/, (route) => json(route, SUPPRESSED_LEADS));

  await page.route(/\/api\/admin\/pool-breakdown(\/refresh)?$/, (route) => {
    if (route.request().method() === "POST") {
      record(route);
      return json(route, { ...POOL_BREAKDOWN, refreshQueued: true, jobId: 41 });
    }
    return json(route, POOL_BREAKDOWN);
  });

  await page.route(/\/api\/admin\/leads\/lookup\?/, (route) => {
    const url = new URL(route.request().url());
    const digits = (url.searchParams.get("phone") ?? "").replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "");
    if (digits === LOOKUP_PHONE || url.searchParams.get("lead") === String(LOOKUP_LEAD_ID)) {
      return json(route, LOOKUP);
    }
    const query = url.searchParams.get("phone") ?? "";
    return json(route, {
      query,
      phone: digits.length === 10 ? digits : null,
      valid: digits.length === 10,
      lead: null,
      exclusions: [],
    });
  });

  await page.route(/\/api\/admin\/leads\/\d+\/(suppression|correction)$/, (route) => {
    record(route);
    return json(route, { ok: true });
  });

  await page.route(/\/api\/admin\/niche-assignments(\/[^?]*)?$/, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/export")) {
      return route.fulfill({ status: 200, contentType: "text/csv", body: "phone,title,state\r\n" });
    }
    const status = {
      id: "55555555-5555-4555-8555-555555555555",
      filename: "niches.csv",
      status: request.method() === "POST" ? "queued" : "complete",
      totalRows: 1_200,
      acceptedRows: 1_150,
      invalidRows: 10,
      duplicateRows: 15,
      skippedMappedRows: 25,
      error: "",
    };
    if (request.method() === "POST") record(route);
    return json(route, status, request.method() === "POST" ? 202 : 200);
  });

  await page.route(/\/api\/admin\/exclusion-lists\/.+$/, (route) => {
    record(route);
    return json(route, { ok: true });
  });

  // Only the fixture phone answers here; every other query falls back to the
  // search mock registered before this one (Today's, or the empty default).
  await page.route(/\/api\/admin\/search\?/, (route) => {
    const query = new URL(route.request().url()).searchParams.get("q") ?? "";
    return query.replace(/\D/g, "") === LOOKUP_PHONE
      ? json(route, { ...PHONE_SEARCH, query })
      : route.fallback();
  });

  return calls;
}
