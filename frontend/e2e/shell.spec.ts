import { expect, test } from "@playwright/test";

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
  test("reaches every primary destination", async ({ page }) => {
    await page.goto("./admin");

    await expect(page).toHaveURL(/\/app\/admin\/overview$/);
    const nav = page.getByRole("navigation", { name: "Administration" });

    for (const destination of ["Fulfillment", "Acquisition", "Customers"]) {
      await nav.getByRole("link", { name: destination }).click();
      await expect(nav.getByRole("link", { name: destination })).toHaveAttribute("aria-current", "page");
    }
  });

  test("switches to the terminal theme inside Acquisition and back out again", async ({ page }) => {
    await page.goto("./admin/overview");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "jawnix");

    const nav = page.getByRole("navigation", { name: "Administration" });
    await nav.getByRole("link", { name: "Acquisition" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "terminal");

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
