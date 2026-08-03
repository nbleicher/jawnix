import type { Page, Route } from "@playwright/test";

export interface AdminCustomersMockOptions {
  /** Serves the details payload that has an unaccepted replacement pending. */
  pendingInvitation?: boolean;
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

const CUSTOMERS = [
  {
    id: 7,
    slug: "harbor-insurance",
    name: "Harbor Insurance",
    agency_id: 4,
    agency: "Gulf Coast Agency",
    licensed_states: ["FL", "TX"],
    customer_status: ACTIVE_CUSTOMER,
    account_status: {
      label: "Account active",
      description: "The User Account can sign in.",
      tone: "success",
    },
    account_email: "owner@harbor.example",
    last_activity_at: "2026-07-20T12:00:00Z",
    problems: [],
    href: "/app/admin/customers/7",
  },
  {
    id: 11,
    slug: "lakeside-brokers",
    name: "Lakeside Brokers",
    agency_id: 9,
    agency: "Lakeside Agency",
    licensed_states: [],
    customer_status: ACTIVE_CUSTOMER,
    account_status: {
      label: "Invitation sent",
      description: "The invited address has not signed in yet.",
      tone: "warning",
    },
    account_email: "owner@lakeside.example",
    last_activity_at: null,
    problems: ["Invitation has not been accepted yet", "No Licensed States"],
    href: "/app/admin/customers/11",
  },
];

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
  billingEnabled: false,
  leadRateCentsPerThousand: null as number | null,
  balanceCents: 2500,
  activeHoldsCents: 500,
  availableBalanceCents: 2000,
  purchases: [] as {
    id: string;
    amountCents: number;
    status: string;
    createdAt: string;
    completedAt: string | null;
  }[],
  ledger: [
    {
      id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
      kind: "admin_adjustment",
      amountCents: 2500,
      reason: "Opening credit",
      actor: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      batchRequestId: null as string | null,
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

function matches(customer: (typeof CUSTOMERS)[number], term: string): boolean {
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
  };

  const billing = structuredClone(BILLING);
  const cooldown = structuredClone(COOLDOWN);
  const nichePolicy = structuredClone(NICHE_POLICY);
  const availability = {
    asOf: "2026-08-03T12:00:00Z",
    available: 412,
    forecast: [
      { offset: 0, date: "2026-08-03", count: 412 },
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

  await page.route(/\/api\/admin\/customers\/directory/, (route) => {
    const url = new URL(route.request().url());
    state.directoryRequests.push(route.request().url());
    const term = url.searchParams.get("q") ?? "";
    const problemsOnly = url.searchParams.get("problems_only") === "true";
    const rows = CUSTOMERS.filter(
      (customer) =>
        matches(customer, term) && (!problemsOnly || customer.problems.length),
    );
    return json(route, {
      filters: {
        query: term,
        status: url.searchParams.get("status") ?? "all",
        agency_id: url.searchParams.get("agency_id")
          ? Number(url.searchParams.get("agency_id"))
          : null,
        state: url.searchParams.get("state") ?? "",
        problems_only: problemsOnly,
      },
      agencies: AGENCIES,
      states: STATES,
      total: CUSTOMERS.length,
      matched: rows.length,
      customers: rows,
    });
  });

  await page.route(/\/api\/admin\/customers\/\d+\/details$/, (route) => {
    state.detailsRequests.push(route.request().url());
    return json(route, {
      ...DETAILS,
      invitation: options.pendingInvitation ? PENDING_INVITATION : null,
    });
  });

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
        batchRequestId: null,
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
        availability.asOf = "2026-08-03T15:30:00Z";
        availability.available = 420;
        availability.forecast[0] = {
          offset: 0,
          date: "2026-08-03",
          count: 420,
        };
        return json(route, availability);
      }
      return json(route, availability);
    },
  );

  return state;
}
