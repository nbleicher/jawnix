import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminLeadReportRoute } from "./AdminLeadReport";
import type {
  ControlAction,
  LeadReportDetail,
} from "./AdminFulfillment";

const REPORT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

/** The Customer's own words. The screen must show them and must never put them
 *  into something editable. */
const DETAILS =
  "The number I was given rings a dental office, not a roofing contractor.";

const HOLD_RELEASE =
  "This Eligibility Hold is released only when an administrator resolves this Lead Report. It cannot be released or bypassed by the Customer.";

const RESTORE_NOTICE =
  "Restoring returns this Lead to the eligible pool. It does not promise the Lead will be allocated to anyone.";

const DISMISS: ControlAction = {
  name: "dismiss",
  label: "Dismiss",
  consequence:
    "Records that the report did not hold up, releases the Eligibility Hold, and returns the Lead to the eligible pool unchanged.",
  destructive: false,
  requiresReason: true,
  requiresOverride: false,
};

const CORRECT: ControlAction = {
  name: "correct",
  label: "Correct",
  consequence:
    "Records a Lead Correction that overrides the listing this Lead came from. The delivered Distribution Event is not rewritten.",
  destructive: false,
  requiresReason: true,
  requiresOverride: true,
};

const SUPPRESS: ControlAction = {
  name: "suppress",
  label: "Suppress",
  consequence:
    "Withholds this Lead from every future Batch Request. Suppression does not delete what was already delivered.",
  destructive: true,
  requiresReason: true,
  requiresOverride: false,
};

const RESTORE: ControlAction = {
  name: "restore",
  label: "Restore",
  consequence: "Returns this Lead to the eligible pool.",
  destructive: false,
  requiresReason: true,
  requiresOverride: false,
};

const REMOVE_CORRECTION: ControlAction = {
  name: "remove-correction",
  label: "Remove correction",
  consequence:
    "Removes the Lead Correction so the Lead reads from its underlying listing again.",
  destructive: true,
  requiresReason: true,
  requiresOverride: false,
};

function report(overrides: Partial<LeadReportDetail> = {}): LeadReportDetail {
  return {
    id: REPORT_ID,
    reason: "wrong_number",
    reasonLabel: "Wrong number",
    details: DETAILS,
    status: "open",
    createdAt: "2026-07-01T10:00:00Z",
    href: `/app/admin/fulfillment/reports/${REPORT_ID}`,
    customer: { id: 7, name: "Northstar Insurance", href: "/app/admin/customers/7" },
    distributionEvent: {
      id: 4021,
      phone: "+15125550123",
      title: "Roofing contractor",
      state: "TX",
      customerName: "Northstar Insurance",
      agencyName: "Northstar Agency",
      deliveredAt: "2026-06-28T12:00:00Z",
      listingProvenance: { scraped_from: "directory.example" },
      requestId: "11111111-1111-4111-8111-111111111111",
    },
    lead: {
      id: 91,
      phone: "+15125550123",
      title: "Roofing contractor",
      state: "TX",
      suppressed: false,
      correction: null,
    },
    evidence: {
      kind: "current_listing",
      label: "Current listing",
      title: "Ridgeline Roofing",
      state: "TX",
      observationId: 552,
      observedAt: "2026-06-01T09:00:00Z",
      source: "directory.example",
    },
    controls: {
      eligibilityHeld: true,
      holdId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      holdReason: "An open Lead Report withholds this Lead.",
      holdReleasedAt: null,
      holdRelease: HOLD_RELEASE,
      holdReleasableByCustomer: false,
      suppressed: false,
      suppressionReason: "",
      restoreNotice: RESTORE_NOTICE,
    },
    resolution: null,
    actions: [DISMISS, CORRECT, SUPPRESS],
    ...overrides,
  };
}

function renderReport(data: LeadReportDetail) {
  const router = createMemoryRouter(
    [
      {
        id: "leadReport",
        path: "/admin/fulfillment/reports/:reportId",
        loader: () => data,
        element: <AdminLeadReportRoute />,
      },
    ],
    {
      initialEntries: [`/admin/fulfillment/reports/${data.id}`],
      hydrationData: { loaderData: { leadReport: data } },
    },
  );
  render(<RouterProvider router={router} />);
}

function okFetch() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("the Lead Report is evidence, not a form", () => {
  it("shows the Customer's words outside every form control", () => {
    renderReport(report());

    const reported = screen.getByText(DETAILS);
    expect(reported).toBeVisible();
    // Not editable anywhere: a report the operator can retype stops being the
    // Customer's account of what happened.
    expect(reported.closest("input, textarea, select")).toBeNull();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });

  it("attributes the report to the Customer who filed it", () => {
    renderReport(report());

    expect(
      screen.getByRole("link", { name: "Northstar Insurance" }),
    ).toHaveAttribute("href", "/admin/customers/7");
  });

  it("says the delivered Distribution Event is never rewritten", () => {
    renderReport(report());

    const delivered = screen.getByRole("region", { name: "What was delivered" });
    expect(
      within(delivered).getByText(
        /never rewrites this Distribution Event/,
      ),
    ).toBeVisible();
  });

  it("explains an absent record rather than showing an empty evidence list", () => {
    renderReport(
      report({
        evidence: {
          kind: "none",
          label: "No underlying record",
          title: "",
          state: "",
          observationId: null,
          observedAt: null,
          source: "",
        },
      }),
    );

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "There is nothing underneath this Lead",
      }),
    ).toBeVisible();
  });
});

describe("the three decisions stay three decisions", () => {
  it("offers dismiss, correct, and suppress as separate buttons", () => {
    renderReport(report());

    const decision = screen.getByRole("region", { name: "Decision" });
    expect(within(decision).getByRole("button", { name: "Dismiss" })).toBeVisible();
    expect(within(decision).getByRole("button", { name: "Correct" })).toBeVisible();
    expect(within(decision).getByRole("button", { name: "Suppress" })).toBeVisible();
    // Not one "Resolve" control with a mode: each decision does something
    // different to the Lead.
    expect(within(decision).queryByRole("button", { name: "Resolve" })).toBeNull();
    expect(within(decision).queryByRole("combobox")).toBeNull();
  });

  it("gives each decision its own consequence", async () => {
    const user = userEvent.setup();
    const seen: string[] = [];
    renderReport(report());
    const decision = screen.getByRole("region", { name: "Decision" });

    for (const label of ["Dismiss", "Correct", "Suppress"]) {
      await user.click(within(decision).getByRole("button", { name: label }));
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveAccessibleName(label);
      const describedBy = dialog.getAttribute("aria-describedby") ?? "";
      seen.push(document.getElementById(describedBy)?.textContent ?? "");
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    }

    expect(seen[0]).toContain("returns the Lead to the eligible pool unchanged");
    expect(seen[1]).toContain("overrides the listing this Lead came from");
    expect(seen[2]).toContain("Withholds this Lead from every future Batch Request");
    expect(new Set(seen).size).toBe(3);
  });

  it("posts a dismissal as the report's own note", async () => {
    const user = userEvent.setup();
    const fetchSpy = okFetch();
    renderReport(report());

    await user.click(
      within(screen.getByRole("region", { name: "Decision" })).getByRole(
        "button",
        { name: "Dismiss" },
      ),
    );
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Number verified as correct.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Dismiss" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/lead-reports/${REPORT_ID}/dismiss`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ note: "Number verified as correct." }),
        }),
      );
    });
  });
});

describe("correcting a Lead sits beside the evidence it overrides", () => {
  it("shows the evidence next to empty override inputs", async () => {
    const user = userEvent.setup();
    renderReport(report());

    await user.click(
      within(screen.getByRole("region", { name: "Decision" })).getByRole(
        "button",
        { name: "Correct" },
      ),
    );
    const dialog = await screen.findByRole("dialog");

    // The record being overridden is legible while the override is typed.
    expect(
      within(dialog).getByRole("heading", { name: "What this overrides" }),
    ).toBeVisible();
    expect(within(dialog).getByText("Current listing")).toBeVisible();
    expect(within(dialog).getByText("Ridgeline Roofing")).toBeVisible();

    // Pre-filling would let the evidence be recorded back over itself.
    expect(within(dialog).getByLabelText("Corrected title")).toHaveValue("");
    expect(within(dialog).getByLabelText("Corrected state")).toHaveValue("");
  });

  it("refuses a correction that overrides nothing", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderReport(report());

    await user.click(
      within(screen.getByRole("region", { name: "Decision" })).getByRole(
        "button",
        { name: "Correct" },
      ),
    );
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Listing is stale.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Correct" }));

    expect(
      await within(dialog).findByRole("alert"),
    ).toHaveTextContent("Enter a corrected title, a corrected state, or both.");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("sends only what was typed", async () => {
    const user = userEvent.setup();
    const fetchSpy = okFetch();
    renderReport(report());

    await user.click(
      within(screen.getByRole("region", { name: "Decision" })).getByRole(
        "button",
        { name: "Correct" },
      ),
    );
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Corrected title"),
      "Ridgeline Dental",
    );
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Listing moved.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Correct" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/lead-reports/${REPORT_ID}/correct`,
        expect.objectContaining({
          method: "POST",
          // No `state`: an untouched field is not an assertion that the state
          // is empty.
          body: JSON.stringify({ note: "Listing moved.", title: "Ridgeline Dental" }),
        }),
      );
    });
  });

  it("shows an existing correction beside what it overrode", () => {
    renderReport(
      report({
        status: "corrected",
        actions: [],
        resolution: {
          action: "correct",
          note: "Listing moved.",
          actorId: "admin-1",
          createdAt: "2026-07-02T12:00:00Z",
        },
        lead: {
          id: 91,
          phone: "+15125550123",
          title: "Ridgeline Dental",
          state: "TX",
          suppressed: false,
          correction: {
            id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            title: "Ridgeline Dental",
            state: "TX",
            reason: "Listing moved.",
            createdAt: "2026-07-02T12:00:00Z",
            basedOnKind: "current_listing",
            basedOnLabel: "Current listing",
            basedOnTitle: "Ridgeline Roofing",
            basedOnState: "TX",
            actions: [REMOVE_CORRECTION],
          },
        },
      }),
    );

    const controls = screen.getByRole("region", { name: "Current controls" });
    expect(
      within(controls).getByRole("heading", { name: "Lead Correction" }),
    ).toBeVisible();
    expect(
      within(controls).getByRole("heading", { name: "What it overrides" }),
    ).toBeVisible();
    expect(within(controls).getByText("Ridgeline Dental")).toBeVisible();
    expect(within(controls).getByText("Ridgeline Roofing")).toBeVisible();
    expect(
      within(controls).getByRole("button", { name: "Remove correction" }),
    ).toBeVisible();
  });

  it("removes a correction through the Lead, not the report", async () => {
    const user = userEvent.setup();
    const fetchSpy = okFetch();
    renderReport(
      report({
        lead: {
          id: 91,
          phone: "+15125550123",
          title: "Ridgeline Dental",
          state: "TX",
          suppressed: false,
          correction: {
            id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            title: "Ridgeline Dental",
            state: "TX",
            reason: "Listing moved.",
            createdAt: "2026-07-02T12:00:00Z",
            basedOnKind: "current_listing",
            basedOnLabel: "Current listing",
            basedOnTitle: "Ridgeline Roofing",
            basedOnState: "TX",
            actions: [REMOVE_CORRECTION],
          },
        },
      }),
    );

    await user.click(screen.getByRole("button", { name: "Remove correction" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Correction was wrong.",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Remove correction" }),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/leads/91/correction",
        expect.objectContaining({
          method: "DELETE",
          body: JSON.stringify({ reason: "Correction was wrong." }),
        }),
      );
    });
  });
});

describe("the Eligibility Hold rule is stated, not implied", () => {
  it("renders the release rule verbatim and names who cannot release it", () => {
    renderReport(report());

    const controls = screen.getByRole("region", { name: "Current controls" });
    expect(within(controls).getByText(HOLD_RELEASE)).toBeVisible();
    expect(
      within(controls).getByText(
        /A Customer cannot release or bypass this Eligibility Hold/,
      ),
    ).toBeVisible();
    expect(within(controls).getByText("Held")).toBeVisible();
  });
});

describe("restoring is not a promise of allocation", () => {
  it("shows the restore notice wherever a restore is offered", () => {
    renderReport(
      report({
        status: "suppressed",
        controls: {
          ...report().controls,
          eligibilityHeld: false,
          suppressed: true,
          suppressionReason: "Reported as a wrong number twice.",
          actions: [RESTORE],
        },
        lead: { ...report().lead, suppressed: true },
        // Restoring is a control on the Lead, not a second decision about the
        // report, so it stays offered after the report is closed.
        actions: [],
      }),
    );

    expect(screen.getByRole("button", { name: "Restore" })).toBeVisible();
    // Once beside the suppression state, once beside the control that offers it.
    expect(screen.getAllByText(RESTORE_NOTICE).length).toBeGreaterThanOrEqual(2);
  });

  it("restores through the Lead's suppression, with a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = okFetch();
    renderReport(
      report({
        controls: { ...report().controls, suppressed: true },
        actions: [RESTORE],
      }),
    );

    await user.click(screen.getByRole("button", { name: "Restore" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Report was mistaken.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Restore" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/leads/91/suppression",
        expect.objectContaining({
          method: "DELETE",
          body: JSON.stringify({ reason: "Report was mistaken." }),
        }),
      );
    });
  });

  it("refuses to restore without a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderReport(
      report({
        controls: { ...report().controls, suppressed: true },
        actions: [RESTORE],
      }),
    );

    await user.click(screen.getByRole("button", { name: "Restore" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Restore" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "A reason is required.",
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("a decided Lead Report", () => {
  it("shows the resolution and offers no second decision", () => {
    renderReport(
      report({
        status: "dismissed",
        actions: [],
        controls: { ...report().controls, eligibilityHeld: false },
        resolution: {
          action: "dismiss",
          note: "Number verified as correct.",
          actorId: "admin-1",
          createdAt: "2026-07-02T12:00:00Z",
        },
      }),
    );

    const decision = screen.getByRole("region", { name: "Decision" });
    expect(within(decision).getByText("Number verified as correct.")).toBeVisible();
    expect(within(decision).getByText("admin-1")).toBeVisible();
    expect(
      within(decision).getByText(
        "This Lead Report is closed and cannot be decided again.",
      ),
    ).toBeVisible();
    for (const label of ["Dismiss", "Correct", "Suppress"]) {
      expect(within(decision).queryByRole("button", { name: label })).toBeNull();
    }
  });
});

describe("a stale view", () => {
  it("keeps the dialog open and reports why the decision was refused", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "This Lead Report has already been resolved." }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderReport(report());

    await user.click(
      within(screen.getByRole("region", { name: "Decision" })).getByRole(
        "button",
        { name: "Suppress" },
      ),
    );
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Bad Lead.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Suppress" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This Lead Report has already been resolved.",
    );
    expect(dialog).toBeVisible();
  });
});
