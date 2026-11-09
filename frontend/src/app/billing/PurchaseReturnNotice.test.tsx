import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Link, Outlet, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreditWalletProvider } from "./CreditWalletContext";
import { PurchaseReturnNotice } from "./PurchaseReturnNotice";
import type { CreditWallet } from "./wallet";

const COMPLETED: CreditWallet = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 500,
  balanceCents: 4_000,
  activeHoldsCents: 0,
  availableBalanceCents: 4_000,
  purchases: [
    {
      id: "purchase-1",
      amountCents: 4_000,
      status: "completed",
      createdAt: "2026-08-03T12:00:00Z",
      completedAt: "2026-08-03T12:01:00Z",
    },
  ],
  ledger: [],
};

async function renderShell(initialEntry: string) {
  const router = createMemoryRouter(
    [
      {
        path: "/app",
        element: (
          <main>
            <PurchaseReturnNotice />
            <Outlet />
          </main>
        ),
        children: [
          {
            path: "requests",
            element: (
              <>
                <h1>Requests</h1>
                <Link to="/app/overview">Overview</Link>
              </>
            ),
          },
          { path: "overview", element: <h1>Overview</h1> },
        ],
      },
    ],
    { initialEntries: [initialEntry] },
  );
  render(
    <CreditWalletProvider>
      <RouterProvider router={router} />
    </CreditWalletProvider>,
  );
  // Let the wallet read settle inside act.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return router;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PurchaseReturnNotice", () => {
  it("confirms the payment at the top of the page Checkout returned to", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(COMPLETED), { status: 200 })),
      ),
    );
    const router = await renderShell("/app/requests?quantity=750&purchase=success");

    const notice = await screen.findByRole("status");
    await waitFor(() =>
      expect(notice).toHaveTextContent(
        "Payment received. Your balance has been updated.",
      ),
    );
    // Above the page's own header.
    expect(
      notice.compareDocumentPosition(screen.getByRole("heading", { name: "Requests" }))
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The outcome leaves the address; everything else the page needs stays.
    await waitFor(() =>
      expect(router.state.location.search).toBe("?quantity=750"),
    );
    expect(screen.getByRole("status")).toBeVisible();
  });

  it("says a cancelled checkout charged nothing, until the page is left", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(COMPLETED), { status: 200 })),
      ),
    );
    await renderShell("/app/requests?purchase=cancelled");

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Payment canceled. You were not charged.",
    );

    await user.click(screen.getByRole("link", { name: "Overview" }));

    expect(
      await screen.findByRole("heading", { name: "Overview" }),
    ).toBeVisible();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows nothing on an ordinary visit", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(COMPLETED), { status: 200 })),
      ),
    );
    await renderShell("/app/requests");

    expect(screen.queryByRole("status")).toBeNull();
  });
});
