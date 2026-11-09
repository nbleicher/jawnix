import type { Page, Route } from "@playwright/test";

import type { MockBillingSummary } from "./customer-billing-fixtures";
import { CUSTOMER_HOME } from "./customer-home-fixtures";
import { BATCH_REQUEST_WORKSPACE } from "./customer-requests-fixtures";

interface MockRequestWorkspace {
  limits: {
    minimum_lead_count: number;
    maximum_lead_count: number;
    licensed_states: string[];
    state_names?: Record<string, string>;
  };
  blocker: unknown;
  requests: Array<{
    id: string;
    states: string[];
    [key: string]: unknown;
  }>;
}

interface MockLicensedStateAccount {
  states: string[];
  version: string;
  options: Array<{ code: string; name: string }>;
}

/** Every state the backend offers, in its order (DC between DE and FL). */
export const STATE_OPTIONS = [
  ["AL", "Alabama"], ["AK", "Alaska"], ["AZ", "Arizona"], ["AR", "Arkansas"],
  ["CA", "California"], ["CO", "Colorado"], ["CT", "Connecticut"],
  ["DE", "Delaware"], ["DC", "District of Columbia"], ["FL", "Florida"],
  ["GA", "Georgia"], ["HI", "Hawaii"], ["ID", "Idaho"], ["IL", "Illinois"],
  ["IN", "Indiana"], ["IA", "Iowa"], ["KS", "Kansas"], ["KY", "Kentucky"],
  ["LA", "Louisiana"], ["ME", "Maine"], ["MD", "Maryland"],
  ["MA", "Massachusetts"], ["MI", "Michigan"], ["MN", "Minnesota"],
  ["MS", "Mississippi"], ["MO", "Missouri"], ["MT", "Montana"],
  ["NE", "Nebraska"], ["NV", "Nevada"], ["NH", "New Hampshire"],
  ["NJ", "New Jersey"], ["NM", "New Mexico"], ["NY", "New York"],
  ["NC", "North Carolina"], ["ND", "North Dakota"], ["OH", "Ohio"],
  ["OK", "Oklahoma"], ["OR", "Oregon"], ["PA", "Pennsylvania"],
  ["RI", "Rhode Island"], ["SC", "South Carolina"], ["SD", "South Dakota"],
  ["TN", "Tennessee"], ["TX", "Texas"], ["UT", "Utah"], ["VT", "Vermont"],
  ["VA", "Virginia"], ["WA", "Washington"], ["WV", "West Virginia"],
  ["WI", "Wisconsin"], ["WY", "Wyoming"],
].map(([code, name]) => ({ code: code!, name: name! }));

export const LICENSED_STATE_ACCOUNT = {
  states: ["FL", "TX"],
  version: "2026-07-29T12:00:00+00:00",
  options: STATE_OPTIONS,
};

export const CUSTOMER_ACCOUNT_IDENTITY = {
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "river@northstar.example",
  first_name: "River",
  last_name: "Morgan",
  phone: "(215) 555-0142",
  customer_id: 7,
  mapping_confirmed_at: "2026-07-28T12:00:00Z",
};

const IMPACTS = [
  {
    request_id: "11111111-1111-4111-8111-111111111111",
    number: 1042,
    lead_count: 750,
    status: "Preparing Batch",
    current_states: ["FL", "TX"],
    resulting_states: ["FL"],
    action: "narrowed",
  },
  {
    request_id: "22222222-2222-4222-8222-222222222222",
    number: 1043,
    lead_count: 300,
    status: "Approved",
    current_states: ["TX"],
    resulting_states: [],
    action: "canceled",
  },
] as const;

export interface LicensedStateMockOptions {
  conflictOnFirstApply?: boolean;
}

export interface LicensedStateMockState {
  previews: Array<{ states: string[]; expected_version: string }>;
  applies: number;
  accountReads: number;
  savedStates: string[];
  requestWorkspace: MockRequestWorkspace;
  account: MockLicensedStateAccount;
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

export async function mockLicensedStates(
  page: Page,
  options: LicensedStateMockOptions = {},
): Promise<LicensedStateMockState> {
  const state: LicensedStateMockState = {
    previews: [],
    applies: 0,
    accountReads: 0,
    savedStates: [...LICENSED_STATE_ACCOUNT.states],
    requestWorkspace: structuredClone(
      BATCH_REQUEST_WORKSPACE,
    ) as unknown as MockRequestWorkspace,
    account: structuredClone(
      LICENSED_STATE_ACCOUNT,
    ) as MockLicensedStateAccount,
  };
  let reviewedStates = state.account.states;

  await page.route(/\/api\/me\/licensed-states\/preview$/, (route) => {
    const body = route.request().postDataJSON() as {
      states: string[];
      expected_version: string;
    };
    state.previews.push(body);
    reviewedStates = [...body.states].sort();
    const removed = state.account.states.filter(
      (code) => !reviewedStates.includes(code),
    );
    const added = reviewedStates.filter(
      (code) => !state.account.states.includes(code),
    );
    return json(route, {
      current_states: state.account.states,
      proposed_states: reviewedStates,
      added_states: added,
      removed_states: removed,
      additions_apply_to_future_requests_only: true,
      impacts: removed.includes("TX") ? IMPACTS : [],
      review_token: `review-${state.previews.length}`,
    });
  });

  await page.route(/\/api\/me\/licensed-states\/apply$/, (route) => {
    state.applies += 1;
    if (options.conflictOnFirstApply && state.applies === 1) {
      state.account = {
        ...state.account,
        states: ["CA", "FL", "TX"],
        version: "2026-07-29T12:06:00+00:00",
      };
      state.savedStates = [...state.account.states];
      return json(
        route,
        {
          detail:
            "Your appointed states changed in another session. Review the latest changes before saving.",
        },
        409,
      );
    }
    state.account = {
      ...state.account,
      states: reviewedStates,
      version: "2026-07-29T12:05:00+00:00",
    };
    state.savedStates = [...reviewedStates];
    state.requestWorkspace = {
      ...state.requestWorkspace,
      limits: {
        ...state.requestWorkspace.limits,
        licensed_states: reviewedStates,
        state_names: Object.fromEntries(
          reviewedStates.map((code) => [
            code,
            state.account.options.find((option) => option.code === code)?.name ?? code,
          ]),
        ),
      },
      requests: state.requestWorkspace.requests.map((request) =>
        request.id === IMPACTS[0].request_id
          ? { ...request, states: ["FL"] }
          : request,
      ),
    };
    return json(route, {
      account: state.account,
      overview: CUSTOMER_HOME,
      requests: state.requestWorkspace,
    });
  });

  return state;
}

/**
 * Account's Billing tab as the approved render has it (V9): $412.50
 * available, $125.00 held for #1042, $1,000.00 spent on 3 batches in 30 days.
 * Times are local to `now`'s day, like Home's fixtures.
 */
export function northstarBillingAt(now: Date): MockBillingSummary {
  const at = (daysAgo: number, hours: number, minutes: number) =>
    new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo, hours, minutes).toISOString();
  const batch = (id: string, number: number) => ({ id, number, href: `/app/batches/${id}` });
  const held = batch("42424242-4242-4242-8242-424242424242", 1042);
  return {
    available_balance_cents: 41_250,
    held_cents: 12_500,
    held_for: [held],
    lead_rate_cents_per_thousand: 500,
    spent_since: at(30, 9, 41),
    spent_cents: 100_000,
    spent_batches: 3,
    history: [
      { id: "hold-1042", kind: "hold", occurred_at: at(0, 9, 41), amount_cents: -12_500, balance_after_cents: 41_250, batch: held, note: null },
      { id: "purchase-2", kind: "purchase", occurred_at: at(0, 9, 30), amount_cents: 50_000, balance_after_cents: 53_750, batch: null, note: null },
      { id: "charge-1038", kind: "charge", occurred_at: at(2, 8, 12), amount_cents: -25_000, balance_after_cents: 3_750, batch: batch("38383838-3838-4838-8838-383838383838", 1038), note: null },
      { id: "charge-1031", kind: "charge", occurred_at: at(8, 7, 55), amount_cents: -50_000, balance_after_cents: 28_750, batch: batch("31313131-3131-4131-8131-313131313131", 1031), note: null },
      { id: "purchase-1", kind: "purchase", occurred_at: at(10, 16, 2), amount_cents: 75_000, balance_after_cents: 78_750, batch: null, note: null },
      { id: "charge-1024", kind: "charge", occurred_at: at(27, 9, 10), amount_cents: -25_000, balance_after_cents: 3_750, batch: batch("24242424-2424-4242-8242-242424242424", 1024), note: null },
    ],
    history_complete: true,
  };
}
