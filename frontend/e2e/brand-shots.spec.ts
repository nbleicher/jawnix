import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { test } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import {
  CUSTOMER_HOME,
  NEW_CUSTOMER_HOME,
  NORTHSTAR_WALLET,
} from "./customer-home-fixtures";
import {
  BATCHES_DAY,
  EMPTY_BATCH_REQUEST_WORKSPACE,
  mockBatchRequests,
} from "./customer-requests-fixtures";
import {
  BILLED_CUSTOMER_WALLET,
  UNDERFUNDED_WALLET,
} from "./customer-billing-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { mockFulfillment } from "./fulfillment-fixtures";
import { mockLeadReports } from "./lead-report-fixtures";
import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockAcquisition } from "./acquisition-fixtures";
import { mockActivity } from "./activity-fixtures";

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

for (const scheme of SCHEMES) {
  test.describe(`brand shots (${scheme})`, () => {
    test.describe.configure({ mode: "serial" });

    test.beforeEach(async ({ page }) => {
      await setScheme(page, scheme);
    });

    test(`home (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page, {
        overview: CUSTOMER_HOME,
        billing: { wallet: NORTHSTAR_WALLET },
      });
      await page.goto("./home");
      await page
        .getByRole("table", { name: "Recent batches" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `home-${scheme}`);
    });

    test(`home empty (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page, { overview: NEW_CUSTOMER_HOME });
      await page.goto("./home");
      await page
        .getByRole("heading", { level: 1, name: "Home" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `home-empty-${scheme}`);
    });

    test(`batches (${scheme})`, async ({ page }, testInfo) => {
      await page.clock.install({ time: BATCHES_DAY });
      await mockCustomerAuth(page, { billing: { wallet: NORTHSTAR_WALLET } });
      await mockBatchRequests(page);
      await page.goto("./batches");
      await page
        .getByRole("table", { name: "Batches" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `batches-${scheme}`);
    });

    test(`batch page (${scheme})`, async ({ page }, testInfo) => {
      await page.clock.install({ time: BATCHES_DAY });
      await mockCustomerAuth(page, { billing: { wallet: NORTHSTAR_WALLET } });
      await mockBatchRequests(page);
      await page.goto("./batches/1017");
      await page
        .getByRole("table", { name: "Leads by state" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `batch-page-${scheme}`);
    });

    test(`new request (${scheme})`, async ({ page }, testInfo) => {
      await mockCustomerAuth(page, { billing: { wallet: NORTHSTAR_WALLET } });
      await mockBatchRequests(page);
      await page.goto("./new-request");
      await page
        .getByRole("textbox", { name: "How many leads?" })
        .fill("100000");
      await page.getByText("Short by").waitFor({ state: "visible" });
      await shoot(page, testInfo, `new-request-${scheme}`);
    });

    test(`new request insufficient funds (${scheme})`, async ({
      page,
    }, testInfo) => {
      await mockCustomerAuth(page, {
        billing: { wallet: UNDERFUNDED_WALLET },
      });
      await mockBatchRequests(page, {
        workspace: EMPTY_BATCH_REQUEST_WORKSPACE,
      });
      await page.goto("./new-request");
      await page
        .getByRole("textbox", { name: "How many leads?" })
        .fill("750");
      await page.getByText("Short by").waitFor({ state: "visible" });
      await shoot(page, testInfo, `new-request-insufficient-funds-${scheme}`);
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

for (const scheme of SCHEMES) {
  test.describe(`admin brand shots (${scheme})`, () => {
    test.describe.configure({ mode: "serial" });

    test.beforeEach(async ({ page }) => {
      await setScheme(page, scheme);
      await mockAdminMFA(page, { assurance: "aal2" });
    });

    test(`admin today (${scheme})`, async ({ page }, testInfo) => {
      await page.goto("./admin/today");
      await page
        .getByRole("table", { name: "Decisions" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `admin-today-${scheme}`);
    });

    test(`admin fulfillment (${scheme})`, async ({ page }, testInfo) => {
      await mockFulfillment(page);
      await mockLeadReports(page);
      await page.goto("./admin/fulfillment");
      await page
        .getByRole("heading", { level: 1, name: "Fulfillment" })
        .waitFor({ state: "visible" });
      await page
        .getByRole("region", { name: "Batch Requests" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `admin-fulfillment-${scheme}`);
    });

    test(`admin customers (${scheme})`, async ({ page }, testInfo) => {
      await mockAdminCustomers(page);
      await page.goto("./admin/customers");
      await page
        .getByRole("heading", { level: 1, name: "Customers" })
        .waitFor({ state: "visible" });
      await page
        .getByRole("article", { name: "Harbor Insurance" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `admin-customers-${scheme}`);
    });

    test(`admin activity (${scheme})`, async ({ page }, testInfo) => {
      await mockAdminCustomers(page);
      await mockAcquisition(page);
      await mockActivity(page);
      await page.goto("./admin/activity");
      await page
        .getByRole("heading", { level: 1, name: "Activity" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `admin-activity-${scheme}`);
    });

    test(`admin acquisition (${scheme})`, async ({ page }, testInfo) => {
      await mockAcquisition(page);
      await page.goto("./admin/acquisition");
      await page
        .getByRole("region", { name: "Acquisition workspace" })
        .waitFor({ state: "visible" });
      await shoot(page, testInfo, `admin-acquisition-${scheme}`);
    });
  });
}
