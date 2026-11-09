import type { Page, Route } from "@playwright/test";

/**
 * New request, Batches, and batch page fixtures (`/api/me/batch-requests`),
 * matching the approved renders (V9): Northstar Insurance, #1042 waiting for
 * approval (25,000 FL leads, $125.00 held at $5.00 per 1,000), #1017 with an
 * expired file, and a $412.50 balance. Dates are local days around the
 * renders' day, Nov 9, 2026; specs that read them fix the browser clock to
 * `BATCHES_DAY`.
 */

/** The approved renders' day. */
export const BATCHES_DAY = new Date(2026, 10, 9, 10, 0);

function local(month: number, day: number, hours = 12, minutes = 0): string {
  return new Date(2026, month - 1, day, hours, minutes).toISOString();
}

function batchId(number: number): string {
  const digits = String(number).padStart(4, "0");
  return `${digits}${digits}-${digits}-4${digits.slice(1)}-8${digits.slice(1)}-${digits.repeat(3)}`;
}

type Stage =
  | "waiting_approval"
  | "preparing"
  | "ready"
  | "expiring"
  | "file_expired"
  | "copy_on_the_way"
  | "delivered"
  | "not_approved"
  | "canceled"
  | "needs_attention";

interface BatchSpec {
  number: number;
  leads: number;
  states: string[];
  stage: Stage;
  submitted: string;
  approved?: string;
  delivered?: string;
  expires?: string;
  rowsPerFile?: number;
  costCents?: number | null;
  closed?: string;
}

const STATUS: Record<Stage, { label: string; description: string; tone: string }> = {
  waiting_approval: { label: "Submitted", description: "Your request is ready for review.", tone: "info" },
  preparing: {
    label: "Preparing Batch",
    description: "We are waiting for enough matching leads. There is nothing you need to do.",
    tone: "warning",
  },
  ready: { label: "Delivered", description: "Your Batch has been delivered.", tone: "success" },
  expiring: { label: "Delivered", description: "Your Batch has been delivered.", tone: "success" },
  file_expired: { label: "Delivered", description: "Your Batch has been delivered.", tone: "success" },
  copy_on_the_way: { label: "Delivered", description: "Your Batch has been delivered.", tone: "success" },
  delivered: { label: "Delivered", description: "Your Batch has been delivered.", tone: "success" },
  not_approved: {
    label: "Not Approved",
    description: "This request was not approved. You can submit a new request.",
    tone: "danger",
  },
  canceled: {
    label: "Canceled",
    description: "This request was canceled. You can submit a new request.",
    tone: "neutral",
  },
  needs_attention: {
    label: "Needs Attention",
    description: "We could not complete this request. Email noah@jawnix.com before trying again.",
    tone: "danger",
  },
};

const DELIVERED_STAGES = new Set<Stage>([
  "ready",
  "expiring",
  "file_expired",
  "copy_on_the_way",
  "delivered",
]);

const OPEN_STAGES = new Set<Stage>(["waiting_approval", "preparing"]);

function history(spec: BatchSpec) {
  const events: Array<{ key: string; label: string; at: string | null; done: boolean }> = [
    { key: "submitted", label: "Submitted", at: spec.submitted, done: true },
  ];
  if (spec.approved) events.push({ key: "approved", label: "Approved", at: spec.approved, done: true });
  if (spec.stage === "not_approved") {
    events.push({ key: "not_approved", label: "Not approved", at: spec.closed ?? null, done: true });
  } else if (spec.stage === "canceled") {
    events.push({ key: "canceled", label: "Canceled", at: spec.closed ?? null, done: true });
  } else if (OPEN_STAGES.has(spec.stage)) {
    if (!spec.approved) events.push({ key: "approved", label: "Approved", at: null, done: false });
    events.push({ key: "delivered", label: "Ready to download", at: null, done: false });
  } else if (DELIVERED_STAGES.has(spec.stage)) {
    events.push({ key: "delivered", label: "Delivered", at: spec.delivered ?? null, done: true });
    if (spec.stage === "file_expired" || spec.stage === "copy_on_the_way") {
      events.push({ key: "file_expired", label: "File expired", at: spec.expires ?? null, done: true });
    }
    if (spec.stage === "copy_on_the_way") {
      events.push({ key: "copy_on_the_way", label: "New copy on the way", at: null, done: false });
    }
  }
  return events;
}

function cost(spec: BatchSpec) {
  if (spec.costCents === null) return null;
  const amount = spec.costCents ?? spec.leads * 5 / 10;
  if (spec.stage === "not_approved" || spec.stage === "canceled") {
    return { rate_cents_per_thousand: 500, amount_cents: amount, state: "released", at: spec.closed ?? null };
  }
  if (OPEN_STAGES.has(spec.stage)) {
    return { rate_cents_per_thousand: 500, amount_cents: amount, state: "held", at: spec.submitted };
  }
  return {
    rate_cents_per_thousand: 500,
    amount_cents: amount,
    state: "charged",
    at: spec.delivered ?? null,
  };
}

function artifact(spec: BatchSpec, id: string) {
  if (!DELIVERED_STAGES.has(spec.stage) || spec.stage === "delivered") return null;
  const rows = spec.rowsPerFile ?? spec.leads;
  const files = Math.ceil(spec.leads / rows);
  const live = spec.stage === "ready" || spec.stage === "expiring";
  const stem = `northstar_batch_${spec.number}`;
  return {
    filename: files > 1 ? `${stem}.zip` : `${stem}_part_001.csv`,
    row_count: spec.leads,
    parts: Array.from({ length: files }, (_, index) => ({
      filename: `${stem}_part_${String(index + 1).padStart(3, "0")}.csv`,
      row_count: Math.min(rows, spec.leads - index * rows),
    })),
    columns: ["phone", "title", "state"],
    expires_at: spec.expires ?? null,
    available: live,
    download_href: live ? `/api/me/batch-requests/${id}/artifact` : null,
  };
}

interface StateCount {
  state: string;
  name: string;
  lead_count: number;
}

/** One batch as `/api/me/batch-requests` returns it. */
export function makeBatch(
  spec: BatchSpec,
  extra: { leads_by_state?: StateCount[] | null } = {},
) {
  const id = batchId(spec.number);
  const delivered = DELIVERED_STAGES.has(spec.stage);
  return {
    id,
    number: spec.number,
    lead_count: spec.leads,
    rows_per_file: spec.rowsPerFile ?? spec.leads,
    states: spec.states,
    submitted_at: spec.submitted,
    delivered_at: delivered ? (spec.delivered ?? null) : null,
    status: STATUS[spec.stage],
    milestones: { milestones: [], current_key: null, pause: null, outcome: null },
    can_cancel: OPEN_STAGES.has(spec.stage),
    next_action: null,
    href: `/app/batches/${spec.number}`,
    receipt_href: `/app/batches/${spec.number}`,
    artifact: artifact(spec, id),
    stage: spec.stage,
    can_request_copy: spec.stage === "file_expired",
    cost: cost(spec),
    history: history(spec),
    leads_by_state: extra.leads_by_state ?? null,
  };
}

export type MockBatch = ReturnType<typeof makeBatch>;

/** Waiting for approval: 25,000 FL leads, $125.00 held. */
export const WAITING_REQUEST = makeBatch({
  number: 1042,
  leads: 25_000,
  states: ["FL"],
  stage: "waiting_approval",
  submitted: local(11, 9, 9, 41),
  rowsPerFile: 10_000,
});

/** Ready to download, in five files of 10,000 rows. */
export const DELIVERED_REQUEST = makeBatch(
  {
    number: 1038,
    leads: 50_000,
    states: ["FL", "TX"],
    stage: "ready",
    submitted: local(11, 7, 9, 10),
    approved: local(11, 7, 9, 30),
    delivered: local(11, 7, 14, 2),
    expires: local(12, 7, 14, 2),
    rowsPerFile: 10_000,
  },
  {
    leads_by_state: [
      { state: "FL", name: "Florida", lead_count: 29_410 },
      { state: "TX", name: "Texas", lead_count: 20_590 },
    ],
  },
);

const READY_1031 = makeBatch({
  number: 1031,
  leads: 100_000,
  states: ["TX"],
  stage: "ready",
  submitted: local(11, 1, 8, 0),
  approved: local(11, 1, 8, 20),
  delivered: local(11, 1, 16, 45),
  expires: local(12, 1, 16, 45),
  rowsPerFile: 10_000,
});

/** A live file never downloaded that expires in three days. */
export const EXPIRING_REQUEST = makeBatch({
  number: 1024,
  leads: 50_000,
  states: ["FL"],
  stage: "expiring",
  submitted: local(10, 13, 9, 0),
  approved: local(10, 13, 9, 15),
  delivered: local(10, 13, 15, 30),
  expires: local(11, 12, 15, 30),
  rowsPerFile: 10_000,
});

/** Delivered Oct 2; the file expired Nov 1 and can be copied again. */
export const EXPIRED_REQUEST = makeBatch(
  {
    number: 1017,
    leads: 50_000,
    states: ["FL", "TX"],
    stage: "file_expired",
    submitted: local(10, 1, 14, 20),
    approved: local(10, 1, 16, 5),
    delivered: local(10, 2, 8, 5),
    expires: local(11, 1, 8, 5),
    rowsPerFile: 10_000,
  },
  {
    leads_by_state: [
      { state: "FL", name: "Florida", lead_count: 31_240 },
      { state: "TX", name: "Texas", lead_count: 18_760 },
    ],
  },
);

/** Not approved: the $100.00 hold was released. */
export const REJECTED_REQUEST = makeBatch({
  number: 1012,
  leads: 20_000,
  states: ["TX"],
  stage: "not_approved",
  submitted: local(9, 24, 11, 0),
  closed: local(9, 24, 15, 0),
});

const EXPIRED_1009 = makeBatch({
  number: 1009,
  leads: 75_000,
  states: ["TX"],
  stage: "file_expired",
  submitted: local(9, 20, 9, 0),
  approved: local(9, 20, 9, 30),
  delivered: local(9, 20, 17, 0),
  expires: local(10, 20, 17, 0),
  rowsPerFile: 25_000,
});

const EXPIRED_1004 = makeBatch({
  number: 1004,
  leads: 50_000,
  states: ["FL"],
  stage: "file_expired",
  submitted: local(9, 12, 10, 0),
  approved: local(9, 12, 10, 30),
  delivered: local(9, 13, 9, 0),
  expires: local(10, 13, 9, 0),
  rowsPerFile: 10_000,
});

/** The first page of Northstar's 24 batches, eight to a page as rendered. */
export const BATCH_REQUEST_WORKSPACE = {
  limits: {
    minimum_lead_count: 1,
    maximum_lead_count: 100_000,
    licensed_states: ["FL", "TX"],
    state_names: { FL: "Florida", TX: "Texas" },
  },
  blocker: null,
  requests: [
    WAITING_REQUEST,
    DELIVERED_REQUEST,
    READY_1031,
    EXPIRING_REQUEST,
    EXPIRED_REQUEST,
    REJECTED_REQUEST,
    EXPIRED_1009,
    EXPIRED_1004,
  ],
  history: { page: 1, page_size: 8, total: 24 },
};

export const EMPTY_BATCH_REQUEST_WORKSPACE = {
  ...BATCH_REQUEST_WORKSPACE,
  requests: [] as MockBatch[],
  history: { page: 1, page_size: 20, total: 0 },
};

/** Twenty-four batches, eight to a page: page 1 is the rendered one. */
export function historyPage(page: number) {
  if (page <= 1) return BATCH_REQUEST_WORKSPACE;
  const first = (page - 1) * 8;
  const count = Math.max(0, Math.min(8, 24 - first));
  return {
    ...BATCH_REQUEST_WORKSPACE,
    requests: Array.from({ length: count }, (_, index) =>
      makeBatch({
        number: 1000 - first - index,
        leads: 10_000,
        states: ["TX"],
        stage: "file_expired",
        submitted: local(8, 28 - index, 10, 0),
        approved: local(8, 28 - index, 10, 30),
        delivered: local(8, 28 - index, 16, 0),
        expires: local(9, 27 - index, 16, 0),
      }),
    ),
    history: { page, page_size: 8, total: 24 },
  };
}

export const BLOCKED_BATCH_REQUEST_WORKSPACE = {
  limits: {
    minimum_lead_count: 1,
    maximum_lead_count: 100_000,
    licensed_states: [],
    state_names: {},
  },
  blocker: {
    reason: "no_licensed_states",
    label: "Add your appointed states first",
    description:
      "A batch covers the states you are appointed in. "
      + "Add at least one before you request a batch.",
    action: {
      kind: "add_licensed_states",
      label: "Add appointed states",
      description: "Save the states you are appointed in.",
      href: "/app/account",
    },
  },
  requests: [] as MockBatch[],
  history: { page: 1, page_size: 20, total: 0 },
};

export const MAPPING_BLOCKED_BATCH_REQUEST_WORKSPACE = {
  ...BATCH_REQUEST_WORKSPACE,
  blocker: {
    reason: "mapping_unconfirmed",
    label: "Your account is still being set up",
    description:
      "We are finishing your account setup. "
      + "You can request a batch as soon as that is done.",
    action: {
      kind: "review_account",
      label: "Review Account",
      description: "Check the account details we have for you.",
      href: "/app/account",
    },
  },
};

/** The phone the report lookup finds within any batch. */
export const REPORTED_PHONE = "(555) 555-0100";

export interface BatchRequestMockOptions {
  workspace?: unknown;
  /** Successive workspace reads, holding on the last value once exhausted. */
  workspaceSequence?: unknown[];
  /** Held open long enough that a second click lands while the first is busy. */
  submitDelayMs?: number;
  /** Batches the single read resolves although no loaded page lists them. */
  extraRequests?: MockBatch[];
  /** Answers `?page=N` reads; the workspace answers page 1 otherwise. */
  pages?: (page: number) => unknown;
  /** Refuse submissions with this detail and status. */
  refuseSubmission?: { status: number; detail: string };
  /** Refuse new copies with this detail and status. */
  refuseCopy?: { status: number; detail: string };
}

export interface BatchRequestMockState {
  workspaceRequests: number;
  workspacePages: number[];
  requestReads: string[];
  submissions: Array<Record<string, unknown>>;
  cancellations: string[];
  copies: string[];
  artifactDownloads: string[];
  lookups: Array<Record<string, unknown>>;
  reports: Array<{ eventId: string; body: Record<string, unknown> }>;
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

/**
 * A backend that behaves like the real one on the points that matter: one
 * batch per submission key no matter how often it arrives, a batch read by
 * number or by UUID, and one copy per expired file.
 */
export async function mockBatchRequests(
  page: Page,
  options: BatchRequestMockOptions = {},
): Promise<BatchRequestMockState> {
  const state: BatchRequestMockState = {
    workspaceRequests: 0,
    workspacePages: [],
    requestReads: [],
    submissions: [],
    cancellations: [],
    copies: [],
    artifactDownloads: [],
    lookups: [],
    reports: [],
  };
  const workspace = options.workspace ?? BATCH_REQUEST_WORKSPACE;
  const bySubmissionKey = new Map<string, MockBatch>();
  const changed = new Map<string, MockBatch>();

  function currentWorkspace(): { requests: MockBatch[] } {
    const sequence = options.workspaceSequence;
    const value = sequence?.length
      ? sequence[Math.min(Math.max(state.workspaceRequests - 1, 0), sequence.length - 1)]
      : workspace;
    return value as { requests: MockBatch[] };
  }

  function find(reference: string): MockBatch | undefined {
    const known = [
      ...changed.values(),
      ...bySubmissionKey.values(),
      ...currentWorkspace().requests,
      ...(options.extraRequests ?? []),
    ];
    return known.find(
      (item) => item.id === reference || String(item.number) === reference,
    );
  }

  await page.route(/\/api\/me\/batch-requests\/[^/?]+$/, (route) => {
    const reference = new URL(route.request().url()).pathname.split("/").at(-1) ?? "";
    if (reference === "events") {
      return route.fulfill({ status: 503, contentType: "text/plain", body: "Push unavailable in this fixture" });
    }
    state.requestReads.push(reference);
    const item = find(reference);
    return item ? json(route, item) : json(route, { detail: "Request was not found." }, 404);
  });

  await page.route(/\/api\/me\/batch-requests\/[^/]+\/artifact$/, (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    state.artifactDownloads.push(id);
    const filename = find(id)?.artifact?.filename ?? "northstar_batch.zip";
    const isZip = filename.endsWith(".zip");
    return route.fulfill({
      status: 200,
      contentType: isZip ? "application/zip" : "text/csv",
      headers: { "Content-Disposition": `attachment; filename="${filename}"` },
      // Minimal zip end-of-directory record so Playwright records a download.
      body: isZip ? Buffer.from("PK\x05\x06" + "\x00".repeat(18), "binary") : "phone,title,state\n",
    });
  });

  await page.route(/\/api\/me\/batch-requests\/[^/]+\/cancel$/, (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    state.cancellations.push(id);
    const item = find(id) ?? WAITING_REQUEST;
    const canceled: MockBatch = {
      ...item,
      stage: "canceled",
      status: STATUS.canceled,
      can_cancel: false,
      cost: item.cost ? { ...item.cost, state: "released", at: local(11, 9, 10, 5) } : null,
      history: [
        ...item.history.filter((event) => event.done),
        { key: "canceled", label: "Canceled", at: local(11, 9, 10, 5), done: true },
      ],
    };
    changed.set(canceled.id, canceled);
    return json(route, canceled);
  });

  await page.route(/\/api\/me\/batch-requests\/[^/]+\/new-copy$/, (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    state.copies.push(id);
    if (options.refuseCopy) {
      return json(route, { detail: options.refuseCopy.detail }, options.refuseCopy.status);
    }
    const item = find(id) ?? EXPIRED_REQUEST;
    const copying: MockBatch = {
      ...item,
      stage: "copy_on_the_way",
      can_request_copy: false,
      history: [
        ...item.history,
        { key: "copy_on_the_way", label: "New copy on the way", at: null, done: false },
      ],
    };
    changed.set(copying.id, copying);
    return json(route, copying, 202);
  });

  await page.route(/\/api\/me\/batch-requests(\?[^/]*)?$/, async (route) => {
    if (route.request().method() !== "POST") {
      state.workspaceRequests += 1;
      const requestedPage = Number(
        new URL(route.request().url()).searchParams.get("page") ?? "1",
      );
      state.workspacePages.push(requestedPage);
      if (options.pages) return json(route, options.pages(requestedPage));
      const current = currentWorkspace() as unknown as { requests: MockBatch[] };
      return json(route, {
        ...current,
        requests: current.requests.map((item) => changed.get(item.id) ?? item),
      });
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    state.submissions.push(body);
    if (options.submitDelayMs) {
      await new Promise((resolve) => setTimeout(resolve, options.submitDelayMs));
    }
    if (options.refuseSubmission) {
      return json(route, { detail: options.refuseSubmission.detail }, options.refuseSubmission.status);
    }
    const key = String(body.idempotency_key);
    const existing = bySubmissionKey.get(key);
    if (existing) return json(route, { created: false, request: existing }, 200);
    const leadCount = Number(body.lead_count);
    const created = makeBatch({
      number: 1043,
      leads: leadCount,
      states: body.state_mode === "all_saved" ? ["FL", "TX"] : (body.states as string[]),
      stage: "waiting_approval",
      submitted: local(11, 9, 10, 0),
      rowsPerFile: body.rows_per_file == null ? leadCount : Number(body.rows_per_file),
    });
    bySubmissionKey.set(key, created);
    return json(route, { created: true, request: created }, 201);
  });

  await page.route(/\/api\/me\/feedback\/lookup$/, (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    state.lookups.push(body);
    const digits = String(body.phone ?? "").replace(/\D+/g, "");
    if (digits.endsWith("5555550100")) {
      return json(route, {
        distributionEventId: 77,
        businessName: "Sunshine Roofing",
        phone: "5555550100",
        deliveredAt: local(10, 2, 8, 5),
        batchId: body.request_id,
        currentDisposition: null,
      });
    }
    return json(route, { detail: "No delivered Lead found." }, 404);
  });

  await page.route(/\/api\/me\/distributions\/[^/]+\/reports$/, (route) => {
    const eventId = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    const body = route.request().postDataJSON() as Record<string, unknown>;
    state.reports.push({ eventId, body });
    return json(
      route,
      { id: "report-1", distributionEventId: Number(eventId), ...body, status: "open" },
      201,
    );
  });

  return state;
}
