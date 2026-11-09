import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreditWalletProvider } from "../billing/CreditWalletContext";
import type { CreditWallet } from "../billing/wallet";
import type { BatchRequest, BatchRequestWorkspace } from "./batchRequests";
import {
  CustomerNewRequestRoute,
  filesPreview,
  formDefaults,
  parseCount,
  readRequestDraft,
  requestDraftQuery,
} from "./CustomerNewRequest";

const WALLET: CreditWallet = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 500,
  balanceCents: 53_750,
  activeHoldsCents: 12_500,
  availableBalanceCents: 41_250,
  purchases: [],
  ledger: [],
};

const LATEST = {
  id: "10421042-1042-4042-8042-104210421042",
  number: 1042,
  lead_count: 25_000,
  rows_per_file: 10_000,
} as BatchRequest;

function workspace(overrides: Partial<BatchRequestWorkspace> = {}): BatchRequestWorkspace {
  return {
    limits: {
      minimum_lead_count: 1,
      maximum_lead_count: 100_000,
      licensed_states: ["FL", "TX"],
      state_names: { FL: "Florida", TX: "Texas" },
    },
    blocker: null,
    requests: [LATEST],
    history: { page: 1, page_size: 20, total: 1 },
    ...overrides,
  };
}

function stubFetch(handler: (url: string, init?: RequestInit) => Response) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    Promise.resolve(handler(String(input), init)),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderPage(data: BatchRequestWorkspace, path = "/app/new-request") {
  const router = createMemoryRouter(
    [
      { id: "page", path: "/new-request", loader: () => data, element: <CustomerNewRequestRoute /> },
      { path: "/batches/:batchId", element: <h1>Batch page</h1> },
    ],
    { basename: "/app", initialEntries: [path], hydrationData: { loaderData: { page: data } } },
  );
  render(
    <CreditWalletProvider>
      <RouterProvider router={router} />
    </CreditWalletProvider>,
  );
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return router;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the request form's pieces", () => {
  it("reads quantities typed with or without separators", () => {
    expect(parseCount("100,000")).toBe(100_000);
    expect(parseCount(" 750 ")).toBe(750);
    expect(parseCount("1e3")).toBeNull();
    expect(parseCount("")).toBeNull();
  });

  it("previews the files a split makes", () => {
    expect(filesPreview(50_000, 10_000)).toBe("5 files of 10,000 rows");
    expect(filesPreview(50_000, 15_000)).toBe("4 files of up to 15,000 rows");
    expect(filesPreview(750, 1_000)).toBe("One file");
  });

  it("round-trips the unfinished request Stripe carries back", () => {
    const draft = readRequestDraft(new URLSearchParams("quantity=750&states=fl,TX&rows_per_file=300"));
    expect(draft).toEqual({ quantity: "750", states: ["FL", "TX"], rowsPerFile: "300" });
    expect(requestDraftQuery(draft!)).toBe("quantity=750&states=FL,TX&rows_per_file=300");
    expect(readRequestDraft(new URLSearchParams("quantity=abc"))).toBeNull();
  });

  it("starts from a draft, or every appointed state and the latest batch's split", () => {
    expect(formDefaults(null, workspace())).toEqual({
      quantity: "",
      states: ["FL", "TX"],
      split: true,
      rowsPerFile: "10000",
    });
    expect(formDefaults(null, workspace({ requests: [] }))).toMatchObject({ split: false });
    expect(
      formDefaults({ quantity: "750", states: ["TX", "NY"], rowsPerFile: null }, workspace()),
    ).toEqual({ quantity: "750", states: ["TX"], split: false, rowsPerFile: "" });
  });
});

describe("CustomerNewRequestRoute", () => {
  it("updates the cost, balance, and shortfall as the quantity changes", async () => {
    const user = userEvent.setup();
    stubFetch(() => new Response(JSON.stringify(WALLET), { status: 200 }));
    await renderPage(workspace());

    const summary = screen.getByRole("region", { name: "Summary" });
    await user.type(screen.getByRole("textbox", { name: "How many leads?" }), "50000");
    expect(summary).toHaveTextContent("Cost$250.00");
    expect(summary).toHaveTextContent("Balance after$162.50");
    expect(within(summary).queryByRole("button", { name: /Buy/ })).toBeNull();

    await user.clear(screen.getByRole("textbox", { name: "How many leads?" }));
    await user.type(screen.getByRole("textbox", { name: "How many leads?" }), "100,000");
    expect(summary).toHaveTextContent("Short by$87.50");
    expect(within(summary).getByRole("button", { name: "Buy $88.00 in credits" })).toBeVisible();
    expect(within(summary).getByRole("button", { name: "Submit request" })).toBeDisabled();
  });

  it("submits the request and opens its batch page", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url) =>
      url === "/api/me/batch-requests"
        ? new Response(
            JSON.stringify({ created: true, request: { ...LATEST, id: "new", number: 1043 } }),
            { status: 201 },
          )
        : new Response(JSON.stringify(WALLET), { status: 200 }),
    );
    const router = await renderPage(workspace());

    await user.type(screen.getByRole("textbox", { name: "How many leads?" }), "25000");
    await user.click(screen.getByRole("checkbox", { name: "Texas" }));
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/app/batches/1043"));
    expect(router.state.location.state).toEqual({ submitted: true, created: true });
    const submit = fetchMock.mock.calls.find(([url]) => String(url) === "/api/me/batch-requests");
    expect(JSON.parse(String(submit?.[1]?.body))).toMatchObject({
      lead_count: 25_000,
      rows_per_file: 10_000,
      state_mode: "selected",
      states: ["FL"],
      expected_lead_rate_cents_per_thousand: 500,
    });
  });

  it("refuses to send what the backend would refuse", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch(() => new Response(JSON.stringify(WALLET), { status: 200 }));
    await renderPage(workspace());

    await user.click(screen.getByRole("checkbox", { name: "Florida" }));
    await user.click(screen.getByRole("checkbox", { name: "Texas" }));
    await user.type(screen.getByRole("textbox", { name: "How many leads?" }), "0");
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    expect(screen.getByText("Enter between 1 and 100,000 leads.")).toBeVisible();
    expect(screen.getByText("Choose at least one appointed state.")).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/me/batch-requests")).toBe(false);
  });

  it("shows the account fix when nothing can be requested yet", async () => {
    stubFetch(() => new Response(JSON.stringify(WALLET), { status: 200 }));
    await renderPage(
      workspace({
        blocker: {
          reason: "no_licensed_states",
          label: "Add your appointed states first",
          description: "A batch covers the states you are appointed in.",
          action: { kind: "add_licensed_states", label: "Add appointed states", description: "", href: "/app/account" },
        },
      }),
    );

    expect(screen.getByRole("heading", { level: 2, name: "Add your appointed states first" })).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "How many leads?" })).toBeNull();
  });
});
