import { expect, test } from "@playwright/test";

import { mockAcquisition } from "./acquisition-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

/**
 * Shell journeys against the compiled application.
 *
 * These run through `vite preview` on the production bundle rather than the dev
 * server, so they exercise the same hashed assets and the same base path the
 * deployment edge serves.
 */

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  // Acquisition reads a real contract since #68; the remaining administrator
  // destinations are still local task maps.
  await mockAcquisition(page);
});

test.describe("Customer shell", () => {
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
  test("keeps primary navigation to the four domain-work destinations", async ({ page }) => {
    await page.goto("./admin/overview");

    const nav = page.getByRole("navigation", { name: "Administration" });
    await expect(nav.getByRole("link")).toHaveCount(4);
    for (const destination of ["Overview", "Fulfillment", "Acquisition", "Customers"]) {
      await expect(nav.getByRole("link", { name: destination })).toBeVisible();
    }
    await expect(nav.getByRole("link", { name: "Security" })).toHaveCount(0);
  });

  test("Overview is a task-first wayfinder at both form factors", async ({ page }) => {
    await page.goto("./admin/overview");

    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

    for (const workspace of ["Fulfillment", "Acquisition", "Customers"]) {
      await page.goto("./admin/overview");
      await expect(page.getByRole("heading", { level: 3, name: workspace })).toBeVisible();
      const action = page.getByRole("link", { name: `Open ${workspace}` });
      await expect(action).toBeVisible();
      await action.click();
      await expect(page.getByRole("heading", { level: 1, name: workspace })).toBeVisible();
    }
  });

  test("Fulfillment groups its operational work at both form factors", async ({ page }) => {
    await page.goto("./admin/fulfillment");

    await expect(page.getByRole("heading", { level: 1, name: "Fulfillment" })).toBeVisible();
    await expect(page.getByText("Not built yet")).toHaveCount(0);

    for (const area of ["Batch delivery", "Inventory decisions", "Lead eligibility"]) {
      await expect(page.getByRole("heading", { level: 3, name: area })).toBeVisible();
    }
    const back = page.getByRole("link", { name: "Back to Overview" });
    await expect(back).toBeVisible();
    await back.click();
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
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
      name: "Acquisition terminal sections",
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

    for (const area of ["Customer directory", "User Account access", "Agency membership"]) {
      await expect(page.getByRole("heading", { level: 3, name: area })).toBeVisible();
    }
    const back = page.getByRole("link", { name: "Back to Overview" });
    await expect(back).toBeVisible();
    await back.click();
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

  test("the real Scraper workspace owns the terminal theme and restores administration", async ({ page }) => {
    await page.goto("./admin/overview");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "jawnix");

    await page.goto("./admin/acquisition/scraper");
    await expect(page.getByRole("heading", {
      level: 1,
      name: "Verify access to Scraper Operations",
    })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "terminal");

    const nav = page.getByRole("navigation", { name: "Administration" });
    await nav.getByRole("link", { name: "Fulfillment" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "jawnix");
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
      if (url.includes("/app/assets/")) assetUrls.push(url);
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
