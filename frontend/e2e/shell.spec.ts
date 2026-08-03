import { expect, test } from "@playwright/test";

import { mockAcquisition } from "./acquisition-fixtures";
import { mockFeedback } from "./customer-feedback-fixtures";
import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockFulfillment } from "./fulfillment-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { mockCustomerAuth } from "./customer-auth-fixtures";

/**
 * Shell journeys against the compiled application.
 *
 * These run through `vite preview` on the production bundle rather than the dev
 * server, so they exercise the same hashed assets and the same base path the
 * deployment edge serves.
 */

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  // Every administrator destination now reads a real contract:
  // Fulfillment since #57, Customers since #59, Acquisition since #68.
  await mockAcquisition(page);
  await mockAdminCustomers(page);
  await mockFulfillment(page);
  // Feedback reads a real contract since #53.
  await mockFeedback(page);
  await mockCustomerAuth(page);
});

test.describe("Customer shell", () => {
  test("keeps email support in the shell chrome across destinations", async ({
    page,
  }) => {
    await page.goto("./overview");

    const support = page.getByRole("banner").getByRole("link", {
      name: "Support",
    });
    await expect(support).toBeVisible();
    await expect(support).toHaveAttribute("href", "mailto:hai@jawnix.com");

    await page
      .getByRole("navigation", { name: "Customer" })
      .getByRole("link", { name: "Account" })
      .click();
    await expect(support).toBeVisible();
  });

  test("lands on Overview and reaches every primary destination", async ({ page }) => {
    await page.goto("./");

    // The index route redirects to Overview.
    await expect(page).toHaveURL(/\/app\/overview$/);
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

    const nav = page.getByRole("navigation", { name: "Customer" });

    for (const destination of ["Requests", "Feedback", "Account"]) {
      await nav.getByRole("link", { name: destination }).click();
      await expect(page.getByRole("heading", { level: 1, name: destination })).toBeVisible();
      await expect(nav.getByRole("link", { name: destination })).toHaveAttribute("aria-current", "page");
    }
  });

  test("exposes every destination at both form factors", async ({ page }) => {
    await page.goto("./");

    const nav = page.getByRole("navigation", { name: "Customer" });
    // Mobile must not be a degraded experience: the same four links are present
    // and visible whichever viewport the project runs at.
    for (const destination of ["Overview", "Requests", "Feedback", "Account"]) {
      await expect(nav.getByRole("link", { name: destination })).toBeVisible();
    }
  });

  test("updates the document title on navigation", async ({ page }) => {
    await page.goto("./");
    await expect(page).toHaveTitle("Overview · Jawnix");

    await page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Requests" }).click();
    await expect(page).toHaveTitle("Requests · Jawnix");
  });
});

test.describe("Administration shell", () => {
  test("shares the customer portal's Opaline palette and Fraunces display face", async ({ page }) => {
    await page.goto("./admin/overview");

    await expect(page.locator("html")).toHaveAttribute("data-theme", "opaline");
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCSS(
      "font-family",
      /Fraunces Variable/,
    );
  });

  test("keeps primary navigation to the domain-work destinations", async ({ page }) => {
    await page.goto("./admin/overview");

    const nav = page.getByRole("navigation", { name: "Administration" });
    await expect(nav.getByRole("link")).toHaveCount(5);
    for (const destination of ["Overview", "Fulfillment", "Acquisition", "Customers", "Activity"]) {
      await expect(nav.getByRole("link", { name: destination })).toBeVisible();
    }
    await expect(nav.getByRole("link", { name: "Security" })).toHaveCount(0);
  });

  test("Overview is the cross-domain work queue at both form factors", async ({ page }) => {
    await page.goto("./admin/overview");

    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

    for (const queue of [
      "Pending Batch Requests",
      "Inventory Conflicts",
      "Lead Reports",
      "Eligibility Holds",
      "Delivery failures",
      "Failed jobs",
      "Scrape Anomalies",
      "Nightly Reviews",
    ]) {
      await expect(page.getByRole("region", { name: queue })).toBeVisible();
    }
    await expect(page.getByRole("link", { name: "Open Fulfillment" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Acquisition" })).toBeVisible();
  });

  test("Fulfillment groups its operational work at both form factors", async ({ page }) => {
    await page.goto("./admin/fulfillment");

    await expect(page.getByRole("heading", { level: 1, name: "Fulfillment" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

    // #57 replaced the task map with the workspace itself: the three areas of
    // outstanding work, each carrying the records it is responsible for.
    for (const area of ["Batch Requests", "Inventory Conflicts", "Delivery failures"]) {
      await expect(page.getByRole("region", { name: area })).toBeVisible();
    }

    await page
      .getByRole("region", { name: "Batch Requests" })
      .locator("summary")
      .click();
    await page
      .getByRole("region", { name: "Batch Requests" })
      .getByRole("link", { name: "Northstar Insurance" })
      .click();
    await expect(
      page.getByRole("heading", { level: 1, name: /^Batch Request — / }),
    ).toBeVisible();

    const back = page.getByRole("link", { name: "Back to Fulfillment" });
    await expect(back).toBeVisible();
    await back.click();
    await expect(page.getByRole("heading", { level: 1, name: "Fulfillment" })).toBeVisible();
  });

  test("Acquisition exposes its operating areas and security action at both form factors", async ({ page }) => {
    await page.goto("./admin/acquisition");

    await expect(page.getByRole("heading", { level: 1, name: "Acquisition" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

    // #68 replaced the task map with the native terminal workspace: the areas
    // are now the records themselves.
    for (const area of [
      "Held Scrape Anomalies",
      "Source Recommendations",
      "Scraper Configuration versions",
      "Nightly Reviews",
    ]) {
      await expect(page.getByRole("region", { name: area })).toBeVisible();
    }

    // Administrator navigation omits Security, so this rail is its only route.
    const rail = page.getByRole("navigation", {
      name: "Acquisition workspace sections",
    });
    const security = rail.getByRole("link", { name: "Administrator security" });
    await expect(security).toBeVisible();
    await security.click();
    await expect(page.getByRole("heading", { level: 1, name: "Administrator security" })).toBeVisible();

    await page.goto("./admin/acquisition");
    const back = rail.getByRole("link", { name: "Exit to Overview" });
    await expect(back).toBeVisible();
    await back.click();
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  });

  test("Customers separates durable identity from account access at both form factors", async ({ page }) => {
    await page.goto("./admin/customers");

    await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

    // Each row carries two separately labelled standings: the durable Customer
    // and the replaceable access.
    const harbor = page.getByRole("article", { name: "Harbor Insurance" });
    await expect(harbor.getByText("Customer", { exact: true })).toBeVisible();
    await expect(harbor.getByText("User Account", { exact: true })).toBeVisible();

    const nav = page.getByRole("navigation", { name: "Administration" });
    await nav.getByRole("link", { name: "Overview" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  });

  test("reaches every primary destination", async ({ page }) => {
    await page.goto("./admin");

    await expect(page).toHaveURL(/\/app\/admin\/overview$/);
    const nav = page.getByRole("navigation", { name: "Administration" });

    for (const destination of ["Fulfillment", "Acquisition", "Customers"]) {
      await nav.getByRole("link", { name: destination }).click();
      await expect(nav.getByRole("link", { name: destination })).toHaveAttribute("aria-current", "page");
    }
  });

  test("the Scraper workspace and administration share Opaline", async ({ page }) => {
    await page.goto("./admin/overview");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "opaline");

    await page.goto("./admin/acquisition/scraper");
    await expect(page.getByRole("heading", {
      level: 1,
      name: "Verify access to Scraper Operations",
    })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "opaline");

    const nav = page.getByRole("navigation", { name: "Administration" });
    await nav.getByRole("link", { name: "Fulfillment" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "opaline");
  });

  test("signs the administrator out from the shell chrome", async ({ page }) => {
    await page.goto("./admin/overview");

    await page
      .getByRole("banner")
      .getByRole("button", { name: "Sign out" })
      .click();

    await expect(page).toHaveURL(/\/app\/sign-in$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Sign in" }),
    ).toBeVisible();
  });
});

test.describe("Direct navigation", () => {
  // A hard load of a deep link must work, not just client-side routing.
  for (const path of ["./requests", "./account", "./admin/fulfillment", "./design-system"]) {
    test(`serves the shell for a hard load of ${path}`, async ({ page }) => {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    });
  }

  test("shows a recovery action for an unknown route", async ({ page }) => {
    await page.goto("./no-such-route");

    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Go to Overview" })).toBeVisible();
  });
});

test.describe("Assets", () => {
  test("serves content-hashed assets", async ({ page }) => {
    const assetUrls: string[] = [];
    page.on("response", (response) => {
      const url = response.url();
      if (/\/app\/assets\/.*\.(?:js|css)$/.test(url)) assetUrls.push(url);
    });

    await page.goto("./");
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

    expect(assetUrls.length).toBeGreaterThan(0);
    // Vite emits `name-<hash>.ext`; the hash is what makes immutable caching safe.
    for (const url of assetUrls) {
      expect(url).toMatch(/\/app\/assets\/[^/]+-[A-Za-z0-9_-]{8,}\.(js|css)$/);
    }
  });
});
