import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminLeadLookupRoute } from "./AdminLeadLookup";
import type { LeadLookupResult } from "./AdminLeadLookup";

const RESULT: LeadLookupResult = {
  query: "(615) 555-0142",
  phone: "6155550142",
  valid: true,
  lead: {
    id: 77,
    phone: "6155550142",
    title: "Nashville Family Dental",
    state: "TN",
    niche: "dentist",
    firstSeenAt: "2026-09-14T06:00:00Z",
    lastDeliveredAt: "2026-11-06T15:00:00Z",
  },
  usable: { usable: true, reasons: [] },
  databases: {
    db1: { member: true, source: "Google Maps listing" },
    db2: {
      member: true,
      state: "TN",
      upload: { id: "u1", filename: "tn-agents-nov.csv", uploadedBy: "Alex", at: "2026-11-06T12:00:00Z" },
    },
  },
  deliveries: [
    {
      id: 2,
      deliveredAt: "2026-11-06T15:00:00Z",
      customer: { id: 4, name: "Keystone Medicare", href: "/app/admin/customers/4" },
      customerName: "Keystone Medicare",
      agencyName: "",
      batch: { number: 1028, href: "/app/admin/batches/1028" },
      database: "db2",
      title: "Nashville Family Dental",
      state: "TN",
      disposition: null,
    },
    {
      id: 1,
      deliveredAt: "2026-10-21T15:00:00Z",
      customer: { id: 3, name: "Harbor Insurance", href: "/app/admin/customers/3" },
      customerName: "Harbor Insurance",
      agencyName: "Gulf Coast Agency",
      batch: { number: 1010, href: "/app/admin/batches/1010" },
      database: "db1",
      title: "Nashville Family Dental",
      state: "TN",
      disposition: "Positive response",
    },
  ],
  listings: [
    {
      database: "db2",
      observedAt: "2026-11-06T12:00:00Z",
      source: "DB2 · tn-agents-nov.csv (Alex)",
      title: "Nashville Family Dental",
      state: "TN",
      valid: true,
    },
    {
      database: "db1",
      observedAt: "2026-11-02T06:00:00Z",
      source: "Run 39 · dentist · TN",
      title: "Nashville Family Dental",
      state: "TN",
      valid: true,
      current: true,
    },
  ],
  reports: [],
  holds: [],
  suppression: { suppressed: false, reason: "", restoreNotice: "Restoring returns it to the ordinary rules." },
  corrections: [],
  exclusions: [],
  nextRecipients: [
    { key: "customer:4", label: "Keystone Medicare", status: "never", reason: "delivered", at: "2026-11-06T15:00:00Z", count: 1, customers: ["Keystone Medicare"] },
    { key: "customer:3", label: "Harbor Insurance", status: "never", reason: "delivered", at: "2026-10-21T15:00:00Z", count: 1, customers: ["Harbor Insurance"] },
    { key: "agency:2", label: "Gulf Coast Agency members", status: "never", reason: "shared_history", count: 2, customers: ["Pelican", "Tarpon"] },
    { key: "cooldown:7:x", label: "Customers with 7-day cooldown", status: "later", reason: "cooldown", at: "2026-11-13T15:00:00Z", count: 3, customers: ["A", "B", "C"] },
    { key: "not_in_database:db1", label: "DB1 customers", status: "blocked", reason: "not_in_database", database: "db1", count: 1, customers: ["D"] },
  ],
  evidence: {
    kind: "current_listing",
    label: "Current Listing (Google Maps observation)",
    title: "Nashville Family Dental",
    state: "TN",
    observationId: 9,
    observedAt: "2026-11-02T06:00:00Z",
    source: "TN::dentist",
  },
  actions: [
    {
      name: "correct",
      label: "Correct title or state",
      consequence: "Overrides the Lead's title or state for every future delivery.",
      destructive: false,
      requiresReason: true,
      requiresOverride: true,
    },
    {
      name: "suppress",
      label: "Suppress",
      consequence: "Makes this Lead ineligible for every Customer until an administrator restores it.",
      destructive: true,
      requiresReason: true,
      requiresOverride: false,
    },
  ],
};

function renderLookup(data: LeadLookupResult | null, path = "/admin/inventory/lookup?phone=6155550142") {
  const router = createMemoryRouter(
    [{ id: "lookup", path: "/admin/inventory/lookup", loader: () => data, element: <AdminLeadLookupRoute /> }],
    { initialEntries: [path], hydrationData: { loaderData: { lookup: data } } },
  );
  render(<RouterProvider router={router} />);
  return router;
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("Lead lookup", () => {
  it("starts with the lead, whether it is usable, its databases, and its deliveries", () => {
    renderLookup(RESULT);
    const summary = screen.getByLabelText("Lead summary");
    expect(within(summary).getAllByRole("term").map((term) => term.textContent)).toEqual([
      "Lead",
      "Usable",
      "Databases",
      "Deliveries",
    ]);
    expect(within(summary).getByText("(615) 555-0142")).toBeVisible();
    expect(within(summary).getByText("Nashville Family Dental · TN · dentist")).toBeVisible();
    expect(within(summary).getByText("Not suppressed · no hold · not excluded")).toBeVisible();
    expect(within(summary).getByText("DB1 + DB2")).toBeVisible();
    expect(within(summary).getByText("Google Maps listing and CSV upload tn-agents-nov.csv")).toBeVisible();
    expect(within(summary).getByText("Last Nov 6 to Keystone Medicare")).toBeVisible();
    expect(screen.getByLabelText("Phone number")).toHaveValue("(615) 555-0142");
  });

  it("lists deliveries with linked customers and batches", () => {
    renderLookup(RESULT);
    const deliveries = screen.getByRole("table", { name: "Deliveries" });
    expect(within(deliveries).getByRole("link", { name: "Keystone Medicare" })).toHaveAttribute("href", "/admin/customers/4");
    expect(within(deliveries).getByRole("link", { name: "#1028" })).toHaveAttribute("href", "/admin/batches/1028");
    expect(within(deliveries).getByText("Positive response")).toBeVisible();
    expect(within(deliveries).getByText("None yet")).toBeVisible();
    const listings = screen.getByRole("table", { name: "Listing history" });
    expect(within(listings).getByText("Run 39 · dentist · TN")).toBeVisible();
    expect(screen.getByText("No reports, holds, suppressions, corrections, or exclusion lists.")).toBeVisible();
  });

  it("says who can receive it next, and when", () => {
    renderLookup(RESULT);
    const next = screen.getByLabelText("Who can receive it next", { selector: "dl" });
    const rows = within(next)
      .getAllByRole("term")
      .map((term) => `${term.textContent} → ${term.nextElementSibling?.textContent?.replaceAll("\u00a0", " ")}`);
    expect(rows).toEqual([
      "Keystone Medicare → Never · delivered Nov 6",
      "Harbor Insurance → Never · delivered Oct 21",
      "Gulf Coast Agency members (2) → Never · shared history",
      "Customers with 7-day cooldown (3) → From Nov 13",
      "DB1 customers (1) → Not in DB1",
    ]);
  });

  it("corrects the title against the evidence it overrides, with a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ leadId: 77 }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    renderLookup(RESULT);

    await user.click(screen.getByRole("button", { name: "Correct title or state" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Current Listing (Google Maps observation)")).toBeVisible();
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Renamed in October.");
    await user.click(within(dialog).getByRole("button", { name: "Correct title or state" }));
    expect(within(dialog).getByText("Enter a corrected title, a corrected state, or both.")).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();

    await user.type(within(dialog).getByLabelText("Corrected title"), "Nashville Dental");
    await user.click(within(dialog).getByRole("button", { name: "Correct title or state" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/leads/77/correction",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ reason: "Renamed in October.", title: "Nashville Dental" }),
        }),
      );
    });
  });

  it("names why a lead is not usable and shows its controls", () => {
    renderLookup({
      ...RESULT,
      usable: {
        usable: false,
        reasons: [
          { kind: "suppressed", label: "Suppressed", detail: "Disconnected" },
          { kind: "held", label: "On an Eligibility Hold", detail: "Invalid phone" },
        ],
      },
      suppression: { suppressed: true, reason: "Disconnected", restoreNotice: "Restoring returns it to the ordinary rules." },
      holds: [
        {
          id: "h1",
          reasonLabel: "Invalid phone",
          active: true,
          createdAt: "2026-11-07T10:00:00Z",
          releasedAt: null,
          reportHref: "/app/admin/fulfillment/reports/r1",
        },
      ],
    });
    const summary = screen.getByLabelText("Lead summary");
    expect(within(summary).getByText("No")).toBeVisible();
    expect(within(summary).getByText("Suppressed · On an Eligibility Hold")).toBeVisible();
    const holds = screen.getByRole("table", { name: "Eligibility holds" });
    expect(within(holds).getByRole("link", { name: "Invalid phone" })).toHaveAttribute("href", "/admin/fulfillment/reports/r1");
    expect(within(holds).getByText("Active")).toBeVisible();
    expect(screen.getByText("Restoring returns it to the ordinary rules.")).toBeVisible();
  });

  it("explains an unknown number and a query that is not a phone", () => {
    renderLookup(
      { query: "212-555-0100", phone: "2125550100", valid: true, lead: null, exclusions: [] },
      "/admin/inventory/lookup?phone=212-555-0100",
    );
    expect(screen.getByText("No lead in DB1 or DB2 has the number (212) 555-0100.")).toBeVisible();
  });

  it("invites a phone number when there is none yet", () => {
    renderLookup(null, "/admin/inventory/lookup");
    expect(
      screen.getByText("Enter a phone number to see its databases, deliveries, listings, and who can receive it next."),
    ).toBeVisible();
  });
});
