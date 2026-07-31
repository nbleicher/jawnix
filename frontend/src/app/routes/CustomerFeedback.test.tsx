import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CustomerFeedbackRoute } from "./CustomerFeedback";
import type { FeedbackCatalog } from "./CustomerFeedback";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";

/** Mirrors `jawnix/dispositions.py`'s payload. */
const CATALOG: FeedbackCatalog = {
  groups: [
    {
      group: "contact_result",
      label: "Contact result",
      options: [
        {
          disposition: "no_contact",
          label: "No Contact",
          description: "You could not reach anyone.",
          requiresNote: false,
          createsReport: false,
          createsHold: false,
          consequence: "",
        },
        {
          disposition: "not_interested",
          label: "Not Interested",
          description: "You reached them and they declined.",
          requiresNote: false,
          createsReport: false,
          createsHold: false,
          consequence: "",
        },
      ],
    },
    {
      group: "positive_progress",
      label: "Positive progress",
      options: [
        {
          disposition: "positive_response",
          label: "Positive Response",
          description: "They were interested.",
          requiresNote: false,
          createsReport: false,
          createsHold: false,
          consequence: "",
        },
      ],
    },
    {
      group: "appointment_follow_up",
      label: "Appointment follow-up",
      options: [
        {
          disposition: "appointment_booked",
          label: "Appointment Booked",
          description: "You scheduled a meeting.",
          requiresNote: false,
          createsReport: false,
          createsHold: false,
          consequence: "",
        },
      ],
    },
    {
      group: "data_compliance_problem",
      label: "Data or compliance problem",
      options: [
        {
          disposition: "invalid_phone",
          label: "Invalid Phone",
          description: "The number is disconnected.",
          requiresNote: false,
          createsReport: true,
          createsHold: true,
          consequence:
            "Submitting this files a Lead Report and places an Eligibility Hold, which withdraws this Lead from future batches. Only an administrator can release the hold.",
        },
        {
          disposition: "wrong_business",
          label: "Wrong Business",
          description: "The number reaches a different business.",
          requiresNote: false,
          createsReport: true,
          createsHold: false,
          consequence:
            "Submitting this files a Lead Report for an administrator to review. It does not place an Eligibility Hold, so this Lead stays eligible for future batches.",
        },
        {
          disposition: "do_not_contact",
          label: "Do Not Contact",
          description: "They asked not to be contacted again.",
          requiresNote: false,
          createsReport: true,
          createsHold: true,
          consequence:
            "Submitting this files a Lead Report and places an Eligibility Hold, which withdraws this Lead from future batches. Only an administrator can release the hold.",
        },
      ],
    },
    {
      group: "other",
      label: "Other",
      options: [
        {
          disposition: "other",
          label: "Other",
          description: "Anything the choices above do not cover.",
          requiresNote: true,
          createsReport: false,
          createsHold: false,
          consequence: "",
        },
      ],
    },
  ],
  qualityRating: {
    optional: true,
    description:
      "Optional, and separate from your answer above. It records how good the Lead was and changes nothing else.",
    options: [
      {
        value: "good",
        label: "Good",
        description: "Counts this Lead as good quality in your feedback history.",
      },
      {
        value: "poor",
        label: "Poor",
        description:
          "Counts this Lead as poor quality in your feedback history. Nothing is withdrawn and nothing is filed for review.",
      },
    ],
  },
};

const LEAD = {
  distributionEventId: 7,
  businessName: "Acme Roofing",
  phone: "2145550001",
  deliveredAt: "2026-07-20T15:00:00Z",
  batchId: "b1b2c3d4",
  currentDisposition: null,
};

function transition(overrides: Record<string, unknown> = {}) {
  return {
    id: "t-1",
    distributionEventId: 7,
    disposition: "no_contact",
    note: "",
    actorUserId: "u-1",
    previousTransitionId: null,
    createdAt: "2026-07-21T10:00:00Z",
    ...overrides,
  };
}

/** The real nested submit response, pinned by tests/test_feedback_privacy.py. */
function receipt(overrides: Record<string, unknown> = {}) {
  return {
    distributionEventId: 7,
    currentDisposition: "no_contact",
    transition: transition(),
    qualityRating: null,
    reportId: null,
    eligibilityHoldId: null,
    ...overrides,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Routes fetches by URL so a test states only what it cares about. */
function mockApi(options: {
  lookup?: Response;
  search?: Response;
  history?: unknown[];
  historyResponse?: Response;
  submit?: Response;
} = {}) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : String(input);
      if (url.includes("/feedback/lookup")) {
        return options.lookup ?? json(LEAD);
      }
      if (url.includes("/feedback/search")) {
        return options.search ?? json([LEAD]);
      }
      if (url.includes("/dispositions")) {
        return options.historyResponse ?? json(options.history ?? []);
      }
      if (url.includes("/api/me/feedback")) {
        return options.submit ?? json(receipt(), 201);
      }
      return json({}, 404);
    });
}

function renderRoute() {
  const router = createMemoryRouter(
    [
      {
        id: "feedback",
        path: "/feedback",
        loader: () => CATALOG,
        element: <CustomerFeedbackRoute />,
      },
    ],
    {
      initialEntries: ["/feedback"],
      hydrationData: { loaderData: { feedback: CATALOG } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

async function lookUp(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByLabelText("Delivered phone number (required)"),
    "2145550001",
  );
  await user.click(screen.getByRole("button", { name: "Look up" }));
  await screen.findByRole("region", { name: "Confirm the Lead" });
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("privacy", () => {
  /**
   * The backend already answers all three causes identically — that is pinned
   * server-side in `tests/test_feedback_privacy.py` and `tests/test_api.py`.
   * What these assert is the half only the client can break: that the screen
   * renders its own constant and never surfaces the response body, so a
   * backend that started differentiating would still not leak through here.
   */
  const causes = [
    ["a phone that is not a phone number", json({ detail: "x" }, 404)],
    ["a phone never delivered", json({ detail: "y" }, 404)],
    ["another Customer's phone", json({ detail: "z" }, 404)],
  ] as const;

  it.each(causes)("ignores the response body for %s", async (_name, response) => {
    const user = userEvent.setup();
    mockApi({ lookup: response });
    renderRoute();

    await user.type(
      screen.getByLabelText("Delivered phone number (required)"),
      "2145559999",
    );
    await user.click(screen.getByRole("button", { name: "Look up" }));

    expect(
      await screen.findByText(
        "No delivered Lead was found for that phone number. Check the number and try again.",
      ),
    ).toBeVisible();
  });

  it("reveals nothing about the Lead when a lookup fails", async () => {
    const user = userEvent.setup();
    mockApi({ lookup: json({ detail: "x" }, 404) });
    renderRoute();

    await user.type(
      screen.getByLabelText("Delivered phone number (required)"),
      "2145559999",
    );
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByText(/No delivered Lead was found/);

    expect(
      screen.queryByRole("region", { name: "Confirm the Lead" }),
    ).toBeNull();
    expect(screen.queryByRole("region", { name: "What happened?" })).toBeNull();
  });
});

describe("confirming the Lead", () => {
  it("confirms business, phone, delivery date, and Batch before entry", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    const confirm = screen.getByRole("region", { name: "Confirm the Lead" });
    expect(within(confirm).getByText("Acme Roofing")).toBeVisible();
    expect(within(confirm).getByText("(214) 555-0001")).toBeVisible();
    expect(within(confirm).getByText("b1b2c3d4")).toBeVisible();
    expect(within(confirm).getByText(/2026/)).toBeVisible();
  });
});

describe("searching delivered batches", () => {
  it("finds a Lead by partial name and enters the existing confirmation flow", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockApi();
    renderRoute();

    await user.type(screen.getByLabelText("Business name or phone"), "roof");
    await user.click(screen.getByRole("button", { name: "Search" }));

    const results = await screen.findByRole("region", {
      name: "Search results",
    });
    await user.click(
      within(results).getByRole("button", { name: /Acme Roofing/ }),
    );

    expect(
      screen.getByRole("region", { name: "Confirm the Lead" }),
    ).toBeVisible();
    const searchCall = fetchSpy.mock.calls.find(([url]) =>
      String(url).includes("/feedback/search"),
    );
    expect(JSON.parse(String((searchCall![1] as RequestInit).body))).toEqual({
      query: "roof",
    });
  });

  it("does not offer a bulk or file-import path", () => {
    mockApi();
    const { container } = renderRoute();

    expect(container.querySelector('input[type="file"]')).toBeNull();
    expect(screen.queryByText(/bulk import|import csv/i)).toBeNull();
  });
});

describe("disposition controls", () => {
  it("keeps exactly one disposition selected and submits only the latest one", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockApi();
    renderRoute();
    await lookUp(user);

    const first = screen.getByRole("button", { name: /No Contact/ });
    const second = screen.getByRole("button", { name: /Positive Response/ });
    await user.click(first);
    await user.click(second);

    expect(first).toHaveAttribute("aria-pressed", "false");
    expect(second).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    await waitFor(() => {
      const submitCall = fetchSpy.mock.calls.find(
        ([url, init]) =>
          String(url) === "/api/me/feedback" &&
          (init as RequestInit)?.method === "POST",
      );
      expect(
        JSON.parse(String((submitCall![1] as RequestInit).body)).disposition,
      ).toBe("positive_response");
    });
  });

  it("materializes every disposition as a visible button, not a dropdown", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    const section = screen.getByRole("region", { name: "What happened?" });
    expect(within(section).queryByRole("combobox")).toBeNull();
    for (const label of [
      "No Contact",
      "Not Interested",
      "Positive Response",
      "Appointment Booked",
      "Invalid Phone",
      "Wrong Business",
      "Do Not Contact",
      "Other",
    ]) {
      expect(within(section).getByRole("button", { name: new RegExp(label) }))
        .toBeVisible();
    }
  });

  it("groups them by meaning", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    for (const group of [
      "Contact result",
      "Positive progress",
      "Appointment follow-up",
      "Data or compliance problem",
      "Other",
    ]) {
      expect(
        screen.getByRole("group", { name: group }),
      ).toBeVisible();
    }
  });

  it("reveals a required note only for Other", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    // An ordinary disposition asks for nothing further: no note field at all,
    // which is what keeps the fast path to three interactions.
    await user.click(screen.getByRole("button", { name: /No Contact/ }));
    expect(screen.queryByLabelText(/^Note/)).toBeNull();

    await user.click(screen.getByRole("button", { name: /^Other/ }));
    expect(screen.getByLabelText("Note (required)")).toBeVisible();

    // And it goes away again when the answer changes.
    await user.click(screen.getByRole("button", { name: /No Contact/ }));
    expect(screen.queryByLabelText(/^Note/)).toBeNull();
  });

  it("refuses to submit Other without a note", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /^Other/ }));
    fetchSpy.mockClear();
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    expect(
      await screen.findByText("A note is required for this answer."),
    ).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("consequences are stated before submission", () => {
  it("says Invalid phone files a report and holds the Lead", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /Invalid Phone/ }));

    const review = screen.getByRole("region", {
      name: "Your answer: Invalid Phone",
    });
    expect(
      within(review).getByText("Files a report and holds the Lead"),
    ).toBeVisible();
    expect(within(review).getByText(/places an Eligibility Hold/)).toBeVisible();
  });

  it("says Do not contact files a report and holds the Lead", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /Do Not Contact/ }));

    expect(
      within(
        screen.getByRole("region", { name: "Your answer: Do Not Contact" }),
      ).getByText(/places an Eligibility Hold/),
    ).toBeVisible();
  });

  it("says Wrong business files a report WITHOUT a hold", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /Wrong Business/ }));

    const review = screen.getByRole("region", {
      name: "Your answer: Wrong Business",
    });
    // The distinction that misleads if collapsed.
    expect(within(review).getByText("Files a report")).toBeVisible();
    expect(
      within(review).getByText(/does not place an Eligibility Hold/),
    ).toBeVisible();
    expect(
      within(review).queryByText("Files a report and holds the Lead"),
    ).toBeNull();
  });

  it("claims no consequence for an ordinary disposition", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /No Contact/ }));

    const review = screen.getByRole("region", {
      name: "Your answer: No Contact",
    });
    expect(within(review).queryByText(/Eligibility Hold/)).toBeNull();
    expect(within(review).queryByText(/Lead Report/)).toBeNull();
  });
});

describe("Quality Rating is optional and independent", () => {
  it("submits without a rating", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /No Contact/ }));
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    await waitFor(() => {
      const submitCall = fetchSpy.mock.calls.find(
        ([url, init]) =>
          String(url) === "/api/me/feedback" &&
          (init as RequestInit)?.method === "POST",
      );
      expect(submitCall).toBeDefined();
      expect(
        JSON.parse(String((submitCall![1] as RequestInit).body)),
      ).not.toHaveProperty("quality_rating");
    });
  });

  it("never describes Poor as suppression or a report", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);
    await user.click(screen.getByRole("button", { name: /No Contact/ }));

    const poor = screen.getByRole("button", { name: /Poor/ });
    expect(poor).toBeVisible();
    expect(within(poor).queryByText(/suppress|Lead Report|hold/i)).toBeNull();
    expect(
      screen.getByText(/Nothing is withdrawn and nothing is filed for review/),
    ).toBeVisible();
  });

  it("sends the rating alongside an unrelated disposition when chosen", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /Appointment Booked/ }));
    await user.click(screen.getByRole("button", { name: /Poor/ }));
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    await waitFor(() => {
      const submitCall = fetchSpy.mock.calls.find(
        ([url, init]) =>
          String(url) === "/api/me/feedback" &&
          (init as RequestInit)?.method === "POST",
      );
      expect(JSON.parse(String((submitCall![1] as RequestInit).body))).toEqual({
        distribution_event_id: 7,
        disposition: "appointment_booked",
        note: "",
        quality_rating: "poor",
      });
    });
  });

  it("lets a rating be deselected", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);
    await user.click(screen.getByRole("button", { name: /No Contact/ }));

    const good = screen.getByRole("button", { name: /Good/ });
    await user.click(good);
    expect(good).toHaveAttribute("aria-pressed", "true");
    await user.click(good);
    expect(good).toHaveAttribute("aria-pressed", "false");
  });
});

describe("receipt and append-only history", () => {
  it("confirms a submitted Quality Rating on the receipt", async () => {
    const user = userEvent.setup();
    mockApi({
      submit: json(
        receipt({ qualityRating: { id: "q-1", kind: "poor", note: "" } }),
        201,
      ),
    });
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /No Contact/ }));
    await user.click(screen.getByRole("button", { name: /Poor/ }));
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    const confirmation = await screen.findByRole("region", { name: "Recorded" });
    expect(within(confirmation).getByText(/Quality rated Poor/)).toBeVisible();
  });

  it("confirms the control that was actually created, not the one promised", async () => {
    const user = userEvent.setup();
    mockApi({
      submit: json(
        receipt({ reportId: "r-1", eligibilityHoldId: null }),
        201,
      ),
    });
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /Wrong Business/ }));
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    const confirmation = await screen.findByRole("region", { name: "Recorded" });
    expect(
      within(confirmation).getByText(/stays eligible for future batches/),
    ).toBeVisible();
  });

  it("shows a receipt naming what was recorded", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /No Contact/ }));
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    const receipt = await screen.findByRole("region", { name: "Recorded" });
    expect(within(receipt).getByText(/No Contact recorded for Acme Roofing/))
      .toBeVisible();
    expect(within(receipt).getByText(/Reference t-1/)).toBeVisible();
  });

  it("lists prior answers oldest first without replacing them", async () => {
    const user = userEvent.setup();
    mockApi({
      history: [
        transition({ id: "t-1", disposition: "no_contact" }),
        transition({
          id: "t-2",
          disposition: "positive_response",
          createdAt: "2026-07-22T10:00:00Z",
        }),
      ],
    });
    renderRoute();
    await lookUp(user);

    const history = screen.getByRole("region", { name: "Feedback history" });
    const entries = within(history).getAllByRole("heading", { level: 3 });
    expect(entries.map((entry) => entry.textContent)).toEqual([
      "No Contact",
      "Positive Response",
    ]);
  });

  it("says plainly when there is no history yet", async () => {
    const user = userEvent.setup();
    mockApi({ history: [] });
    renderRoute();
    await lookUp(user);

    expect(
      screen.getByRole("heading", { level: 2, name: "No feedback yet" }),
    ).toBeVisible();
  });

  it("renders a history fetch error differently from an empty history", async () => {
    const user = userEvent.setup();
    mockApi({
      historyResponse: json({ detail: "temporarily unavailable" }, 503),
    });
    renderRoute();
    await lookUp(user);

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Feedback history unavailable",
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { level: 2, name: "No feedback yet" }),
    ).toBeNull();
  });
});

describe("failure handling", () => {
  it("says nothing was saved when submission fails", async () => {
    const user = userEvent.setup();
    mockApi({ submit: json({ detail: "boom" }, 500) });
    renderRoute();
    await lookUp(user);

    await user.click(screen.getByRole("button", { name: /No Contact/ }));
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    expect(await screen.findByText(/Nothing was saved/)).toBeVisible();
    expect(screen.queryByRole("region", { name: "Recorded" })).toBeNull();
  });
});

describe("speed", () => {
  /**
   * "A valid disposition can be completed in under one minute after phone
   * entry" is an acceptance criterion. Wall-clock in a unit test is
   * meaningless, so this pins the thing that actually governs it: the number
   * of interactions on the fast path. The browser test measures elapsed time.
   */
  it("reaches a recorded disposition in three interactions", async () => {
    const user = userEvent.setup();
    mockApi();
    renderRoute();

    await user.type(
      screen.getByLabelText("Delivered phone number (required)"),
      "2145550001",
    );
    // 1
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByRole("region", { name: "What happened?" });
    // 2
    await user.click(screen.getByRole("button", { name: /No Contact/ }));
    // 3
    await user.click(screen.getByRole("button", { name: "Submit feedback" }));

    expect(await screen.findByRole("region", { name: "Recorded" })).toBeVisible();
  });
});
