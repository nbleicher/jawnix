import type { Page, Route } from "@playwright/test";

import { CUSTOMER_OVERVIEW } from "./customer-overview-fixtures";
import { BATCH_REQUEST_WORKSPACE } from "./customer-requests-fixtures";
import {
  monitoringRegion,
  monitoringSnapshot,
  pausedPipelineResult,
} from "./scraper-monitoring-fixtures";
import type { RegionKey } from "./scraper-monitoring-fixtures";
import {
  KEYWORD_GENERATION,
  KEYWORD_PREVIEW,
  KEYWORD_WORKSPACE,
} from "./scraper-keywords-fixtures";

export interface MockFactor {
  id: string;
  name: string;
  status: "verified" | "unverified";
  type: "totp";
  createdAt: string;
  lastUsedAt: string | null;
  lastUsedFrom: {
    ipAddress: string;
    userAgent: string;
  } | null;
}

export interface MockMFAState {
  assurance: "aal1" | "aal2";
  factors: MockFactor[];
  stage: string;
  throttled: boolean;
  lockedUntil: string | null;
  challengeFailures: number;
  cancelCalls: number;
}

export function verifiedFactors(): MockFactor[] {
  return [
    {
      id: "11111111-1111-4111-8111-111111111111",
      name: "Jawnix primary",
      status: "verified",
      type: "totp",
      createdAt: "2026-07-28T12:00:00Z",
      lastUsedAt: "2026-07-28T12:30:00Z",
      lastUsedFrom: {
        ipAddress: "203.0.113.10",
        userAgent: "Desktop browser",
      },
    },
    {
      id: "22222222-2222-4222-8222-222222222222",
      name: "Jawnix backup",
      status: "verified",
      type: "totp",
      createdAt: "2026-07-28T12:10:00Z",
      lastUsedAt: null,
      lastUsedFrom: null,
    },
  ];
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

export async function mockAdminMFA(
  page: Page,
  initial: Partial<MockMFAState> = {},
): Promise<MockMFAState> {
  const state: MockMFAState = {
    assurance: "aal1",
    factors: verifiedFactors(),
    stage: "complete",
    throttled: false,
    lockedUntil: null,
    challengeFailures: 0,
    cancelCalls: 0,
    ...initial,
  };

  await page.addInitScript(() => {
    sessionStorage.setItem(
      "jawnix_provider_access_token",
      "provider-aal1-access-token-long-enough",
    );
    sessionStorage.setItem(
      "jawnix_provider_refresh_token",
      "provider-refresh-token-long-enough",
    );
    document.cookie = "jawnix_csrf=e2e-csrf; path=/";
  });

  await page.route(
    /\/api\/auth\/admin-mfa(?:\/.*)?$/,
    async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const method = request.method();

      if (path.endsWith("/access")) {
        return json(route, { ok: true });
      }
      if (path === "/api/auth/admin-mfa" && method === "GET") {
        const verifiedCount = state.factors.filter(
          (factor) => factor.status === "verified",
        ).length;
        return json(route, {
          assurance: state.assurance,
          enforced: verifiedCount >= 2,
          stage: state.stage,
          factors: state.factors,
          throttled: state.throttled,
          lockedUntil: state.lockedUntil,
          next:
            verifiedCount < 2
              ? "/app/admin/mfa/enroll"
              : state.assurance === "aal2"
                ? "/app/admin/overview"
                : "/app/admin/mfa/challenge",
        });
      }
      if (path.endsWith("/enrollment") && method === "POST") {
        const body = request.postDataJSON() as { slot: "primary" | "backup" };
        const id =
          body.slot === "primary"
            ? "33333333-3333-4333-8333-333333333333"
            : "44444444-4444-4444-8444-444444444444";
        state.stage = `${body.slot}_pending`;
        state.factors.push({
          id,
          name: `Jawnix ${body.slot}`,
          status: "unverified",
          type: "totp",
          createdAt: "2026-07-28T13:00:00Z",
          lastUsedAt: null,
          lastUsedFrom: null,
        });
        return json(route, {
          factorId: id,
          slot: body.slot,
          qrCode: "<svg xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"10\" height=\"10\"/></svg>",
          manualKey: `MANUAL-${body.slot.toUpperCase()}-KEY`,
          uri: "otpauth://totp/Jawnix",
        });
      }
      if (path.endsWith("/enrollment/verify")) {
        const pending = state.factors.find(
          (factor) => factor.status === "unverified",
        );
        if (pending) pending.status = "verified";
        const complete =
          state.factors.filter((factor) => factor.status === "verified").length >=
          2;
        state.stage = complete ? "complete" : "primary_verified";
        state.assurance = "aal2";
        return json(route, {
          ok: true,
          complete,
          next: complete
            ? "/app/admin/overview"
            : "/app/admin/mfa/enroll",
          session: {
            accessToken: "provider-aal2-access-token-long-enough",
            refreshToken: "provider-refresh-token-long-enough",
            expiresIn: 3600,
          },
        });
      }
      if (path.endsWith("/enrollment/cancel")) {
        state.cancelCalls += 1;
        state.factors = state.factors.filter(
          (factor) => factor.status === "verified",
        );
        state.stage = state.factors.length ? "complete" : "idle";
        return json(route, {
          ok: true,
          next: "/app/admin/mfa/enroll",
        });
      }
      if (path.endsWith("/challenge")) {
        const body = request.postDataJSON() as { code: string };
        if (body.code === "000000") {
          state.challengeFailures += 1;
          return json(
            route,
            {
              detail:
                "That code could not be verified. Check it and try again.",
            },
            422,
          );
        }
        state.assurance = "aal2";
        return json(route, {
          ok: true,
          next: "/app/admin/overview",
          session: {
            accessToken: "provider-aal2-access-token-long-enough",
            refreshToken: "provider-refresh-token-long-enough",
            expiresIn: 3600,
          },
        });
      }
      if (path.endsWith("/replacement")) {
        state.stage = "replacement_pending";
        const replacement: MockFactor = {
          id: "55555555-5555-4555-8555-555555555555",
          name: "Jawnix replacement",
          status: "unverified",
          type: "totp",
          createdAt: "2026-07-28T14:00:00Z",
          lastUsedAt: null,
          lastUsedFrom: null,
        };
        state.factors.push(replacement);
        return json(route, {
          factorId: replacement.id,
          slot: "replacement",
          qrCode: "<svg xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"10\" height=\"10\"/></svg>",
          manualKey: "MANUAL-REPLACEMENT-KEY",
          uri: "otpauth://totp/Jawnix",
        });
      }
      if (path.endsWith("/logout-everywhere")) {
        return json(route, {
          ok: true,
          providerLogoutCompleted: true,
        });
      }
      return json(route, { detail: "Unexpected mock request" }, 500);
    },
  );

  await page.route(/\/api\/admin\/scraper(?:\/.*)?$/, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith("/entry")) {
      return json(route, {
        factors: state.factors.map(({ id, name, type }) => ({
          id,
          name,
          type,
        })),
        idleExpiresIn: 900,
      });
    }
    if (path.endsWith("/workspace")) {
      return json(route, {
        serviceState: "connected",
        lastSuccessfulAt: "2026-07-28T12:00:00Z",
        idleExpiresIn: 900,
      });
    }
    if (path.endsWith("/monitoring")) {
      return json(route, monitoringSnapshot());
    }
    if (path.includes("/monitoring/")) {
      return json(
        route,
        monitoringRegion(path.split("/").pop() as RegionKey),
      );
    }
    if (path.endsWith("/pipeline")) {
      return json(route, pausedPipelineResult());
    }
    if (path.endsWith("/keywords") && method === "GET") {
      return json(route, KEYWORD_WORKSPACE);
    }
    if (path.endsWith("/keywords/preview")) {
      return json(route, KEYWORD_PREVIEW);
    }
    if (path.endsWith("/keywords/save")) {
      return json(route, {
        saved: true,
        enqueued: false,
        current: KEYWORD_PREVIEW.proposed,
        version:
          "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        diff: KEYWORD_PREVIEW,
      });
    }
    if (path.endsWith("/keywords/generate")) {
      return json(route, KEYWORD_GENERATION);
    }
    if (path.endsWith("/keywords/rollover")) {
      return json(route, {
        ...KEYWORD_WORKSPACE.rollover,
        enabled: true,
        state: "working",
        label: "Current batch active",
        detail: "12 of 20 coverage jobs enqueued",
        posted_jobs: 12,
        expected_jobs: 20,
      });
    }
    if (path.endsWith("/step-up")) {
      return json(route, {
        ok: true,
        idleExpiresIn: 900,
        session: {
          accessToken: "provider-aal2-access-token-long-enough",
          refreshToken: "provider-refresh-token-long-enough",
          expiresIn: 3600,
        },
      });
    }
    return json(route, { detail: "Unexpected Scraper mock request" }, 500);
  });

  await page.route(/\/api\/me\/overview$/, (route) =>
    json(route, CUSTOMER_OVERVIEW),
  );

  await page.route(/\/api\/me\/batch-requests$/, (route) =>
    json(route, BATCH_REQUEST_WORKSPACE),
  );

  await page.route(/\/api\/me\/profile$/, (route) =>
    json(route, {
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "customer@example.com",
      first_name: "Customer",
      last_name: "Example",
      phone: "",
      licensed_states: ["TX"],
      customer_id: 1,
      mapping_confirmed_at: "2026-07-28T12:00:00Z",
    }),
  );

  return state;
}
