import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { invitationHash, mockCustomerAuth } from "./customer-auth-fixtures";

const INVITATION_RECOVERY =
  "This invitation cannot be used. Email noah@jawnix.com and I'll send a replacement, or sign in if you already set your password.";
const WCAG_AA_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

test.describe("Static authentication", () => {
  test("sign-in is a static plate with no WebGL scene", async ({ page }) => {
    await mockCustomerAuth(page);
    await page.goto("./sign-in");

    await expect(page.locator(".jx-opaline-scene")).toHaveCount(0);
    await expect(page.locator("canvas")).toHaveCount(0);
    // The plate is the JX stamp at rest, and the lockup's stamp is the scheme
    // control — the whole brand row, nothing rendered behind it.
    await expect(page.locator(".jx-routing-plate")).toBeAttached();
    await expect(
      page.getByRole("button", { name: "Switch to dark desk" }),
    ).toBeVisible();
    await expect(page.getByText("JAWNIX", { exact: true })).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();
    expect(results.violations).toEqual([]);
    await expect(page).toHaveScreenshot("sign-in.png", {
      // Variable-font rasterization (interpolated weights/widths) shifts
      // sub-pixel anti-aliasing by up to ~2% between CI runs; structural
      // regressions produce far larger diffs, so 3% still guards the layout.
      maxDiffPixelRatio: 0.03,
      animations: "disabled",
      caret: "hide",
    });
  });

  test("invitation form stays usable without a scene", async ({ page }) => {
    await mockCustomerAuth(page);
    await page.goto(`./accept-invitation${invitationHash()}`);

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
});

test.describe("Customer sign-in and session lifecycle", () => {
  test("uses the light scheme and the quiet register", async ({ page }) => {
    await mockCustomerAuth(page);
    await page.goto("./sign-in");

    await expect(page.locator("html")).toHaveAttribute("data-scheme", "light");
    // Headings are Inter in the fresh register; Archivo survives only in the
    // frozen wordmark.
    await expect(
      page.getByRole("heading", { level: 1, name: "Sign in" }),
    ).toHaveCSS("font-family", /Inter/);
    await expect(page.locator(".jx-lockup__wordmark")).toHaveCSS(
      "font-family",
      /Archivo/,
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
    await expect(
      page.getByRole("button", { name: "Switch to dark desk" }),
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
    await page
      .getByLabel("Email address (required)")
      .fill("customer@example.com");
    await page
      .getByLabel("Password (required)")
      .fill("customer-known-password-48");
    await page.getByRole("button", { name: "Sign in" }).click();

    const error = page.getByRole("alert");
    await expect(error).toBeFocused();
    await expect(error).toHaveText(
      "We could not sign you in. Check your details, or email noah@jawnix.com — Noah answers it.",
    );
    await expect(page.locator("body")).not.toContainText(
      "provider-known-secret-48",
    );
    await expect(page.locator("body")).not.toContainText(
      "customer-known-password-48",
    );
  });

  test("returns an expired protected session to sign-in with a safe destination", async ({
    page,
  }) => {
    await mockCustomerAuth(page, { overviewStatus: 401 });
    await page.goto("./account");

    await expect(page).toHaveURL(/\/app\/sign-in\?next=%2Fapp%2Faccount$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Sign in" }),
    ).toBeVisible();
    await expect(page.locator("body")).not.toContainText(
      "expired-session-known-secret-48",
    );
  });
});

test.describe("Customer invitation acceptance", () => {
  test("sets the Customer's password and returns to sign-in", async ({
    page,
  }) => {
    const state = await mockCustomerAuth(page);
    const password = "customer-known-password-48";
    await page.goto(`./accept-invitation${invitationHash()}`);

    await expect(
      page.getByText("Your administrator cannot see or set this password.", {
        exact: false,
      }),
    ).toBeVisible();
    await page
      .getByLabel("Password (required)", { exact: true })
      .fill(password);
    await page
      .getByLabel("Confirm password (required)", { exact: true })
      .fill(password);
    await page
      .getByLabel("Confirm password (required)", { exact: true })
      .press("Enter");

    await expect(page).toHaveURL(/\/app\/sign-in\?password=updated$/);
    const update = state.providerRequests.find(
      (request) =>
        request.path === "/auth/reset-password" && request.method === "POST",
    );
    expect(update?.body).toMatchObject({ newPassword: password });
    expect(state.sessionRequests).toEqual([]);
    expect(JSON.stringify(state.sessionRequests)).not.toContain(password);
  });

  test("associates a keyboard-submitted mismatch with the password field", async ({
    page,
  }) => {
    await mockCustomerAuth(page);
    await page.goto(`./accept-invitation${invitationHash()}`);
    const password = page.getByLabel("Password (required)", { exact: true });
    await password.fill("first-known-password-48");
    await page
      .getByLabel("Confirm password (required)", { exact: true })
      .fill("second-known-password-48");
    await page
      .getByLabel("Confirm password (required)", { exact: true })
      .press("Enter");

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
    {
      name: "expired",
      hash: invitationHash(),
      options: { passwordUpdateAccepted: false },
    },
    { name: "reused", hash: "", options: {} },
    {
      name: "replaced",
      hash: invitationHash(),
      options: { passwordUpdateAccepted: false },
    },
  ] as const) {
    test(`${scenario.name} invitations have the same accessible recovery response`, async ({
      page,
    }) => {
      await mockCustomerAuth(page, scenario.options);
      await page.goto(`./accept-invitation${scenario.hash}`);

      if (scenario.hash) {
        await page
          .getByLabel("Password (required)", { exact: true })
          .fill("customer-known-password-48");
        await page
          .getByLabel("Confirm password (required)", { exact: true })
          .fill("customer-known-password-48");
        await page.getByRole("button", { name: "Create password" }).click();
      }

      const recovery = page.getByRole("alert");
      await expect(recovery).toContainText("Request a new invitation");
      await expect(recovery).toContainText(INVITATION_RECOVERY);
      await expect(
        page.getByRole("link", { name: "Go to sign in" }),
      ).toBeVisible();
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

test.describe("Customer password reset", () => {
  test("asking for a link explains the wait for an email and points at the reset page", async ({
    page,
  }) => {
    const state = await mockCustomerAuth(page);
    await page.goto("./sign-in");

    const reset = page.getByRole("button", { name: "Set or reset password" });
    await expect(reset).toBeDisabled();
    await expect(reset).toHaveAccessibleDescription(
      "Enter your email address above to get a link for setting a new password.",
    );

    await page.getByLabel("Email address (required)").fill("customer@example.com");
    await expect(reset).toBeEnabled();
    await expect(page.getByText(/Enter your email address above/)).toHaveCount(0);
    await reset.click();

    await expect(page.getByRole("status")).toContainText(
      "a link to choose a new password is on its way",
    );
    const request = state.providerRequests.find((item) =>
      item.path.endsWith("/request-password-reset"),
    );
    expect(request?.body).toMatchObject({
      email: "customer@example.com",
      redirectTo: expect.stringMatching(/\/app\/reset-password$/),
    });
  });

  test("the reset page uses its own words and confirms on sign-in", async ({
    page,
  }) => {
    const state = await mockCustomerAuth(page);
    const password = "customer-known-password-48";
    await page.goto(`./reset-password${invitationHash()}`);

    await expect(
      page.getByRole("heading", { level: 1, name: "Choose a new password" }),
    ).toBeVisible();
    await expect(page.locator("body")).not.toContainText(/invitation/i);
    await page.getByLabel("Password (required)", { exact: true }).fill(password);
    await page
      .getByLabel("Confirm password (required)", { exact: true })
      .fill(password);
    await page.getByRole("button", { name: "Save new password" }).click();

    await expect(page).toHaveURL(/\/app\/sign-in\?password=updated$/);
    await expect(page.getByRole("status")).toHaveText(
      "Your password was changed. Sign in with your new password.",
    );
    const update = state.providerRequests.find(
      (request) =>
        request.path === "/auth/reset-password" && request.method === "POST",
    );
    expect(update?.body).toMatchObject({ newPassword: password });
  });

  for (const scenario of [
    { name: "missing", hash: "", options: {} },
    { name: "provider-refused", hash: "?error=INVALID_TOKEN", options: {} },
    {
      name: "expired",
      hash: invitationHash(),
      options: { passwordUpdateAccepted: false },
    },
  ] as const) {
    test(`a ${scenario.name} reset link explains itself without invitation words`, async ({
      page,
    }) => {
      await mockCustomerAuth(page, scenario.options);
      await page.goto(`./reset-password${scenario.hash}`);

      if (scenario.hash === invitationHash()) {
        await page
          .getByLabel("Password (required)", { exact: true })
          .fill("customer-known-password-48");
        await page
          .getByLabel("Confirm password (required)", { exact: true })
          .fill("customer-known-password-48");
        await page.getByRole("button", { name: "Save new password" }).click();
      }

      const recovery = page.getByRole("alert");
      await expect(recovery).toContainText("This password link has expired");
      await expect(recovery).toContainText(
        "Password links work once and expire after an hour.",
      );
      await expect(recovery).not.toContainText(/invitation/i);
      await expect(page.getByRole("link", { name: "Go to sign in" })).toBeVisible();
      await expect(page.locator("body")).not.toContainText(
        "provider-known-secret-48",
      );

      const results = await new AxeBuilder({ page })
        .withTags(WCAG_AA_TAGS)
        .analyze();
      expect(results.violations).toEqual([]);
    });
  }
});
