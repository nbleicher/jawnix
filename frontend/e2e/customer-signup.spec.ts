import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockCustomerAuth } from "./customer-auth-fixtures";

test("signup collects appointed states and sends credentials only to the provider", async ({ page }) => {
  const state = await mockCustomerAuth(page);
  await page.route(/\/api\/auth\/signup-options$/, route => route.fulfill({ json: { states: [{ code:"TN", name:"Tennessee" }, { code:"SC", name:"South Carolina" }] } }));
  let body: Record<string, unknown> = {};
  await page.route(/\/auth\/sign-up\/email$/, route => {
    body = route.request().postDataJSON();
    return route.fulfill({ json: { token: null, user: { emailVerified: false } } });
  });
  await page.goto("./sign-in");
  await page.getByRole("link", { name:"Create an account" }).click();
  await page.getByLabel("First name (required)").fill("River");
  await page.getByLabel("Last name (required)").fill("Morgan");
  await page.getByLabel("Phone number (required)").fill("6155550100");
  await page.getByLabel("Email address (required)").fill("river@example.test");
  await page.getByLabel("Password (required)").fill("signup-password-test-only");
  await page.getByRole("button", { name:"Create account", exact:true }).click();
  await expect(page.getByRole("alert")).toHaveText("Select at least one appointed state.");
  await page.getByLabel("Tennessee (TN)").check();
  await page.getByLabel("South Carolina (SC)").check();
  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.getByRole("button", { name:"Create account", exact:true }).click();
  await expect(page.getByRole("heading", { name:"Check your email" })).toBeVisible();
  expect(body).toMatchObject({ email:"river@example.test", password:"signup-password-test-only", signupProfile:{ first_name:"River", last_name:"Morgan", phone:"6155550100", licensed_states:["SC", "TN"] } });
  expect(state.sessionRequests).toEqual([]);
  expect(body).not.toHaveProperty("jawnixRole");
});

test("verified signup refreshes its provider session and reaches the portal", async ({ page }) => {
  const state = await mockCustomerAuth(page);
  await page.goto("./complete-signup");
  await expect(page).toHaveURL(/\/app\/overview$/);
  expect(state.sessionRequests).toEqual([{ access_token:"customer-provider-access-token-long-enough", requested_next:"/app/overview" }]);
});

test("a matching registration waits for operator review and checks approval without a redirect loop", async ({ page }) => {
  await mockCustomerAuth(page, { overviewStatus:409 });
  let approved = false;
  await page.route(/\/api\/me\/registration$/, route => route.fulfill({ json: { status:approved ? "active" : "awaiting_review", next:approved ? "/app/overview" : "/app/registration-status" } }));
  await page.goto("./overview");
  await expect(page).toHaveURL(/\/app\/registration-status$/);
  await expect(page.getByRole("heading", { name:"Account awaiting review" })).toBeVisible();
  approved = true;
  await page.route(/\/api\/me\/overview$/, route => route.fulfill({ json: { items:[] } }));
  await page.getByRole("button", { name:"Check status" }).click();
  await expect(page).toHaveURL(/\/app\/overview$/);
});
