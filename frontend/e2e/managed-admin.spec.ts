import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const MANAGER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const UPLOAD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const queued = { id:UPLOAD, filename:"leads.csv", state:"TN", status:"queued", bytes:55, processedRows:0, validRows:0, rejectedRows:0, importedRows:0, updatedRows:0, duplicateRows:0, error:"", createdAt:"2026-10-13T12:00:00Z", completedAt:null };
const complete = { ...queued, status:"complete", processedRows:1000001, validRows:1000000, importedRows:999999, duplicateRows:1, rejectedRows:1, completedAt:"2026-10-13T12:01:00Z" };

async function mockManager(page: Page) {
  let uploaded = false;
  let finished = false;
  let submitted = "";
  let listReads = 0;
  let assigned = false;
  const customer = { id:7, name:"Harbor Insurance", email:"owner@harbor.example", active:true, licensed_states:["TN"], lead_database:"db2" };
  await page.context().addCookies([{ name:"jawnix_csrf", value:"fixture-csrf", domain:"127.0.0.1", path:"/" }]);
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown;
    if (path === "/api/manager/access") body = { ok:true, id:MANAGER, email:"alex@example.test" };
    else if (path === "/api/manager/uploads" && route.request().method() === "POST") {
      submitted = route.request().postDataBuffer()?.toString() ?? ""; uploaded = true;
      await route.fulfill({ status:202, json:queued }); return;
    } else if (path === "/api/manager/uploads") {
      listReads += 1;
      body = { uploads:uploaded ? [finished ? complete : queued] : [], states:[{code:"TN",name:"Tennessee"}], inventory:{ total:finished ? 999999 : 0, states:finished ? {TN:999999} : {}, asOf:null } };
    } else if (path === `/api/manager/uploads/${UPLOAD}`) { finished = true; body = complete; }
    else if (path === "/api/manager/customers") {
      if (route.request().method() === "POST") assigned = true;
      body = { customers:assigned ? [customer] : [], available:assigned ? [] : [customer] };
    } else { await route.fulfill({ status:404, json:{detail:"Not found"} }); return; }
    await route.fulfill({ json:body });
  });
  return { submitted:() => submitted, listReads:() => listReads };
}

test("CSV upload sends its state and shows scoped background import progress", async ({ page }) => {
  const state = await mockManager(page);
  await page.goto("./manager/uploads");
  await expect(page.getByRole("heading",{level:1,name:"Upload leads"})).toBeVisible();
  await page.getByLabel(/^CSV file/).setInputFiles({ name:"leads.csv", mimeType:"text/csv", buffer:Buffer.from("Phone,Name\n6155550100,Fixture\n") });
  await page.getByLabel(/^State/).selectOption("TN");
  await page.getByRole("button",{name:"Upload CSV",exact:true}).click();
  await expect(page.getByText("Queued",{exact:true})).toBeVisible();
  expect(state.submitted()).toContain('name="state"');
  expect(state.submitted()).toContain("TN");
  expect(state.submitted()).toContain("6155550100,Fixture");
  expect(state.listReads()).toBe(1);
  await expect(page.getByText("Ready",{exact:true})).toBeVisible({ timeout:12000 });
  await expect(page.getByText("TN · 1,000,001 records processed",{exact:true})).toBeVisible();
  await expect(page.getByRole("link",{name:"Download rejected rows"})).toHaveAttribute("href",`/api/manager/uploads/${UPLOAD}/rejected`);
});

test("manager can add only the users shown in the operator-provided selection", async ({ page }) => {
  await mockManager(page);
  await page.goto("./manager/customers");
  const available = page.getByRole("region",{name:"Available users"});
  await available.getByRole("checkbox").check();
  await page.getByRole("button",{name:"Add selected users"}).click();
  await expect(page.getByRole("article",{name:"Harbor Insurance"})).toBeVisible();
  await expect(available.getByText("No additional users are available.")).toBeVisible();
  await expect(page.getByLabel("Lead database for Harbor Insurance")).toHaveCount(0);
});

test("manager can drop a CSV and upload it with the selected state", async ({ page }) => {
  const state = await mockManager(page);
  await page.goto("./manager/uploads");
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(new File(["Phone,Name\n6155550100,Dropped fixture\n"], "dropped.csv", {type:"text/csv"}));
    return data;
  });
  await page.locator(".managed-upload__drop").dispatchEvent("drop", {dataTransfer:transfer});
  await expect(page.getByText("dropped.csv · 0.0 MB", {exact:true})).toBeVisible();
  await page.getByLabel(/^State/).selectOption("TN");
  await page.getByRole("button", {name:"Upload CSV",exact:true}).click();
  await expect(page.getByText("Queued", {exact:true})).toBeVisible();
  expect(state.submitted()).toContain("Dropped fixture");
  expect(state.submitted()).toContain('filename="dropped.csv"');
  await transfer.dispose();
});

