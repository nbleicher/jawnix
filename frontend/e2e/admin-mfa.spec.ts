import { expect, test } from "@playwright/test";

import { mockAdminMFA, verifiedFactors } from "./mfa-fixtures";

test.describe("Administrator MFA", () => {
  test("retries a non-revealing sign-in challenge and then continues", async ({
    page,
  }) => {
    const state = await mockAdminMFA(page);
    await page.goto("./admin/mfa/challenge");

    await expect(
      page.getByRole("heading", {
        level: 1,
        name: "Verify your administrator sign-in",
      }),
    ).toBeVisible();
    await page
      .getByLabel("Six-digit authenticator code (required)")
      .fill("000 000");
    await page
      .getByRole("button", { name: "Verify and continue" })
      .click();
    await expect(page.getByRole("alert")).toContainText(
      "could not be verified",
    );
    expect(state.challengeFailures).toBe(1);

    await page
      .getByLabel("Six-digit authenticator code (required)")
      .fill("123-456");
    await page
      .getByRole("button", { name: "Verify and continue" })
      .click();
    await expect(page).toHaveURL(/\/app\/admin\/overview$/);
  });

  test("enrolls primary and separately stored backup factors", async ({
    page,
  }) => {
    const state = await mockAdminMFA(page, {
      factors: [],
      stage: "idle",
    });
    await page.goto("./admin/mfa/enroll");

    await page
      .getByRole("button", { name: "Set up primary authenticator" })
      .click();
    await expect(
      page.getByAltText("QR code for the primary authenticator"),
    ).toBeVisible();
    await expect(page.getByText("MANUAL-PRIMARY-KEY")).toBeVisible();

    // A refresh resumes the unverified scan from session storage without
    // asking the provider to create a different secret.
    await page.reload();
    await expect(page.getByText("MANUAL-PRIMARY-KEY")).toBeVisible();
    await page
      .getByLabel("Six-digit authenticator code (required)")
      .fill("123 456");
    await page.getByRole("button", { name: "Verify authenticator" }).click();

    await expect(
      page.getByRole("heading", {
        level: 1,
        name: "Set up your backup authenticator",
      }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Set up backup authenticator" })
      .click();
    await expect(page.getByText(/somewhere other than your primary device/)).toBeVisible();
    await page
      .getByLabel("Six-digit authenticator code (required)")
      .fill("654321");
    await page.getByRole("button", { name: "Verify authenticator" }).click();

    await expect(page).toHaveURL(/\/app\/admin\/overview$/);
    expect(
      state.factors.filter((factor) => factor.status === "verified"),
    ).toHaveLength(2);
  });

  test("cancels an incomplete enrollment without leaving a factor behind", async ({
    page,
  }) => {
    const state = await mockAdminMFA(page, {
      factors: [],
      stage: "idle",
    });
    await page.goto("./admin/mfa/enroll");
    await page
      .getByRole("button", { name: "Set up primary authenticator" })
      .click();
    await page
      .getByRole("button", { name: "Cancel enrollment" })
      .click();

    await expect(
      page.getByRole("button", { name: "Set up primary authenticator" }),
    ).toBeVisible();
    expect(state.cancelCalls).toBe(1);
    expect(state.factors).toEqual([]);
  });

  test("guides a lost-device recovery and begins safe replacement", async ({
    page,
  }) => {
    const state = await mockAdminMFA(page, {
      assurance: "aal2",
      factors: verifiedFactors(),
    });
    await page.goto("./admin/mfa/recover");

    await expect(
      page.getByRole("heading", {
        level: 1,
        name: "Recover administrator access",
      }),
    ).toBeVisible();
    await expect(
      page.getByText("There is no self-service reset."),
    ).toBeVisible();
    await page
      .getByRole("link", { name: "Choose the backup authenticator" })
      .click();
    await expect(
      page.getByText("Jawnix backup", { exact: true }),
    ).toBeVisible();

    await page.goto("./admin/security");
    await page
      .getByRole("button", { name: "Replace this authenticator" })
      .first()
      .click();
    await expect(page).toHaveURL(/\/app\/admin\/mfa\/enroll$/);
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: "Replace lost authenticator",
      }),
    ).toBeVisible();
    await expect(page.getByText("MANUAL-REPLACEMENT-KEY")).toBeVisible();
    expect(state.stage).toBe("replacement_pending");
  });
});
