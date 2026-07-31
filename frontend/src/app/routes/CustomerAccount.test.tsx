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
      lead_count: 100,
      status: "Under Review",
      current_states: ["FL", "TX"],
      resulting_states: ["FL"],
      action: "narrowed",
    },
    {
      request_id: "22222222-2222-4222-8222-222222222222",
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
    overview: {
      first_name: "Licensed",
      licensed_states: account.states,
      current_request: null,
      recent_deliveries: [],
      next_action: {
        kind: "request_batch",
        label: "Request a Batch",
        description: "Start a request.",
        href: "/app/requests",
      },
      primary_actions: [],
    },
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
) {
  const router = createMemoryRouter(
    [
      {
        id: "account",
        path: "/account",
        loader: customerAccountLoader,
        element: <CustomerAccountRoute />,
      },
    ],
    {
      initialEntries: ["/account"],
      hydrationData: {
        loaderData: {
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
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Account identity and setup status", () => {
  it("renders identity and every Setup Problem with its next step and actor", () => {
    renderRoute(
      { ...ACCOUNT, states: [] },
      { ...IDENTITY, customer_id: null, mapping_confirmed_at: null },
    );

    const identity = screen.getByRole("region", { name: "Identity" });
    expect(within(identity).getByText("River Morgan")).toBeVisible();
    expect(within(identity).getByText("river@northstar.example")).toBeVisible();
    expect(within(identity).getByText("(215) 555-0142")).toBeVisible();

    const status = screen.getByRole("region", { name: "Setup status" });
    expect(
      within(status).getByRole("heading", {
        name: "Customer mapping needs confirmation",
      }),
    ).toBeVisible();
    expect(
      within(status).getByText(/Jawnix will confirm which Customer record/),
    ).toBeVisible();
    expect(
      within(status).getByRole("heading", { name: "No Licensed States" }),
    ).toBeVisible();
    expect(
      within(status).getByText(/Add at least one Licensed State below/),
    ).toBeVisible();
    expect(within(status).getAllByText("What happens next")).toHaveLength(2);
    expect(within(status).getAllByText("Who acts")).toHaveLength(2);
    expect(within(status).getByText("Jawnix", { exact: true })).toBeVisible();
    expect(within(status).getByText("You", { exact: true })).toBeVisible();
  });

  it("shows a calm ready state when no Setup Problems remain", () => {
    renderRoute();

    const status = screen.getByRole("region", { name: "Setup status" });
    expect(
      within(status).getByRole("heading", { name: "Setup complete" }),
    ).toBeVisible();
    expect(within(status).getByText("Ready")).toBeVisible();
    expect(within(status).queryByText("Needs attention")).not.toBeInTheDocument();
  });
});

describe("Licensed State management", () => {
  it("searches by name or code while keeping full checkbox labels", async () => {
    const user = userEvent.setup();
    renderRoute();

    const search = screen.getByRole("searchbox", { name: "Search states" });
    await user.type(search, "carolina");

    expect(
      screen.getByRole("checkbox", { name: "North Carolina NC" }),
    ).toBeVisible();
    expect(
      screen.getByRole("checkbox", { name: "South Carolina SC" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("checkbox", { name: "Texas TX" }),
    ).not.toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "TX");
    expect(
      screen.getByRole("checkbox", { name: "Texas TX" }),
    ).toBeChecked();
  });

  it("previews every consequence and applies nothing until confirmation", async () => {
    const calls: string[] = [];
    const updated = {
      ...ACCOUNT,
      states: ["CA", "FL"],
      version: "2026-07-29T12:05:00+00:00",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = typeof input === "string" ? input : String(input);
        calls.push(`${init?.method ?? "GET"} ${path}`);
        if (path.endsWith("/preview")) return json(REVIEW);
        if (path.endsWith("/apply")) return json(result(updated));
        if (path.endsWith("/profile")) return json(IDENTITY);
        return json(updated);
      }),
    );
    const user = userEvent.setup();
    renderRoute();

    await user.click(
      screen.getByRole("checkbox", { name: "California CA" }),
    );
    await user.click(screen.getByRole("checkbox", { name: "Texas TX" }));
    await user.click(screen.getByRole("button", { name: "Review changes" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Review Licensed State changes",
    });
    expect(dialog).toHaveAccessibleDescription(
      /Nothing changes until you confirm/,
    );
    expect(
      within(dialog).getByText(
        /Added states apply only to future Batch Requests/,
      ),
    ).toBeVisible();
    expect(
      within(dialog).getByText("100-lead Batch Request"),
    ).toBeVisible();
    expect(
      within(dialog).getByText("50-lead Batch Request"),
    ).toBeVisible();
    expect(
      within(dialog).getByText("Will be canceled"),
    ).toBeVisible();
    expect(calls).toEqual(["POST /api/me/licensed-states/preview"]);

    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(calls).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Review changes" }));
    await user.click(
      within(
        await screen.findByRole("dialog"),
      ).getByRole("button", { name: "Confirm and save" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Overview and Batch Requests are up to date",
      ),
    );
    expect(calls).toContain("POST /api/me/licensed-states/apply");
  });

  it("rejects a stale review and reloads the concurrent selection", async () => {
    const concurrent = {
      ...ACCOUNT,
      states: ["CA", "FL", "TX"],
      version: "2026-07-29T12:06:00+00:00",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = typeof input === "string" ? input : String(input);
        if (path.endsWith("/preview")) return json(REVIEW);
        if (path.endsWith("/apply")) {
          return json(
            {
              detail:
                "Licensed States changed in another session. Review the latest impact before saving.",
            },
            409,
          );
        }
        if (path.endsWith("/profile")) return json(IDENTITY);
        return json(concurrent);
      }),
    );
    const user = userEvent.setup();
    renderRoute();

    await user.click(
      screen.getByRole("checkbox", { name: "California CA" }),
    );
    await user.click(screen.getByRole("checkbox", { name: "Texas TX" }));
    await user.click(screen.getByRole("button", { name: "Review changes" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Confirm and save",
      }),
    );

    expect(
      await screen.findByRole("alert"),
    ).toHaveTextContent("changed in another session");
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "Texas TX" }),
      ).toBeChecked(),
    );
    expect(
      screen.getByRole("checkbox", { name: "California CA" }),
    ).toBeChecked();
  });
});
