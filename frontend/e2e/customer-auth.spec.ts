import { expect, test } from "@playwright/test";

import {
  invitationHash,
  mockCustomerAuth,
} from "./customer-auth-fixtures";

const INVITATION_RECOVERY =
  "This invitation cannot be used. Ask your administrator for a new invitation, or sign in if you already set your password.";

test.describe("Customer sign-in and session lifecycle", () => {
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
    await mockCustomerAuth(page, { profileStatus: 401 });
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
