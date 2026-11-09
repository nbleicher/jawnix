import { expect, test } from "@playwright/test";

import { mockAcquisition } from "./acquisition-fixtures";
import { mockActivity } from "./activity-fixtures";
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
  await mockActivity(page);
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
    await expect(support).toHaveAttribute("href", "mailto:noah@jawnix.com");

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
    await expect(page).toHaveTitle("Overview · JAWNIX");

    await page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Requests" }).click();
    await expect(page).toHaveTitle("Requests · JAWNIX");
  });
});

test.describe("Administration shell", () => {
  test("shares the scheme and the quiet register", async ({ page }) => {
    await page.goto("./admin/today");

    await expect(page.locator("html")).toHaveAttribute("data-scheme", "light");
    // Headings are Inter in the fresh register; Archivo survives only in the
    // frozen wordmark.
    await expect(page.getByRole("heading", { level: 1, name: "Today" })).toHaveCSS(
      "font-family",
      /Inter/,
    );
    await expect(page.locator(".jx-lockup__wordmark")).toHaveCSS(
      "font-family",
      /Archivo/,
    );
  });

  test("navigates by how often each tool is used, with search in the header", async ({ page }) => {
    await page.goto("./admin/today");

    const nav = page.getByRole("navigation", { name: "Administration" });
    // Each link also carries its decorative, hidden glyph.
    await expect(nav.getByRole("link")).toHaveText([
      /Today$/,
      /Customers$/,
      /Inventory$/,
      /Insights$/,
      /Setup$/,
      /Activity$/,
    ]);
    // Every destination fits without scrolling, phones included (V8).
    for (const link of await nav.getByRole("link").all()) {
      await expect(link).toBeInViewport({ ratio: 1 });
    }
    await expect(nav.getByRole("link", { name: "Fulfillment" })).toHaveCount(0);
    await expect(
      page.getByRole("banner").getByRole("combobox", { name: "Search customers, batches, and uploads" }),
    ).toBeVisible();
  });

  test("Inventory, Insights, and Setup reach the pages they stand in for", async ({ page }) => {
    await page.goto("./admin/today");
    const nav = page.getByRole("navigation", { name: "Administration" });
    await expect(nav.getByRole("link", { name: "Inventory" })).toHaveAttribute("href", "/app/admin/lead-uploads");
    await expect(nav.getByRole("link", { name: "Insights" })).toHaveAttribute(
      "href",
      "/app/admin/acquisition/performance",
    );

    await nav.getByRole("link", { name: "Setup" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Acquisition" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Setup" })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: "Insights" })).not.toHaveAttribute("aria-current", "page");
  });

  test("the old Overview address lands on Today", async ({ page }) => {
    await page.goto("./admin/overview");
    await expect(page).toHaveURL(/\/app\/admin\/today$/);
    await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();

    await page.goto("./admin");
    await expect(page).toHaveURL(/\/app\/admin\/today$/);
  });

  test("Fulfillment stays reachable at its address, out of the navigation", async ({ page }) => {
    await page.goto("./admin/fulfillment");

    await expect(page.getByRole("heading", { level: 1, name: "Fulfillment" })).toBeVisible();
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
    await expect(page).toHaveURL(/\/app\/admin\/batches\//);
  });

  test("Acquisition exposes its operating areas and security action at both form factors", async ({ page }) => {
    await page.goto("./admin/acquisition");

    await expect(page.getByRole("heading", { level: 1, name: "Acquisition" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

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
    const back = rail.getByRole("link", { name: "Exit to Today" });
    await expect(back).toBeVisible();
    await back.click();
    await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  });

  test("Customers lists every customer with what it needs at both form factors", async ({ page }) => {
    await page.goto("./admin/customers");

    await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

    const table = page.getByRole("table", { name: "Customers" });
    await expect(table.getByRole("link", { name: "Harbor Insurance" })).toBeVisible();
    await expect(table.getByText("Conflict with Lakeside Brokers")).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);

    const nav = page.getByRole("navigation", { name: "Administration" });
    await nav.getByRole("link", { name: "Today" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  });

  test("reaches every primary destination", async ({ page }) => {
    await page.goto("./admin");

    const nav = page.getByRole("navigation", { name: "Administration" });
    for (const destination of ["Customers", "Setup", "Activity", "Today"]) {
      await nav.getByRole("link", { name: destination }).click();
      await expect(nav.getByRole("link", { name: destination })).toHaveAttribute("aria-current", "page");
    }
  });

  test("the Scraper workspace and administration share the scheme", async ({ page }) => {
    await page.goto("./admin/today");
    await expect(page.locator("html")).toHaveAttribute("data-scheme", "light");

    await page.goto("./admin/acquisition/scraper");
    await expect(page.getByRole("heading", {
      level: 1,
      name: "Verify access to Scraper Operations",
    })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-scheme", "light");

    const nav = page.getByRole("navigation", { name: "Administration" });
    await nav.getByRole("link", { name: "Customers" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-scheme", "light");
  });

  test("signs the administrator out from the shell chrome", async ({ page }) => {
    await page.goto("./admin/today");

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
