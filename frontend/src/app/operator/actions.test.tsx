import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ControlActionBar,
  RenotifyAction,
  leadControlRequest,
  postReason,
  requestEndpoint,
} from "./actions";
import type { ControlAction } from "./actions";

/* The shared confirmation flow, tested on its own now that the Fulfillment
   workspace that used to exercise it is gone (A4). */

const REQUEST_ID = "11111111-1111-1111-1111-111111111111";

function action(overrides: Partial<ControlAction> = {}): ControlAction {
  return {
    name: "approve",
    label: "Approve",
    consequence: "Authorizes this Batch Request's first allocation attempt.",
    requiresReason: true,
    destructive: false,
    ...overrides,
  };
}

function renderInRouter(element: ReactElement) {
  const router = createMemoryRouter([{ id: "page", path: "/", loader: () => null, element }], {
    hydrationData: { loaderData: { page: null } },
  });
  render(<RouterProvider router={router} />);
}

function respond(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("confirming a consequential action", () => {
  it("offers exactly the server's actions, and says so when there are none", () => {
    renderInRouter(
      <>
        <ControlActionBar actions={[action()]} resolve={postReason(requestEndpoint(REQUEST_ID))} settled="" />
        <ControlActionBar actions={[]} resolve={postReason(requestEndpoint(REQUEST_ID))} settled="No action is valid in this state." />
      </>,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(screen.getByText("No action is valid in this state.")).toBeVisible();
  });

  it("states the consequence and posts the typed reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond({ ok: true });
    renderInRouter(
      <ControlActionBar actions={[action()]} resolve={postReason(requestEndpoint(REQUEST_ID))} settled="" />,
    );

    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(/Authorizes this Batch Request's first allocation attempt/);
    await user.type(screen.getByLabelText("Reason (required)"), "Inventory verified.");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/requests/${REQUEST_ID}/approve`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ reason: "Inventory verified." }),
        }),
      );
    });
  });

  it("refuses to submit without a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderInRouter(
      <ControlActionBar actions={[action()]} resolve={postReason(requestEndpoint(REQUEST_ID))} settled="" />,
    );

    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("A reason is required.")).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("surfaces a refused action without losing the dialog", async () => {
    const user = userEvent.setup();
    respond({ detail: "Action approve is not valid while request is approved." }, 409);
    renderInRouter(
      <ControlActionBar
        actions={[action()]}
        resolve={postReason(requestEndpoint(REQUEST_ID))}
        settled=""
        refresh={() => undefined}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(screen.getByLabelText("Reason (required)"), "Stale view.");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Action approve is not valid while request is approved.")).toBeVisible();
  });

  it("routes regeneration to the artifact endpoint, not the transition one", () => {
    const resolve = postReason(requestEndpoint(REQUEST_ID));
    expect(resolve(action({ name: "regenerate" }), "Customer asked.", {}).url).toBe(
      `/api/admin/requests/${REQUEST_ID}/artifact/regenerate`,
    );
    expect(resolve(action({ name: "retry_delivery" }), "x", {}).url).toBe(
      `/api/admin/requests/${REQUEST_ID}/retry_delivery`,
    );
  });
});

describe("Lead-level controls", () => {
  it("send restore and suppression through the Lead's suppression", () => {
    const resolve = leadControlRequest(91);
    expect(resolve(action({ name: "restore" }), "Mistaken.", {})).toEqual({
      url: "/api/admin/leads/91/suppression",
      method: "DELETE",
      body: { reason: "Mistaken." },
    });
    expect(resolve(action({ name: "suppress", destructive: true }), "Disconnected.", {})).toEqual({
      url: "/api/admin/leads/91/suppression",
      method: "PUT",
      body: { reason: "Disconnected." },
    });
    expect(resolve(action({ name: "remove-correction" }), "Wrong.", {}).method).toBe("DELETE");
  });

  it("send a correction with only the values that were typed", () => {
    const resolve = leadControlRequest(91);
    const correct = action({ name: "correct", requiresOverride: true });
    expect(resolve(correct, "Renamed.", { title: " Nashville Dental ", state: "" })).toEqual({
      url: "/api/admin/leads/91/correction",
      method: "PUT",
      body: { reason: "Renamed.", title: "Nashville Dental" },
    });
  });
});

describe("re-notifying Telegram", () => {
  it("posts its reason to the notify endpoint and says it was queued", async () => {
    const user = userEvent.setup();
    const fetchSpy = respond({ requestId: REQUEST_ID, jobId: 9, jobKind: "update_notification" });
    renderInRouter(<RenotifyAction requestId={REQUEST_ID} />);

    await user.click(screen.getByRole("button", { name: "Re-notify in Telegram" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(/The Batch Request itself does not change/);
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Telegram message was deleted.");
    await user.click(within(dialog).getByRole("button", { name: "Re-notify" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        `/api/admin/requests/${REQUEST_ID}/notify`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ reason: "Telegram message was deleted." }),
        }),
      );
    });
    expect(await screen.findByText("Telegram re-notification queued.")).toBeVisible();
  });

  it("refuses to re-notify without a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderInRouter(<RenotifyAction requestId={REQUEST_ID} />);

    await user.click(screen.getByRole("button", { name: "Re-notify in Telegram" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Re-notify" }));

    expect(within(dialog).getByRole("alert")).toHaveTextContent("A reason is required.");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
