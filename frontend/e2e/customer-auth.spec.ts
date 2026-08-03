import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import {
  invitationHash,
  mockCustomerAuth,
} from "./customer-auth-fixtures";

const INVITATION_RECOVERY =
  "This invitation cannot be used. Ask your administrator for a new invitation, or sign in if you already set your password.";
const WCAG_AA_TAGS = [
  "wcag2a",
  "wcag2aa",
  "wcag21a",
  "wcag21aa",
  "wcag22aa",
];

test.describe("Opaline authentication scene", () => {
  test("reduced motion renders one static, accessible frame", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(() => {
      let seed = 116;
      Math.random = () => {
        seed = (seed * 16807) % 2147483647;
        return (seed - 1) / 2147483646;
      };
    });
    await mockCustomerAuth(page);
    await page.goto("./sign-in");

    const scene = page.locator(".jx-opaline-scene");
    const canvas = scene.locator("canvas");
    await expect(scene).toHaveAttribute("data-opaline-state", "static");
    await expect(canvas).toBeVisible();

    const firstFrame = await canvas.screenshot();
    await page.waitForTimeout(200);
    const secondFrame = await canvas.screenshot();
    expect(secondFrame.equals(firstFrame)).toBe(true);

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();
    expect(results.violations).toEqual([]);
    await expect(page).toHaveScreenshot("opaline-sign-in.png", {
      animations: "disabled",
      caret: "hide",
    });
  });

  test("pauses animation while the form has keyboard focus", async ({ page }) => {
    await mockCustomerAuth(page);
    await page.goto("./sign-in");

    const scene = page.locator(".jx-opaline-scene");
    const canvas = scene.locator("canvas");
    await expect(scene).toHaveAttribute("data-opaline-state", "animated");
    await page.getByLabel("Email address (required)").focus();
    await expect(scene).toHaveAttribute("data-opaline-paused", "true");

    await page.waitForTimeout(200);
    const focusedFrame = await canvas.screenshot();
    await page.waitForTimeout(200);
    const laterFrame = await canvas.screenshot();
    expect(laterFrame.equals(focusedFrame)).toBe(true);
  });

  test("resumes animation once focus leaves the form", async ({ page }) => {
    await mockCustomerAuth(page);
    await page.goto("./sign-in");

    const scene = page.locator(".jx-opaline-scene");
    await expect(scene).toHaveAttribute("data-opaline-state", "animated");
    await page.getByLabel("Email address (required)").focus();
    await expect(scene).toHaveAttribute("data-opaline-paused", "true");

    // Moving focus out of the form region resumes the scene; without the
    // blur-capture reset the pause latched on the first focus forever.
    // Blur the active field directly — the centred card covers the viewport,
    // so a background click would land back inside the form and keep focus.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await expect(scene).not.toHaveAttribute("data-opaline-paused", "true");
  });

  test("WebGL failure keeps the gradient fallback and invitation form usable", async ({
    page,
  }) => {
    await page.addInitScript({
      content: `
        const getContext = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (type, ...args) {
          if (type === "webgl" || type === "experimental-webgl" || type === "webgl2") {
            return null;
          }
          return getContext.call(this, type, ...args);
        };
      `,
    });
    await mockCustomerAuth(page);
    await page.goto(`./accept-invitation${invitationHash()}`);

    const scene = page.locator(".jx-opaline-scene");
    await expect(scene).toHaveAttribute("data-opaline-state", "fallback");
    await expect(scene.locator("canvas")).toHaveCSS("opacity", "0");
    const background = await scene.evaluate(
      (element) => getComputedStyle(element).backgroundImage,
    );
    expect(background).toContain("linear-gradient");
    expect(background).toContain("rgb(18, 6, 13)");
    expect(background).toContain("rgb(3, 4, 12)");

    await page
      .getByLabel("Password (required)", { exact: true })
      .fill("first-known-password-48");
    await page
      .getByLabel("Confirm password (required)", { exact: true })
      .fill("second-known-password-48");
    await page.getByRole("button", { name: "Create password" }).click();
    await expect(page.getByRole("alert")).toHaveText(
      "The passwords do not match.",
    );

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test("working-app routes never request the auth scene", async ({ page }) => {
    const scripts: string[] = [];
    page.on("response", (response) => {
      if (response.request().resourceType() === "script") {
        scripts.push(response.url());
      }
    });
    await mockCustomerAuth(page);
    await page.goto("./account");
    await expect(
      page.getByRole("heading", { level: 1, name: "Account" }),
    ).toBeVisible();
    await expect(page.locator(".jx-opaline-scene")).toHaveCount(0);
    expect(
      scripts.filter((url) => /CustomerAuth|opaline-three/.test(url)),
    ).toEqual([]);
  });
});

test.describe("Customer sign-in and session lifecycle", () => {
  test("uses Opaline with the chosen Fraunces display face", async ({ page }) => {
    await mockCustomerAuth(page);
    await page.goto("./sign-in");

    await expect(page.locator("html")).toHaveAttribute("data-theme", "opaline");
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toHaveCSS(
      "font-family",
      /Fraunces Variable/,
    );
  });

  test("signs in through the Jawnix boundary and signs out with the keyboard", async ({
    page,
  }) => {
    const state = await mockCustomerAuth(page);
    const password = "customer-known-password-48";
    await page.goto("./sign-in?next=/app/account");

    await page.locator("body").press("Tab");
    await expect(
      page.getByRole("link", { name: "Skip to main content" }),
    ).toBeFocused();
    await page.keyboard.press("Tab");
    await page.keyboard.type("customer@example.com");
    await page.keyboard.press("Tab");
    await page.keyboard.type(password);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/app\/account$/);
    await expect.poll(() => state.sessionRequests.length).toBe(1);
    expect(state.sessionRequests).toEqual([
      {
        access_token: "customer-provider-access-token-long-enough",
        requested_next: "/app/account",
      },
    ]);
    expect(JSON.stringify(state.sessionRequests)).not.toContain(password);

    const signOut = page.getByRole("button", { name: "Sign out" });
    await signOut.focus();
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/app\/sign-in$/);
    expect(state.jawnixLogoutCalls).toBe(1);
    expect(state.providerLogoutCalls).toBe(1);
  });

  test("presents a focused, non-revealing sign-in error", async ({ page }) => {
    await mockCustomerAuth(page, { signInAccepted: false });
    await page.goto("./sign-in");
    await page.getByLabel("Email address (required)").fill("customer@example.com");
    await page.getByLabel("Password (required)").fill("customer-known-password-48");
    await page.getByRole("button", { name: "Sign in" }).click();

    const error = page.getByRole("alert");
    await expect(error).toBeFocused();
    await expect(error).toHaveText(
      "We could not sign you in. Check your details or ask your administrator for help.",
    );
    await expect(page.locator("body")).not.toContainText("provider-known-secret-48");
    await expect(page.locator("body")).not.toContainText("customer-known-password-48");
  });

  test("returns an expired protected session to sign-in with a safe destination", async ({
    page,
  }) => {
    await mockCustomerAuth(page, { overviewStatus: 401 });
    await page.goto("./account");

    await expect(page).toHaveURL(
      /\/app\/sign-in\?next=%2Fapp%2Faccount$/,
    );
    await expect(
      page.getByRole("heading", { level: 1, name: "Sign in" }),
    ).toBeVisible();
    await expect(page.locator("body")).not.toContainText(
      "expired-session-known-secret-48",
    );
  });
});

test.describe("Customer invitation acceptance", () => {
  test("sets the Customer's password and establishes the Jawnix session", async ({
    page,
  }) => {
    const state = await mockCustomerAuth(page);
    const password = "customer-known-password-48";
    await page.goto(`./accept-invitation${invitationHash()}`);

    await expect(
      page.getByText(
        "Your administrator cannot see or set this password.",
        { exact: false },
      ),
    ).toBeVisible();
    await page.getByLabel("Password (required)", { exact: true }).fill(password);
    await page.getByLabel("Confirm password (required)", { exact: true }).fill(password);
    await page.getByLabel("Confirm password (required)", { exact: true }).press("Enter");

    await expect(page).toHaveURL(/\/app\/overview$/);
    const update = state.providerRequests.find(
      (request) =>
        request.path === "/auth/v1/user" && request.method === "PUT",
    );
    expect(update?.body).toMatchObject({ password });
    expect(JSON.stringify(state.sessionRequests)).not.toContain(password);
  });

  test("associates a keyboard-submitted mismatch with the password field", async ({
    page,
  }) => {
    await mockCustomerAuth(page);
    await page.goto(`./accept-invitation${invitationHash()}`);
    const password = page.getByLabel("Password (required)", { exact: true });
    await password.fill("first-known-password-48");
    await page.getByLabel("Confirm password (required)", { exact: true }).fill(
      "second-known-password-48",
    );
    await page.getByLabel("Confirm password (required)", { exact: true }).press("Enter");

    await expect(page.getByRole("alert")).toHaveText(
      "The passwords do not match.",
    );
    await expect(
      page.getByLabel("Confirm password (required)", { exact: true }),
    ).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator("body")).not.toContainText(
      "first-known-password-48",
    );
  });

  for (const scenario of [
    { name: "invalid", hash: "", options: {} },
    { name: "expired", hash: invitationHash(), options: { passwordUpdateAccepted: false } },
    { name: "reused", hash: "", options: {} },
    { name: "replaced", hash: invitationHash(), options: { sessionExchangeStatus: 403 } },
  ] as const) {
    test(`${scenario.name} invitations have the same accessible recovery response`, async ({
      page,
    }) => {
      await mockCustomerAuth(page, scenario.options);
      await page.goto(`./accept-invitation${scenario.hash}`);

      if (scenario.hash) {
        await page.getByLabel("Password (required)", { exact: true }).fill(
          "customer-known-password-48",
        );
        await page.getByLabel("Confirm password (required)", { exact: true }).fill(
          "customer-known-password-48",
        );
        await page.getByRole("button", { name: "Create password" }).click();
      }

      const recovery = page.getByRole("alert");
      await expect(recovery).toContainText("Request a new invitation");
      await expect(recovery).toContainText(INVITATION_RECOVERY);
      await expect(page.getByRole("link", { name: "Go to sign in" })).toBeVisible();
      await expect(page.locator("body")).not.toContainText(
        "provider-known-secret-48",
      );
      await expect(page.locator("body")).not.toContainText(
        "inactive-account-known-secret-48",
      );
      await expect(page.locator("body")).not.toContainText(
        "customer-known-password-48",
      );
    });
  }
});
