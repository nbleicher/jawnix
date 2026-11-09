import type { Page, Route } from "@playwright/test";

/**
 * Seeded Setup and Agencies reads. Shapes mirror `operator_setup` in
 * jawnix/operator_setup.py and `agency_directory` / `agency_details` in
 * jawnix/agency_management.py; figures match the approved render (op-10).
 */

function json(route: Route, value: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

function area(
  key: string,
  label: string,
  href: string,
  state: string,
  extra: Record<string, unknown> = {},
) {
  return {
    key,
    label,
    href,
    state,
    attention: false,
    changedAt: null,
    changedNote: null,
    upcoming: null,
    ...extra,
  };
}

export const SETUP = {
  generatedAt: "2026-11-09T12:00:00Z",
  areas: [
    area("scraperWorkspace", "Scraper workspace", "/app/admin/acquisition/scraper", "Run 42 committed", {
      changedAt: "2026-11-09T05:12:00Z",
    }),
    area(
      "scraperSettings",
      "Scraper settings",
      "/app/admin/acquisition/scraper/workspace/runtime",
      "12 active states · queue depth 50",
      { changedAt: "2026-11-01T15:00:00Z" },
    ),
    area("configurations", "Configuration versions", "/app/admin/setup/configurations", "v12 active · 1,240 segments", {
      changedAt: "2026-11-01T15:00:00Z",
    }),
    area("keywords", "Keywords", "/app/admin/acquisition/scraper/workspace/keywords", "418 keywords in reserve", {
      changedAt: "2026-11-04T15:00:00Z",
      upcoming: { label: "next refill", at: "2026-11-15T15:00:00Z" },
    }),
    area("nicheMappings", "Niche mappings", "/app/admin/setup/niche-mappings", "1 awaiting review", {
      attention: true,
      changedAt: "2026-11-08T15:00:00Z",
    }),
    area("agencies", "Agencies", "/app/admin/agencies", "2 active · 14 members", {
      changedAt: "2026-10-02T15:00:00Z",
    }),
    area("security", "Security", "/app/admin/security", "Authenticator required at every sign-in", {
      changedAt: "2026-07-24T15:00:00Z",
    }),
    area("billing", "Billing defaults", "/app/admin/setup?area=billing", "DB1 starting rate $5.00 per 1,000", {
      changedNote: "Server setting",
    }),
  ],
  billing: { enabled: true, startingRateCentsPerThousand: 500, billedCustomers: 11 },
};

const ACTIVE = {
  label: "Active",
  description: "Customers may be assigned to this Agency.",
  tone: "success",
};

function member(
  id: number,
  name: string,
  states: string[],
  delivered30d: number,
  openBatches: number,
  active = true,
) {
  return {
    id,
    slug: name.toLowerCase().replaceAll(" ", "-"),
    name,
    active,
    licensedStates: states,
    delivered30d,
    openBatches,
    href: `/app/admin/customers/${id}`,
  };
}

const GULF_MEMBERS = [
  member(7, "Harbor Insurance", ["FL", "TX"], 300_000, 1),
  member(8, "Pinecrest Agency", ["GA"], 75_000, 1),
];
const LAKESIDE_MEMBERS = [
  member(3, "Ridgeline Benefits", ["AZ"], 200_000, 1),
  member(11, "Lakeside Brokers", [], 0, 0),
];

export const AGENCY_DIRECTORY = {
  filters: { query: "", status: "all" },
  total: 2,
  matched: 2,
  agencies: [
    {
      id: 4,
      slug: "gulf-coast",
      name: "Gulf Coast Agency",
      active: true,
      status: ACTIVE,
      members: GULF_MEMBERS,
      currentMembers: GULF_MEMBERS.length,
      delivered30d: 375_000,
      openBatches: 2,
      sharedHistory: { customers: 4, agencies: 1, distributedLeads: 1_240_000 },
      lastActivityAt: "2026-10-02T15:00:00Z",
      href: "/app/admin/agencies/4",
    },
    {
      id: 9,
      slug: "lakeside",
      name: "Lakeside Agency",
      active: true,
      status: ACTIVE,
      members: LAKESIDE_MEMBERS,
      currentMembers: LAKESIDE_MEMBERS.length,
      delivered30d: 200_000,
      openBatches: 1,
      sharedHistory: { customers: 2, agencies: 1, distributedLeads: 650_000 },
      lastActivityAt: "2026-09-18T15:00:00Z",
      href: "/app/admin/agencies/9",
    },
  ],
  independent: [
    member(1, "Summit Benefits Group", ["OH", "PA"], 450_000, 0),
    member(12, "Northstar Insurance", ["FL", "TX"], 150_000, 1),
  ],
};

export const AGENCY_DETAILS = {
  agency: { id: 4, slug: "gulf-coast", name: "Gulf Coast Agency", active: true, status: ACTIVE },
  members: GULF_MEMBERS,
  sharedHistory: { customers: 4, agencies: 2, distributedLeads: 1_240_000 },
  membershipHistory: [
    {
      id: 2,
      customerId: 8,
      customer: "Pinecrest Agency",
      customerHref: "/app/admin/customers/8",
      startedAt: "2026-10-02T15:00:00Z",
      endedAt: null,
      assignedBy: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      assignedByName: "Operator",
      reason: "Consolidated servicing ownership.",
    },
    {
      id: 1,
      customerId: 7,
      customer: "Harbor Insurance",
      customerHref: "/app/admin/customers/7",
      startedAt: "2026-01-20T15:00:00Z",
      endedAt: null,
      assignedBy: "system:existing-membership",
      assignedByName: "System",
      reason: "Imported existing membership.",
    },
  ],
  activity: [],
  deletion: {
    dependencies: { customers: 2, distributions: 1_240_000, membershipHistory: 2 },
    requiresDeactivation: true,
    canHardDelete: false,
  },
};

/** Setup's own read, plus the Scraper grant check its loader makes. */
export async function mockSetup(page: Page, options: { granted?: boolean } = {}) {
  await page.route(/\/api\/admin\/setup$/, (route) => json(route, SETUP));
  await page.route(/\/api\/admin\/scraper\/grant$/, (route) =>
    json(route, { granted: options.granted ?? true }),
  );
}

/** Registered after `mockAdminCustomers`, these win for the Agency reads. */
export async function mockAgencies(page: Page) {
  await page.route(/\/api\/admin\/agencies\/directory(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const status = url.searchParams.get("status") ?? "all";
    const agencies = AGENCY_DIRECTORY.agencies.filter(
      (agency) =>
        (status === "all" || (status === "active") === agency.active) &&
        (!query ||
          agency.name.toLowerCase().includes(query) ||
          agency.members.some((item) => item.name.toLowerCase().includes(query))),
    );
    return json(route, {
      ...AGENCY_DIRECTORY,
      filters: { query, status },
      matched: agencies.length,
      agencies,
    });
  });
  await page.route(/\/api\/admin\/agencies\/4\/details$/, (route) => json(route, AGENCY_DETAILS));
}
