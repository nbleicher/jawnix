import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CreditWalletProvider,
  useCreditWallet,
} from "./CreditWalletContext";
import type { CreditWallet } from "./wallet";

const PROCESSING: CreditWallet = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 1_000,
  balanceCents: 0,
  activeHoldsCents: 0,
  availableBalanceCents: 0,
  purchases: [
    {
      id: "purchase-1",
      amountCents: 1_000,
      status: "processing",
      createdAt: "2026-08-03T12:00:00Z",
      completedAt: null,
    },
  ],
  ledger: [],
};

function Probe() {
  const { wallet } = useCreditWallet();
  return <div>{wallet ? wallet.balanceCents : "missing"}</div>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("CreditWalletProvider", () => {
  it("preserves a known wallet through a transient poll failure and retries", async () => {
    vi.useFakeTimers();
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        call += 1;
        if (call === 2) return Promise.reject(new Error("temporary 503"));
        const wallet =
          call >= 3
            ? {
                ...PROCESSING,
                balanceCents: 1_000,
                availableBalanceCents: 1_000,
                purchases: [
                  { ...PROCESSING.purchases[0], status: "completed" as const },
                ],
              }
            : PROCESSING;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(wallet),
        } as Response);
      }),
    );

    render(
      <CreditWalletProvider>
        <Probe />
      </CreditWalletProvider>,
    );
    await act(async () => Promise.resolve());
    expect(screen.getByText("0")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(screen.getByText("0")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(screen.getByText("1000")).toBeVisible();
  });

  it("keeps a known wallet through a spurious 401 and backs off", async () => {
    vi.useFakeTimers();
    let call = 0;
    const fetchMock = vi.fn(() => {
      call += 1;
      if (call === 1) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(PROCESSING),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 401 } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <CreditWalletProvider>
        <Probe />
      </CreditWalletProvider>,
    );
    await act(async () => Promise.resolve());
    expect(screen.getByText("0")).toBeVisible();

    // A 401 is a failure to read, not an answer that billing does not apply.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("0")).toBeVisible();
    expect(screen.queryByText("missing")).toBeNull();

    // The first retry keeps the poll cadence; each further failure doubles
    // the wait, so a sustained outage is not hammered every 2.5 seconds.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
