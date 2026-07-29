import type { Page, Route } from "@playwright/test";

import { CUSTOMER_OVERVIEW } from "./customer-overview-fixtures";
import { BATCH_REQUEST_WORKSPACE } from "./customer-requests-fixtures";

interface MockRequestWorkspace {
  limits: {
    minimum_lead_count: number;
    maximum_lead_count: number;
    licensed_states: string[];
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

export const LICENSED_STATE_ACCOUNT = {
  states: ["FL", "TX"],
  version: "2026-07-29T12:00:00+00:00",
  options: [
    { code: "CA", name: "California" },
    { code: "FL", name: "Florida" },
    { code: "NC", name: "North Carolina" },
    { code: "SC", name: "South Carolina" },
    { code: "TX", name: "Texas" },
  ],
};

const IMPACTS = [
  {
    request_id: "11111111-1111-4111-8111-111111111111",
    lead_count: 750,
    status: "Preparing Batch",
    current_states: ["FL", "TX"],
    resulting_states: ["FL"],
    action: "narrowed",
  },
  {
    request_id: "22222222-2222-4222-8222-222222222222",
    lead_count: 300,
    status: "Under Review",
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
            "Licensed States changed in another session. Review the latest impact before saving.",
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
      },
      requests: state.requestWorkspace.requests.map((request) =>
        request.id === IMPACTS[0].request_id
          ? { ...request, states: ["FL"] }
          : request,
      ),
    };
    return json(route, {
      account: state.account,
      overview: {
        ...CUSTOMER_OVERVIEW,
        licensed_states: reviewedStates,
      },
      requests: state.requestWorkspace,
    });
  });

  return state;
}
