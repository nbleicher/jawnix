import { useEffect, useState } from "react";
import { useLocation, useSearchParams } from "react-router";

import { useCreditWallet } from "./CreditWalletContext";
import { hasProcessingPurchase } from "./wallet";

import "./PurchaseReturnNotice.css";

type Outcome = "success" | "cancelled";

function outcomeOf(value: string | null): Outcome | null {
  return value === "success" || value === "cancelled" ? value : null;
}

/**
 * The Stripe Checkout outcome, at the top of whichever page Checkout returned
 * the Customer to.
 *
 * Checkout appends `?purchase=success|cancelled` to the page the purchase
 * started on. The notice reads it once, removes it from the address so a
 * reload does not repeat it, and stays until the Customer leaves the page.
 * Money events never vanish on a timer.
 */
export function PurchaseReturnNotice() {
  const { wallet, refresh } = useCreditWallet();
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const [shown, setShown] = useState<{ outcome: Outcome; pathname: string } | null>(
    () => {
      const outcome = outcomeOf(params.get("purchase"));
      return outcome ? { outcome, pathname } : null;
    },
  );

  const returned = outcomeOf(params.get("purchase"));
  useEffect(() => {
    if (!returned) return;
    setShown({ outcome: returned, pathname });
    const next = new URLSearchParams(params);
    next.delete("purchase");
    setParams(next, { replace: true });
  }, [returned, pathname, params, setParams]);

  // Leaving the page dismisses the notice; coming back does not repeat it.
  useEffect(() => {
    setShown((current) =>
      current && current.pathname !== pathname ? null : current,
    );
  }, [pathname]);

  useEffect(() => {
    if (shown?.outcome === "success") void refresh();
  }, [shown?.outcome, refresh]);

  if (!shown || shown.pathname !== pathname) return null;

  if (shown.outcome === "cancelled") {
    return (
      <div
        className="purchase-return-notice purchase-return-notice--neutral"
        role="status"
      >
        Payment canceled. You were not charged.
      </div>
    );
  }

  const processing = wallet === null || hasProcessingPurchase(wallet);
  const failed =
    wallet !== null
    && ["failed", "expired"].includes(wallet.purchases[0]?.status ?? "");
  const tone = processing ? "info" : failed ? "danger" : "success";
  return (
    <div
      className={`purchase-return-notice purchase-return-notice--${tone}`}
      role="status"
    >
      {processing
        ? "Payment processing. Your balance updates as soon as Stripe confirms it."
        : failed
          ? "Payment not completed. You were not charged."
          : "Payment received. Your balance has been updated."}
    </div>
  );
}
