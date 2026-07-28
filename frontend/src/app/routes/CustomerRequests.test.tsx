import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import { CustomerRequestsRoute } from "./CustomerRequests";
import type {
  BatchRequest,
  BatchRequestWorkspace,
  MilestoneGraphData,
} from "./batchRequests";

const SUBMITTED = "2026-07-20T12:00:00Z";
const REQUEST_ID = "11111111-1111-4111-8111-111111111111";

function graph(overrides: Partial<MilestoneGraphData> = {}): MilestoneGraphData {
  return {
    milestones: [
      {
        key: "submitted",
        label: "Submitted",
        description: "We have your request.",
        state: "complete",
        occurred_at: SUBMITTED,
      },
      {
        key: "under_review",
        label: "Under Review",
        description: "Jawnix is checking it.",
        state: "current",
        occurred_at: null,
      },
      {
        key: "preparing_batch",
        label: "Preparing Batch",
        description: "We are building your file.",
        state: "upcoming",
        occurred_at: null,
      },
      {
        key: "delivered",
        label: "Delivered",
        description: "Emailed to you.",
        state: "upcoming",
        occurred_at: null,
      },
    ],
    current_key: "under_review",
    pause: null,
    outcome: null,
    ...overrides,
  };
}

function batchRequest(overrides: Partial<BatchRequest> = {}): BatchRequest {
  return {
    id: REQUEST_ID,
    lead_count: 750,
    states: ["FL", "TX"],
    submitted_at: SUBMITTED,
    delivered_at: null,
    status: {
      label: "Under Review",
      description: "We are reviewing your request.",
      tone: "info",
    },
    milestones: graph(),
    can_cancel: true,
    next_action: null,
    receipt_href: `/app/requests?request=${REQUEST_ID}`,
    ...overrides,
  };
}

function workspace(
  overrides: Partial<BatchRequestWorkspace> = {},
): BatchRequestWorkspace {
  return {
    limits: {
      minimum_lead_count: 1,
      maximum_lead_count: 100_000,
      licensed_states: ["FL", "TX"],
    },
    blocker: null,
    requests: [],
    ...overrides,
  };
}

function renderRoute(data: BatchRequestWorkspace) {
  const router = createMemoryRouter(
    [
      {
        id: "customer",
        path: "/app",
        element: <Outlet />,
        children: [
          {
            id: "requests",
            path: "requests",
            loader: () => data,
            element: <CustomerRequestsRoute />,
          },
        ],
      },
    ],
    {
      initialEntries: ["/app/requests"],
      hydrationData: { loaderData: { requests: data } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

function mockFetch(handler: (body: unknown) => unknown, status = 201) {
  const calls: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((_input: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push(body);
      return Promise.resolve({
        ok: status < 400,
        status,
        json: () => Promise.resolve(handler(body)),
      } as Response);
    }),
  );
  return calls;
}

async function completeQuantityStage(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByRole("spinbutton", { name: /How many leads/ }),
    "750",
  );
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the guided Batch Request flow", () => {
  it("walks quantity, then scope, then review — never one long form", async () => {
    const user = userEvent.setup();
    renderRoute(workspace());

    expect(
      screen.getByRole("spinbutton", { name: /How many leads/ }),
    ).toBeVisible();
    expect(
      screen.queryByRole("group", { name: /Which states/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Submit request" }),
    ).not.toBeInTheDocument();

    await completeQuantityStage(user);

    expect(screen.getByRole("group", { name: /Which states/ })).toBeVisible();
    expect(
      screen.queryByRole("spinbutton", { name: /How many leads/ }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(
      screen.getByRole("heading", { name: "Review your request" }),
    ).toBeVisible();
    expect(screen.getByText("750 leads")).toBeVisible();
    expect(screen.getByText("FL, TX")).toBeVisible();
    expect(screen.getByRole("button", { name: "Submit request" })).toBeVisible();
  });

  it("marks the current stage so progress is never guessed from styling", async () => {
    const user = userEvent.setup();
    renderRoute(workspace());

    const stages = within(
      screen.getByRole("list", { name: "Request stages" }),
    ).getAllByRole("listitem");
    expect(stages[0]).toHaveAttribute("aria-current", "step");
    expect(stages[0]).toHaveTextContent("Current stage");
    expect(stages[1]).toHaveTextContent("Not started");

    await completeQuantityStage(user);

    expect(screen.getAllByRole("listitem")[0]).toHaveTextContent("Completed");
    expect(screen.getAllByRole("listitem")[1]).toHaveAttribute(
      "aria-current",
      "step",
    );
  });

  it.each([
    ["", "Enter how many leads you want."],
    ["0", "Enter between 1 and 100,000 leads."],
    ["100001", "Enter between 1 and 100,000 leads."],
  ])("refuses quantity %j before the review stage", async (entry, message) => {
    const user = userEvent.setup();
    renderRoute(workspace());

    if (entry) {
      await user.type(
        screen.getByRole("spinbutton", { name: /How many leads/ }),
        entry,
      );
    }
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(
      screen.queryByRole("button", { name: "Submit request" }),
    ).not.toBeInTheDocument();
  });

  it("refuses an empty state scope before the review stage", async () => {
    const user = userEvent.setup();
    renderRoute(workspace());
    await completeQuantityStage(user);

    await user.click(
      screen.getByRole("radio", { name: /Choose specific states/ }),
    );
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose at least one Licensed State.",
    );
    expect(
      screen.queryByRole("heading", { name: "Review your request" }),
    ).not.toBeInTheDocument();
  });

  it("offers only Licensed States, so an unlicensed scope cannot be entered", async () => {
    const user = userEvent.setup();
    renderRoute(workspace());
    await completeQuantityStage(user);
    await user.click(
      screen.getByRole("radio", { name: /Choose specific states/ }),
    );

    const choices = screen
      .getAllByRole("checkbox")
      .map((box) => box.closest("label")?.textContent);

    expect(choices).toEqual(["FL", "TX"]);
    expect(screen.queryByRole("checkbox", { name: "NY" })).not.toBeInTheDocument();
  });

  it("submits the reviewed request once and lands on a linked receipt", async () => {
    const user = userEvent.setup();
    const created = batchRequest({ lead_count: 750 });
    const calls = mockFetch(() => ({ created: true, request: created }));
    renderRoute(workspace());

    await completeQuantityStage(user);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Request submitted" }),
      ).toBeVisible(),
    );
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      lead_count: 750,
      state_mode: "all_saved",
      states: [],
    });
    expect(
      screen.getByRole("link", { name: "View this Batch Request" }),
    ).toHaveAttribute("href", `/app/requests?request=${REQUEST_ID}`);
  });

  it("sends one submission key so a retry cannot become a second request", async () => {
    const user = userEvent.setup();
    const calls = mockFetch(() => ({ created: true, request: batchRequest() }));
    renderRoute(workspace());

    await completeQuantityStage(user);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    const submit = screen.getByRole("button", { name: "Submit request" });
    await user.dblClick(submit);

    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Request submitted" }),
      ).toBeVisible(),
    );
    const keys = new Set(
      calls.map((call) => (call as { idempotency_key: string }).idempotency_key),
    );
    expect(keys.size).toBe(1);
  });

  it("tells the Customer when a replay resolved to the existing request", async () => {
    const user = userEvent.setup();
    mockFetch(() => ({ created: false, request: batchRequest() }), 200);
    renderRoute(workspace());

    await completeQuantityStage(user);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    await waitFor(() =>
      expect(screen.getByText(/was not sent twice/)).toBeVisible(),
    );
  });

  it("shows the backend's own refusal rather than a generic error", async () => {
    const user = userEvent.setup();
    mockFetch(
      () => ({ detail: "These are not Licensed States on your account: NY." }),
      422,
    );
    renderRoute(workspace());

    await completeQuantityStage(user);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "These are not Licensed States on your account: NY.",
      ),
    );
  });

  it("offers the fix instead of the stages when the flow cannot start", () => {
    renderRoute(
      workspace({
        blocker: {
          reason: "no_licensed_states",
          label: "Add a Licensed State first",
          description: "Add at least one before requesting a Batch.",
          action: {
            kind: "add_licensed_states",
            label: "Add Licensed States",
            description: "Save the states you are licensed in.",
            href: "/app/account",
          },
        },
      }),
    );

    expect(
      screen.getByRole("heading", { name: "Add a Licensed State first" }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Add Licensed States" }),
    ).toHaveAttribute("href", "/app/account");
    expect(
      screen.queryByRole("spinbutton", { name: /How many leads/ }),
    ).not.toBeInTheDocument();
  });
});

describe("the submitted request list", () => {
  it("explains a Waiting for Inventory pause instead of reporting a failure", () => {
    renderRoute(
      workspace({
        requests: [
          batchRequest({
            status: {
              label: "Preparing Batch",
              description: "We are preparing your leads.",
              tone: "warning",
            },
            milestones: graph({
              pause: {
                kind: "inventory_wait",
                milestone_key: "preparing_batch",
                label: "Waiting for Inventory",
                description:
                  "Nothing has gone wrong and there is nothing you need to do.",
              },
            }),
          }),
        ],
      }),
    );

    expect(screen.getByText("Waiting for Inventory")).toBeVisible();
    expect(
      screen.getByText(/nothing you need to do/, { exact: false }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([
    ["rejected", "Not Approved", "Request another Batch", "/app/requests"],
    ["canceled", "Canceled", "Request another Batch", "/app/requests"],
    ["failed", "Needs Attention", "Contact Jawnix", "mailto:hai@jawnix.com"],
  ] as const)(
    "gives a %s request a specific outcome and a valid next action",
    (kind, label, actionLabel, href) => {
      renderRoute(
        workspace({
          requests: [
            batchRequest({
              can_cancel: false,
              next_action: {
                kind:
                  kind === "failed" ? "contact_support" : "request_batch",
                label: actionLabel,
                description: "What to do next.",
                href,
              },
              milestones: graph({
                current_key: null,
                outcome: {
                  kind,
                  milestone_key: "under_review",
                  label,
                  description: `Because the request was ${kind}.`,
                  tone: kind === "canceled" ? "neutral" : "danger",
                  occurred_at: "2026-07-21T09:00:00Z",
                },
              }),
            }),
          ],
        }),
      );

      // The outcome note headlines the label with when it happened, which is
      // what distinguishes it from the same label inside the graph summary.
      expect(screen.getByText(new RegExp(`^${label} · `))).toBeVisible();
      expect(
        screen.getByText(`Because the request was ${kind}.`),
      ).toBeVisible();
      expect(screen.getByRole("link", { name: actionLabel })).toHaveAttribute(
        "href",
        href,
      );
    },
  );

  it("offers cancellation only while the domain still allows it", () => {
    renderRoute(
      workspace({
        requests: [
          batchRequest({ id: "a", can_cancel: true }),
          batchRequest({ id: "b", can_cancel: false }),
        ],
      }),
    );

    expect(
      screen.getAllByRole("button", { name: "Cancel request" }),
    ).toHaveLength(1);
  });

  it("updates the timeline as soon as the cancellation is confirmed", async () => {
    const user = userEvent.setup();
    mockFetch(
      () =>
        batchRequest({
          can_cancel: false,
          status: {
            label: "Canceled",
            description: "This request was canceled.",
            tone: "neutral",
          },
          next_action: {
            kind: "request_batch",
            label: "Request another Batch",
            description: "Start again.",
            href: "/app/requests",
          },
          milestones: graph({
            current_key: null,
            milestones: graph().milestones.map((milestone) =>
              milestone.key === "under_review"
                ? { ...milestone, state: "stopped" as const }
                : milestone,
            ),
            outcome: {
              kind: "canceled",
              milestone_key: "under_review",
              label: "Canceled",
              description: "You withdrew this request.",
              tone: "neutral",
              occurred_at: "2026-07-21T09:00:00Z",
            },
          }),
        }),
      200,
    );
    renderRoute(workspace({ requests: [batchRequest()] }));

    await user.click(screen.getByRole("button", { name: "Cancel request" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Cancel request",
      }),
    );

    await waitFor(() =>
      expect(screen.getByText("You withdrew this request.")).toBeVisible(),
    );
    expect(
      screen.queryByRole("button", { name: "Cancel request" }),
    ).not.toBeInTheDocument();
    const nodes = within(
      screen.getByRole("list", { name: /Progress for the 750 lead request/ }),
    ).getAllByRole("listitem");
    expect(nodes[1]).toHaveTextContent("Stopped");
  });

  it("never renders internal fulfillment vocabulary", () => {
    renderRoute(workspace({ requests: [batchRequest()] }));

    expect(document.body).not.toHaveTextContent("waiting_inventory");
    expect(document.body).not.toHaveTextContent("status_message");
  });

  it("presents an empty history as a nothing-yet state, not an error", () => {
    renderRoute(workspace());

    expect(
      screen.getByRole("heading", { name: "No Batch Requests yet" }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
