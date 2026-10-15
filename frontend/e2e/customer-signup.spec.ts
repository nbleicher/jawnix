import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockCustomerAuth } from "./customer-auth-fixtures";

test("signup collects identity before states and sends credentials only to the provider", async ({ page }) => {
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
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.getByRole("button", { name:"Create account", exact:true }).click();
  await expect(page.getByRole("heading", { name:"Check your email" })).toBeVisible();
  expect(body).toMatchObject({ email:"river@example.test", password:"signup-password-test-only", signupProfile:{ first_name:"River", last_name:"Morgan", phone:"6155550100" } });
  expect(body.signupProfile).not.toHaveProperty("licensed_states");
  expect(state.sessionRequests).toEqual([]);
  expect(body).not.toHaveProperty("jawnixRole");
});

test("verified signup selects states in a separate step before reaching the portal", async ({ page }) => {
  const state = await mockCustomerAuth(page);
  let completed = false;
  await page.route(/\/api\/auth\/session$/, route => {
    state.sessionRequests.push(route.request().postDataJSON());
    return route.fulfill({ json:{ ok:true, role:"customer", next:"/app/appointed-states" } });
  });
  await page.route(/\/api\/me\/registration$/, route => route.fulfill({ json: { status:completed ? "active" : "awaiting_review", next:completed ? "/app/overview" : "/app/appointed-states" } }));
  await page.route(/\/api\/auth\/signup-options$/, route => route.fulfill({ json: { states: [{ code:"TN", name:"Tennessee" }, { code:"SC", name:"South Carolina" }] } }));
  await page.route(/\/api\/me\/registration\/states$/, route => {
    expect(route.request().postDataJSON()).toEqual({ states:["SC", "TN"] });
    expect(route.request().headers()["x-csrf-token"]).toBe("e2e-csrf");
    completed = true;
    return route.fulfill({ json: { status:"active", next:"/app/overview" } });
  });
  await page.goto("./complete-signup");
  await expect(page).toHaveURL(/\/app\/appointed-states$/);
  await expect(page.getByRole("heading", { name:"Select your appointed states" })).toBeVisible();
  await page.getByRole("button", { name:"Continue", exact:true }).click();
  await expect(page.getByRole("alert")).toHaveText("Select at least one appointed state.");
  await page.getByLabel("Tennessee (TN)").check();
  await page.getByLabel("South Carolina (SC)").check();
  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  expect(await page.locator(".jx-signup-states__options").evaluate(element => getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(5);
  await page.getByRole("button", { name:"Continue", exact:true }).click();
  await expect(page).toHaveURL(/\/app\/overview$/);
  expect(state.sessionRequests).toEqual([{ access_token:"customer-provider-access-token-long-enough", requested_next:"/app/overview" }]);
  await page.goto("./appointed-states");
  await expect(page).toHaveURL(/\/app\/overview$/);
});

test("unfinished state setup resumes from a direct portal visit and can proceed to history review", async ({ page }) => {
  await mockCustomerAuth(page, { overviewStatus:409 });
  let completed = false;
  await page.route(/\/api\/me\/registration$/, route => route.fulfill({ json: { status:"awaiting_review", next:completed ? "/app/registration-status" : "/app/appointed-states" } }));
  await page.route(/\/api\/auth\/signup-options$/, route => route.fulfill({ json: { states: [{ code:"TN", name:"Tennessee" }] } }));
  await page.route(/\/api\/me\/registration\/states$/, route => {
    completed = true;
    return route.fulfill({ json: { status:"awaiting_review", next:"/app/registration-status" } });
  });
  await page.goto("./overview");
  await expect(page).toHaveURL(/\/app\/appointed-states$/);
  await page.reload();
  await expect(page.getByRole("heading", { name:"Select your appointed states" })).toBeVisible();
  await page.getByLabel("Tennessee (TN)").check();
  await page.getByRole("button", { name:"Continue", exact:true }).click();
  await expect(page).toHaveURL(/\/app\/registration-status$/);
  await expect(page.getByRole("heading", { name:"Account awaiting review" })).toBeVisible();
});

test("state save failure preserves the selection for retry", async ({ page }) => {
  await mockCustomerAuth(page);
  await page.route(/\/api\/me\/registration$/, route => route.fulfill({ json: { status:"awaiting_review", next:"/app/appointed-states" } }));
  await page.route(/\/api\/auth\/signup-options$/, route => route.fulfill({ json: { states: [{ code:"TN", name:"Tennessee" }] } }));
  await page.route(/\/api\/me\/registration\/states$/, route => route.fulfill({ status:503, json:{ detail:"Unavailable" } }));
  await page.goto("./appointed-states");
  await page.getByLabel("Tennessee (TN)").check();
  await page.getByRole("button", { name:"Continue", exact:true }).click();
  await expect(page.getByRole("alert")).toHaveText("We could not save your appointed states. Please try again.");
  await expect(page.getByLabel("Tennessee (TN)")).toBeChecked();
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
