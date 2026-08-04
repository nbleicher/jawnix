import type { Page, Route } from "@playwright/test";

import { CUSTOMER_OVERVIEW } from "./customer-overview-fixtures";
import { BATCH_REQUEST_WORKSPACE } from "./customer-requests-fixtures";
import {
  CUSTOMER_ACCOUNT_IDENTITY,
  LICENSED_STATE_ACCOUNT,
} from "./customer-account-fixtures";
import {
  FREE_CUSTOMER_WALLET,
  mockCustomerBilling,
} from "./customer-billing-fixtures";
import type { CustomerBillingMockOptions } from "./customer-billing-fixtures";

export interface CustomerAuthMockOptions {
  signInAccepted?: boolean;
  passwordUpdateAccepted?: boolean;
  sessionExchangeStatus?: number;
  overviewStatus?: number;
  overview?: unknown | (() => unknown);
  batchRequests?: unknown | (() => unknown);
  licensedStates?: unknown | (() => unknown);
  exclusionLists?: unknown | (() => unknown);
  profile?: unknown | (() => unknown);
  /** Defaults to a Free Customer wallet so billing chrome stays hidden. */
  billing?: CustomerBillingMockOptions;
}

export interface CustomerAuthMockState {
  providerRequests: Array<{ path: string; method: string; body: unknown }>;
  sessionRequests: unknown[];
  overviewRequests: number;
  jawnixLogoutCalls: number;
  providerLogoutCalls: number;
}

const ACCESS_TOKEN = "customer-provider-access-token-long-enough";
const REFRESH_TOKEN = "customer-provider-refresh-token-long-enough";

const USER = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  aud: "authenticated",
  role: "authenticated",
  email: "customer@example.com",
  app_metadata: { jawnix_role: "customer", provider: "email", providers: ["email"] },
  user_metadata: {},
  identities: [],
  created_at: "2026-07-28T12:00:00Z",
  updated_at: "2026-07-28T12:00:00Z",
};

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

function providerSession() {
  return {
    access_token: ACCESS_TOKEN,
    refresh_token: REFRESH_TOKEN,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: USER,
  };
}

export function invitationHash(): string {
  const params = new URLSearchParams({
    access_token: ACCESS_TOKEN,
    refresh_token: REFRESH_TOKEN,
    expires_in: "3600",
    token_type: "bearer",
    type: "invite",
  });
  return `#${params.toString()}`;
}

export async function mockCustomerAuth(
  page: Page,
  options: CustomerAuthMockOptions = {},
): Promise<CustomerAuthMockState> {
  const state: CustomerAuthMockState = {
    providerRequests: [],
    sessionRequests: [],
    overviewRequests: 0,
    jawnixLogoutCalls: 0,
    providerLogoutCalls: 0,
  };
  const settings = {
    signInAccepted: true,
    passwordUpdateAccepted: true,
    sessionExchangeStatus: 200,
    overviewStatus: 200,
    overview: CUSTOMER_OVERVIEW,
    batchRequests: BATCH_REQUEST_WORKSPACE,
    licensedStates: LICENSED_STATE_ACCOUNT,
    exclusionLists: [] as unknown[],
    profile: CUSTOMER_ACCOUNT_IDENTITY,
    ...options,
  };
  const current = (value: unknown | (() => unknown)) =>
    typeof value === "function" ? value() : value;

  await page.addInitScript(() => {
    (window as Window & {
      JAWNIX_CONFIG?: {
        supabaseUrl?: string;
        supabaseAnonKey?: string;
      };
    }).JAWNIX_CONFIG = {
      supabaseUrl: "https://auth.example.test",
      supabaseAnonKey: "e2e-anon-key",
    };
    document.cookie = "jawnix_csrf=e2e-csrf; path=/";
  });

  await page.route("https://auth.example.test/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.postDataJSON() as unknown;
    state.providerRequests.push({
      path: `${url.pathname}${url.search}`,
      method: request.method(),
      body,
    });

    if (url.pathname.endsWith("/token")) {
      if (!settings.signInAccepted) {
        return json(
          route,
          {
            error: "invalid_grant",
            error_description: "provider-known-secret-48",
          },
          400,
        );
      }
      return json(route, providerSession());
    }
    if (url.pathname.endsWith("/user") && request.method() === "PUT") {
      if (!settings.passwordUpdateAccepted) {
        return json(
          route,
          {
            code: "invite_not_found",
            message: "provider-known-secret-48",
          },
          422,
        );
      }
      return json(route, USER);
    }
    if (url.pathname.endsWith("/user")) {
      return json(route, USER);
    }
    if (url.pathname.endsWith("/logout")) {
      state.providerLogoutCalls += 1;
      return route.fulfill({ status: 204 });
    }
    return json(route, { message: "Unexpected provider request" }, 500);
  });

  await page.route(/\/api\/auth\/session$/, (route) => {
    const body = route.request().postDataJSON() as unknown;
    state.sessionRequests.push(body);
    if (settings.sessionExchangeStatus !== 200) {
      return json(
        route,
        { detail: "inactive-account-known-secret-48" },
        settings.sessionExchangeStatus,
      );
    }
    return json(route, {
      ok: true,
      role: "customer",
      assurance: "aal1",
      next: (body as { requested_next?: string }).requested_next ?? "/app/overview",
    });
  });

  await page.route(/\/api\/me\/overview$/, (route) => {
    state.overviewRequests += 1;
    if (settings.overviewStatus !== 200) {
      return json(
        route,
        { detail: "expired-session-known-secret-48" },
        settings.overviewStatus,
      );
    }
    return json(route, current(settings.overview));
  });

  // Enough for a customer journey to arrive at /app/requests. Specs that
  // exercise the guided flow itself layer mockBatchRequests over this.
  await page.route(/\/api\/me\/batch-requests$/, (route) =>
    json(route, current(settings.batchRequests)),
  );

  await page.route(/\/api\/me\/licensed-states$/, (route) =>
    json(route, current(settings.licensedStates)),
  );

  await page.route(/\/api\/me\/exclusion-lists$/, (route) =>
    json(route, current(settings.exclusionLists)),
  );

  await page.route(/\/api\/me\/profile$/, (route) =>
    json(route, current(settings.profile)),
  );

  await page.route(/\/api\/auth\/logout$/, (route) => {
    state.jawnixLogoutCalls += 1;
    return json(route, { ok: true });
  });

  // Free Customer by default so existing specs stay free of Credit Wallet UI.
  // Specs that exercise billing pass `billing` or layer mockCustomerBilling.
  await mockCustomerBilling(page, {
    wallet: FREE_CUSTOMER_WALLET,
    ...settings.billing,
  });

  return state;
}
