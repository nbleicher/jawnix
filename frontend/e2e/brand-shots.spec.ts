import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { test } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import {
  CUSTOMER_OVERVIEW,
  EMPTY_CUSTOMER_OVERVIEW,
} from "./customer-overview-fixtures";
import {
  BATCH_REQUEST_WORKSPACE,
  DELIVERED_REQUEST,
  EMPTY_BATCH_REQUEST_WORKSPACE,
  WAITING_REQUEST,
  mockBatchRequests,
} from "./customer-requests-fixtures";
import { mockFeedback } from "./customer-feedback-fixtures";
import {
  BILLED_CUSTOMER_WALLET,
  UNDERFUNDED_WALLET,
} from "./customer-billing-fixtures";

/** Delivered request whose artifact retention has already lapsed. */
const EXPIRED_ARTIFACT_REQUEST = {
  ...DELIVERED_REQUEST,
  artifact: {
    ...DELIVERED_REQUEST.artifact,
    expires_at: "2026-07-01T16:00:00Z",
  },
};

/** Drive the feedback flow to a looked-up Lead, then select the disposition
 *  that carries the strongest consequence card. Tolerant of the disposition
 *  control being a toggle button or a radio (DR-P5 migration). */
async function selectDispositionWithConsequence(page: Page) {
  await page
    .getByLabel("Delivered phone number (required)")
    .fill("2145550001");
  await page.getByRole("button", { name: "Look up" }).click();
  await page
    .getByRole("region", { name: "What happened?" })
    .waitFor({ state: "visible" });
  const invalidPhone = page
    .getByRole("radio", { name: /Invalid Phone/ })
    .or(page.getByRole("button", { name: /Invalid Phone/ }));
  await invalidPhone.click();
  await page
    .getByRole("region", { name: "Your answer: Invalid Phone" })
    .waitFor({ state: "visible" });
}

/**
 * Brand screenshot harness. Renders the customer-facing surfaces against the
 * mocked API fixtures and saves full-page PNGs to brand/before-after/after/.
 * Nothing here asserts; every test ends in a screenshot.
 */

const OUT_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../brand/before-after/after",
);

const SCHEMES = ["light", "dark"] as const;
type Scheme = (typeof SCHEMES)[number];

async function setScheme(page: Page, scheme: Scheme) {
  await page.addInitScript((value) => {
    window.localStorage.setItem("jx-scheme", value);
  }, scheme);
}

async function shoot(page: Page, testInfo: TestInfo, name: string) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const viewport = testInfo.project.name === "mobile" ? "mobile" : "desktop";
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(OUT_DIR, `${name}-${viewport}.png`),
    fullPage: true,
    animations: "disabled",
    caret: "hide",
  });
}

async function openGuidedFlowToReview(page: Page) {
  await page.getByRole("spinbutton", { name: /How many leads/ }).fill("750");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page
    .getByRole("heading", { name: "Review your request" })
    .waitFor({ state: "visible" });
}

for (const scheme of SCHEMES) {
  test.describe(`brand shots (${scheme})`, () => {
    test.describe.configure({ mode: "serial" });

    test.beforeEach(async ({ page }) => {
      await setScheme(page, scheme);
    });

    test(`overview queue (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page, { overview: CUSTOMER_OVERVIEW });
      await page.goto("./overview");
      await page
        .getByRole("list", { name: "Attention queue" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `overview-queue-${scheme}`);
    });

    test(`overview empty (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page, { overview: EMPTY_CUSTOMER_OVERVIEW });
      await page.goto("./overview");
      await page
        .getByRole("heading", { level: 1, name: "Overview" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `overview-empty-${scheme}`);
    });

    test(`requests list (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page);
      await mockBatchRequests(page, {
        workspace: {
          ...BATCH_REQUEST_WORKSPACE,
          requests: [WAITING_REQUEST, DELIVERED_REQUEST],
        },
      });
      await page.goto("./requests");
      await page
        .getByRole("heading", { level: 1, name: "Requests" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `requests-list-${scheme}`);
    });

    test(`requests review stage (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page);
      await mockBatchRequests(page, {
        workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
      });
      await page.goto("./requests");
      await openGuidedFlowToReview(page);
      await shoot(page, testInfo, `requests-review-${scheme}`);
    });

    test(`requests insufficient funds (${scheme})`, async ({
      page,
    }, testInfo) => {
      await mockCustomerAuth(page, {
        billing: { wallet: UNDERFUNDED_WALLET },
      });
      await mockBatchRequests(page, {
        workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
      });
      await page.goto("./requests");
      await openGuidedFlowToReview(page);
      await page
        .getByRole("button", { name: "Submit request" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `requests-insufficient-funds-${scheme}`);
    });

    test(`request detail live artifact (${scheme})`, async ({
      page,
    }, testInfo) => {
      await page.clock.install({ time: new Date("2026-07-31T16:00:00Z") });
      await mockCustomerAuth(page);
      await mockBatchRequests(page, {
        workspace: {
          ...BATCH_REQUEST_WORKSPACE,
          requests: [DELIVERED_REQUEST],
        },
      });
      await page.goto(`./requests?request=${DELIVERED_REQUEST.id}`);
      await page
        .getByRole("region", { name: "Batch Artifact" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `requests-detail-artifact-${scheme}`);
    });

    test(`feedback find the lead (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page);
      await mockFeedback(page);
      await page.goto("./feedback");
      await page
        .getByRole("region", { name: "Find the Lead" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `feedback-initial-${scheme}`);
    });

    test(`feedback confirm the lead (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page);
      await mockFeedback(page);
      await page.goto("./feedback");
      await page
        .getByLabel("Delivered phone number (required)")
        .fill("2145550001");
      await page.getByRole("button", { name: "Look up" }).click();
      await page
        .getByRole("region", { name: "Confirm the Lead" })
        .waitFor({ state: "visible" });
      await page
        .getByRole("region", { name: "What happened?" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `feedback-confirm-${scheme}`);
    });

    test(`feedback disposition consequence (${scheme})`, async ({
      page,
    }, testInfo) => {
      await mockCustomerAuth(page);
      await mockFeedback(page);
      await page.goto("./feedback");
      await selectDispositionWithConsequence(page);
      await page
        .getByText(/files a Lead Report and places an Eligibility Hold/)
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `feedback-disposition-${scheme}`);
    });

    test(`feedback receipt (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page);
      await mockFeedback(page);
      await page.goto("./feedback");
      await selectDispositionWithConsequence(page);
      await page.getByRole("button", { name: "Submit feedback" }).click();
      await page
        .getByRole("region", { name: "Recorded" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `feedback-receipt-${scheme}`);
    });

    test(`request detail artifact expired (${scheme})`, async ({
      page,
    }, testInfo) => {
      await page.clock.install({ time: new Date("2026-07-31T16:00:00Z") });
      await mockCustomerAuth(page);
      await mockBatchRequests(page, {
        workspace: {
          ...BATCH_REQUEST_WORKSPACE,
          requests: [EXPIRED_ARTIFACT_REQUEST],
        },
      });
      await page.goto(`./requests?request=${DELIVERED_REQUEST.id}`);
      await page
        .getByRole("region", { name: "Batch Artifact" })
        .waitFor({ state: "visible" });
      await page
        .getByText(/Expired — email noah@jawnix\.com/)
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `requests-detail-artifact-expired-${scheme}`);
    });

    test(`account billed (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page, {
        billing: { wallet: BILLED_CUSTOMER_WALLET },
      });
      await page.goto("./account");
      await page
        .getByRole("heading", { level: 1, name: "Account" })
        .waitFor({ state: "visible" });
      await page
        .getByRole("heading", { level: 2, name: "Credit Wallet" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `account-billed-${scheme}`);
    });

    test(`sign in (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page);
      await page.goto("./sign-in");
      await page
        .getByRole("heading", { level: 1, name: "Sign in" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `sign-in-initial-${scheme}`);
    });

    test(`sign in error (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page, { signInAccepted: false });
      await page.goto("./sign-in");
      await page
        .getByLabel("Email address (required)")
        .fill("customer@example.com");
      await page
        .getByLabel("Password (required)")
        .fill("customer-known-password-48");
      await page.getByRole("button", { name: "Sign in" }).click();
      await page
        .getByRole("alert")
        .filter({ hasText: "We could not sign you in" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `sign-in-error-${scheme}`);
    });

    test(`accept invitation invalid (${scheme})`, async ({
      page,
    }, testInfo) => {
      await mockCustomerAuth(page);
      await page.goto("./accept-invitation");
      await page
        .getByRole("alert")
        .filter({ hasText: "Request a new invitation" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `accept-invitation-invalid-${scheme}`);
    });

    test(`route error 404 (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page);
      await page.goto("./no-such-page");
      await page
        .getByRole("heading", { name: "Page not found" })
        .first()
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `route-error-404-${scheme}`);
    });

    test(`design system (${scheme})`, async ({ page }, testInfo) => {
      await page.goto("./design-system");
      await page
        .getByRole("heading", { level: 1, name: "Design system" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `design-system-gallery-${scheme}`);
    });
  });
}
