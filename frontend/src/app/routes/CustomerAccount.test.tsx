import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import { CustomerAccountRoute } from "./CustomerAccount";
import { customerAccountLoader } from "./licensedStates";
import type {
  CustomerAccountIdentity,
  LicensedStateReview,
  LicensedStateWorkspace,
} from "./licensedStates";

const IDENTITY: CustomerAccountIdentity = {
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "river@northstar.example",
  first_name: "River",
  last_name: "Morgan",
  phone: "(215) 555-0142",
  customer_id: 7,
  mapping_confirmed_at: "2026-07-28T12:00:00Z",
};

const ACCOUNT: LicensedStateWorkspace = {
  states: ["FL", "TX"],
  version: "2026-07-29T12:00:00+00:00",
  options: [
    { code: "CA", name: "California" },
    { code: "DC", name: "District of Columbia" },
    { code: "FL", name: "Florida" },
    { code: "NC", name: "North Carolina" },
    { code: "SC", name: "South Carolina" },
    { code: "TX", name: "Texas" },
  ],
};

const REVIEW: LicensedStateReview = {
  current_states: ["FL", "TX"],
  proposed_states: ["CA", "FL"],
  added_states: ["CA"],
  removed_states: ["TX"],
  additions_apply_to_future_requests_only: true,
  impacts: [
    {
      request_id: "11111111-1111-4111-8111-111111111111",
      number: 1042,
      lead_count: 100,
      status: "Approved",
      current_states: ["FL", "TX"],
      resulting_states: ["FL"],
      action: "narrowed",
    },
    {
      request_id: "22222222-2222-4222-8222-222222222222",
      number: 1043,
      lead_count: 50,
      status: "Preparing Batch",
      current_states: ["TX"],
      resulting_states: [],
      action: "canceled",
    },
  ],
  review_token: "signed-review",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function result(account: LicensedStateWorkspace) {
  return {
    account,
    overview: { items: [] },
    requests: {
      limits: {
        minimum_lead_count: 1,
        maximum_lead_count: 100_000,
        licensed_states: account.states,
      },
      blocker: null,
      requests: [],
    },
  };
}

function renderRoute(
  licensedStates = ACCOUNT,
  identity = IDENTITY,
  path = "/account",
) {
  const router = createMemoryRouter(
    [
      {
        id: "customer",
        path: "/",
        loader: () => ({ customer_name: "Northstar Insurance" }),
        children: [
          {
            id: "account",
            path: "account/:tab?",
            loader: customerAccountLoader,
            element: <CustomerAccountRoute />,
          },
        ],
      },
    ],
    {
      initialEntries: [path],
      hydrationData: {
        loaderData: {
          customer: { customer_name: "Northstar Insurance" },
          account: { identity, licensed_states: licensedStates },
        },
      },
    },
  );
  render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
  return router;
}

/** Fetch that answers the Account reads and records every write. */
function stubFetch(answers: { preview?: unknown; apply?: () => Response; account?: unknown }) {
  const writes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = typeof input === "string" ? input : String(input);
      if (init?.method && init.method !== "GET") writes.push(`${init.method} ${path}`);
      if (path.endsWith("/preview")) return json(answers.preview ?? REVIEW);
      if (path.endsWith("/apply")) return answers.apply ? answers.apply() : json(result(ACCOUNT));
      if (path.endsWith("/profile")) return json(IDENTITY);
      return json(answers.account ?? ACCOUNT);
    }),
  );
  return writes;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Account tabs", () => {
  it("names the person and company and marks the current tab", () => {
    renderRoute(ACCOUNT, IDENTITY, "/account/states");

    expect(screen.getByText("River Morgan · Northstar Insurance")).toBeVisible();
    const tabs = screen.getByRole("navigation", { name: "Account" });
    expect(within(tabs).getByRole("link", { name: "Appointed states" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(tabs).getByRole("link", { name: "Profile" })).toHaveAttribute("href", "/app/account");
    expect(within(tabs).getByRole("link", { name: "Password" })).toHaveAttribute(
      "href",
      "/app/account/password",
    );
    // Billing shows for billed Customers only; this one has no Credit Wallet.
    expect(within(tabs).queryByRole("link", { name: "Billing" })).not.toBeInTheDocument();
  });

  it("sends an unknown tab back to Profile", async () => {
    stubFetch({});
    const router = renderRoute(ACCOUNT, IDENTITY, "/account/nowhere");

    await waitFor(() => expect(router.state.location.pathname).toBe("/account"));
    expect(await screen.findByRole("region", { name: "Your details" })).toBeVisible();
  });
});

describe("Profile", () => {
  it("shows the details and what stands between the Customer and a first batch", () => {
    renderRoute(
      { ...ACCOUNT, states: [] },
      { ...IDENTITY, customer_id: null, mapping_confirmed_at: null },
    );

    const details = screen.getByRole("region", { name: "Your details" });
    expect(within(details).getByDisplayValue("River")).toBeVisible();
    expect(within(details).getByDisplayValue("Morgan")).toBeVisible();
    expect(within(details).getByDisplayValue("river@northstar.example")).toBeDisabled();
    expect(within(details).getByDisplayValue("(215) 555-0142")).toBeVisible();
    expect(within(details).getByRole("button", { name: "Save profile" })).toBeDisabled();

    const setup = screen.getByRole("region", { name: "Before you can request batches" });
    expect(within(setup).getByText("Your account is being set up")).toBeVisible();
    expect(within(setup).getByText("No appointed states")).toBeVisible();
    expect(within(setup).getByRole("link", { name: "Appointed states" })).toHaveAttribute(
      "href",
      "/account/states",
    );
    expect(screen.getByRole("region", { name: "Your contact" })).toHaveTextContent("noah@jawnix.com");
  });

  it("has nothing to say about setup once it is done", () => {
    renderRoute();

    expect(
      screen.queryByRole("region", { name: "Before you can request batches" }),
    ).not.toBeInTheDocument();
  });
});

describe("Appointed states", () => {
  it("saves an added state straight away when nothing in progress changes", async () => {
    const added = { ...ACCOUNT, states: ["CA", "FL", "TX"], version: "v2" };
    const writes = stubFetch({
      preview: { ...REVIEW, proposed_states: added.states, added_states: ["CA"], removed_states: [], impacts: [] },
      apply: () => json(result(added)),
      account: added,
    });
    const user = userEvent.setup();
    renderRoute(ACCOUNT, IDENTITY, "/account/states");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "California" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Appointed states saved.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(writes).toEqual([
      "POST /api/me/licensed-states/preview",
      "POST /api/me/licensed-states/apply",
    ]);
    expect(screen.getByText("3 selected · California, Florida, Texas")).toBeVisible();
  });

  it("asks before a change narrows or cancels a batch, and applies nothing until confirmed", async () => {
    const writes = stubFetch({
      apply: () => json(result({ ...ACCOUNT, states: ["CA", "FL"], version: "v2" })),
    });
    const user = userEvent.setup();
    renderRoute(ACCOUNT, IDENTITY, "/account/states");

    await user.click(screen.getByRole("checkbox", { name: "California" }));
    await user.click(screen.getByRole("checkbox", { name: "Texas" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    const dialog = await screen.findByRole("dialog", { name: "Confirm appointed states" });
    expect(dialog).toHaveAccessibleDescription(/Nothing changes until you confirm/);
    expect(within(dialog).getByText("Batch #1042")).toBeVisible();
    expect(within(dialog).getByText("Batch #1043")).toBeVisible();
    expect(within(dialog).getByText("Will be canceled")).toBeVisible();
    expect(within(dialog).getByText("California")).toBeVisible();
    expect(within(dialog).getByText("Texas")).toBeVisible();
    expect(writes).toEqual(["POST /api/me/licensed-states/preview"]);

    await user.click(within(dialog).getByRole("button", { name: "Keep editing" }));
    expect(writes).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm and save" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Appointed states saved."),
    );
    expect(writes).toContain("POST /api/me/licensed-states/apply");
  });

  it("rejects a stale review and reloads the concurrent selection", async () => {
    const concurrent = { ...ACCOUNT, states: ["CA", "FL", "TX"], version: "2026-07-29T12:06:00+00:00" };
    stubFetch({
      apply: () =>
        json(
          {
            detail:
              "Your appointed states changed in another session. Review the latest changes before saving.",
          },
          409,
        ),
      account: concurrent,
    });
    const user = userEvent.setup();
    renderRoute(ACCOUNT, IDENTITY, "/account/states");

    await user.click(screen.getByRole("checkbox", { name: "California" }));
    await user.click(screen.getByRole("checkbox", { name: "Texas" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm and save" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("changed in another session");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Texas" })).toBeChecked());
    expect(screen.getByRole("checkbox", { name: "California" })).toBeChecked();
  });
});
