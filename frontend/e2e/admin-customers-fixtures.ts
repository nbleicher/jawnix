import type { Page, Route } from "@playwright/test";

import { BATCH_DETAILS } from "./today-fixtures";

/**
 * Customers and the Customer page (A3).
 *
 * Figures follow the approved renders (V9): Harbor Insurance has $1,232.00
 * available with $250.00 held for #1041 (50,000 FL, TX leads at $5.00 per
 * 1,000), 412,880 available now, and a conflict with Lakeside Brokers;
 * Northstar Insurance's #1042 waits for approval.
 */

export interface AdminCustomersMockOptions {
  /** Serves the details payload that has an unaccepted replacement pending. */
  pendingInvitation?: boolean;
  /** Pending registrations served to the Customers page. */
  registrations?: unknown[];
}

export interface AdminCustomersMockState {
  /** Full request URLs, so a spec can assert which filters reached the API. */
  directoryRequests: string[];
  detailsRequests: string[];
  invitationRequests: unknown[];
  assignmentRequests: unknown[];
  customerPatchRequests: unknown[];
  passwordResetRequests: string[];
  billingPutRequests: unknown[];
  adjustmentRequests: unknown[];
  cooldownPutRequests: unknown[];
  nichePolicyPutRequests: unknown[];
  nichePolicyPreviewRequests: unknown[];
  availabilityRefreshRequests: number;
  databaseRequests: { url: string; body: unknown }[];
  customerBatchRequests: string[];
}

const AGENCIES = [
  { id: 4, name: "Gulf Coast Agency", active: true },
  { id: 9, name: "Lakeside Agency", active: true },
];

const AGENCY_DIRECTORY = {
  filters: { query: "", status: "all" },
  total: 2,
  matched: 2,
  agencies: [
    {
      id: 4,
      slug: "gulf-coast",
      name: "Gulf Coast Agency",
      active: true,
      status: {
        label: "Active",
        description: "Customers may be assigned to this Agency.",
        tone: "success",
      },
      members: [
        {
          id: 7,
          slug: "harbor-insurance",
          name: "Harbor Insurance",
          active: true,
          licensedStates: ["FL", "TX"],
          href: "/app/admin/customers/7",
        },
      ],
      currentMembers: 1,
      sharedHistory: {
        customers: 4,
        agencies: 1,
        distributedLeads: 240,
      },
      lastActivityAt: "2026-07-20T12:00:00Z",
      href: "/app/admin/agencies/4",
    },
    {
      id: 9,
      slug: "lakeside",
      name: "Lakeside Agency",
      active: true,
      status: {
        label: "Active",
        description: "Customers may be assigned to this Agency.",
        tone: "success",
      },
      members: [
        {
          id: 11,
          slug: "lakeside-brokers",
          name: "Lakeside Brokers",
          active: true,
          licensedStates: [],
          href: "/app/admin/customers/11",
        },
      ],
      currentMembers: 1,
      sharedHistory: {
        customers: 2,
        agencies: 1,
        distributedLeads: 110,
      },
      lastActivityAt: "2026-07-18T12:00:00Z",
      href: "/app/admin/agencies/9",
    },
  ],
  independent: [
    {
      id: 12,
      slug: "independent-risk",
      name: "Independent Risk",
      active: true,
      licensedStates: ["GA"],
      href: "/app/admin/customers/12",
    },
  ],
};

const AGENCY_DETAILS = {
  agency: {
    id: 4,
    slug: "gulf-coast",
    name: "Gulf Coast Agency",
    active: true,
    status: {
      label: "Active",
      description: "Customers may be assigned to this Agency.",
      tone: "success",
    },
  },
  members: [
    {
      id: 7,
      slug: "harbor-insurance",
      name: "Harbor Insurance",
      active: true,
      licensedStates: ["FL", "TX"],
      href: "/app/admin/customers/7",
    },
  ],
  sharedHistory: {
    customers: 4,
    agencies: 2,
    distributedLeads: 240,
  },
  membershipHistory: [
    {
      id: 1,
      customerId: 7,
      customer: "Harbor Insurance",
      startedAt: "2026-01-20T12:00:00Z",
      endedAt: null,
      assignedBy: "admin@jawnix.example",
      reason: "Initial servicing assignment",
    },
  ],
  activity: [
    {
      id: "membership-1",
      action: "customer_assigned",
      label: "Harbor Insurance assigned",
      actor: "admin@jawnix.example",
      reason: "Initial servicing assignment",
      createdAt: "2026-01-20T12:00:00Z",
    },
  ],
  deletion: {
    dependencies: {
      customers: 1,
      distributions: 240,
      membershipHistory: 1,
    },
    requiresDeactivation: true,
    canHardDelete: false,
  },
};

const STATES = ["FL", "GA", "NY", "TX"];

const ACTIVE_CUSTOMER = {
  label: "Active",
  description: "This Customer can receive Batches.",
  tone: "success",
};

function daysAgo(days: number, hour = 12): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  date.setHours(hour, 0, 0, 0);
  return date.toISOString();
}

function todayAt(hours: number, minutes = 0): string {
  const date = new Date();
  date.setHours(hours, minutes, 0, 0);
  return date.toISOString();
}

const ACCOUNT_ACTIVE = {
  label: "Account active",
  description: "The User Account can sign in.",
  tone: "success",
};

interface CustomerSeed {
  id: number;
  slug: string;
  name: string;
  agency_id?: number | null;
  agency?: string;
  licensed_states?: string[];
  account_email?: string;
  account_status?: typeof ACCOUNT_ACTIVE;
  problems?: string[];
  lead_database?: "db1" | "db2";
  needs?: { kind: string; label: string; href: string }[];
  billed?: boolean;
  balance_cents?: number;
  held_cents?: number;
  held_for?: number[];
  open_batches?: number;
  open_batch?: { number: number; leadCount: number; stage: string; href: string } | null;
  delivered_30d?: number;
  delivered_30d_batches?: number;
  last_delivery_at?: string | null;
  available?: number | null;
  active?: boolean;
}

function customerRow(seed: CustomerSeed) {
  const billed = seed.billed ?? true;
  return {
    id: seed.id,
    slug: seed.slug,
    name: seed.name,
    agency_id: seed.agency_id ?? null,
    agency: seed.agency ?? "",
    licensed_states: seed.licensed_states ?? ["FL"],
    customer_status: seed.active === false
      ? { label: "Deactivated", description: "Blocked from sign-in and new Batch Requests.", tone: "warning" }
      : ACTIVE_CUSTOMER,
    account_status: seed.account_status ?? ACCOUNT_ACTIVE,
    account_email: seed.account_email ?? `owner@${seed.slug}.example`,
    last_activity_at: seed.last_delivery_at ?? null,
    problems: seed.problems ?? [],
    href: `/app/admin/customers/${seed.id}`,
    lead_database: seed.lead_database ?? "db1",
    needs: seed.needs ?? [],
    billed,
    balance_cents: seed.balance_cents ?? 0,
    held_cents: seed.held_cents ?? 0,
    held_for: seed.held_for ?? [],
    lead_rate_cents_per_thousand: billed ? 500 : null,
    open_batches: seed.open_batches ?? 0,
    open_batch: seed.open_batch ?? null,
    delivered_30d: seed.delivered_30d ?? 0,
    delivered_30d_batches: seed.delivered_30d_batches ?? 0,
    last_delivery_at: seed.last_delivery_at ?? null,
    available: seed.available ?? null,
    available_as_of: seed.available === undefined || seed.available === null ? null : todayAt(6),
    active: seed.active ?? true,
  };
}

export const CONFLICT_HREF = "/app/admin/fulfillment/conflicts/33333333-3333-4333-8333-333333333333";

/** In the order the backend sorts them: needs first, worst first, then the
 *  busiest customers. */
const CUSTOMERS = [
  customerRow({
    id: 3,
    slug: "northstar-insurance",
    name: "Northstar Insurance",
    agency_id: 9,
    agency: "Lakeside Agency",
    needs: [{ kind: "approval", label: "Approve #1042", href: "/app/admin/batches/1042" }],
    balance_cents: 41_250,
    held_cents: 12_500,
    held_for: [1042],
    open_batches: 1,
    open_batch: { number: 1042, leadCount: 25_000, stage: "awaiting_approval", href: "/app/admin/batches/1042" },
    delivered_30d: 150_000,
    delivered_30d_batches: 3,
    last_delivery_at: daysAgo(2),
    available: 1_188_205,
  }),
  customerRow({
    id: 5,
    slug: "pinecrest-agency",
    name: "Pinecrest Agency",
    licensed_states: ["GA"],
    needs: [{ kind: "deliveryFailed", label: "Re-notify #1040", href: "/app/admin/batches/1040" }],
    balance_cents: 12_000,
    open_batches: 1,
    open_batch: { number: 1040, leadCount: 25_000, stage: "delivery_failed", href: "/app/admin/batches/1040" },
    delivered_30d: 75_000,
    delivered_30d_batches: 2,
    last_delivery_at: daysAgo(0),
  }),
  customerRow({
    id: 6,
    slug: "ridgeline-benefits",
    name: "Ridgeline Benefits",
    licensed_states: ["AZ"],
    needs: [{ kind: "short", label: "#1039 short 31,590", href: "/app/admin/batches/1039" }],
    balance_cents: 100_000,
    open_batches: 1,
    open_batch: { number: 1039, leadCount: 100_000, stage: "waiting", href: "/app/admin/batches/1039" },
    delivered_30d: 200_000,
    delivered_30d_batches: 2,
    last_delivery_at: daysAgo(12),
  }),
  customerRow({
    id: 7,
    slug: "harbor-insurance",
    name: "Harbor Insurance",
    agency_id: 4,
    agency: "Gulf Coast Agency",
    licensed_states: ["FL", "TX"],
    account_email: "owner@harbor.example",
    needs: [{ kind: "conflict", label: "Conflict with Lakeside Brokers", href: CONFLICT_HREF }],
    balance_cents: 123_200,
    held_cents: 25_000,
    held_for: [1041],
    open_batches: 1,
    open_batch: { number: 1041, leadCount: 50_000, stage: "generating", href: "/app/admin/batches/1041" },
    delivered_30d: 300_000,
    delivered_30d_batches: 5,
    last_delivery_at: daysAgo(2),
    available: 412_880,
  }),
  customerRow({
    id: 8,
    slug: "gulfshore-advisors",
    name: "Gulfshore Advisors",
    needs: [{ kind: "report", label: "Report · invalid phone", href: "/app/admin/fulfillment/reports/44444444-4444-4444-8444-444444444444" }],
    balance_cents: 3_100,
    delivered_30d: 69_666,
    delivered_30d_batches: 1,
    last_delivery_at: daysAgo(9),
  }),
  customerRow({
    id: 11,
    slug: "lakeside-brokers",
    name: "Lakeside Brokers",
    agency_id: 9,
    agency: "Lakeside Agency",
    licensed_states: [],
    lead_database: "db2",
    billed: false,
    account_email: "owner@lakeside.example",
    account_status: {
      label: "Invitation sent",
      description: "The invited address has not signed in yet.",
      tone: "warning",
    },
    problems: ["Invitation has not been accepted yet", "No Licensed States"],
    needs: [
      { kind: "setup", label: "Setup · invitation not accepted", href: "/app/admin/customers/11/access" },
      { kind: "setup", label: "Setup · no appointed states", href: "/app/admin/customers/11/rules" },
    ],
  }),
  customerRow({
    id: 15,
    slug: "summit-benefits-group",
    name: "Summit Benefits Group",
    licensed_states: ["PA", "OH"],
    balance_cents: 251_800,
    delivered_30d: 450_000,
    delivered_30d_batches: 4,
    last_delivery_at: daysAgo(0),
  }),
  customerRow({
    id: 16,
    slug: "meridian-health-brokers",
    name: "Meridian Health Brokers",
    lead_database: "db2",
    billed: false,
    delivered_30d: 175_000,
    delivered_30d_batches: 2,
    last_delivery_at: daysAgo(5),
  }),
  customerRow({
    id: 17,
    slug: "bluewater-insurance",
    name: "Bluewater Insurance",
    balance_cents: 68_400,
    delivered_30d: 150_000,
    delivered_30d_batches: 2,
    last_delivery_at: daysAgo(3),
  }),
  customerRow({
    id: 18,
    slug: "keystone-medicare",
    name: "Keystone Medicare",
    active: false,
    billed: false,
    last_delivery_at: daysAgo(80),
  }),
];

const SUMMARY = {
  customerId: 7,
  database: "db1",
  states: ["FL", "TX"],
  customerSince: daysAgo(300),
  needs: [{ kind: "conflict", label: "Conflict with Lakeside Brokers", href: CONFLICT_HREF }],
  balance: {
    billed: true,
    availableCents: 123_200,
    heldCents: 25_000,
    heldFor: [1041],
    rateCentsPerThousand: 500,
  },
  available: { count: 412_880, asOf: todayAt(6) },
  openBatches: {
    count: 1,
    first: { number: 1041, leadCount: 50_000, stage: "generating", href: "/app/admin/batches/1041" },
  },
  delivered: { windowDays: 30, leads: 300_000, batches: 5 },
  lastDeliveryAt: daysAgo(2),
  leadReports: { open: 0, total: 1 },
};

const LEAD_REPORTS = [
  {
    id: "55555555-5555-4555-8555-555555555555",
    href: "/app/admin/fulfillment/reports/55555555-5555-4555-8555-555555555555",
    reason: "wrong_business_or_title",
    reasonLabel: "Wrong business or title",
    details: "Number reaches a dentist, not a roofer.",
    status: "dismissed",
    statusLabel: "Dismissed",
    createdAt: daysAgo(20),
    lead: { phone: "8135550142", title: "Bayside Roofing", state: "FL" },
    batch: { number: 1027, href: "/app/admin/batches/1027" },
    eligibilityHeld: false,
  },
];

function batchRow(
  number: number,
  leadCount: number,
  states: string[],
  stage: string,
  status: string,
  createdAt: string,
  cost: { billed: boolean; amountCents?: number; hold?: string },
  needsDecision = false,
) {
  return {
    id: `${number}${number}-${number}-4${String(number).slice(1)}-8${String(number).slice(1)}-${number}${number}${number}`,
    number,
    href: `/app/admin/batches/${number}`,
    customer: { id: 7, name: "Harbor Insurance", href: "/app/admin/customers/7" },
    leadCount,
    states,
    database: "db1",
    status,
    stage,
    statusMessage: "",
    availability: { kind: "committed", committed: leadCount },
    needsDecision,
    createdAt,
    deliveredAt: stage === "delivered" ? createdAt : null,
    closedAt: null,
    cost,
  };
}

/** Harbor Insurance's batches, newest first. #1036 waits on the conflict. */
const HARBOR_BATCHES = [
  batchRow(1041, 50_000, ["FL", "TX"], "generating", "processing", todayAt(8, 2), { billed: true, amountCents: 25_000, hold: "active" }),
  batchRow(1036, 25_000, ["FL"], "waiting", "waiting_inventory", daysAgo(1), { billed: true, amountCents: 12_500, hold: "active" }, true),
  batchRow(1033, 50_000, ["FL", "TX"], "delivered", "delivered", daysAgo(2), { billed: true, amountCents: 25_000, hold: "captured" }),
  batchRow(1027, 100_000, ["TX"], "delivered", "delivered", daysAgo(7), { billed: true, amountCents: 50_000, hold: "captured" }),
  batchRow(1019, 75_000, ["FL"], "delivered", "delivered", daysAgo(16), { billed: true, amountCents: 37_500, hold: "captured" }),
];

const HARBOR_1041 = {
  ...BATCH_DETAILS[1041],
  email: "owner@harbor.example",
  customer: "Casey Reyes",
  agencyRecord: { id: 4, name: "Gulf Coast Agency", href: "/app/admin/agencies/4" },
  actions: [
    {
      name: "cancel",
      label: "Cancel batch",
      consequence:
        "Withdraws this Batch Request before any Distribution Event commits. Cancellation is terminal.",
      requiresReason: true,
      destructive: true,
    },
  ],
  cost: {
    billed: true,
    rateCentsPerThousand: 500,
    amountCents: 25_000,
    hold: "active",
    availableBalanceCents: 123_200,
  },
  progress: [
    { key: "submitted", label: "Submitted", done: true, at: todayAt(8, 2) },
    { key: "approved", label: "Approved", done: true, at: todayAt(8, 10) },
    { key: "allocated", label: "Allocated", done: true, at: todayAt(8, 11) },
    { key: "generated", label: "File generated", done: false, at: null },
    { key: "delivered", label: "Delivered", done: false, at: null },
  ],
};

const CURRENT_ACCOUNT = {
  auth_user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "owner@harbor.example",
  name: "Casey Reyes",
  active: true,
  created_at: "2026-01-05T12:00:00Z",
  replaced_at: null,
  replaced_by_auth_user_id: null,
};

const DETAILS = {
  customer: {
    id: 7,
    slug: "harbor-insurance",
    name: "Harbor Insurance",
    agency_id: 4,
    agency: "Gulf Coast Agency",
    active: true,
    licensed_states: ["FL", "TX"],
    status: ACTIVE_CUSTOMER,
    last_activity_at: "2026-07-20T12:00:00Z",
    lead_database: "db1",
  },
  history: {
    requests: 18,
    distributions: 16,
    outcomes: 240,
    reports: 9,
    first_delivered_at: "2026-01-20T12:00:00Z",
    last_delivered_at: "2026-07-20T12:00:00Z",
  },
  user_account: CURRENT_ACCOUNT,
  invitation: null as unknown,
  former_accounts: [
    {
      auth_user_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      email: "previous@harbor.example",
      name: "Jordan Vale",
      active: false,
      created_at: "2025-03-01T12:00:00Z",
      replaced_at: "2026-01-05T12:00:00Z",
      replaced_by_auth_user_id: CURRENT_ACCOUNT.auth_user_id,
    },
  ],
  activity: [
    {
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      action: "user_account_replaced",
      label: "User Account replaced",
      actor: "admin@jawnix.example",
      reason: "Owner changed",
      created_at: "2026-01-05T12:00:00Z",
    },
  ],
  deletion: {
    dependencies: { requests: 18, distributions: 16, userAccounts: 1 },
    requires_deactivation: true,
    can_hard_delete: false,
    tombstoned: false,
  },
};

const PENDING_INVITATION = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  email: "newowner@harbor.example",
  invited_at: "2026-07-25T12:00:00Z",
  replaces_auth_user_id: CURRENT_ACCOUNT.auth_user_id,
  status: {
    label: "Awaiting acceptance",
    description: "The invited address has not signed in yet.",
    tone: "info",
  },
};

const BILLING = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 500 as number | null,
  balanceCents: 148_200,
  activeHoldsCents: 25_000,
  availableBalanceCents: 123_200,
  purchases: [
    {
      id: "abcdabcd-abcd-4bcd-8bcd-abcdabcdabcd",
      amountCents: 170_700,
      status: "completed",
      createdAt: "2026-07-02T15:00:00Z",
      completedAt: "2026-07-02T15:01:00Z",
    },
  ] as {
    id: string;
    amountCents: number;
    status: string;
    createdAt: string;
    completedAt: string | null;
  }[],
  ledger: [
    {
      id: "eeeeeeee-1033-4eee-8eee-eeeeeeeeeeee",
      kind: "batch_charge",
      amountCents: -25_000,
      reason: "Batch #1033 delivered",
      actor: "system:allocation",
      actorLabel: "System",
      batchRequestId: "10331033-1033-4033-8033-103310331033" as string | null,
      batchNumber: 1033 as number | null,
      createdAt: "2026-07-10T12:00:00Z",
    },
    {
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      kind: "purchase",
      amountCents: 170_700,
      reason: "Card purchase",
      actor: "system:stripe",
      actorLabel: "Stripe",
      batchRequestId: null as string | null,
      batchNumber: null as number | null,
      createdAt: "2026-07-02T15:01:00Z",
    },
    {
      id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
      kind: "admin_adjustment",
      amountCents: 2500,
      reason: "Opening credit",
      actor: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      actorLabel: "Operator",
      batchRequestId: null as string | null,
      batchNumber: null as number | null,
      createdAt: "2026-07-01T12:00:00Z",
    },
  ],
};

const COOLDOWN = { days: 7 };

const NICHE_POLICY: {
  rows: {
    state: string | null;
    mode: "exclude" | "only";
    niches: string[];
  }[];
} = {
  rows: [
    {
      state: null,
      mode: "exclude",
      niches: ["roofing"],
    },
  ],
};

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

function matches(customer: ReturnType<typeof customerRow>, term: string): boolean {
  const needle = term.trim().toLowerCase();
  if (!needle) return true;
  return [customer.name, customer.slug, customer.account_email].some((value) =>
    value.toLowerCase().includes(needle),
  );
}

export async function mockAdminCustomers(
  page: Page,
  options: AdminCustomersMockOptions = {},
): Promise<AdminCustomersMockState> {
  const state: AdminCustomersMockState = {
    directoryRequests: [],
    detailsRequests: [],
    invitationRequests: [],
    assignmentRequests: [],
    customerPatchRequests: [],
    passwordResetRequests: [],
    billingPutRequests: [],
    adjustmentRequests: [],
    cooldownPutRequests: [],
    nichePolicyPutRequests: [],
    nichePolicyPreviewRequests: [],
    availabilityRefreshRequests: 0,
    databaseRequests: [],
    customerBatchRequests: [],
  };

  const billing = structuredClone(BILLING);
  const cooldown = structuredClone(COOLDOWN);
  const nichePolicy = structuredClone(NICHE_POLICY);
  const availability = {
    asOf: todayAt(6),
    available: 412_880,
    forecast: [
      { offset: 0, date: "2026-08-03", count: 412_880 },
      { offset: 1, date: "2026-08-04", count: 18 },
      { offset: 2, date: "2026-08-05", count: 0 },
      { offset: 3, date: "2026-08-06", count: 7 },
      { offset: 4, date: "2026-08-07", count: 0 },
      { offset: 5, date: "2026-08-08", count: 0 },
      { offset: 6, date: "2026-08-09", count: 0 },
      { offset: 7, date: "2026-08-10", count: 0 },
      { offset: 8, date: "2026-08-11", count: 0 },
      { offset: 9, date: "2026-08-12", count: 0 },
      { offset: 10, date: "2026-08-13", count: 0 },
      { offset: 11, date: "2026-08-14", count: 0 },
      { offset: 12, date: "2026-08-15", count: 0 },
      { offset: 13, date: "2026-08-16", count: 0 },
      { offset: 14, date: "2026-08-17", count: 0 },
    ],
  };

  await page.addInitScript(() => {
    document.cookie = "jawnix_csrf=e2e-csrf; path=/";
  });

  await page.route(/\/api\/auth\/admin-mfa\/access$/, (route) =>
    json(route, { ok: true }),
  );
  await page.route(/\/api\/admin\/registrations$/, route => json(route, options.registrations ?? []));

  await page.route(/\/api\/admin\/customers\/directory/, (route) => {
    const url = new URL(route.request().url());
    state.directoryRequests.push(route.request().url());
    const term = url.searchParams.get("q") ?? "";
    const status = url.searchParams.get("status") ?? "active";
    const agency = url.searchParams.get("agency_id");
    const database = url.searchParams.get("database") ?? "";
    const billing = url.searchParams.get("billing") ?? "";
    const needsOnly = url.searchParams.get("needs_only") === "true";
    const rows = CUSTOMERS.filter(
      (customer) =>
        matches(customer, term) &&
        (status === "all" || (status === "active") === customer.active) &&
        (!agency || String(customer.agency_id) === agency) &&
        (!database || customer.lead_database === database) &&
        (!billing || (billing === "billed") === customer.billed) &&
        (!needsOnly || customer.needs.length > 0),
    ).map(({ active: _active, ...row }) => row);
    return json(route, {
      filters: {
        query: term,
        status,
        agency_id: agency ? Number(agency) : null,
        state: "",
        problems_only: false,
        database,
        billing,
        needs_only: needsOnly,
      },
      agencies: AGENCIES,
      states: STATES,
      total: CUSTOMERS.length,
      matched: rows.length,
      counts: {
        active: CUSTOMERS.filter((customer) => customer.active).length,
        deactivated: CUSTOMERS.filter((customer) => !customer.active).length,
      },
      customers: rows,
    });
  });

  await page.route(/\/api\/admin\/customers\/\d+\/summary$/, (route) => json(route, SUMMARY));

  await page.route(/\/api\/admin\/customers\/\d+\/lead-reports$/, (route) =>
    json(route, LEAD_REPORTS),
  );

  await page.route(/\/api\/admin\/customers\/\d+\/lead-database$/, (route) => {
    state.databaseRequests.push({
      url: route.request().url(),
      body: route.request().postDataJSON(),
    });
    const body = route.request().postDataJSON() as { lead_database: string };
    return json(route, { customerId: 7, leadDatabase: body.lead_database, changed: true });
  });

  // One Customer's batches. Anything else falls through to the operator
  // desk fixtures when a spec registers both.
  await page.route(/\/api\/admin\/batches(\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    if (!url.searchParams.has("customer")) return route.fallback();
    state.customerBatchRequests.push(route.request().url());
    const items = url.searchParams.get("customer") === "7" ? HARBOR_BATCHES : [];
    return json(route, {
      items,
      page: 1,
      pageSize: 25,
      total: items.length,
      pages: 1,
      counts: { delivered: 3, expired: 0 },
    });
  });

  await page.route(/\/api\/admin\/batches\/1041$/, (route) => json(route, HARBOR_1041));

  await page.route(/\/api\/admin\/customers\/\d+\/details$/, (route) => {
    state.detailsRequests.push(route.request().url());
    return json(route, {
      ...DETAILS,
      invitation: options.pendingInvitation ? PENDING_INVITATION : null,
    });
  });

  await page.route(
    /\/api\/admin\/customers\/\d+\/exclusion-lists$/,
    (route) => json(route, [
      {
        id: "abababab-abab-4bab-8bab-abababababab",
        type: "dnc",
        filename: "harbor-dnc.csv",
        status: "active",
        totalRows: 250,
        acceptedRows: 248,
        invalidRows: 2,
        duplicateRows: 4,
        poolImpact: 61,
        global: false,
        error: "",
        createdAt: "2026-07-28T10:00:00Z",
        ingestedAt: "2026-07-28T10:05:00Z",
        decidedAt: null,
      },
    ]),
  );

  await page.route(/\/api\/admin\/agencies\/directory(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    const term = (url.searchParams.get("q") ?? "").toLowerCase();
    const rows = AGENCY_DIRECTORY.agencies.filter(
      (agency) =>
        !term ||
        agency.name.toLowerCase().includes(term) ||
        agency.slug.includes(term) ||
        agency.members.some(
          (member) =>
            member.name.toLowerCase().includes(term) ||
            member.slug.includes(term),
        ),
    );
    return json(route, {
      ...AGENCY_DIRECTORY,
      filters: {
        query: url.searchParams.get("q") ?? "",
        status: url.searchParams.get("status") ?? "all",
      },
      matched: rows.length,
      agencies: rows,
    });
  });

  await page.route(/\/api\/admin\/agencies\/4\/details$/, (route) =>
    json(route, AGENCY_DETAILS),
  );

  await page.route(
    /\/api\/admin\/customers\/\d+\/agency-assignment-preview(?:\?.*)?$/,
    (route) =>
      json(route, {
        customer: {
          id: 7,
          name: "Harbor Insurance",
          agencyId: 4,
          agency: "Gulf Coast Agency",
        },
        destination: {
          id: 9,
          name: "Lakeside Agency",
          active: true,
          currentMembers: 2,
        },
        inventory: { eligibleBefore: 460, eligibleAfter: 350 },
        sharedHistory: {
          customersAfter: 6,
          agenciesAfter: 2,
          distributedLeadsAfter: 350,
        },
        consequences: {
          customerHistoryBlockedForDestination: 240,
          destinationHistoryBlockedForCustomer: 110,
          historyMergeIsPermanent: true,
        },
      }),
  );

  await page.route(
    /\/api\/admin\/user-accounts\/[^/]+\/send-password-reset$/,
    (route) => {
      state.passwordResetRequests.push(route.request().url());
      return json(route, { ok: true });
    },
  );

  await page.route(/\/api\/admin\/customers\/\d+$/, (route) => {
    state.customerPatchRequests.push(route.request().postDataJSON());
    return json(route, { ok: true });
  });

  await page.route(
    /\/api\/admin\/customers\/\d+\/agency-assignment$/,
    (route) => {
      state.assignmentRequests.push(route.request().postDataJSON());
      return json(route, { ok: true });
    },
  );

  await page.route(
    /\/api\/admin\/customers\/\d+\/user-account-invitation$/,
    (route) => {
      state.invitationRequests.push(route.request().postDataJSON());
      return json(
        route,
        {
          customerId: 7,
          authUserId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
          email: "newowner@harbor.example",
          licensedStates: ["FL", "TX"],
          activated: false,
          invitationId: PENDING_INVITATION.id,
          replacesAuthUserId: CURRENT_ACCOUNT.auth_user_id,
        },
        201,
      );
    },
  );

  await page.route(/\/api\/admin\/activity/, (route) =>
    json(route, {
      entries: [],
      page: 1,
      pageSize: 25,
      total: 0,
      pages: 1,
    }),
  );

  await page.route(/\/api\/admin\/customers\/\d+\/billing$/, (route) => {
    if (route.request().method() === "GET") {
      return json(route, billing);
    }
    const body = route.request().postDataJSON() as {
      billing_enabled: boolean;
      lead_rate_cents_per_thousand: number | null;
      reason: string;
    };
    state.billingPutRequests.push(body);
    billing.billingEnabled = body.billing_enabled;
    billing.leadRateCentsPerThousand = body.lead_rate_cents_per_thousand;
    return json(route, billing);
  });

  await page.route(
    /\/api\/admin\/customers\/\d+\/billing\/adjustments$/,
    (route) => {
      const body = route.request().postDataJSON() as {
        amount_cents: number;
        reason: string;
      };
      state.adjustmentRequests.push(body);
      billing.balanceCents += body.amount_cents;
      billing.availableBalanceCents =
        billing.balanceCents - billing.activeHoldsCents;
      billing.ledger.unshift({
        id: `adjustment-${state.adjustmentRequests.length}`,
        kind: "admin_adjustment",
        amountCents: body.amount_cents,
        reason: body.reason,
        actor: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        actorLabel: "Operator",
        batchRequestId: null,
        batchNumber: null,
        createdAt: "2026-07-28T12:00:00Z",
      });
      return json(route, billing, 201);
    },
  );

  await page.route(/\/api\/admin\/customers\/\d+\/cooldown-window$/, (route) => {
    if (route.request().method() === "GET") {
      return json(route, cooldown);
    }
    const body = route.request().postDataJSON() as {
      days: number;
      reason: string;
    };
    state.cooldownPutRequests.push(body);
    cooldown.days = body.days;
    return json(route, cooldown);
  });

  await page.route(/\/api\/admin\/customers\/\d+\/niche-policy$/, (route) => {
    if (route.request().method() === "GET") {
      return json(route, nichePolicy);
    }
    const body = route.request().postDataJSON() as {
      rows: {
        state: string | null;
        mode: "exclude" | "only";
        niches: string[];
      }[];
      reason: string;
    };
    state.nichePolicyPutRequests.push(body);
    nichePolicy.rows = body.rows;
    return json(route, nichePolicy);
  });

  await page.route(
    /\/api\/admin\/customers\/\d+\/niche-policy\/projected-availability$/,
    (route) => {
      state.nichePolicyPreviewRequests.push(route.request().postDataJSON());
      return json(route, {
        available: 128,
        asOf: "2026-08-03T15:00:00Z",
      });
    },
  );

  await page.route(
    /\/api\/admin\/customers\/\d+\/availability(?:\/refresh)?$/,
    (route) => {
      if (route.request().method() === "POST") {
        state.availabilityRefreshRequests += 1;
        availability.asOf = todayAt(9, 30);
        availability.available = 420_000;
        availability.forecast[0] = {
          offset: 0,
          date: "2026-08-03",
          count: 420_000,
        };
        return json(route, availability);
      }
      return json(route, availability);
    },
  );

  return state;
}
